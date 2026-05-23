import os
import json
import base64
import requests
from canteen_db import CATEGORIES, PRICE_RANGES

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"

PROMPT = f"""
你是一個台灣學生餐廳推薦系統的食物辨識助手。
請分析這張食物照片，回傳 JSON 格式（不要加任何其他文字、不要加 markdown）：

分類選項（從以下選一個最符合的）：
{chr(10).join(f'{i+1}. {c}' for i, c in enumerate(CATEGORIES))}

價位選項（從以下選一個最符合的）：
{chr(10).join(f'{i+1}. {p}' for i, p in enumerate(PRICE_RANGES))}

回傳格式：
{{
  "food_name": "食物名稱（中文，簡短描述）",
  "category": "分類名稱（從上面選項原文複製）",
  "price_range": "價位名稱（從上面選項原文複製）",
  "confidence": "high/medium/low"
}}

如果看不出來是食物，food_name 填「無法辨識」，其他填第一個選項。
"""


def analyze_food_image(image_bytes: bytes) -> dict:
    try:
        b64 = base64.b64encode(image_bytes).decode("utf-8")

        response = requests.post(
            GROQ_API_URL,
            headers={
                "Authorization": f"Bearer {os.getenv('GROQ_API_KEY')}",
                "Content-Type": "application/json",
            },
            json={
                "model": "meta-llama/llama-4-scout-17b-16e-instruct",
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/jpeg;base64,{b64}"
                                }
                            },
                            {
                                "type": "text",
                                "text": PROMPT
                            }
                        ]
                    }
                ],
                "max_tokens": 300,
                "temperature": 0.1,
            },
            timeout=30,
        )

        data = response.json()
        text = data["choices"][0]["message"]["content"].strip()

        if "```" in text:
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        text = text.strip()

        result = json.loads(text)
        result["success"] = True

        if result.get("category") not in CATEGORIES:
            result["category"] = CATEGORIES[0]
        if result.get("price_range") not in PRICE_RANGES:
            result["price_range"] = PRICE_RANGES[0]

        return result

    except Exception as e:
        print(f"Groq analyze error: {e}")
        return {
            "food_name": "無法辨識",
            "category": CATEGORIES[0],
            "price_range": PRICE_RANGES[0],
            "confidence": "low",
            "success": False
        }
