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

# ── LINE 設定 ─────────────────────────────────────────────────────────────────
channel_secret = os.getenv('LINE_CHANNEL_SECRET')
channel_access_token = os.getenv('LINE_CHANNEL_ACCESS_TOKEN')
if not channel_secret or not channel_access_token:
    print('Missing LINE credentials.')
    sys.exit(1)

line_bot_api = LineBotApi(channel_access_token)
parser = WebhookParser(channel_secret)

# ── Cloudinary 設定 ───────────────────────────────────────────────────────────
cloudinary.config(
    cloud_name=os.getenv('CLOUDINARY_CLOUD_NAME'),
    api_key=os.getenv('CLOUDINARY_API_KEY'),
    api_secret=os.getenv('CLOUDINARY_API_SECRET'),
)

db = CanteenDB()
states = StateManager()


# ── 圖片上傳 ──────────────────────────────────────────────────────────────────
def upload_image(image_content, message_id: str):
    raw_bytes = b''.join(image_content.iter_content())
    result = cloudinary.uploader.upload(
        raw_bytes,
        public_id=f'canteen/{message_id}',
        overwrite=True,
    )
    return result['secure_url']


# ── Webhook ───────────────────────────────────────────────────────────────────
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

        # ── 文字訊息 ──
        if isinstance(event.message, TextMessage):
            msg = event.message.text.strip()

            if msg in ('說明', 'help'):
                reply(reply_token, introduction())
                states.reset(user_id)
                continue

            if msg == '我要分享':
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

            if msg == '近期推薦':
                restaurants = db.get_recent(limit=10)
                if not restaurants:
                    reply(reply_token, '目前還沒有任何分享，輸入「我要分享」來成為第一個！')
                    continue
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

            reply(reply_token, '輸入「說明」查看所有功能 😊')

        # ── 圖片訊息 ──
        elif isinstance(event.message, ImageMessage):
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
            else:
                reply(reply_token, '目前不需要照片，輸入「說明」查看功能')

    return 'OK'


# ── 工具函式 ──────────────────────────────────────────────────────────────────
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


def introduction() -> str:
    return (
        '📋 學生餐廳分享系統 使用說明\n'
        '━━━━━━━━━━━━━━━━━━\n'
        '📤 分享餐廳：\n'
        '  輸入「我要分享」\n'
        '  → 店家名稱 → 食物照片 → 一句評論\n\n'
        '📋 查看推薦：\n'
        '  輸入「近期推薦」\n'
        '  → 看到列表後輸入 1~10\n'
        '  → 查看對應照片與評論\n\n'
        '📖 說明：輸入「說明」'
    )


if __name__ == '__main__':
    arg_parser = ArgumentParser()
    arg_parser.add_argument('-p', '--port', type=int, default=8000)
    arg_parser.add_argument('-d', '--debug', default=False)
    options = arg_parser.parse_args()
    app.run(debug=options.debug, port=options.port)
