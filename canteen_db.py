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
        """
        建立所有資料表（3NF）
        - 移除 report_count 衍生欄位，改為即時 COUNT
        - status 只由管理員手動控制
        - 舊資料庫相容：用 IF NOT EXISTS 和 ADD COLUMN IF NOT EXISTS
        """
        with self._connect() as conn:
            with conn.cursor() as cur:
                # restaurants：移除 report_count，保留 status
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS restaurants (
                        id          SERIAL PRIMARY KEY,
                        user_id     TEXT NOT NULL,
                        name        TEXT NOT NULL,
                        category    TEXT NOT NULL DEFAULT '其他',
                        price_range TEXT NOT NULL DEFAULT '',
                        review      TEXT NOT NULL,
                        status      TEXT NOT NULL DEFAULT 'active',
                        created_at  TIMESTAMP NOT NULL DEFAULT NOW()
                    )
                """)
                # photos：移除 report_count，保留 status
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS photos (
                        id            SERIAL PRIMARY KEY,
                        restaurant_id INTEGER NOT NULL REFERENCES restaurants(id) ON DELETE CASCADE,
                        user_id       TEXT NOT NULL,
                        image_url     TEXT NOT NULL,
                        uploaded_at   TIMESTAMP NOT NULL DEFAULT NOW(),
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
                    CREATE TABLE IF NOT EXISTS restaurant_reports (
                        id            SERIAL PRIMARY KEY,
                        restaurant_id INTEGER NOT NULL REFERENCES restaurants(id) ON DELETE CASCADE,
                        user_id       TEXT NOT NULL,
                        reported_at   TIMESTAMP NOT NULL DEFAULT NOW(),
                        UNIQUE(restaurant_id, user_id)
                    )
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS photo_reports (
                        id          SERIAL PRIMARY KEY,
                        photo_id    INTEGER NOT NULL REFERENCES photos(id) ON DELETE CASCADE,
                        user_id     TEXT NOT NULL,
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

                # ── 舊資料庫相容（不動現有資料）────────────────────────────
                migrations = [
                    # 新增 status 欄位（若不存在）
                    "ALTER TABLE restaurants ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'active'",
                    "ALTER TABLE photos ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'active'",
                    # 移除舊的 report_count（若存在）— 資料已在 reports 表，安全移除
                    "ALTER TABLE restaurants DROP COLUMN IF EXISTS report_count",
                    "ALTER TABLE photos DROP COLUMN IF EXISTS report_count",
                    # 建立 reports 表（若不存在）
                    """CREATE TABLE IF NOT EXISTS restaurant_reports (
                        id SERIAL PRIMARY KEY,
                        restaurant_id INTEGER NOT NULL REFERENCES restaurants(id) ON DELETE CASCADE,
                        user_id TEXT NOT NULL,
                        reported_at TIMESTAMP NOT NULL DEFAULT NOW(),
                        UNIQUE(restaurant_id, user_id)
                    )""",
                    """CREATE TABLE IF NOT EXISTS photo_reports (
                        id SERIAL PRIMARY KEY,
                        photo_id INTEGER NOT NULL REFERENCES photos(id) ON DELETE CASCADE,
                        user_id TEXT NOT NULL,
                        reported_at TIMESTAMP NOT NULL DEFAULT NOW(),
                        UNIQUE(photo_id, user_id)
                    )""",
                ]
                for sql in migrations:
                    try:
                        cur.execute(sql)
                    except Exception:
                        conn.rollback()
            conn.commit()

    # ── 內部計算（不存衍生資料，即時 COUNT）──────────────────────────────────

    def _restaurant_report_count(self, restaurant_id: int, cur) -> int:
        cur.execute("SELECT COUNT(*) as cnt FROM restaurant_reports WHERE restaurant_id=%s", (restaurant_id,))
        return cur.fetchone()["cnt"]

    def _photo_report_count(self, photo_id: int, cur) -> int:
        cur.execute("SELECT COUNT(*) as cnt FROM photo_reports WHERE photo_id=%s", (photo_id,))
        return cur.fetchone()["cnt"]

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

    def _view_count(self, restaurant_id: int) -> int:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) as cnt FROM views_log WHERE restaurant_id=%s", (restaurant_id,))
                return cur.fetchone()["cnt"]

    def _photo_like_count(self, photo_id: int, cur) -> int:
        cur.execute("SELECT COUNT(*) as cnt FROM photo_likes WHERE photo_id=%s", (photo_id,))
        return cur.fetchone()["cnt"]

    def _photo_score(self, photo_id: int, uploaded_at: datetime, cur) -> float:
        """照片分數 = 按讚時間衰減 × 0.8 + 新鮮度 × 0.2"""
        cur.execute("SELECT liked_at FROM photo_likes WHERE photo_id=%s", (photo_id,))
        now = datetime.now()
        like_score = sum(
            1.0 / (max((now - r["liked_at"]).total_seconds() / 86400, 0) + 1)
            for r in cur.fetchall()
        )
        age_days = max((now - uploaded_at).total_seconds() / 86400, 0)
        return like_score * 0.8 + (1.0 / (age_days + 1)) * 0.2

    def _restaurant_score(self, restaurant_id: int, created_at: datetime, likes_rows: list) -> float:
        """店家分數 = 按讚時間衰減 × 0.7 + 新鮮度 × 0.3"""
        now = datetime.now()
        like_score = sum(
            1.0 / (max((now - l["liked_at"]).total_seconds() / 86400, 0) + 1)
            for l in likes_rows if l["restaurant_id"] == restaurant_id
        )
        age_days = max((now - created_at).total_seconds() / 86400, 0)
        return like_score * 0.7 + (1.0 / (age_days + 1)) * 0.3

    def _get_all_photos(self, restaurant_id: int, cur, include_hidden: bool = False) -> list[dict]:
        if include_hidden:
            cur.execute("SELECT * FROM photos WHERE restaurant_id=%s ORDER BY uploaded_at DESC", (restaurant_id,))
        else:
            cur.execute("SELECT * FROM photos WHERE restaurant_id=%s AND status='active' ORDER BY uploaded_at DESC", (restaurant_id,))
        photos = [dict(r) for r in cur.fetchall()]
        for p in photos:
            p["score"] = self._photo_score(p["id"], p["uploaded_at"], cur)
            p["like_count"] = self._photo_like_count(p["id"], cur)
            p["report_count"] = self._photo_report_count(p["id"], cur)
        return photos

    def _get_main_photo(self, restaurant_id: int, cur) -> Optional[dict]:
        photos = self._get_all_photos(restaurant_id, cur)
        return max(photos, key=lambda p: p["score"]) if photos else None

    # ── 新增餐廳 ──────────────────────────────────────────────────────────────

    def add_restaurant(self, user_id, name, category, price_range, image_url, review) -> int:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO restaurants (user_id, name, category, price_range, review) VALUES (%s,%s,%s,%s,%s) RETURNING id",
                    (user_id, name, category, price_range, review)
                )
                new_id = cur.fetchone()["id"]
                cur.execute(
                    "INSERT INTO photos (restaurant_id, user_id, image_url) VALUES (%s,%s,%s)",
                    (new_id, user_id, image_url)
                )
            conn.commit()
        return new_id

    # ── 新增照片 ──────────────────────────────────────────────────────────────

    def add_photo(self, restaurant_id: int, user_id: str, image_url: str) -> str:
        """回傳 'ok' / 'exceeded'"""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) as cnt FROM photos WHERE restaurant_id=%s AND user_id=%s",
                    (restaurant_id, user_id)
                )
                if cur.fetchone()["cnt"] >= MAX_PHOTOS_PER_USER:
                    return "exceeded"

                photos = self._get_all_photos(restaurant_id, cur)
                if len(photos) >= MAX_PHOTOS:
                    now = datetime.now()
                    unprotected = [p for p in photos if p["uploaded_at"] < now - timedelta(days=PHOTO_PROTECT_DAYS)]
                    candidates = unprotected if unprotected else photos
                    worst = min(candidates, key=lambda p: p["score"])
                    cur.execute("DELETE FROM photos WHERE id=%s", (worst["id"],))

                cur.execute(
                    "INSERT INTO photos (restaurant_id, user_id, image_url) VALUES (%s,%s,%s)",
                    (restaurant_id, user_id, image_url)
                )
            conn.commit()
        return "ok"

    # ── 查詢 ──────────────────────────────────────────────────────────────────

    def get_recent(self, limit: int = 30, category: str = None) -> list[dict]:
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
                    r["report_count"] = self._restaurant_report_count(r["id"], cur)
                    main = self._get_main_photo(r["id"], cur)
                    r["image_url"] = main["image_url"] if main else None
                    r["photo_count"] = len(self._get_all_photos(r["id"], cur))
                    if isinstance(r.get("created_at"), datetime):
                        r["created_at"] = r["created_at"].strftime("%Y-%m-%d %H:%M:%S")

        restaurants.sort(key=lambda x: x["score"], reverse=True)
        return restaurants[:limit]

    def get_by_id(self, restaurant_id: int, include_hidden: bool = False) -> Optional[dict]:
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
                r["report_count"] = self._restaurant_report_count(restaurant_id, cur)
                photos = self._get_all_photos(restaurant_id, cur, include_hidden=include_hidden)
                main = max(photos, key=lambda p: p["score"]) if photos else None
                r["image_url"] = main["image_url"] if main else None
                r["photo_count"] = len(photos)
                r["photos"] = photos
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

    # ── 觀看數 ────────────────────────────────────────────────────────────────

    def log_view(self, restaurant_id: int, user_id: str):
        with self._connect() as conn:
            with conn.cursor() as cur:
                try:
                    cur.execute("INSERT INTO views_log (restaurant_id, user_id) VALUES (%s,%s)", (restaurant_id, user_id))
                    conn.commit()
                except Exception:
                    pass

    # ── 店家按讚 ──────────────────────────────────────────────────────────────

    def toggle_like(self, restaurant_id: int, user_id: str) -> str:
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

    def has_liked(self, restaurant_id: int, user_id: str) -> bool:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM likes WHERE restaurant_id=%s AND user_id=%s", (restaurant_id, user_id))
                return cur.fetchone() is not None

    # ── 照片按讚 ──────────────────────────────────────────────────────────────

    def toggle_photo_like(self, photo_id: int, user_id: str) -> str:
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

    def has_photo_liked(self, photo_id: int, user_id: str) -> bool:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM photo_likes WHERE photo_id=%s AND user_id=%s", (photo_id, user_id))
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
                r["report_count"] = self._photo_report_count(photo_id, cur)
                return r

    # ── 店家檢舉 ──────────────────────────────────────────────────────────────

    def report_restaurant(self, restaurant_id: int, user_id: str) -> str:
        """
        檢舉店家，狀態由管理員手動控制
        回傳：already_reported / pending_review / hidden / reported
        """
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM restaurant_reports WHERE restaurant_id=%s AND user_id=%s", (restaurant_id, user_id))
                if cur.fetchone():
                    return "already_reported"
                cur.execute("INSERT INTO restaurant_reports (restaurant_id, user_id) VALUES (%s,%s)", (restaurant_id, user_id))
                count = self._restaurant_report_count(restaurant_id, cur) + 1
                # 達門檻時自動更新 status，管理員可之後手動覆蓋
                if count >= REPORT_HIDE_THRESHOLD:
                    cur.execute("UPDATE restaurants SET status='hidden' WHERE id=%s", (restaurant_id,))
                    conn.commit()
                    return "hidden"
                elif count >= REPORT_REVIEW_THRESHOLD:
                    cur.execute("UPDATE restaurants SET status='pending_review' WHERE id=%s AND status='active'", (restaurant_id,))
                    conn.commit()
                    return "pending_review"
                conn.commit()
                return "reported"

    def has_reported_restaurant(self, restaurant_id: int, user_id: str) -> bool:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM restaurant_reports WHERE restaurant_id=%s AND user_id=%s", (restaurant_id, user_id))
                return cur.fetchone() is not None

    # ── 照片檢舉 ──────────────────────────────────────────────────────────────

    def report_photo(self, photo_id: int, user_id: str) -> str:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM photo_reports WHERE photo_id=%s AND user_id=%s", (photo_id, user_id))
                if cur.fetchone():
                    return "already_reported"
                cur.execute("INSERT INTO photo_reports (photo_id, user_id) VALUES (%s,%s)", (photo_id, user_id))
                count = self._photo_report_count(photo_id, cur) + 1
                if count >= REPORT_HIDE_THRESHOLD:
                    cur.execute("UPDATE photos SET status='hidden' WHERE id=%s", (photo_id,))
                    conn.commit()
                    return "hidden"
                elif count >= REPORT_REVIEW_THRESHOLD:
                    cur.execute("UPDATE photos SET status='pending_review' WHERE id=%s AND status='active'", (photo_id,))
                    conn.commit()
                    return "pending_review"
                conn.commit()
                return "reported"

    def has_reported_photo(self, photo_id: int, user_id: str) -> bool:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM photo_reports WHERE photo_id=%s AND user_id=%s", (photo_id, user_id))
                return cur.fetchone() is not None

    # ── 評論 ──────────────────────────────────────────────────────────────────

    def add_comment(self, restaurant_id: int, user_id: str, content: str):
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("INSERT INTO comments (restaurant_id, user_id, content) VALUES (%s,%s,%s)", (restaurant_id, user_id, content))
            conn.commit()

    def get_comments(self, restaurant_id: int, limit: int = 3) -> list[dict]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM comments WHERE restaurant_id=%s ORDER BY created_at DESC LIMIT %s", (restaurant_id, limit))
                rows = [dict(r) for r in cur.fetchall()]
                for r in rows:
                    if isinstance(r.get("created_at"), datetime):
                        r["created_at"] = r["created_at"].strftime("%Y-%m-%d %H:%M:%S")
                return rows

    # ── 店家欄位更新 ──────────────────────────────────────────────────────────

    def _update(self, field: str, rid: int, user_id: str, value) -> bool:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(f"UPDATE restaurants SET {field}=%s WHERE id=%s AND user_id=%s", (value, rid, user_id))
                conn.commit()
                return cur.rowcount > 0

    def update_name(self, rid, uid, v): return self._update("name", rid, uid, v)
    def update_review(self, rid, uid, v): return self._update("review", rid, uid, v)
    def update_category(self, rid, uid, v): return self._update("category", rid, uid, v)
    def update_price_range(self, rid, uid, v): return self._update("price_range", rid, uid, v)

    # ── 管理員功能 ────────────────────────────────────────────────────────────

    def admin_get_all_restaurants(self, status: str = None) -> list[dict]:
        """列出所有店家，可依 status 篩選"""
        with self._connect() as conn:
            with conn.cursor() as cur:
                if status:
                    cur.execute(
                        "SELECT id, name, category, price_range, status, created_at FROM restaurants WHERE status=%s ORDER BY id DESC",
                        (status,)
                    )
                else:
                    cur.execute(
                        "SELECT id, name, category, price_range, status, created_at FROM restaurants ORDER BY id DESC"
                    )
                rows = [dict(r) for r in cur.fetchall()]
                for r in rows:
                    r["report_count"] = self._restaurant_report_count(r["id"], cur)
                    if isinstance(r.get("created_at"), datetime):
                        r["created_at"] = r["created_at"].strftime("%Y-%m-%d %H:%M:%S")
                return rows

    def admin_get_restaurant(self, restaurant_id: int) -> Optional[dict]:
        """查看店家完整資訊（含隱藏照片）"""
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
                r["report_count"] = self._restaurant_report_count(restaurant_id, cur)
                photos = self._get_all_photos(restaurant_id, cur, include_hidden=True)
                r["photo_count"] = len(photos)
                r["photos"] = photos
                if isinstance(r.get("created_at"), datetime):
                    r["created_at"] = r["created_at"].strftime("%Y-%m-%d %H:%M:%S")
                return r

    def admin_get_photo(self, photo_id: int) -> Optional[dict]:
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
                r = dict(row)
                r["like_count"] = self._photo_like_count(photo_id, cur)
                r["report_count"] = self._photo_report_count(photo_id, cur)
                return r

    def admin_get_reported(self) -> dict:
        """取得所有待審核或下架的店家和照片"""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM restaurants WHERE status IN ('pending_review','hidden') ORDER BY id DESC"
                )
                restaurants = [dict(r) for r in cur.fetchall()]
                for r in restaurants:
                    r["report_count"] = self._restaurant_report_count(r["id"], cur)
                    if isinstance(r.get("created_at"), datetime):
                        r["created_at"] = r["created_at"].strftime("%Y-%m-%d %H:%M:%S")

                cur.execute("""
                    SELECT p.*, r.name as restaurant_name
                    FROM photos p JOIN restaurants r ON p.restaurant_id=r.id
                    WHERE p.status IN ('pending_review','hidden')
                    ORDER BY p.id DESC
                """)
                photos = [dict(r) for r in cur.fetchall()]
                for p in photos:
                    p["report_count"] = self._photo_report_count(p["id"], cur)

        return {"restaurants": restaurants, "photos": photos}

    def admin_get_comments(self, restaurant_id: int) -> list[dict]:
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

    def admin_hide_restaurant(self, restaurant_id: int) -> bool:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE restaurants SET status='hidden' WHERE id=%s", (restaurant_id,))
                conn.commit()
                return cur.rowcount > 0

    def admin_restore_restaurant(self, restaurant_id: int) -> bool:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE restaurants SET status='active' WHERE id=%s", (restaurant_id,))
                conn.commit()
                return cur.rowcount > 0

    def admin_delete_restaurant(self, restaurant_id: int) -> bool:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM restaurants WHERE id=%s", (restaurant_id,))
                conn.commit()
                return cur.rowcount > 0

    def admin_restore_photo(self, photo_id: int) -> bool:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE photos SET status='active' WHERE id=%s", (photo_id,))
                conn.commit()
                return cur.rowcount > 0

    def admin_delete_photo(self, photo_id: int) -> bool:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM photos WHERE id=%s", (photo_id,))
                conn.commit()
                return cur.rowcount > 0

    def admin_delete_comment(self, comment_id: int) -> bool:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM comments WHERE id=%s", (comment_id,))
                conn.commit()
                return cur.rowcount > 0
