# -*- coding: utf-8 -*-
import os
import sys
from argparse import ArgumentParser
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookParser
from linebot.exceptions import InvalidSignatureError, LineBotApiError
from linebot.models import (
    MessageEvent, TextMessage, ImageMessage,
    TextSendMessage, SourceGroup, SourceRoom,
)
import cloudinary
import cloudinary.uploader
from canteen_db import CanteenDB, CATEGORIES, PRICE_RANGES
from state_manager import StateManager, State
from ai_analyzer import analyze_food_image
from flex_messages import (
    restaurant_list_flex, restaurant_detail_flex,
    share_success_flex, filter_menu_flex, photos_carousel_flex,
)

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

# 管理員 LINE user_id（多個用逗號隔開）
ADMIN_IDS = set(os.getenv('ADMIN_IDS', '').split(','))

db = CanteenDB()
states = StateManager()

CATEGORY_MENU = "請選擇分類：\n" + "\n".join(f"{i + 1}. {c}" for i, c in enumerate(CATEGORIES)) + "\n\n輸入 1~7"
PRICE_MENU = "請選擇價位區間：\n" + "\n".join(f"{i + 1}. {p}" for i, p in enumerate(PRICE_RANGES)) + "\n\n輸入 1~4"


def is_group(event) -> bool:
    return isinstance(event.source, (SourceGroup, SourceRoom))


def is_admin(user_id: str) -> bool:
    return user_id in ADMIN_IDS and '' not in ADMIN_IDS


def upload_image(image_bytes: bytes, message_id: str) -> str:
    result = cloudinary.uploader.upload(
        image_bytes, public_id=f'canteen/{message_id}', overwrite=True,
    )
    return result['secure_url']


def reply(token, msg):
    if isinstance(msg, str):
        line_bot_api.reply_message(token, TextSendMessage(text=msg))
    else:
        line_bot_api.reply_message(token, msg)


def show_detail(reply_token, user_id, restaurant_id, id_list=None):
    r = db.get_by_id(restaurant_id)
    if not r:
        reply(reply_token, '找不到這筆資料')
        return
    db.log_view(restaurant_id, user_id)
    liked = db.has_liked(restaurant_id, user_id)
    comments = db.get_comments(restaurant_id, limit=3)
    states.set(user_id, State.WAIT_LIKE)
    states.set_data(user_id, 'view_id', restaurant_id)
    if id_list is not None:
        states.set_data(user_id, 'list', id_list)
    reply(reply_token, restaurant_detail_flex(r, liked, comments))


def refresh_detail(user_id, restaurant_id):
    r = db.get_by_id(restaurant_id)
    if not r:
        return
    liked = db.has_liked(restaurant_id, user_id)
    comments = db.get_comments(restaurant_id, limit=3)
    states.set(user_id, State.WAIT_LIKE)
    states.set_data(user_id, 'view_id', restaurant_id)
    line_bot_api.push_message(user_id, restaurant_detail_flex(r, liked, comments))


def notify_admin(photo_id: int, restaurant_name: str, report_status: str):
    """通知管理員有照片被檢舉"""
    if not ADMIN_IDS or '' in ADMIN_IDS:
        return
    status_text = {
        'pending_review': '⚠️ 待審核（達3個檢舉）',
        'hidden': '🚫 已暫時下架（達10個檢舉）',
    }.get(report_status, '')
    if not status_text:
        return
    msg = (
        f"🚩 照片檢舉通知\n\n"
        f"餐廳：{restaurant_name}\n"
        f"照片 ID：{photo_id}\n"
        f"狀態：{status_text}\n\n"
        f"管理員指令：\n"
        f"刪照片 {photo_id}　→ 永久刪除\n"
        f"恢復照片 {photo_id}　→ 恢復上架"
    )
    for admin_id in ADMIN_IDS:
        if admin_id:
            try:
                line_bot_api.push_message(admin_id, TextSendMessage(text=msg))
            except Exception:
                pass


def handle_photos_view(reply_token, user_id, rid):
    r = db.get_by_id(rid)
    if r and r.get('photos'):
        liked_ids = {p['id'] for p in r['photos'] if db.has_photo_liked(p['id'], user_id)}
        reported_ids = {p['id'] for p in r['photos'] if db.has_reported(p['id'], user_id)}
        reply(reply_token, photos_carousel_flex(r['name'], r['photos'], liked_ids, reported_ids))


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
        group = is_group(event)

        # ── 圖片訊息 ──────────────────────────────────────────────────────────
        if isinstance(event.message, ImageMessage):
            if group:
                continue
            raw = line_bot_api.get_message_content(event.message.id)
            image_bytes = b''.join(raw.iter_content())
            if state == State.WAIT_IMAGE:
                try:
                    image_url = upload_image(image_bytes, event.message.id)
                    states.set_data(user_id, 'image_url', image_url)
                    reply(reply_token, '📷 照片收到，AI 辨識中...')
                    ai = analyze_food_image(image_bytes)
                    states.set_data(user_id, 'ai_category', ai['category'])
                    states.set_data(user_id, 'ai_price', ai['price_range'])
                    states.set(user_id, State.WAIT_AI_CONFIRM)
                    confidence_note = {'high': '✅ 很有把握', 'medium': '🔍 有點把握', 'low': '⚠️ 建議手動修改'}.get(
                        ai['confidence'], '')
                    line_bot_api.push_message(user_id, TextSendMessage(
                        text=f"🤖 AI 辨識結果：\n🍽️ {ai['food_name']}\n🏷️ {ai['category']}\n💰 {ai['price_range']}\n{confidence_note}\n\n1. 確認使用\n2. 修改分類\n3. 修改價位\n4. 全部重選\n\n輸入 1~4"
                    ))
                except Exception as e:
                    print(f'Error: {e}')
                    reply(reply_token, '❌ 處理失敗，請再試一次')
            elif state == State.ADD_PHOTO:
                rid = states.get_data(user_id).get('view_id')
                try:
                    image_url = upload_image(image_bytes, event.message.id)
                    result = db.add_photo(rid, user_id, image_url)
                    if result == 'exceeded':
                        reply(reply_token, f'❌ 你對這家店已上傳 3 張照片，達到上限')
                    else:
                        reply(reply_token, '✅ 照片新增成功！')
                        refresh_detail(user_id, rid)
                except Exception as e:
                    print(f'Error: {e}')
                    reply(reply_token, '❌ 照片上傳失敗，請再試一次')
            else:
                reply(reply_token, '目前不需要照片，輸入「說明」查看功能')
            continue

        # ── 文字訊息 ──────────────────────────────────────────────────────────
        if not isinstance(event.message, TextMessage):
            continue
        msg = event.message.text.strip()

        # ── 管理員專屬指令（私訊才有效）──────────────────────────────────────
        if not group and is_admin(user_id):
            if msg.startswith('刪照片 ') and msg.split()[-1].isdigit():
                photo_id = int(msg.split()[-1])
                success = db.admin_delete_photo(photo_id)
                reply(reply_token, f'✅ 照片 {photo_id} 已永久刪除' if success else '找不到這張照片')
                continue
            if msg.startswith('恢復照片 ') and msg.split()[-1].isdigit():
                photo_id = int(msg.split()[-1])
                success = db.admin_restore_photo(photo_id)
                reply(reply_token, f'✅ 照片 {photo_id} 已恢復上架' if success else '找不到這張照片')
                continue
            if msg == '待審核照片':
                photos = db.admin_get_reported_photos()
                if not photos:
                    reply(reply_token, '目前沒有待審核的照片 ✅')
                else:
                    lines = ['🚩 待審核照片列表\n']
                    for p in photos:
                        status = '⚠️ 待審核' if p['status'] == 'pending_review' else '🚫 已下架'
                        lines.append(
                            f"ID:{p['id']} {status} {p['report_count']}個檢舉\n   餐廳：{p['restaurant_name']}\n   刪照片 {p['id']} / 恢復照片 {p['id']}")
                    reply(reply_token, '\n\n'.join(lines))
                continue

        # ══════════════════════════════════════════════════════════════════════
        # 群組
        # ══════════════════════════════════════════════════════════════════════
        if group:
            if msg == '近期推薦':
                restaurants = db.get_recent(limit=30)
                if not restaurants:
                    reply(reply_token, '目前還沒有