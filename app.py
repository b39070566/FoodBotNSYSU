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

from canteen_db import CanteenDB, CATEGORIES, PRICE_RANGES
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

CATEGORY_MENU = "請選擇分類：\n" + "\n".join(
    f"{i+1}. {c}" for i, c in enumerate(CATEGORIES)
) + "\n\n輸入 1~7"

PRICE_MENU = "請選擇價位區間：\n" + "\n".join(
    f"{i+1}. {p}" for i, p in enumerate(PRICE_RANGES)
) + "\n\n輸入 1~4"

FILTER_MENU = "請選擇要篩選的分類：\n" + "\n".join(
    f"{i+1}. {c}" for i, c in enumerate(CATEGORIES)
) + "\n8. 全部\n\n輸入 1~8"


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
                rid = states.get_data(user_id).get('edit_id')
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
        if msg == '取消':
            states.reset(user_id)
            reply(reply_token, '已取消，輸入「說明」查看功能')
            continue

        if msg in ('說明', 'help'):
            reply(reply_token, introduction())
            states.reset(user_id)
            continue

        # ── 分享流程 ──────────────────────────────────────────────────────────
        if msg == '我要分享':
            states.reset(user_id)
            states.set(user_id, State.WAIT_NAME)
            reply(reply_token, '🍱 好！請先輸入【店家名稱】\n\n（輸入「取消」可隨時離開）')
            continue

        if state == State.WAIT_NAME:
            existing = db.find_by_name(msg)
            if existing:
                states.set_data(user_id, 'name', msg)
                states.set_data(user_id, 'dup_id', existing['id'])
                states.set(user_id, State.WAIT_DUP_CONFIRM)
                reply(reply_token,
                      f"⚠️ 「{msg}」已經有人分享過了！\n\n"
                      f"🏷️ {existing.get('category','')}\n"
                      f"💬 「{existing['review']}」\n\n"
                      f"你的按讚會直接加到這筆資料上。\n\n"
                      f"1. 幫這家店按讚 👍\n"
                      f"2. 還是要另外新增\n\n"
                      f"輸入 1 或 2")
            else:
                states.set_data(user_id, 'name', msg)
                states.set(user_id, State.WAIT_CATEGORY)
                reply(reply_token, f'✅ 店家名稱：{msg}\n\n' + CATEGORY_MENU)
            continue

        if state == State.WAIT_DUP_CONFIRM and msg.isdigit():
            choice = int(msg)
            if choice == 1:
                rid = states.get_data(user_id).get('dup_id')
                result = db.toggle_like(rid, user_id)
                r = db.get_by_id(rid)
                states.reset(user_id)
                if result == 'liked':
                    reply(reply_token, f"👍 已幫「{r['name']}」按讚！\n現在共 {r['like_count']} 個讚")
                else:
                    reply(reply_token, f"已收回「{r['name']}」的讚")
            elif choice == 2:
                states.set(user_id, State.WAIT_CATEGORY)
                reply(reply_token, CATEGORY_MENU)
            else:
                reply(reply_token, '請輸入 1 或 2')
            continue

        if state == State.WAIT_CATEGORY and msg.isdigit():
            idx = int(msg) - 1
            if 0 <= idx < len(CATEGORIES):
                states.set_data(user_id, 'category', CATEGORIES[idx])
                states.set(user_id, State.WAIT_PRICE)
                reply(reply_token, f'✅ 分類：{CATEGORIES[idx]}\n\n' + PRICE_MENU)
            else:
                reply(reply_token, CATEGORY_MENU)
            continue

        if state == State.WAIT_PRICE and msg.isdigit():
            idx = int(msg) - 1
            if 0 <= idx < len(PRICE_RANGES):
                states.set_data(user_id, 'price_range', PRICE_RANGES[idx])
                states.set(user_id, State.WAIT_IMAGE)
                reply(reply_token, f'✅ 價位：{PRICE_RANGES[idx]}\n\n請上傳一張【食物照片】📷')
            else:
                reply(reply_token, PRICE_MENU)
            continue

        if state == State.WAIT_REVIEW:
            data = states.get_data(user_id)
            db.add_restaurant(
                user_id=user_id,
                name=data['name'],
                category=data['category'],
                price_range=data['price_range'],
                image_url=data['image_url'],
                review=msg,
            )
            states.reset(user_id)
            reply(reply_token,
                  f"🎉 分享成功！\n\n"
                  f"📍 {data['name']}\n"
                  f"🏷️ {data['category']}　{data['price_range']}\n"
                  f"💬 「{msg}」\n\n"
                  f"其他同學可以輸入「近期推薦」查看！")
            continue

        # ── 近期推薦 ──────────────────────────────────────────────────────────
        if msg == '近期推薦':
            restaurants = db.get_recent(limit=30)
            if not restaurants:
                reply(reply_token, '目前還沒有任何分享，輸入「我要分享」來成為第一個！')
                continue
            states.reset(user_id)
            states.set(user_id, State.WAIT_PICK)
            states.set_data(user_id, 'list', [r['id'] for r in restaurants])
            reply(reply_token, format_list(restaurants))
            continue

        # ── 篩選分類 ──────────────────────────────────────────────────────────
        if msg == '篩選分類':
            states.reset(user_id)
            states.set(user_id, State.FILTER_PICK)
            reply(reply_token, FILTER_MENU)
            continue

        if state == State.FILTER_PICK and msg.isdigit():
            idx = int(msg) - 1
            if msg == '8' or idx == 7:
                # 全部
                restaurants = db.get_recent(limit=30)
                label = '全部推薦'
                category = None
            elif 0 <= idx < len(CATEGORIES):
                category = CATEGORIES[idx]
                restaurants = db.get_recent(limit=30, category=category)
                label = category
            else:
                reply(reply_token, FILTER_MENU)
                continue

            if not restaurants:
                states.reset(user_id)
                reply(reply_token, f'目前沒有「{label}」的分享')
                continue

            states.set(user_id, State.WAIT_PICK)
            states.set_data(user_id, 'list', [r['id'] for r in restaurants])
            reply(reply_token, f'📂 {label}\n\n' + format_list(restaurants))
            continue

        # ── 看店家詳情 ────────────────────────────────────────────────────────
        if state == State.WAIT_PICK and msg.isdigit():
            idx = int(msg) - 1
            id_list = states.get_data(user_id).get('list', [])
            if 0 <= idx < len(id_list):
                r = db.get_by_id(id_list[idx])
                if r:
                    liked = db.has_liked(r['id'], user_id)
                    states.set(user_id, State.WAIT_LIKE)
                    states.set_data(user_id, 'view_id', r['id'])
                    heart = '❤️' if liked else '🤍'
                    line_bot_api.reply_message(reply_token, [
                        ImageSendMessage(
                            original_content_url=r['image_url'],
                            preview_image_url=r['image_url'],
                        ),
                        TextSendMessage(
                            text=f"📍 {r['name']}\n"
                                 f"🏷️ {r.get('category','')}　{r.get('price_range','')}\n"
                                 f"💬 「{r['review']}」\n"
                                 f"👍 {r['like_count']} 個讚\n\n"
                                 f"輸入「讚」{heart} 按讚／收回讚\n"
                                 f"輸入「近期推薦」或「篩選分類」繼續瀏覽"
                        ),
                    ])
                    continue
            reply(reply_token, '請輸入有效的編號')
            continue

        if state == State.WAIT_LIKE and msg == '讚':
            rid = states.get_data(user_id).get('view_id')
            result = db.toggle_like(rid, user_id)
            r = db.get_by_id(rid)
            states.reset(user_id)
            if result == 'liked':
                reply(reply_token, f"👍 已幫「{r['name']}」按讚！\n現在共 {r['like_count']} 個讚\n\n輸入「近期推薦」或「篩選分類」繼續瀏覽")
            else:
                reply(reply_token, f"已收回「{r['name']}」的讚\n現在共 {r['like_count']} 個讚")
            continue

        # ── 管理我的分享 ──────────────────────────────────────────────────────
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
                r = db.get_by_id(rid)
                if r:
                    states.set(user_id, State.MANAGE_ACTION)
                    states.set_data(user_id, 'edit_id', rid)
                    reply(reply_token,
                          f"📍 {r['name']}\n"
                          f"🏷️ {r.get('category','')}　{r.get('price_range','')}\n"
                          f"💬 「{r['review']}」\n"
                          f"👍 {r['like_count']} 個讚\n\n"
                          f"要做什麼？\n"
                          f"1. 修改店家名稱\n"
                          f"2. 修改評論\n"
                          f"3. 修改照片\n"
                          f"4. 修改分類\n"
                          f"5. 修改價位\n"
                          f"6. 刪除這筆分享\n\n"
                          f"輸入 1~6")
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
                states.set(user_id, State.EDIT_CATEGORY)
                reply(reply_token, CATEGORY_MENU)
            elif action == 5:
                states.set(user_id, State.EDIT_PRICE)
                reply(reply_token, PRICE_MENU)
            elif action == 6:
                rid = states.get_data(user_id).get('edit_id')
                db.delete(rid, user_id)
                states.reset(user_id)
                reply(reply_token, '🗑️ 已刪除這筆分享')
            else:
                reply(reply_token, '請輸入 1~6')
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

        if state == State.EDIT_CATEGORY and msg.isdigit():
            idx = int(msg) - 1
            if 0 <= idx < len(CATEGORIES):
                rid = states.get_data(user_id).get('edit_id')
                db.update_category(rid, user_id, CATEGORIES[idx])
                states.reset(user_id)
                reply(reply_token, f'✅ 分類已更新為：{CATEGORIES[idx]}')
            else:
                reply(reply_token, CATEGORY_MENU)
            continue

        if state == State.EDIT_PRICE and msg.isdigit():
            idx = int(msg) - 1
            if 0 <= idx < len(PRICE_RANGES):
                rid = states.get_data(user_id).get('edit_id')
                db.update_price_range(rid, user_id, PRICE_RANGES[idx])
                states.reset(user_id)
                reply(reply_token, f'✅ 價位已更新為：{PRICE_RANGES[idx]}')
            else:
                reply(reply_token, PRICE_MENU)
            continue

        reply(reply_token, '輸入「說明」查看所有功能 😊')

    return 'OK'


def reply(token, text_or_list):
    if isinstance(text_or_list, str):
        line_bot_api.reply_message(token, TextSendMessage(text=text_or_list))
    else:
        line_bot_api.reply_message(token, text_or_list)


def format_list(restaurants: list) -> str:
    lines = ['🍽️ 推薦清單（按讚數＋新鮮度排序）\n']
    for i, r in enumerate(restaurants, 1):
        likes = r.get('like_count', 0)
        heart = f" 👍{likes}" if likes > 0 else ""
        lines.append(f"{i}. {r['name']}　{r.get('category','')}　{r.get('price_range','')}{heart}")
    lines.append('\n👉 輸入編號查看照片與評論')
    return '\n'.join(lines)


def format_my_list(restaurants: list) -> str:
    lines = ['📋 我的分享（輸入編號選擇要管理的）\n']
    for i, r in enumerate(restaurants, 1):
        preview = r['review'][:10] + ('...' if len(r['review']) > 10 else '')
        lines.append(f"{i}. {r['name']}　{r.get('category','')}\n   💬 「{preview}」")
    return '\n'.join(lines)


def introduction() -> str:
    return (
        '📋 學生餐廳分享系統 使用說明\n'
        '━━━━━━━━━━━━━━━━━━\n'
        '📤 我要分享\n'
        '   → 名稱→分類→價位→照片→評論\n\n'
        '📋 近期推薦\n'
        '   → 熱度排序，輸入編號看詳情\n\n'
        '🔍 篩選分類\n'
        '   → 選分類後看該分類推薦\n\n'
        '✏️ 管理我的分享\n'
        '   → 修改或刪除自己的貼文\n\n'
        '❌ 取消　　📖 說明'
    )


if __name__ == '__main__':
    arg_parser = ArgumentParser()
    arg_parser.add_argument('-p', '--port', type=int, default=8000)
    arg_parser.add_argument('-d', '--debug', default=False)
    options = arg_parser.parse_args()
    app.run(debug=options.debug, port=options.port)
