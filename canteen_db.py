import os
from datetime import datetime, timedelta
from typing import Optional
from urllib.parse import urlparse
import psycopg2
from psycopg2.extras import RealDictCursor

MAX_PHOTOS = 10
MAX_PHOTOS_PER_USER = 3
PHOTO_PROTECT_DAYS = 7
REPORT_REVIEW_THRESHOLD = 3
REPORT_HIDE_THRESHOLD = 10

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
                        report_count INTEGER NOT NULL DEFAULT 0,
                        status      TEXT NOT NULL DEFAULT 'active',
                        created_at  TIMESTAMP NOT NULL DEFAULT NOW()
                    )
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS restaurant_reports (
                        id          SERIAL PRIMARY KEY,
                        restaurant_id INTEGER NOT NULL REFERENCES restaurants(id) ON DELETE CASCADE,
                        user_id     TEXT NOT NULL,
                        reported_at TIMESTAMP NOT NULL DEFAULT NOW(),
                        UNIQUE(restaurant_id, user_id)
                    )
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS photos (
                        id            SERIAL PRIMARY KEY,
                        restaurant_id INTEGER NOT NULL REFERENCES restaurants(id) ON DELETE CASCADE,
                        user_id       TEXT NOT NULL,
                        image_url     TEXT NOT NULL,
                        uploaded_at   TIMESTAMP NOT NULL DEFAULT NOW(),
                        report_count  INTEGER NOT NULL DEFAULT 0,
                        status        TEXT NOT NULL DEFAULT 'active'
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
                    CREATE TABLE IF NOT EXISTS photo_reports (
                        id        SERIAL PRIMARY KEY,
                        photo_id  INTEGER NOT NULL REFERENCES photos(id) ON DELETE CASCADE,
                        user_id   TEXT NOT NULL,
                        reported_at TIMESTAMP NOT NULL DEFAULT NOW(),
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
                for sql in [
                    "ALTER TABLE restaurants ADD COLUMN IF NOT EXISTS report_count INTEGER NOT NULL DEFAULT 0",
                    "ALTER TABLE restaurants ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'active'",
                    "ALTER TABLE photos ADD COLUMN IF NOT EXISTS report_count INTEGER NOT NULL DEFAULT 0",
                    "ALTER TABLE photos ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'active'",
                ]:
                    try:
                        cur.execute(sql)
                    except Exception:
                        pass
            conn.commit()

    # ── 照片分數 ──────────────────────────────────────────────────────────────
    def _photo_score(self, photo_id, uploaded_at, cur) -> float:
        cur.execute("SELECT liked_at FROM photo_likes WHERE photo_id=%s", (photo_id,))
        now = datetime.now()
        like_score = sum(1.0 / (max((now - r["liked_at"]).total_seconds() / 86400, 0) + 1) for r in cur.fetchall())
        age_days = max((now - uploaded_at).total_seconds() / 86400, 0)
        return like_score * 0.8 + (1.0 / (age_days + 1)) * 0.2

    def _get_all_photos(self, restaurant_id, cur, include_hidden=False) -> list[dict]:
        if include_hidden:
            cur.execute("SELECT * FROM photos WHERE restaurant_id=%s ORDER BY uploaded_at DESC", (restaurant_id,))
        else:
            cur.execute("SELECT * FROM photos WHERE restaurant_id=%s AND status='active' ORDER BY uploaded_at DESC", (restaurant_id,))
        photos = [dict(r) for r in cur.fetchall()]
        for p in photos:
            p["score"] = self._photo_score(p["id"], p["uploaded_at"], cur)
            p["like_count"] = self._photo_like_count(p["id"], cur)
        return photos

    def _get_main_photo(self, restaurant_id, cur) -> Optional[dict]:
        photos = self._get_all_photos(restaurant_id, cur)
        return max(photos, key=lambda p: p["score"]) if photos else None

    def _photo_like_count(self, photo_id, cur) -> int:
        cur.execute("SELECT COUNT(*) as cnt FROM photo_likes WHERE photo_id=%s", (photo_id,))
        return cur.fetchone()["cnt"]

    def _restaurant_score(self, restaurant_id, created_at, likes_rows) -> float:
        now = datetime.now()
        like_score = sum(
            1.0 / (max((now - l["liked_at"]).total_seconds() / 86400, 0) + 1)
            for l in likes_rows if l["restaurant_id"] == restaurant_id
        )
        age_days = max((now - created_at).total_seconds() / 86400, 0)
        return like_score * 0.7 + (1.0 / (age_days + 1)) * 0.3

    # ── 新增餐廳 ──────────────────────────────────────────────────────────────
    def add_restaurant(self, user_id, name, category, price_range, image_url, review) -> int:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO restaurants (user_id, name, category, price_range, review) VALUES (%s,%s,%s,%s,%s) RETURNING id",
                    (user_id, name, category, price_range, review)
                )
                new_id = cur.fetchone()["id"]
                cur.execute("INSERT INTO photos (restaurant_id, user_id, image_url) VALUES (%s,%s,%s)", (new_id, user_id, image_url))
            conn.commit()
        return new_id

    # ── 新增照片 ──────────────────────────────────────────────────────────────
    def add_photo(self, restaurant_id, user_id, image_url) -> str:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) as cnt FROM photos WHERE restaurant_id=%s AND user_id=%s", (restaurant_id, user_id))
                if cur.fetchone()["cnt"] >= MAX_PHOTOS_PER_USER:
                    return "exceeded"
                photos = self._get_all_photos(restaurant_id, cur)
                if len(photos) >= MAX_PHOTOS:
                    now = datetime.now()
                    unprotected = [p for p in photos if p["uploaded_at"] < now - timedelta(days=PHOTO_PROTECT_DAYS)]
                    candidates = unprotected if unprotected else photos
                    worst = min(candidates, key=lambda p: p["score"])
                    cur.execute("DELETE FROM photos WHERE id=%s", (worst["id"],))
                cur.execute("INSERT INTO photos (restaurant_id, user_id, image_url) VALUES (%s,%s,%s)", (restaurant_id, user_id, image_url))
            conn.commit()
        return "ok"

    # ── 查詢 ──────────────────────────────────────────────────────────────────
    def get_recent(self, limit=30, category=None) -> list[dict]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                if category:
                    cur.execute("SELECT * FROM restaurants WHERE category=%s AND status='active'", (category,))
                else:
                    cur.execute("SELECT * FROM restaurants WHERE status='active'")
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

    def get_by_id(self, restaurant_id, include_hidden=False) -> Optional[dict]:
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
                photos = self._get_all_photos(restaurant_id, cur, include_hidden=include_hidden)
                main = max(photos, key=lambda p: p["score"]) if photos else None
                r["image_url"] = main["image_url"] if main else None
                r["photo_count"] = len(photos)
                r["photos"] = photos
                if isinstance(r.get("created_at"), datetime):
                    r["created_at"] = r["created_at"].strftime("%Y-%m-%d %H:%M:%S")
                return r

    def get_by_user(self, user_id) -> list[dict]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM restaurants WHERE user_id=%s ORDER BY id DESC", (user_id,))
                rows = [dict(r) for r in cur.fetchall()]
                for r in rows:
                    main = self._get_main_photo(r["id"], cur)
                    r["image_url"] = main["image_url"] if main else None
                return rows

    def find_by_name(self, name) -> Optional[dict]:
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

    def _like_count(self, rid):
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) as cnt FROM likes WHERE restaurant_id=%s", (rid,))
                return cur.fetchone()["cnt"]

    def _comment_count(self, rid):
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) as cnt FROM comments WHERE restaurant_id=%s", (rid,))
                return cur.fetchone()["cnt"]

    def _view_count(self, rid):
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) as cnt FROM views_log WHERE restaurant_id=%s", (rid,))
                return cur.fetchone()["cnt"]

    def log_view(self, restaurant_id, user_id):
        with self._connect() as conn:
            with conn.cursor() as cur:
                try:
                    cur.execute("INSERT INTO views_log (restaurant_id, user_id) VALUES (%s,%s)", (restaurant_id, user_id))
                    conn.commit()
                except Exception:
                    pass

    # ── 店家按讚 ──────────────────────────────────────────────────────────────
    def toggle_like(self, restaurant_id, user_id) -> str:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM likes WHERE restaurant_id=%s AND user_id=%s", (restaurant_id, user_id))
                if cur.fetchone():
                    cur.execute("DELETE FROM likes WHERE restaurant_id=%s AND user_id=%s", (restaurant_id, user_id))
                    conn.commit()
                    return "unliked"
                cur.execute("INSERT INTO likes (restaurant_id, user_id) VALUES (%s,%s)", (restaurant_id, user_id))
                conn.commit()
                return "liked"

    def has_liked(self, restaurant_id, user_id) -> bool:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM likes WHERE restaurant_id=%s AND user_id=%s", (restaurant_id, user_id))
                return cur.fetchone() is not None

    # ── 照片按讚 ──────────────────────────────────────────────────────────────
    def toggle_photo_like(self, photo_id, user_id) -> str:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM photo_likes WHERE photo_id=%s AND user_id=%s", (photo_id, user_id))
                if cur.fetchone():
                    cur.execute("DELETE FROM photo_likes WHERE photo_id=%s AND user_id=%s", (photo_id, user_id))
                    conn.commit()
                    return "unliked"
                cur.execute("INSERT INTO photo_likes (photo_id, user_id) VALUES (%s,%s)", (photo_id, user_id))
                conn.commit()
                return "liked"

    def has_photo_liked(self, photo_id, user_id) -> bool:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM photo_likes WHERE photo_id=%s AND user_id=%s", (photo_id, user_id))
                return cur.fetchone() is not None

    def get_photo_by_id(self, photo_id) -> Optional[dict]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM photos WHERE id=%s", (photo_id,))
                row = cur.fetchone()
                if not row:
                    return None
                r = dict(row)
                r["like_count"] = self._photo_like_count(photo_id, cur)
                return r

    # ── 店家檢舉 ──────────────────────────────────────────────────────────────
    def report_restaurant(self, restaurant_id, user_id) -> str:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM restaurant_reports WHERE restaurant_id=%s AND user_id=%s", (restaurant_id, user_id))
                if cur.fetchone():
                    return "already_reported"
                cur.execute("INSERT INTO restaurant_reports (restaurant_id, user_id) VALUES (%s,%s)", (restaurant_id, user_id))
                cur.execute("UPDATE restaurants SET report_count=report_count+1 WHERE id=%s RETURNING report_count", (restaurant_id,))
                count = cur.fetchone()["report_count"]
                if count >= REPORT_HIDE_THRESHOLD:
                    cur.execute("UPDATE restaurants SET status='hidden' WHERE id=%s", (restaurant_id,))
                    conn.commit()
                    return "hidden"
                elif count >= REPORT_REVIEW_THRESHOLD:
                    cur.execute("UPDATE restaurants SET status='pending_review' WHERE id=%s", (restaurant_id,))
                    conn.commit()
                    return "pending_review"
                conn.commit()
                return "reported"

    def has_reported_restaurant(self, restaurant_id, user_id) -> bool:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM restaurant_reports WHERE restaurant_id=%s AND user_id=%s", (restaurant_id, user_id))
                return cur.fetchone() is not None

    # ── 照片檢舉 ──────────────────────────────────────────────────────────────
    def report_photo(self, photo_id, user_id) -> str:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM photo_reports WHERE photo_id=%s AND user_id=%s", (photo_id, user_id))
                if cur.fetchone():
                    return "already_reported"
                cur.execute("INSERT INTO photo_reports (photo_id, user_id) VALUES (%s,%s)", (photo_id, user_id))
                cur.execute("UPDATE photos SET report_count=report_count+1 WHERE id=%s RETURNING report_count", (photo_id,))
                count = cur.fetchone()["report_count"]
                if count >= REPORT_HIDE_THRESHOLD:
                    cur.execute("UPDATE photos SET status='hidden' WHERE id=%s", (photo_id,))
                    conn.commit()
                    return "hidden"
                elif count >= REPORT_REVIEW_THRESHOLD:
                    cur.execute("UPDATE photos SET status='pending_review' WHERE id=%s", (photo_id,))
                    conn.commit()
                    return "pending_review"
                conn.commit()
                return "reported"

    def has_reported_photo(self, photo_id, user_id) -> bool:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM photo_reports WHERE photo_id=%s AND user_id=%s", (photo_id, user_id))
                return cur.fetchone() is not None

    # ── 管理員 ────────────────────────────────────────────────────────────────
    def admin_delete_photo(self, photo_id) -> bool:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM photos WHERE id=%s", (photo_id,))
                conn.commit()
                return cur.rowcount > 0

    def admin_restore_photo(self, photo_id) -> bool:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE photos SET status='active', report_count=0 WHERE id=%s", (photo_id,))
                conn.commit()
                return cur.rowcount > 0

    def admin_restore_restaurant(self, restaurant_id) -> bool:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE restaurants SET status='active', report_count=0 WHERE id=%s", (restaurant_id,))
                conn.commit()
                return cur.rowcount > 0

    def admin_get_reported(self) -> dict:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM restaurants WHERE status IN ('pending_review','hidden') ORDER BY report_count DESC")
                restaurants = [dict(r) for r in cur.fetchall()]
                cur.execute("""
                    SELECT p.*, r.name as restaurant_name
                    FROM photos p JOIN restaurants r ON p.restaurant_id=r.id
                    WHERE p.status IN ('pending_review','hidden')
                    ORDER BY p.report_count DESC
                """)
                photos = [dict(r) for r in cur.fetchall()]
        return {"restaurants": restaurants, "photos": photos}

    # ── 評論 ──────────────────────────────────────────────────────────────────
    def add_comment(self, restaurant_id, user_id, content):
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("INSERT INTO comments (restaurant_id, user_id, content) VALUES (%s,%s,%s)", (restaurant_id, user_id, content))
            conn.commit()

    def get_comments(self, restaurant_id, limit=3) -> list[dict]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM comments WHERE restaurant_id=%s ORDER BY created_at DESC LIMIT %s", (restaurant_id, limit))
                rows = [dict(r) for r in cur.fetchall()]
                for r in rows:
                    if isinstance(r.get("created_at"), datetime):
                        r["created_at"] = r["created_at"].strftime("%Y-%m-%d %H:%M:%S")
                return rows

    # ── 編輯 ──────────────────────────────────────────────────────────────────
    def _update(self, field, rid, user_id, value):
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(f"UPDATE restaurants SET {field}=%s WHERE id=%s AND user_id=%s", (value, rid, user_id))
                conn.commit()
                return cur.rowcount > 0

    def update_name(self, rid, uid, v): return self._update("name", rid, uid, v)
    def update_review(self, rid, uid, v): return self._update("review", rid, uid, v)
    def update_category(self, rid, uid, v): return self._update("category", rid, uid, v)
    def update_price_range(self, rid, uid, v): return self._update("price_range", rid, uid, v)

    # ── 管理員完整功能（補充）────────────────────────────────────────────────
    def admin_delete_restaurant(self, restaurant_id: int) -> bool:
        """永久刪除店家（連同所有照片、按讚、評論，CASCADE 自動處理）"""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM restaurants WHERE id=%s", (restaurant_id,))
                conn.commit()
                return cur.rowcount > 0

    def admin_hide_restaurant(self, restaurant_id: int) -> bool:
        """暫時下架但保留資料"""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE restaurants SET status='hidden' WHERE id=%s", (restaurant_id,))
                conn.commit()
                return cur.rowcount > 0

    def admin_get_restaurant(self, restaurant_id: int) -> Optional[dict]:
        """查看店家完整資訊（含隱藏狀態）"""
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
                photos = self._get_all_photos(restaurant_id, cur, include_hidden=True)
                r["photo_count"] = len(photos)
                r["photos"] = photos
                if isinstance(r.get("created_at"), datetime):
                    r["created_at"] = r["created_at"].strftime("%Y-%m-%d %H:%M:%S")
                return r

    def admin_get_photo(self, photo_id: int) -> Optional[dict]:
        """查看照片完整資訊"""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT p.*, r.name as restaurant_name
                    FROM photos p JOIN restaurants r ON p.restaurant_id=r.id
                    WHERE p.id=%s
                """, (photo_id,))
                row = cur.fetchone()
                if not row:
                    return None
                result = dict(row)
                result["like_count"] = self._photo_like_count(photo_id, cur)
                return result

    def admin_get_comments(self, restaurant_id: int) -> list[dict]:
        """取得店家所有評論"""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM comments WHERE restaurant_id=%s ORDER BY created_at DESC",
                    (restaurant_id,)
                )
                rows = [dict(r) for r in cur.fetchall()]
                for r in rows:
                    if isinstance(r.get("created_at"), datetime):
                        r["created_at"] = r["created_at"].strftime("%Y-%m-%d %H:%M:%S")
                return rows

    def admin_delete_comment(self, comment_id: int) -> bool:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM comments WHERE id=%s", (comment_id,))
                conn.commit()
                return cur.rowcount > 0
