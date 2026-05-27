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
                    reply(reply_token, '目前還沒有任何分享，私訊 Bot 輸入「我要分享」來新增！')
                    continue
                states.reset(user_id)
                states.set(user_id, State.WAIT_PICK)
                states.set_data(user_id, 'list', [r['id'] for r in restaurants])
                reply(reply_token, restaurant_list_flex(restaurants))
                continue
            if msg == '篩選分類':
                states.reset(user_id)
                states.set(user_id, State.FILTER_PICK)
                reply(reply_token, filter_menu_flex())
                continue
            if msg.startswith('分類選擇 ') and msg.split()[-1].isdigit():
                idx = int(msg.split()[-1]) - 1
                if idx == 7:
                    restaurants = db.get_recent(limit=30)
                    label = '全部推薦'
                elif 0 <= idx < len(CATEGORIES):
                    restaurants = db.get_recent(limit=30, category=CATEGORIES[idx])
                    label = CATEGORIES[idx]
                else:
                    continue
                if not restaurants:
                    reply(reply_token, f'目前沒有「{label}」的分享')
                    continue
                states.set(user_id, State.WAIT_PICK)
                states.set_data(user_id, 'list', [r['id'] for r in restaurants])
                reply(reply_token, restaurant_list_flex(restaurants))
                continue
            if msg.startswith('詳情 ') and msg.split()[-1].isdigit():
                idx = int(msg.split()[-1]) - 1
                id_list = states.get_data(user_id).get('list', [])
                if 0 <= idx < len(id_list):
                    show_detail(reply_token, user_id, id_list[idx], id_list)
                continue
            if state == State.WAIT_LIKE and msg == '讚':
                rid = states.get_data(user_id).get('view_id')
                result = db.toggle_like(rid, user_id)
                r = db.get_by_id(rid)
                reply(reply_token,
                      f"👍 已幫「{r['name']}」按讚！現在共 {r['like_count']} 個讚" if result == 'liked' else f"已收回「{r['name']}」的讚")
                refresh_detail(user_id, rid)
                continue
            if state == State.WAIT_LIKE and msg == '留評論':
                states.set(user_id, State.WAIT_COMMENT)
                reply(reply_token, '請輸入你的評論：')
                continue
            if state == State.WAIT_LIKE and msg == '新增照片':
                states.set(user_id, State.ADD_PHOTO)
                reply(reply_token, '請私訊 Bot 上傳照片 📷')
                continue
            if state == State.WAIT_LIKE and msg == '看所有照片':
                rid = states.get_data(user_id).get('view_id')
                handle_photos_view(reply_token, user_id, rid)
                continue
            if msg.startswith('照片讚 ') and msg.split()[-1].isdigit():
                photo_id = int(msg.split()[-1])
                result = db.toggle_photo_like(photo_id, user_id)
                p = db.get_photo_by_id(photo_id)
                if p:
                    reply(reply_token,
                          f"{'👍 按讚成功！' if result == 'liked' else '已收回讚'} 這張照片有 {p['like_count']} 個讚")
                continue
            if msg.startswith('檢舉照片 ') and msg.split()[-1].isdigit():
                photo_id = int(msg.split()[-1])
                p = db.get_photo_by_id(photo_id)
                if not p:
                    reply(reply_token, '找不到這張照片')
                    continue
                result = db.report_photo(photo_id, user_id)
                if result == 'already_reported':
                    reply(reply_token, '你已經檢舉過這張照片了')
                elif result == 'hidden':
                    reply(reply_token, '🚫 此照片已達檢舉上限，暫時下架等待管理員審核')
                    r = db.get_by_id(p['restaurant_id'])
                    notify_admin(photo_id, r['name'] if r else '未知', result)
                elif result == 'pending_review':
                    reply(reply_token, '⚠️ 檢舉已送出，此照片將等待管理員審核')
                    r = db.get_by_id(p['restaurant_id'])
                    notify_admin(photo_id, r['name'] if r else '未知', result)
                else:
                    reply(reply_token, '✅ 檢舉已送出，謝謝你的回報')
                continue
            if state == State.WAIT_COMMENT:
                rid = states.get_data(user_id).get('view_id')
                db.add_comment(rid, user_id, msg)
                reply(reply_token, f'✅ 評論已新增：「{msg}」')
                refresh_detail(user_id, rid)
                continue
            if msg in ('我要分享', '管理我的分享'):
                reply(reply_token, '請私訊 Bot 來進行這個操作 😊')
                continue
            continue

        # ── 私訊 ──────────────────────────────────────────────────────────
        if msg == '取消':
            states.reset(user_id)
            reply(reply_token, '已取消，輸入「說明」查看功能')
            continue
        if msg in ('說明', 'help'):
            reply(reply_token, introduction())
            states.reset(user_id)
            continue
        if state == State.WAIT_AI_CONFIRM and msg.isdigit():
            choice = int(msg)
            data = states.get_data(user_id)
            if choice == 1:
                states.set_data(user_id, 'category', data['ai_category'])
                states.set_data(user_id, 'price_range', data['ai_price'])
                states.set(user_id, State.WAIT_REVIEW)
                reply(reply_token, '✅ 好！請輸入一句【評論】')
            elif choice == 2:
                states.set(user_id, State.WAIT_CATEGORY)
                reply(reply_token, CATEGORY_MENU)
            elif choice == 3:
                states.set_data(user_id, 'category', data['ai_category'])
                states.set(user_id, State.WAIT_PRICE)
                reply(reply_token, PRICE_MENU)
            elif choice == 4:
                states.set(user_id, State.WAIT_CATEGORY)
                reply(reply_token, CATEGORY_MENU)
            else:
                reply(reply_token, '請輸入 1~4')
            continue
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
                      f"⚠️ 「{msg}」已有人分享過！\n\n"
                      f"🏷️ {existing.get('category', '')}\n"
                      f"💬 「{existing['review']}」\n\n"
                      f"1. 幫這家店按讚 👍\n"
                      f"2. 新增照片 📷\n"
                      f"3. 留評論 ✏️\n"
                      f"4. 另外新增\n\n輸入 1~4")
            else:
                states.set_data(user_id, 'name', msg)
                states.set(user_id, State.WAIT_IMAGE)
                reply(reply_token, f'✅ 店家名稱：{msg}\n\n請上傳一張【食物照片】📷\n（AI 會自動辨識分類和價位）')
            continue
        if state == State.WAIT_DUP_CONFIRM and msg.isdigit():
            choice = int(msg)
            rid = states.get_data(user_id).get('dup_id')
            if choice == 1:
                result = db.toggle_like(rid, user_id)
                r = db.get_by_id(rid)
                states.reset(user_id)
                reply(reply_token,
                      f"👍 已幫「{r['name']}」按讚！現在共 {r['like_count']} 個讚" if result == 'liked' else "已收回讚")
            elif choice == 2:
                states.set_data(user_id, 'view_id', rid)
                states.set(user_id, State.ADD_PHOTO)
                reply(reply_token, '請上傳新照片 📷')
            elif choice == 3:
                states.set_data(user_id, 'view_id', rid)
                states.set(user_id, State.WAIT_COMMENT)
                reply(reply_token, '請輸入你的評論：')
            elif choice == 4:
                states.set(user_id, State.WAIT_IMAGE)
                reply(reply_token, '請上傳一張【食物照片】📷')
            else:
                reply(reply_token, '請輸入 1~4')
            continue
        if state == State.WAIT_CATEGORY and msg.isdigit():
            idx = int(msg) - 1
            if 0 <= idx < len(CATEGORIES):
                states.set_data(user_id, 'category', CATEGORIES[idx])
                data = states.get_data(user_id)
                if data.get('price_range'):
                    states.set(user_id, State.WAIT_REVIEW)
                    reply(reply_token, f'✅ 分類：{CATEGORIES[idx]}\n\n請輸入一句【評論】')
                else:
                    states.set(user_id, State.WAIT_PRICE)
                    reply(reply_token, f'✅ 分類：{CATEGORIES[idx]}\n\n' + PRICE_MENU)
            else:
                reply(reply_token, CATEGORY_MENU)
            continue
        if state == State.WAIT_PRICE and msg.isdigit():
            idx = int(msg) - 1
            if 0 <= idx < len(PRICE_RANGES):
                states.set_data(user_id, 'price_range', PRICE_RANGES[idx])
                states.set(user_id, State.WAIT_REVIEW)
                reply(reply_token, f'✅ 價位：{PRICE_RANGES[idx]}\n\n請輸入一句【評論】')
            else:
                reply(reply_token, PRICE_MENU)
            continue
        if state == State.WAIT_REVIEW:
            data = states.get_data(user_id)
            db.add_restaurant(
                user_id=user_id, name=data['name'], category=data['category'],
                price_range=data['price_range'], image_url=data['image_url'], review=msg,
            )
            states.reset(user_id)
            reply(reply_token, share_success_flex(data['name'], data['category'], data['price_range'], msg))
            continue
        if state == State.WAIT_COMMENT:
            rid = states.get_data(user_id).get('view_id')
            db.add_comment(rid, user_id, msg)
            reply(reply_token, f'✅ 評論已新增：「{msg}」')
            refresh_detail(user_id, rid)
            continue
        if msg == '近期推薦':
            restaurants = db.get_recent(limit=30)
            if not restaurants:
                reply(reply_token, '目前還沒有任何分享，輸入「我要分享」來成為第一個！')
                continue
            states.reset(user_id)
            states.set(user_id, State.WAIT_PICK)
            states.set_data(user_id, 'list', [r['id'] for r in restaurants])
            reply(reply_token, restaurant_list_flex(restaurants))
            continue
        if msg == '篩選分類':
            states.reset(user_id)
            states.set(user_id, State.FILTER_PICK)
            reply(reply_token, filter_menu_flex())
            continue
        if msg.startswith('分類選擇 ') and msg.split()[-1].isdigit():
            idx = int(msg.split()[-1]) - 1
            if idx == 7:
                restaurants = db.get_recent(limit=30)
                label = '全部推薦'
            elif 0 <= idx < len(CATEGORIES):
                restaurants = db.get_recent(limit=30, category=CATEGORIES[idx])
                label = CATEGORIES[idx]
            else:
                reply(reply_token, '請重新選擇')
                continue
            if not restaurants:
                states.reset(user_id)
                reply(reply_token, f'目前沒有「{label}」的分享')
                continue
            states.set(user_id, State.WAIT_PICK)
            states.set_data(user_id, 'list', [r['id'] for r in restaurants])
            reply(reply_token, restaurant_list_flex(restaurants))
            continue
        if msg.startswith('詳情 ') and msg.split()[-1].isdigit():
            idx = int(msg.split()[-1]) - 1
            id_list = states.get_data(user_id).get('list', [])
            if 0 <= idx < len(id_list):
                show_detail(reply_token, user_id, id_list[idx], id_list)
            continue
        if state == State.WAIT_LIKE and msg == '看所有照片':
            rid = states.get_data(user_id).get('view_id')
            handle_photos_view(reply_token, user_id, rid)
            continue
        if msg.startswith('照片讚 ') and msg.split()[-1].isdigit():
            photo_id = int(msg.split()[-1])
            result = db.toggle_photo_like(photo_id, user_id)
            p = db.get_photo_by_id(photo_id)
            if p:
                reply(reply_token,
                      f"{'👍 按讚成功！' if result == 'liked' else '已收回讚'} 這張照片有 {p['like_count']} 個讚")
            continue
        if msg.startswith('檢舉照片 ') and msg.split()[-1].isdigit():
            photo_id = int(msg.split()[-1])
            p = db.get_photo_by_id(photo_id)
            if not p:
                reply(reply_token, '找不到這張照片')
                continue
            result = db.report_photo(photo_id, user_id)
            if result == 'already_reported':
                reply(reply_token, '你已經檢舉過這張照片了')
            elif result == 'hidden':
                reply(reply_token, '🚫 此照片已達檢舉上限，暫時下架等待管理員審核')
                r = db.get_by_id(p['restaurant_id'])
                notify_admin(photo_id, r['name'] if r else '未知', result)
            elif result == 'pending_review':
                reply(reply_token, '⚠️ 檢舉已送出，此照片將等待管理員審核')
                r = db.get_by_id(p['restaurant_id'])
                notify_admin(photo_id, r['name'] if r else '未知', result)
            else:
                reply(reply_token, '✅ 檢舉已送出，謝謝你的回報')
            continue
        if state == State.WAIT_LIKE and msg == '讚':
            rid = states.get_data(user_id).get('view_id')
            result = db.toggle_like(rid, user_id)
            r = db.get_by_id(rid)
            reply(reply_token,
                  f"👍 已幫「{r['name']}」按讚！現在共 {r['like_count']} 個讚" if result == 'liked' else f"已收回「{r['name']}」的讚")
            refresh_detail(user_id, rid)
            continue
        if state == State.WAIT_LIKE and msg == '留評論':
            states.set(user_id, State.WAIT_COMMENT)
            reply(reply_token, '請輸入你的評論：')
            continue
        if state == State.WAIT_LIKE and msg == '新增照片':
            states.set(user_id, State.ADD_PHOTO)
            reply(reply_token, '請上傳新的食物照片 📷')
            continue
        if msg == '管理我的分享':
            my_list = db.get_by_user(user_id)
            if not my_list:
                reply(reply_token, '你還沒有任何分享，輸入「我要分享」新增！')
                continue
            states.reset(user_id)
            states.set(user_id, State.MANAGE_PICK)
            states.set_data(user_id, 'my_list', [r['id'] for r in my_list])
            lines = ['📋 我的分享（輸入編號選擇）\n']
            for i, r in enumerate(my_list, 1):
                lines.append(f"{i}. {r['name']}　{r.get('category', '')}")
            reply(reply_token, '\n'.join(lines))
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
                          f"🏷️ {r.get('category', '')}　{r.get('price_range', '')}\n\n"
                          f"1. 修改店家名稱\n2. 修改分類\n3. 修改價位\n4. 新增照片\n\n輸入 1~4")
                    continue
            reply(reply_token, '請輸入有效的編號')
            continue
        if state == State.MANAGE_ACTION and msg.isdigit():
            action = int(msg)
            rid = states.get_data(user_id).get('edit_id')
            if action == 1:
                states.set(user_id, State.EDIT_NAME)
                reply(reply_token, '請輸入新的【店家名稱】')
            elif action == 2:
                states.set(user_id, State.EDIT_CATEGORY)
                reply(reply_token, CATEGORY_MENU)
            elif action == 3:
                states.set(user_id, State.EDIT_PRICE)
                reply(reply_token, PRICE_MENU)
            elif action == 4:
                states.set_data(user_id, 'view_id', rid)
                states.set(user_id, State.ADD_PHOTO)
                reply(reply_token, '請上傳新的食物照片 📷')
            else:
                reply(reply_token, '請輸入 1~4')
            continue
        if state == State.EDIT_NAME:
            rid = states.get_data(user_id).get('edit_id')
            db.update_name(rid, user_id, msg)
            states.reset(user_id)
            reply(reply_token, f'✅ 店家名稱已更新為：{msg}')
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


def introduction() -> str:
    return (
        '📋 學生餐廳分享系統 使用說明\n'
        '━━━━━━━━━━━━━━━━━━\n'
        '📤 我要分享（私訊）\n'
        '   → 名稱→照片（AI辨識）→評論\n\n'
        '📋 近期推薦（私訊或群組）\n'
        '   → 卡片點「查看詳情」\n'
        '   → 按讚 / 留評論 / 新增照片 / 看所有照片\n\n'
        '🔍 篩選分類（私訊或群組）\n\n'
        '✏️ 管理我的分享（私訊）\n\n'
        '❌ 取消　　📖 說明'
    )


if __name__ == '__main__':
    arg_parser = ArgumentParser()
    arg_parser.add_argument('-p', '--port', type=int, default=8000)
    arg_parser.add_argument('-d', '--debug', default=False)
    options = arg_parser.parse_args()
    app.run(debug=options.debug, port=options.port)