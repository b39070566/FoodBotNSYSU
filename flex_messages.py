# flex_messages.py
# 所有 Flex Message 模板

from linebot.models import FlexSendMessage, BubbleContainer, BoxComponent, TextComponent, ImageComponent, ButtonComponent, SeparatorComponent
from linebot.models.flex_message import CarouselContainer


def restaurant_list_flex(restaurants: list) -> FlexSendMessage:
    """推薦清單 Carousel（每筆一個 Bubble）"""
    bubbles = []
    for i, r in enumerate(restaurants[:10], 1):  # Carousel 最多 12 個
        likes = r.get('like_count', 0)
        bubble = BubbleContainer(
            body=BoxComponent(
                layout='vertical',
                spacing='sm',
                contents=[
                    TextComponent(
                        text=f"{i}. {r['name']}",
                        weight='bold',
                        size='lg',
                        wrap=True,
                    ),
                    BoxComponent(
                        layout='horizontal',
                        contents=[
                            TextComponent(
                                text=r.get('category', ''),
                                size='sm',
                                color='#888888',
                                flex=3,
                            ),
                            TextComponent(
                                text=r.get('price_range', ''),
                                size='sm',
                                color='#888888',
                                flex=3,
                                align='end',
                            ),
                        ]
                    ),
                    TextComponent(
                        text=f"👍 {likes} 個讚" if likes > 0 else "還沒有讚",
                        size='sm',
                        color='#aaaaaa',
                    ),
                    SeparatorComponent(margin='sm'),
                    TextComponent(
                        text=f"💬 {r['review'][:30]}{'...' if len(r['review'])>30 else ''}",
                        size='sm',
                        wrap=True,
                        color='#555555',
                    ),
                ]
            ),
            footer=BoxComponent(
                layout='vertical',
                contents=[
                    ButtonComponent(
                        action={
                            'type': 'message',
                            'label': '查看詳情',
                            'text': f'詳情 {i}'
                        },
                        style='primary',
                        color='#27ACB2',
                        height='sm',
                    )
                ]
            )
        )
        bubbles.append(bubble)

    return FlexSendMessage(
        alt_text='📋 近期推薦餐廳',
        contents=CarouselContainer(contents=bubbles)
    )


def restaurant_detail_flex(restaurant: dict, user_has_liked: bool) -> FlexSendMessage:
    """店家詳情 Bubble"""
    likes = restaurant.get('like_count', 0)
    heart = '❤️ 收回讚' if user_has_liked else '🤍 按讚'

    bubble = BubbleContainer(
        hero=ImageComponent(
            url=restaurant['image_url'],
            size='full',
            aspect_ratio='20:13',
            aspect_mode='cover',
        ),
        body=BoxComponent(
            layout='vertical',
            spacing='sm',
            contents=[
                TextComponent(
                    text=restaurant['name'],
                    weight='bold',
                    size='xl',
                    wrap=True,
                ),
                BoxComponent(
                    layout='horizontal',
                    contents=[
                        TextComponent(
                            text=restaurant.get('category', ''),
                            size='sm',
                            color='#555555',
                            flex=1,
                        ),
                        TextComponent(
                            text=restaurant.get('price_range', ''),
                            size='sm',
                            color='#555555',
                            flex=1,
                            align='end',
                        ),
                    ]
                ),
                SeparatorComponent(margin='sm'),
                TextComponent(
                    text=f"💬 {restaurant['review']}",
                    size='sm',
                    wrap=True,
                    color='#333333',
                ),
                TextComponent(
                    text=f"👍 {likes} 個讚",
                    size='sm',
                    color='#aaaaaa',
                    margin='sm',
                ),
            ]
        ),
        footer=BoxComponent(
            layout='vertical',
            spacing='sm',
            contents=[
                ButtonComponent(
                    action={
                        'type': 'message',
                        'label': heart,
                        'text': '讚'
                    },
                    style='primary',
                    color='#27ACB2',
                    height='sm',
                ),
            ]
        )
    )

    return FlexSendMessage(
        alt_text=f"📍 {restaurant['name']}",
        contents=bubble
    )


def share_success_flex(name: str, category: str, price_range: str, review: str) -> FlexSendMessage:
    """分享成功確認卡"""
    bubble = BubbleContainer(
        body=BoxComponent(
            layout='vertical',
            spacing='sm',
            contents=[
                TextComponent(text='🎉 分享成功！', weight='bold', size='xl', color='#27ACB2'),
                SeparatorComponent(margin='sm'),
                TextComponent(text=f'📍 {name}', weight='bold', size='lg', wrap=True),
                TextComponent(text=f'🏷️ {category}　{price_range}', size='sm', color='#888888'),
                TextComponent(text=f'💬 「{review}」', size='sm', wrap=True, color='#555555', margin='sm'),
                SeparatorComponent(margin='sm'),
                TextComponent(
                    text='其他同學可以輸入「近期推薦」查看！',
                    size='xs',
                    color='#aaaaaa',
                    wrap=True,
                ),
            ]
        )
    )
    return FlexSendMessage(alt_text='🎉 分享成功！', contents=bubble)


def filter_menu_flex() -> FlexSendMessage:
    """分類篩選選單"""
    from canteen_db import CATEGORIES
    buttons = []
    for i, c in enumerate(CATEGORIES, 1):
        buttons.append(
            ButtonComponent(
                action={
                    'type': 'message',
                    'label': c,
                    'text': f'分類選擇 {i}'
                },
                style='secondary',
                height='sm',
                margin='xs',
            )
        )
    buttons.append(
        ButtonComponent(
            action={
                'type': 'message',
                'label': '🍽️ 全部',
                'text': '分類選擇 8'
            },
            style='primary',
            color='#27ACB2',
            height='sm',
            margin='xs',
        )
    )

    bubble = BubbleContainer(
        body=BoxComponent(
            layout='vertical',
            spacing='xs',
            contents=[
                TextComponent(text='🔍 請選擇分類', weight='bold', size='lg', margin='md'),
                SeparatorComponent(margin='sm'),
                *buttons,
            ]
        )
    )
    return FlexSendMessage(alt_text='請選擇分類', contents=bubble)
