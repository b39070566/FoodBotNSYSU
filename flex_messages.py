from linebot.models import (
    FlexSendMessage, BubbleContainer, BoxComponent,
    TextComponent, ImageComponent, ButtonComponent, SeparatorComponent
)
from linebot.models.flex_message import CarouselContainer

def restaurant_list_flex(restaurants: list) -> FlexSendMessage:
    bubbles = []
    for i, r in enumerate(restaurants[:10], 1):
        likes = r.get('like_count', 0)
        comments = r.get('comment_count', 0)
        views = r.get('view_count', 0)
        image_url = r.get('image_url')
        bubble = BubbleContainer(
            hero=ImageComponent(
                url=image_url, size='full',
                aspect_ratio='4:3', aspect_mode='cover',
            ) if image_url else None,
            body=BoxComponent(
                layout='vertical', spacing='sm',
                contents=[
                    TextComponent(text=f"{i}. {r['name']}", weight='bold', size='lg', wrap=True),
                    BoxComponent(
                        layout='horizontal',
                        contents=[
                            TextComponent(text=r.get('category', ''), size='sm', color='#888888', flex=3),
                            TextComponent(text=r.get('price_range', ''), size='sm', color='#888888', flex=3, align='end'),
                        ]                    ),
                    BoxComponent(
                        layout='horizontal', margin='sm',
                        contents=[
                            TextComponent(text=f"👁 {views}", size='xs', color='#aaaaaa', flex=1),
                            TextComponent(text=f"👍 {likes}", size='xs', color='#aaaaaa', flex=1),
                            TextComponent(text=f"💬 {comments}", size='xs', color='#aaaaaa', flex=1),
                        ]                    ),
                    SeparatorComponent(margin='sm'),
                    TextComponent(
                        text=f"💬 {r['review'][:25]}{'...' if len(r['review'])>25 else ''}",
                        size='sm', wrap=True, color='#555555',
                    ),
                ]
            ),
            footer=BoxComponent(
                layout='vertical',
                contents=[
                    ButtonComponent(
                        action={'type': 'message', 'label': '查看詳情', 'text': f'詳情 {i}'},
                        style='primary', color='#27ACB2', height='sm',
                    )
                ]
            )
        )
        bubbles.append(bubble)
    return FlexSendMessage(
        alt_text='📋 近期推薦餐廳',
        contents=CarouselContainer(contents=bubbles)
    )

def restaurant_detail_flex(restaurant: dict, user_has_liked: bool, comments: list) -> FlexSendMessage:
    likes = restaurant.get('like_count', 0)
    views = restaurant.get('view_count', 0)
    comment_count = restaurant.get('comment_count', 0)
    photo_count = restaurant.get('photo_count', 1)
    heart = '❤️ 收回讚' if user_has_liked else '🤍 按讚'
    image_url = restaurant.get('image_url')
    comment_contents = []
    if comments:
        comment_contents.append(SeparatorComponent(margin='sm'))
        comment_contents.append(
            TextComponent(text=f"💬 評論（{comment_count}則）", size='sm', weight='bold', color='#333333')
        )
        for c in comments[:3]:
            comment_contents.append(
                TextComponent(
                    text=f"• {c['content'][:30]}{'...' if len(c['content'])>30 else ''}",
                    size='xs', wrap=True, color='#555555',
                )
            )
    bubble = BubbleContainer(
        hero=ImageComponent(
            url=image_url, size='full',
            aspect_ratio='4:3', aspect_mode='cover',
        ) if image_url else None,
        body=BoxComponent(
            layout='vertical', spacing='sm',
            contents=[
                TextComponent(text=restaurant['name'], weight='bold', size='xl', wrap=True),
                BoxComponent(
                    layout='horizontal',
                    contents=[
                        TextComponent(text=restaurant.get('category', ''), size='sm', color='#555555', flex=1),
                        TextComponent(text=restaurant.get('price_range', ''), size='sm', color='#555555', flex=1, align='end'),
                    ]                ),
                BoxComponent(
                    layout='horizontal', margin='sm',
                    contents=[
                        TextComponent(text=f"👁 {views}人看過", size='xs', color='#aaaaaa', flex=1),
                        TextComponent(text=f"👍 {likes}個讚", size='xs', color='#aaaaaa', flex=1),
                        TextComponent(text=f"📷 {photo_count}張照片", size='xs', color='#aaaaaa', flex=1),
                    ]                ),
                SeparatorComponent(margin='sm'),
                TextComponent(text=f"💬 {restaurant['review']}", size='sm', wrap=True, color='#333333'),
                *comment_contents,
            ]
        ),
        footer=BoxComponent(
            layout='vertical', spacing='sm',
            contents=[
                BoxComponent(
                    layout='horizontal', spacing='sm',
                    contents=[
                        ButtonComponent(
                            action={'type': 'message', 'label': heart, 'text': '讚'},
                            style='primary', color='#27ACB2', height='sm', flex=1,
                        ),
                        ButtonComponent(
                            action={'type': 'message', 'label': '✏️ 留評論', 'text': '留評論'},
                            style='secondary', height='sm', flex=1,
                        ),
                    ]                ),
                BoxComponent(
                    layout='horizontal', spacing='sm',
                    contents=[
                        ButtonComponent(
                            action={'type': 'message', 'label': '📷 新增照片', 'text': '新增照片'},
                            style='secondary', height='sm', flex=1,
                        ),
                        ButtonComponent(
                            action={'type': 'message', 'label': f'🖼 看所有照片({photo_count})', 'text': '看所有照片'},
                            style='secondary', height='sm', flex=1,
                        ),
                    ]                ),
            ]
        )
    )
    return FlexSendMessage(alt_text=f"📍 {restaurant['name']}", contents=bubble)

def photos_carousel_flex(restaurant_name: str, photos: list, user_liked_ids: set, user_reported_ids: set) -> FlexSendMessage:
    """所有照片輪播，每張有獨立按讚和檢舉按鈕"""
    bubbles = []
    for i, p in enumerate(photos, 1):
        liked = p['id'] in user_liked_ids
        reported = p['id'] in user_reported_ids
        heart = '❤️ 收回讚' if liked else f"🤍 讚 ({p.get('like_count', 0)})"
        status_text = None
        if p.get('status') == 'pending_review':
            status_text = TextComponent(text='⚠️ 此照片待審核', size='xs', color='#E67E22', margin='sm')
        footer_buttons = [
            ButtonComponent(
                action={'type': 'message', 'label': heart, 'text': f'照片讚 {p["id"]}'},
                style='primary', color='#27ACB2', height='sm',
            ),
        ]
        if not reported:
            footer_buttons.append(
                ButtonComponent(
                    action={'type': 'message', 'label': '🚩 檢舉', 'text': f'檢舉照片 {p["id"]}'},
                    style='secondary', height='sm', color='#E74C3C',
                )
            )
        body_contents = [
            TextComponent(text=f"📷 第 {i} 張 / 共 {len(photos)} 張", size='sm', color='#888888'),
            TextComponent(text=f"👍 {p.get('like_count', 0)} 個讚", size='sm', color='#555555'),
        ]
        if status_text:
            body_contents.append(status_text)
        bubble = BubbleContainer(
            hero=ImageComponent(
                url=p['image_url'], size='full',
                aspect_ratio='4:3', aspect_mode='cover',
            ),
            body=BoxComponent(layout='vertical', spacing='sm', contents=body_contents),
            footer=BoxComponent(layout='vertical', spacing='sm', contents=footer_buttons)
        )
        bubbles.append(bubble)
    return FlexSendMessage(
        alt_text=f"📷 {restaurant_name} 的所有照片",
        contents=CarouselContainer(contents=bubbles)
    )

def share_success_flex(name, category, price_range, review) -> FlexSendMessage:
    bubble = BubbleContainer(
        body=BoxComponent(
            layout='vertical', spacing='sm',
            contents=[
                TextComponent(text='🎉 分享成功！', weight='bold', size='xl', color='#27ACB2'),
                SeparatorComponent(margin='sm'),
                TextComponent(text=f'📍 {name}', weight='bold', size='lg', wrap=True),
                TextComponent(text=f'🏷️ {category}　{price_range}', size='sm', color='#888888'),
                TextComponent(text=f'💬 「{review}」', size='sm', wrap=True, color='#555555', margin='sm'),
                SeparatorComponent(margin='sm'),
                TextComponent(text='其他同學可以輸入「近期推薦」查看！', size='xs', color='#aaaaaa', wrap=True),
            ]
        )
    )
    return FlexSendMessage(alt_text='🎉 分享成功！', contents=bubble)

def filter_menu_flex() -> FlexSendMessage:
    from canteen_db import CATEGORIES
    buttons = []
    for i, c in enumerate(CATEGORIES, 1):
        buttons.append(
            ButtonComponent(
                action={'type': 'message', 'label': c, 'text': f'分類選擇 {i}'},
                style='secondary', height='sm', margin='xs',
            )
        )
    buttons.append(
        ButtonComponent(
            action={'type': 'message', 'label': '🍽️ 全部', 'text': '分類選擇 8'},
            style='primary', color='#27ACB2', height='sm', margin='xs',
        )
    )
    bubble = BubbleContainer(
        body=BoxComponent(
            layout='vertical', spacing='xs',
            contents=[
                TextComponent(text='🔍 請選擇分類', weight='bold', size='lg', margin='md'),
                SeparatorComponent(margin='sm'),
                *buttons,
            ]
        )
    )
    return FlexSendMessage(alt_text='請選擇分類', contents=bubble)