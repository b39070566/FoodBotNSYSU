# -*- coding: utf-8 -*-
import os
import sys
from argparse import ArgumentParser

from flask import Flask, request, abort
from linebot import LineBotApi, WebhookParser
from linebot.exceptions import InvalidSignatureError, LineBotApiError
from linebot.models import (
    MessageEvent, TextMessage, ImageMessage,
    TextSendMessage, ImageSendMessage,
)

import cloudinary
import cloudinary.uploader

from canteen_db import CanteenDB
from state_manager import StateManager, State

app = Flask(__name__)

channel_secret = os.getenv('LINE_CHANNEL_SECRET')
channel_access_token = os.getenv('LINE_CHANNEL_ACCESS_TOKEN')
if not channel_secret or not channel_access_token:
    print('Missing LINE credentials.')
    sys.exit(1)

line_bot_api = LineBotApi(channel_access_token)
parser = WebhookParser(channel_secret)

cloudinary.config(
    cloud_name=os.getenv('CLOUDINARY_CLOUD_NAME'),
    api_key=os.getenv('CLOUDINARY_API_KEY'),
    api_secret=os.getenv('CLOUDINARY_API_SECRET'),
)

db = CanteenDB()
states = StateManager()


def upload_image(image_content, message_id: str):
    raw_bytes = b''.join(image_content.iter_content())
    result = cloudinary.uploader.upload(
        raw_bytes,
        public_id=f'canteen/{message_id}',
        overwrite=True,
    )
    return result['secure_url']


@app.route('/callback', methods=['POST'])
def callback():
    if request.method != 'POST':
        return abort(400)
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    try:
        events = parser.parse(body, signature)
    except (InvalidSignatureError, LineBotApiError):
        return abort(400)

    for event in events:
        if not isinstance(event, MessageEvent):
            continue
        user_id = event.source.user_id
        reply_token = event.reply_token
        state = states.get(user_id)

        # ── 圖片訊息 ──────────────────────────────────────────────────────────
        if isinstance(event.message, ImageMessage):
            if state == State.WAIT_IMAGE:
                image_content = line_bot_api.get_message_content(event.message.id)
                try:
                    image_url = upload_image(image_content, event.message.id)
                    states.set_data(user_id, 'image_url', image_url)
                    states.set(user_id, State.WAIT_REVIEW)
                    reply(reply_token, '📷 照片收到！\n\n最後，請輸入一句【評論】\n例如：滷肉飯超香，CP 值超高！')
                except Exception as e:
                    print(f'Upload error: {e}')
                    reply(reply_token, '❌ 照片上傳失敗，請再試一次')

            elif state == State.EDIT_IMAGE:
                data = states.get_data(user_id)
                rid = data.get('edit_id')
                image_content = line_bot_api.get_message_content(event.message.id)
                try:
                    image_url = upload_image(image_content, event.message.id)
                    db.update_image(rid, user_id, image_url)
                    states.reset(user_id)
                    reply(reply_token, '✅ 照片已更新！')
                except Exception as e:
                    print(f'Upload error: {e}')
                    reply(reply_token, '❌ 照片上傳失敗，請再試一次')
            else:
                reply(reply_token, '目前不需要照片，輸入「說明」查看功能')
            continue

        # ── 文字訊息 ──────────────────────────────────────────────────────────
        if not isinstance(event.message, TextMessage):
            continue
        msg = event.message.text.strip()

        # 任何時候都能觸發
        if msg in ('說明', 'help'):
            reply(reply_token, introduction())
            states.reset(user_id)
            continue

        # ── 分享流程 ──
        if msg == '我要分享':
            states.reset(user_id)
            states.set(user_id, State.WAIT_NAME)
            reply(reply_token, '🍱 好！請先輸入【店家名稱】')
            continue

        if state == State.WAIT_NAME:
            states.set_data(user_id, 'name', msg)
            states.set(user_id, State.WAIT_IMAGE)
            reply(reply_token, f'✅ 店家名稱：{msg}\n\n請上傳一張【食物照片】📷')
            continue

        if state == State.WAIT_REVIEW:
            data = states.get_data(user_id)
            db.add_restaurant(
                user_id=user_id,
                name=data['name'],
                image_url=data['image_url'],
                review=msg,
            )
            states.reset(user_id)
            reply(reply_token,
                  f"🎉 分享成功！\n\n📍 {data['name']}\n💬 「{msg}」\n\n"
                  f"其他同學可以輸入「近期推薦」查看！")
            continue

        # ── 近期推薦 ──
        if msg == '近期推薦':
            restaurants = db.get_recent(limit=10)
            if not restaurants:
                reply(reply_token, '目前還沒有任何分享，輸入「我要分享」來成為第一個！')
                continue
            states.reset(user_id)
            states.set(user_id, State.WAIT_PICK)
            states.set_data(user_id, 'list', [r['id'] for r in restaurants])
            reply(reply_token, format_list(restaurants))
            continue

        if state == State.WAIT_PICK and msg.isdigit():
            idx = int(msg) - 1
            id_list = states.get_data(user_id).get('list', [])
            if 0 <= idx < len(id_list):
                restaurant = db.get_by_id(id_list[idx])
                if restaurant:
                    states.reset(user_id)
                    line_bot_api.reply_message(reply_token, [
                        ImageSendMessage(
                            original_content_url=restaurant['image_url'],
                            preview_image_url=restaurant['image_url'],
                        ),
                        TextSendMessage(
                            text=f"📍 {restaurant['name']}\n💬 「{restaurant['review']}」\n\n"
                                 f"還想看其他的嗎？輸入「近期推薦」繼續瀏覽"
                        ),
                    ])
                    continue
            reply(reply_token, '請輸入有效的編號 (1~10)')
            continue

        # ── 管理我的分享 ──
        if msg == '管理我的分享':
            my_list = db.get_by_user(user_id)
            if not my_list:
                reply(reply_token, '你還沒有任何分享，輸入「我要分享」新增！')
                continue
            states.reset(user_id)
            states.set(user_id, State.MANAGE_PICK)
            states.set_data(user_id, 'my_list', [r['id'] for r in my_list])
            reply(reply_token, format_my_list(my_list))
            continue

        if state == State.MANAGE_PICK and msg.isdigit():
            idx = int(msg) - 1
            my_list = states.get_data(user_id).get('my_list', [])
            if 0 <= idx < len(my_list):
                rid = my_list[idx]
                restaurant = db.get_by_id(rid)
                if restaurant:
                    states.set(user_id, State.MANAGE_ACTION)
                    states.set_data(user_id, 'edit_id', rid)
                    reply(reply_token,
                          f"📍 {restaurant['name']}\n💬 「{restaurant['review']}」\n\n"
                          f"要做什麼？\n"
                          f"1. 修改店家名稱\n"
                          f"2. 修改評論\n"
                          f"3. 修改照片\n"
                          f"4. 刪除這筆分享\n\n"
                          f"輸入 1~4")
                    continue
            reply(reply_token, '請輸入有效的編號')
            continue

        if state == State.MANAGE_ACTION and msg.isdigit():
            action = int(msg)
            if action == 1:
                states.set(user_id, State.EDIT_NAME)
                reply(reply_token, '請輸入新的【店家名稱】')
            elif action == 2:
                states.set(user_id, State.EDIT_REVIEW)
                reply(reply_token, '請輸入新的【評論】')
            elif action == 3:
                states.set(user_id, State.EDIT_IMAGE)
                reply(reply_token, '請上傳新的【食物照片】📷')
            elif action == 4:
                rid = states.get_data(user_id).get('edit_id')
                db.delete(rid, user_id)
                states.reset(user_id)
                reply(reply_token, '🗑️ 已刪除這筆分享')
            else:
                reply(reply_token, '請輸入 1~4')
            continue

        if state == State.EDIT_NAME:
            rid = states.get_data(user_id).get('edit_id')
            db.update_name(rid, user_id, msg)
            states.reset(user_id)
            reply(reply_token, f'✅ 店家名稱已更新為：{msg}')
            continue

        if state == State.EDIT_REVIEW:
            rid = states.get_data(user_id).get('edit_id')
            db.update_review(rid, user_id, msg)
            states.reset(user_id)
            reply(reply_token, f'✅ 評論已更新為：「{msg}」')
            continue

        # 預設
        reply(reply_token, '輸入「說明」查看所有功能 😊')

    return 'OK'


def reply(token, text_or_list):
    if isinstance(text_or_list, str):
        line_bot_api.reply_message(token, TextSendMessage(text=text_or_list))
    else:
        line_bot_api.reply_message(token, text_or_list)


def format_list(restaurants: list) -> str:
    lines = ['🍽️ 近期推薦餐廳（輸入編號查看照片與評論）\n']
    for i, r in enumerate(restaurants, 1):
        lines.append(f'{i}. {r["name"]}')
    lines.append('\n👉 輸入 1~10 查看詳細資訊')
    return '\n'.join(lines)


def format_my_list(restaurants: list) -> str:
    lines = ['📋 我的分享（輸入編號選擇要管理的）\n']
    for i, r in enumerate(restaurants, 1):
        lines.append(f'{i}. {r["name"]} ── 「{r["review"][:10]}{"..." if len(r["review"])>10 else ""}」')
    return '\n'.join(lines)


def introduction() -> str:
    return (
        '📋 學生餐廳分享系統 使用說明\n'
        '━━━━━━━━━━━━━━━━━━\n'
        '📤 分享餐廳：\n'
        '  輸入「我要分享」\n'
        '  → 店家名稱 → 食物照片 → 一句評論\n\n'
        '📋 查看推薦：\n'
        '  輸入「近期推薦」→ 輸入 1~10\n\n'
        '✏️ 管理我的分享：\n'
        '  輸入「管理我的分享」\n'
        '  → 選編號 → 修改名稱/評論/照片 或刪除\n\n'
        '📖 說明：輸入「說明」'
    )


if __name__ == '__main__':
    arg_parser = ArgumentParser()
    arg_parser.add_argument('-p', '--port', type=int, default=8000)
    arg_parser.add_argument('-d', '--debug', default=False)
    options = arg_parser.parse_args()
    app.run(debug=options.debug, port=options.port)
