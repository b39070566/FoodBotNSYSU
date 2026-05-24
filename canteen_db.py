import os
from datetime import datetime
from typing import Optional
from urllib.parse import urlparse
import psycopg2
from psycopg2.extras import RealDictCursor

MAX_RESTAURANTS = 30
MAX_PHOTOS = 5

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


def _get_conn_params():
    url = os.getenv("DATABASE_URL", "")
    parsed = urlparse(url)
    return {
        "host": parsed.hostname,
        "port": parsed.port or 5432,
        "dbname": parsed.path.lstrip("/"),
        "user": parsed.username,
        "password": parsed.password,
        "sslmode": "require",
    }


class CanteenDB:
    def __init__(self):
        self._conn_params = _get_conn_params()
        self._init_db()

    def _connect(self):
        return psycopg2.connect(cursor_factory=RealDictCursor, **self._conn_params)

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
                        review      TEXT NOT NULL,
                        views       INTEGER NOT NULL DEFAULT 0,
                        created_at  TIMESTAMP NOT NULL DEFAULT NOW()
                    )
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS photos (
                        id            SERIAL PRIMARY KEY,
                        restaurant_id INTEGER NOT NULL REFERENCES restaurants(id) ON DELETE CASCADE,
                        user_id       TEXT NOT NULL,
                        image_url     TEXT NOT NULL,
                        uploaded_at   TIMESTAMP NOT NULL DEFAULT NOW()
                    )
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS photo_likes (
                        id        SERIAL PRIMARY KEY,
                        photo_id  INTEGER NOT NULL REFERENCES photos(id) ON DELETE CASCADE,
                        user_id   TEXT NOT NULL,
                        liked_at  TIMESTAMP NOT NULL DEFAULT NOW(),
                        UNIQUE(photo_id, user_id)
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
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS comments (
                        id            SERIAL PRIMARY KEY,
                        restaurant_id INTEGER NOT NULL REFERENCES restaurants(id) ON DELETE CASCADE,
                        user_id       TEXT NOT NULL,
                        content       TEXT NOT NULL,
                        created_at    TIMESTAMP NOT NULL DEFAULT NOW()
                    )
                """)
                # 舊資料相容
                for sql in [
                    "ALTER TABLE restaurants ADD COLUMN IF NOT EXISTS views INTEGER NOT NULL DEFAULT 0",
                    "ALTER TABLE restaurants DROP COLUMN IF EXISTS image_url",
                ]:
                    try:
                        cur.execute(sql)
                    except Exception:
                        pass
            conn.commit()

    # ── 照片分數計算 ──────────────────────────────────────────────────────────
    def _photo_score(self, photo_id: int, cur) -> float:
        cur.execute("SELECT liked_at FROM photo_likes WHERE photo_id=%s", (photo_id,))
        rows = cur.fetchall()
        now = datetime.now()
        score = 0.0
        for r in rows:
            days = max((now - r["liked_at"]).total_seconds() / 86400, 0)
            score += 1.0 / (days + 1)
        return score

    def _get_main_photo(self, restaurant_id: int, cur) -> Optional[dict]:
        """取得分數最高的照片當主圖，沒照片回 None"""
        cur.execute(
            "SELECT * FROM photos WHERE restaurant_id=%s ORDER BY uploaded_at DESC",
            (restaurant_id,)
        )
        photos = cur.fetchall()
        if not photos:
            return None
        best = max(photos, key=lambda p: self._photo_score(p["id"], cur))
        return dict(best)

    # ── 新增餐廳 ──────────────────────────────────────────────────────────────
    def add_restaurant(self, user_id, name, category, price_range, image_url, review) -> int:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO restaurants (user_id, name, category, price_range, review)
                       VALUES (%s,%s,%s,%s,%s) RETURNING id""",
                    (user_id, name, category, price_range, review)
                )
                new_id = cur.fetchone()["id"]
                # 新增第一張照片
                cur.execute(
                    "INSERT INTO photos (restaurant_id, user_id, image_url) VALUES (%s,%s,%s)",
                    (new_id, user_id, image_url)
                )
                # 超過上限刪最舊
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

    # ── 新增照片 ──────────────────────────────────────────────────────────────
    def add_photo(self, restaurant_id: int, user_id: str, image_url: str):
        """新增照片，超過5張刪分數最低的"""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM photos WHERE restaurant_id=%s",
                    (restaurant_id,)
                )
                photos = cur.fetchall()
                if len(photos) >= MAX_PHOTOS:
                    # 找分數最低的刪掉
                    worst = min(photos, key=lambda p: self._photo_score(p["id"], cur))
                    cur.execute("DELETE FROM photos WHERE id=%s", (worst["id"],))
                cur.execute(
                    "INSERT INTO photos (restaurant_id, user_id, image_url) VALUES (%s,%s,%s)",
                    (restaurant_id, user_id, image_url)
                )
            conn.commit()

    # ── 查詢 ──────────────────────────────────────────────────────────────────
    def get_recent(self, limit: int = 30, category: str = None) -> list[dict]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                if category:
                    cur.execute("SELECT * FROM restaurants WHERE category=%s ORDER BY id DESC", (category,))
                else:
                    cur.execute("SELECT * FROM restaurants ORDER BY id DESC")
                restaurants = [dict(r) for r in cur.fetchall()]

                cur.execute("SELECT restaurant_id, liked_at FROM likes")
                likes_rows = cur.fetchall()

        now = datetime.now()
        like_map: dict[int, float] = {}
        for like in likes_rows:
            rid = like["restaurant_id"]
            days = max((now - like["liked_at"]).total_seconds() / 86400, 0)
            like_map[rid] = like_map.get(rid, 0) + 1.0 / (days + 1)

        with self._connect() as conn:
            with conn.cursor() as cur:
                for r in restaurants:
                    r["like_score"] = like_map.get(r["id"], 0)
                    r["like_count"] = self._like_count(r["id"])
                    r["comment_count"] = self._comment_count(r["id"])
                    main = self._get_main_photo(r["id"], cur)
                    r["image_url"] = main["image_url"] if main else None
                    if isinstance(r.get("created_at"), datetime):
                        r["created_at"] = r["created_at"].strftime("%Y-%m-%d %H:%M:%S")

        restaurants.sort(key=lambda x: x["like_score"], reverse=True)
        return restaurants[:limit]

    def get_by_id(self, restaurant_id: int) -> Optional[dict]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM restaurants WHERE id=%s", (restaurant_id,))
                row = cur.fetchone()
                if not row:
                    return None
                r = dict(row)
                r["like_count"] = self._like_count(restaurant_id)
                r["comment_count"] = self._comment_count(restaurant_id)
                main = self._get_main_photo(restaurant_id, cur)
                r["image_url"] = main["image_url"] if main else None
                r["photo_count"] = self._photo_count(restaurant_id, cur)
                if isinstance(r.get("created_at"), datetime):
                    r["created_at"] = r["created_at"].strftime("%Y-%m-%d %H:%M:%S")
                return r

    def get_by_user(self, user_id: str) -> list[dict]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM restaurants WHERE user_id=%s ORDER BY id DESC", (user_id,))
                rows = [dict(r) for r in cur.fetchall()]
                for r in rows:
                    main = self._get_main_photo(r["id"], cur)
                    r["image_url"] = main["image_url"] if main else None
                return rows

    def find_by_name(self, name: str) -> Optional[dict]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM restaurants WHERE name=%s", (name,))
                row = cur.fetchone()
                if not row:
                    return None
                r = dict(row)
                main = self._get_main_photo(r["id"], cur)
                r["image_url"] = main["image_url"] if main else None
                return r

    def _like_count(self, restaurant_id: int) -> int:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) as cnt FROM likes WHERE restaurant_id=%s", (restaurant_id,))
                return cur.fetchone()["cnt"]

    def _comment_count(self, restaurant_id: int) -> int:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) as cnt FROM comments WHERE restaurant_id=%s", (restaurant_id,))
                return cur.fetchone()["cnt"]

    def _photo_count(self, restaurant_id: int, cur) -> int:
        cur.execute("SELECT COUNT(*) as cnt FROM photos WHERE restaurant_id=%s", (restaurant_id,))
        return cur.fetchone()["cnt"]

    # ── 觀看數 ────────────────────────────────────────────────────────────────
    def increment_views(self, restaurant_id: int):
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE restaurants SET views=views+1 WHERE id=%s",
                    (restaurant_id,)
                )
            conn.commit()

    # ── 按讚 ──────────────────────────────────────────────────────────────────
    def toggle_like(self, restaurant_id: int, user_id: str) -> str:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id FROM likes WHERE restaurant_id=%s AND user_id=%s",
                    (restaurant_id, user_id)
                )
                if cur.fetchone():
                    cur.execute("DELETE FROM likes WHERE restaurant_id=%s AND user_id=%s", (restaurant_id, user_id))
                    # 同步移除對應照片的按讚
                    main = self._get_main_photo(restaurant_id, cur)
                    if main:
                        cur.execute("DELETE FROM photo_likes WHERE photo_id=%s AND user_id=%s", (main["id"], user_id))
                    conn.commit()
                    return "unliked"
                else:
                    cur.execute("INSERT INTO likes (restaurant_id, user_id) VALUES (%s,%s)", (restaurant_id, user_id))
                    # 同步對當下主圖按讚
                    main = self._get_main_photo(restaurant_id, cur)
                    if main:
                        try:
                            cur.execute(
                                "INSERT INTO photo_likes (photo_id, user_id) VALUES (%s,%s)",
                                (main["id"], user_id)
                            )
                        except Exception:
                            pass
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

    # ── 評論 ──────────────────────────────────────────────────────────────────
    def add_comment(self, restaurant_id: int, user_id: str, content: str):
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO comments (restaurant_id, user_id, content) VALUES (%s,%s,%s)",
                    (restaurant_id, user_id, content)
                )
            conn.commit()

    def get_comments(self, restaurant_id: int, limit: int = 5) -> list[dict]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM comments WHERE restaurant_id=%s ORDER BY created_at DESC LIMIT %s",
                    (restaurant_id, limit)
                )
                rows = [dict(r) for r in cur.fetchall()]
                for r in rows:
                    if isinstance(r.get("created_at"), datetime):
                        r["created_at"] = r["created_at"].strftime("%Y-%m-%d %H:%M:%S")
                return rows

    # ── 編輯（只保留名稱、評論、新增照片）──────────────────────────────────
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
    def update_category(self, rid, user_id, category): return self._update("category", rid, user_id, category)
    def update_price_range(self, rid, user_id, price_range): return self._update("price_range", rid, user_id, price_range)
