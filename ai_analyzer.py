# ai_analyzer.py
# 用 Gemini Flash 辨識食物照片，回傳建議分類與價位

import os
import json
import google.generativeai as genai
from canteen_db import CATEGORIES, PRICE_RANGES

genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
model = genai.GenerativeModel("gemini-1.5-flash")

PROMPT = f"""
你是一個台灣學生餐廳推薦系統的食物辨識助手。
請分析這張食物照片，回傳 JSON 格式（不要加任何其他文字）：

分類選項（從以下選一個最符合的）：
{chr(10).join(f'{i+1}. {c}' for i, c in enumerate(CATEGORIES))}

價位選項（從以下選一個最符合的）：
{chr(10).join(f'{i+1}. {p}' for i, p in enumerate(PRICE_RANGES))}

回傳格式：
{{
  "food_name": "食物名稱（中文，簡短描述）",
  "category": "分類名稱（從上面選項原文複製）",
  "price_range": "價位名稱（從上面選項原文複製）",
  "confidence": "high/medium/low（辨識把握度）"
}}

如果看不出來是食物，food_name 填「無法辨識」，其他填第一個選項。
"""


def analyze_food_image(image_bytes: bytes) -> dict:
    """
    傳入圖片 bytes，回傳辨識結果 dict：
    {
        "food_name": str,
        "category": str,
        "price_range": str,
        "confidence": str,
        "success": bool
    }
    """
    try:
        import PIL.Image
        import io
        img = PIL.Image.open(io.BytesIO(image_bytes))
        response = model.generate_content([PROMPT, img])
        text = response.text.strip()

        # 清除可能的 markdown 包裝
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        text = text.strip()

        result = json.loads(text)
        result["success"] = True

        # 確保分類和價位是合法值
        if result.get("category") not in CATEGORIES:
            result["category"] = CATEGORIES[0]
        if result.get("price_range") not in PRICE_RANGES:
            result["price_range"] = PRICE_RANGES[0]

        return result

    except Exception as e:
        print(f"Gemini analyze error: {e}")
        return {
            "food_name": "無法辨識",
            "category": CATEGORIES[0],
            "price_range": PRICE_RANGES[0],
            "confidence": "low",
            "success": False
        }
