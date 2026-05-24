import os
import json
from datetime import datetime
from typing import Optional
import psycopg2
from psycopg2.extras import RealDictCursor

DATABASE_URL = os.getenv("DATABASE_URL")

MAX_RESTAURANTS = 30

CATEGORIES = [
    "🍱 便當／快餐",
    "🍜 麵食／湯品",
    "🥗 輕食／沙拉",
    "🧋 飲料／甜點",
    "🍔 西式／速食",
    "🍣 日韓料理",
    "🥞 早午餐",
]

PRICE_RANGES = [
    "🟢 100元以下",
    "🟡 100~200元",
    "🟠 200~400元",
    "🔴 400元以上",
]


class CanteenDB:
    def __init__(self):
        self._init_db()

    def _connect(self):
        return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

    def _init_db(self):
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS restaurants (
                        id          SERIAL PRIMARY KEY,
                        user_id     TEXT NOT NULL,
                        name        TEXT NOT NULL,
                        category    TEXT NOT NULL DEFAULT '其他',
                        price_range TEXT NOT NULL DEFAULT '',
                        image_url   TEXT NOT NULL,
                        review      TEXT NOT NULL,
                        created_at  TIMESTAMP NOT NULL DEFAULT NOW()
                    )
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS likes (
                        id            SERIAL PRIMARY KEY,
                        restaurant_id INTEGER NOT NULL REFERENCES restaurants(id) ON DELETE CASCADE,
                        user_id       TEXT NOT NULL,
                        liked_at      TIMESTAMP NOT NULL DEFAULT NOW(),
                        UNIQUE(restaurant_id, user_id)
                    )
                """)
            conn.commit()

    # ── 新增 ──────────────────────────────────────────────────────────────────
    def add_restaurant(self, user_id, name, category, price_range, image_url, review) -> int:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO restaurants (user_id, name, category, price_range, image_url, review)
                       VALUES (%s,%s,%s,%s,%s,%s) RETURNING id""",
                    (user_id, name, category, price_range, image_url, review)
                )
                new_id = cur.fetchone()["id"]
                # 超過上限刪最舊的
                cur.execute("SELECT COUNT(*) as cnt FROM restaurants")
                count = cur.fetchone()["cnt"]
                if count > MAX_RESTAURANTS:
                    cur.execute("""
                        DELETE FROM restaurants WHERE id IN (
                            SELECT id FROM restaurants ORDER BY id ASC LIMIT %s
                        )
                    """, (count - MAX_RESTAURANTS,))
            conn.commit()
        return new_id

    # ── 查詢 ──────────────────────────────────────────────────────────────────
    def get_recent(self, limit: int = 30, category: str = None) -> list[dict]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                if category:
                    cur.execute(
                        "SELECT * FROM restaurants WHERE category=%s ORDER BY id DESC",
                        (category,)
                    )
                else:
                    cur.execute("SELECT * FROM restaurants ORDER BY id DESC")
                restaurants = [dict(r) for r in cur.fetchall()]

                cur.execute("SELECT restaurant_id, liked_at FROM likes")
                likes_rows = cur.fetchall()

        now = datetime.now()
        like_map: dict[int, float] = {}
        for like in likes_rows:
            rid = like["restaurant_id"]
            liked_at = like["liked_at"]
            days_ago = max((now - liked_at).total_seconds() / 86400, 0)
            weight = 1.0 / (days_ago + 1)
            like_map[rid] = like_map.get(rid, 0) + weight

        for r in restaurants:
            r["like_score"] = like_map.get(r["id"], 0)
            r["like_count"] = self._like_count(r["id"])
            # datetime 轉字串
            if isinstance(r.get("created_at"), datetime):
                r["created_at"] = r["created_at"].strftime("%Y-%m-%d %H:%M:%S")

        restaurants.sort(key=lambda x: x["like_score"], reverse=True)
        return restaurants[:limit]

    def _like_count(self, restaurant_id: int) -> int:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) as cnt FROM likes WHERE restaurant_id=%s",
                    (restaurant_id,)
                )
                return cur.fetchone()["cnt"]

    def get_by_id(self, restaurant_id: int) -> Optional[dict]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM restaurants WHERE id=%s", (restaurant_id,))
                row = cur.fetchone()
                if not row:
                    return None
                r = dict(row)
                r["like_count"] = self._like_count(restaurant_id)
                if isinstance(r.get("created_at"), datetime):
                    r["created_at"] = r["created_at"].strftime("%Y-%m-%d %H:%M:%S")
                return r

    def get_by_user(self, user_id: str) -> list[dict]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM restaurants WHERE user_id=%s ORDER BY id DESC",
                    (user_id,)
                )
                return [dict(r) for r in cur.fetchall()]

    def find_by_name(self, name: str) -> Optional[dict]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM restaurants WHERE name=%s", (name,))
                row = cur.fetchone()
                return dict(row) if row else None

    # ── 按讚 ──────────────────────────────────────────────────────────────────
    def toggle_like(self, restaurant_id: int, user_id: str) -> str:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id FROM likes WHERE restaurant_id=%s AND user_id=%s",
                    (restaurant_id, user_id)
                )
                if cur.fetchone():
                    cur.execute(
                        "DELETE FROM likes WHERE restaurant_id=%s AND user_id=%s",
                        (restaurant_id, user_id)
                    )
                    conn.commit()
                    return "unliked"
                else:
                    cur.execute(
                        "INSERT INTO likes (restaurant_id, user_id) VALUES (%s,%s)",
                        (restaurant_id, user_id)
                    )
                    conn.commit()
                    return "liked"

    def has_liked(self, restaurant_id: int, user_id: str) -> bool:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id FROM likes WHERE restaurant_id=%s AND user_id=%s",
                    (restaurant_id, user_id)
                )
                return cur.fetchone() is not None

    # ── 編輯 ──────────────────────────────────────────────────────────────────
    def _update(self, field, rid, user_id, value):
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"UPDATE restaurants SET {field}=%s WHERE id=%s AND user_id=%s",
                    (value, rid, user_id)
                )
                conn.commit()
                return cur.rowcount > 0

    def update_name(self, rid, user_id, name): return self._update("name", rid, user_id, name)
    def update_review(self, rid, user_id, review): return self._update("review", rid, user_id, review)
    def update_image(self, rid, user_id, image_url): return self._update("image_url", rid, user_id, image_url)
    def update_category(self, rid, user_id, category): return self._update("category", rid, user_id, category)
    def update_price_range(self, rid, user_id, price_range): return self._update("price_range", rid, user_id, price_range)

    def delete(self, rid, user_id):
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM restaurants WHERE id=%s AND user_id=%s",
                    (rid, user_id)
                )
                conn.commit()
                return cur.rowcount > 0
