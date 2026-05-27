import os
from datetime import datetime, timedelta
from typing import Optional
from urllib.parse import urlparse
import psycopg2
from psycopg2.extras import RealDictCursor

MAX_PHOTOS = 10
MAX_PHOTOS_PER_USER = 3   # 每人每店最多上傳張數
PHOTO_PROTECT_DAYS = 7
REPORT_REVIEW_THRESHOLD = 3   # 超過 3 個檢舉 → 待審核
REPORT_HIDE_THRESHOLD = 10    # 超過 10 個檢舉 → 暫時下架

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
                        uploaded_at   TIMESTAMP NOT NULL DEFAULT NOW(),
                        report_count  INTEGER NOT NULL DEFAULT 0,
                        status        TEXT NOT NULL DEFAULT 'active'
                        -- active / pending_review / hidden
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
                        reason    TEXT NOT NULL DEFAULT '不當內容',
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
                # 舊資料相容
                for sql in [
                    "ALTER TABLE photos ADD COLUMN IF NOT EXISTS report_count INTEGER NOT NULL DEFAULT 0",
                    "ALTER TABLE photos ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'active'",
                ]:
                    try:
                        cur.execute(sql)
                    except Exception:
                        pass
            conn.commit()

    # ── 照片分數 ──────────────────────────────────────────────────────────────
    def _photo_score(self, photo_id: int, uploaded_at: datetime, cur) -> float:
        cur.execute("SELECT liked_at FROM photo_likes WHERE photo_id=%s", (photo_id,))
        rows = cur.fetchall()
        now = datetime.now()
        like_score = sum(1.0 / (max((now - r["liked_at"]).total_seconds() / 86400, 0) + 1) for r in rows)
        age_days = max((now - uploaded_at).total_seconds() / 86400, 0)
        freshness = 1.0 / (age_days + 1)
        return like_score * 0.8 + freshness * 0.2

    def _get_all_photos(self, restaurant_id: int, cur, include_hidden=False) -> list[dict]:
        """只回傳 active 的照片（預設），管理員可看全部"""
        if include_hidden:
            cur.execute("SELECT * FROM photos WHERE restaurant_id=%s ORDER BY uploaded_at DESC", (restaurant_id,))
        else:
            cur.execute(
                "SELECT * FROM photos WHERE restaurant_id=%s AND status='active' ORDER BY uploaded_at DESC",
                (restaurant_id,)
            )
        photos = [dict(r) for r in cur.fetchall()]
        for p in photos:
            p["score"] = self._photo_score(p["id"], p["uploaded_at"], cur)
            p["like_count"] = self._photo_like_count(p["id"], cur)
        return photos

    def _get_main_photo(self, restaurant_id: int, cur) -> Optional[dict]:
        photos = self._get_all_photos(restaurant_id, cur)
        if not photos:
            return None
        return max(photos, key=lambda p: p["score"])

    def _photo_like_count(self, photo_id: int, cur) -> int:
        cur.execute("SELECT COUNT(*) as cnt FROM photo_likes WHERE photo_id=%s", (photo_id,))
        return cur.fetchone()["cnt"]

    # ── 餐廳分數 ──────────────────────────────────────────────────────────────
    def _restaurant_score(self, restaurant_id: int, created_at: datetime, likes_rows: list) -> float:
        now = datetime.now()
        like_score = sum(
            1.0 / (max((now - l["liked_at"]).total_seconds() / 86400, 0) + 1)
            for l in likes_rows if l["restaurant_id"] == restaurant_id
        )
        age_days = max((now - created_at).total_seconds() / 86400, 0)
        freshness = 1.0 / (age_days + 1)
        return like_score * 0.7 + freshness * 0.3

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

    # ── 新增照片（含防刷檢查）────────────────────────────────────────────────
    def add_photo(self, restaurant_id: int, user_id: str, image_url: str) -> str:
        """
        回傳：
          'ok'         → 成功
          'exceeded'   → 這個人對這家店已上傳 MAX_PHOTOS_PER_USER 張
          'full'       → 這家店已有 MAX_PHOTOS 張（全部 active）
        """
        with self._connect() as conn:
            with conn.cursor() as cur:
                # 每人每店上傳上限
                cur.execute(
                    "SELECT COUNT(*) as cnt FROM photos WHERE restaurant_id=%s AND user_id=%s",
                    (restaurant_id, user_id)
                )
                if cur.fetchone()["cnt"] >= MAX_PHOTOS_PER_USER:
                    return "exceeded"
                photos = self._get_all_photos(restaurant_id, cur)
                if len(photos) >= MAX_PHOTOS:
                    # 找保護期外分數最低的刪掉
                    now = datetime.now()
                    protect_cutoff = now - timedelta(days=PHOTO_PROTECT_DAYS)
                    unprotected = [p for p in photos if p["uploaded_at"] < protect_cutoff]
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

    def get_by_id(self, restaurant_id: int, include_hidden=False) -> Optional[dict]:
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
                else:
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
                else:
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
                r["report_count"] = r.get("report_count", 0)
                return r

    # ── 照片檢舉 ──────────────────────────────────────────────────────────────
    def report_photo(self, photo_id: int, user_id: str) -> str:
        """
        回傳：
          'already_reported' → 已檢舉過
          'pending_review'   → 達 3 個，待管理員審核
          'hidden'           → 達 10 個，暫時下架
          'reported'         → 普通檢舉成功
        """
        with self._connect() as conn:
            with conn.cursor() as cur:
                # 已檢舉過
                cur.execute("SELECT id FROM photo_reports WHERE photo_id=%s AND user_id=%s", (photo_id, user_id))
                if cur.fetchone():
                    return "already_reported"
                cur.execute("INSERT INTO photo_reports (photo_id, user_id) VALUES (%s,%s)", (photo_id, user_id))
                cur.execute("UPDATE photos SET report_count=report_count+1 WHERE id=%s RETURNING report_count", (photo_id,))
                new_count = cur.fetchone()["report_count"]
                if new_count >= REPORT_HIDE_THRESHOLD:
                    cur.execute("UPDATE photos SET status='hidden' WHERE id=%s", (photo_id,))
                    conn.commit()
                    return "hidden"
                elif new_count >= REPORT_REVIEW_THRESHOLD:
                    cur.execute("UPDATE photos SET status='pending_review' WHERE id=%s", (photo_id,))
                    conn.commit()
                    return "pending_review"
                conn.commit()
                return "reported"

    def has_reported(self, photo_id: int, user_id: str) -> bool:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM photo_reports WHERE photo_id=%s AND user_id=%s", (photo_id, user_id))
                return cur.fetchone() is not None

    # ── 管理員功能 ────────────────────────────────────────────────────────────
    def admin_delete_photo(self, photo_id: int) -> bool:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM photos WHERE id=%s", (photo_id,))
                conn.commit()
                return cur.rowcount > 0

    def admin_restore_photo(self, photo_id: int) -> bool:
        """恢復被誤報的照片"""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE photos SET status='active', report_count=0 WHERE id=%s",
                    (photo_id,)
                )
                conn.commit()
                return cur.rowcount > 0

    def admin_get_reported_photos(self) -> list[dict]:
        """取得所有待審核或下架的照片"""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT p.*, r.name as restaurant_name
                    FROM photos p
                    JOIN restaurants r ON p.restaurant_id = r.id
                    WHERE p.status IN ('pending_review', 'hidden')
                    ORDER BY p.report_count DESC
                """)
                return [dict(row) for row in cur.fetchall()]

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
                cur.execute(f"UPDATE restaurants SET {field}=%s WHERE id=%s AND user_id=%s", (value, rid, user_id))
                conn.commit()
                return cur.rowcount > 0

    def update_name(self, rid, user_id, name): return self._update("name", rid, user_id, name)
    def update_review(self, rid, user_id, review): return self._update("review", rid, user_id, review)
    def update_category(self, rid, user_id, category): return self._update("category", rid, user_id, category)
    def update_price_range(self, rid, user_id, price_range): return self._update("price_range", rid, user_id, price_range)