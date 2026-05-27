import os
from datetime import datetime, timedelta
from typing import Optional
from urllib.parse import urlparse
import psycopg2
from psycopg2.extras import RealDictCursor

MAX_RESTAURANTS = 30
MAX_PHOTOS = 10
PHOTO_PROTECT_DAYS = 7  # 新照片保護期

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
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS views_log (
                        restaurant_id INTEGER NOT NULL REFERENCES restaurants(id) ON DELETE CASCADE,
                        user_id       TEXT NOT NULL,
                        UNIQUE(restaurant_id, user_id)
                    )
                """)
            conn.commit()

    # ── 照片分數 ──────────────────────────────────────────────────────────────
    def _photo_score(self, photo_id: int, uploaded_at: datetime, cur) -> float:
        """
        照片分數 = 按讚時間衰減 × 0.8 + 照片新鮮度 × 0.2
        新照片（7天內）不受刪除影響
        """
        cur.execute("SELECT liked_at FROM photo_likes WHERE photo_id=%s", (photo_id,))
        rows = cur.fetchall()
        now = datetime.now()

        like_score = 0.0
        for r in rows:
            days = max((now - r["liked_at"]).total_seconds() / 86400, 0)
            like_score += 1.0 / (days + 1)

        # 照片新鮮度
        age_days = max((now - uploaded_at).total_seconds() / 86400, 0)
        freshness = 1.0 / (age_days + 1)

        return like_score * 0.8 + freshness * 0.2

    def _get_all_photos(self, restaurant_id: int, cur) -> list[dict]:
        cur.execute(
            "SELECT * FROM photos WHERE restaurant_id=%s ORDER BY uploaded_at DESC",
            (restaurant_id,)
        )
        photos = [dict(r) for r in cur.fetchall()]
        for p in photos:
            p["score"] = self._photo_score(p["id"], p["uploaded_at"], cur)
            p["like_count"] = self._photo_like_count(p["id"], cur)
        return photos

    def _get_main_photo(self, restaurant_id: int, cur) -> Optional[dict]:
        """分數最高的照片當主圖"""
        photos = self._get_all_photos(restaurant_id, cur)
        if not photos:
            return None
        return max(photos, key=lambda p: p["score"])

    def _photo_like_count(self, photo_id: int, cur) -> int:
        cur.execute("SELECT COUNT(*) as cnt FROM photo_likes WHERE photo_id=%s", (photo_id,))
        return cur.fetchone()["cnt"]

    # ── 餐廳推薦分數（按讚時間衰減 + 新鮮度）────────────────────────────────
    def _restaurant_score(self, restaurant_id: int, created_at: datetime, likes_rows: list) -> float:
        now = datetime.now()
        like_score = 0.0
        for like in likes_rows:
            if like["restaurant_id"] == restaurant_id:
                days = max((now - like["liked_at"]).total_seconds() / 86400, 0)
                like_score += 1.0 / (days + 1)

        age_days = max((now - created_at).total_seconds() / 86400, 0)
        freshness = 1.0 / (age_days + 1)

        return like_score * 0.7 + freshness * 0.3

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
                cur.execute(
                    "INSERT INTO photos (restaurant_id, user_id, image_url) VALUES (%s,%s,%s)",
                    (new_id, user_id, image_url)
                )
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
        """
        新增照片，超過10張時：
        - 保護7天內的新照片
        - 刪分數最低且不在保護期的照片
        - 若全部都在保護期，刪分數最低的（無論新舊）
        """
        with self._connect() as conn:
            with conn.cursor() as cur:
                photos = self._get_all_photos(restaurant_id, cur)
                if len(photos) >= MAX_PHOTOS:
                    now = datetime.now()
                    protect_cutoff = now - timedelta(days=PHOTO_PROTECT_DAYS)
                    # 找不在保護期的照片
                    unprotected = [p for p in photos if p["uploaded_at"] < protect_cutoff]
                    candidates = unprotected if unprotected else photos
                    worst = min(candidates, key=lambda p: p["score"])
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
                    cur.execute("SELECT * FROM restaurants WHERE category=%s", (category,))
                else:
                    cur.execute("SELECT * FROM restaurants")
                restaurants = [dict(r) for r in cur.fetchall()]

                cur.execute("SELECT restaurant_id, liked_at FROM likes")
                likes_rows = cur.fetchall()

        with self._connect() as conn:
            with conn.cursor() as cur:
                for r in restaurants:
                    r["score"] = self._restaurant_score(r["id"], r["created_at"], likes_rows)
                    r["like_count"] = self._like_count(r["id"])
                    r["comment_count"] = self._comment_count(r["id"])
                    r["view_count"] = self._view_count(r["id"])
                    main = self._get_main_photo(r["id"], cur)
                    r["image_url"] = main["image_url"] if main else None
                    r["photo_count"] = len(self._get_all_photos(r["id"], cur))
                    if isinstance(r.get("created_at"), datetime):
                        r["created_at"] = r["created_at"].strftime("%Y-%m-%d %H:%M:%S")

        restaurants.sort(key=lambda x: x["score"], reverse=True)
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
                r["view_count"] = self._view_count(restaurant_id)
                photos = self._get_all_photos(restaurant_id, cur)
                main = max(photos, key=lambda p: p["score"]) if photos else None
                r["image_url"] = main["image_url"] if main else None
                r["photo_count"] = len(photos)
                r["photos"] = photos  # 全部照片資訊
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

    def _like_count(self, rid: int) -> int:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) as cnt FROM likes WHERE restaurant_id=%s", (rid,))
                return cur.fetchone()["cnt"]

    def _comment_count(self, rid: int) -> int:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) as cnt FROM comments WHERE restaurant_id=%s", (rid,))
                return cur.fetchone()["cnt"]

    def _view_count(self, rid: int) -> int:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) as cnt FROM views_log WHERE restaurant_id=%s", (rid,))
                return cur.fetchone()["cnt"]

    # ── 觀看數（不重複）──────────────────────────────────────────────────────
    def log_view(self, restaurant_id: int, user_id: str):
        """同一人同一店只記一次"""
        with self._connect() as conn:
            with conn.cursor() as cur:
                try:
                    cur.execute(
                        "INSERT INTO views_log (restaurant_id, user_id) VALUES (%s,%s)",
                        (restaurant_id, user_id)
                    )
                    conn.commit()
                except Exception:
                    pass  # UNIQUE 衝突就忽略

    # ── 店家按讚 ──────────────────────────────────────────────────────────────
    def toggle_like(self, restaurant_id: int, user_id: str) -> str:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id FROM likes WHERE restaurant_id=%s AND user_id=%s",
                    (restaurant_id, user_id)
                )
                if cur.fetchone():
                    cur.execute("DELETE FROM likes WHERE restaurant_id=%s AND user_id=%s",
                                (restaurant_id, user_id))
                    conn.commit()
                    return "unliked"
                else:
                    cur.execute("INSERT INTO likes (restaurant_id, user_id) VALUES (%s,%s)",
                                (restaurant_id, user_id))
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

    # ── 照片按讚 ──────────────────────────────────────────────────────────────
    def toggle_photo_like(self, photo_id: int, user_id: str) -> str:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id FROM photo_likes WHERE photo_id=%s AND user_id=%s",
                    (photo_id, user_id)
                )
                if cur.fetchone():
                    cur.execute("DELETE FROM photo_likes WHERE photo_id=%s AND user_id=%s",
                                (photo_id, user_id))
                    conn.commit()
                    return "unliked"
                else:
                    cur.execute("INSERT INTO photo_likes (photo_id, user_id) VALUES (%s,%s)",
                                (photo_id, user_id))
                    conn.commit()
                    return "liked"

    def has_photo_liked(self, photo_id: int, user_id: str) -> bool:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id FROM photo_likes WHERE photo_id=%s AND user_id=%s",
                    (photo_id, user_id)
                )
                return cur.fetchone() is not None

    def get_photo_by_id(self, photo_id: int) -> Optional[dict]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM photos WHERE id=%s", (photo_id,))
                row = cur.fetchone()
                if not row:
                    return None
                r = dict(row)
                r["like_count"] = self._photo_like_count(photo_id, cur)
                return r

    # ── 評論 ──────────────────────────────────────────────────────────────────
    def add_comment(self, restaurant_id: int, user_id: str, content: str):
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO comments (restaurant_id, user_id, content) VALUES (%s,%s,%s)",
                    (restaurant_id, user_id, content)
                )
            conn.commit()

    def get_comments(self, restaurant_id: int, limit: int = 3) -> list[dict]:
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
    def update_category(self, rid, user_id, category): return self._update("category", rid, user_id, category)
    def update_price_range(self, rid, user_id, price_range): return self._update("price_range", rid, user_id, price_range)
