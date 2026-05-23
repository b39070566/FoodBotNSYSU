import sqlite3
import os
import math
from datetime import datetime
from typing import Optional

DB_PATH = os.getenv("CANTEEN_DB_PATH", "canteen.db")
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
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._init_db()

    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS restaurants (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id     TEXT NOT NULL,
                    name        TEXT NOT NULL,
                    category    TEXT NOT NULL DEFAULT '其他',
                    price_range TEXT NOT NULL DEFAULT '',
                    image_url   TEXT NOT NULL,
                    review      TEXT NOT NULL,
                    created_at  TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS likes (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    restaurant_id INTEGER NOT NULL,
                    user_id       TEXT NOT NULL,
                    liked_at      TEXT NOT NULL,
                    UNIQUE(restaurant_id, user_id)
                )
            """)
            # 舊資料庫相容：補欄位
            for col, default in [("category", "'其他'"), ("price_range", "''")]:
                try:
                    conn.execute(f"ALTER TABLE restaurants ADD COLUMN {col} TEXT NOT NULL DEFAULT {default}")
                except Exception:
                    pass
            conn.commit()

    # ── 新增 ──────────────────────────────────────────────────────────────────
    def add_restaurant(self, user_id, name, category, price_range, image_url, review) -> int:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO restaurants (user_id, name, category, price_range, image_url, review, created_at) VALUES (?,?,?,?,?,?,?)",
                (user_id, name, category, price_range, image_url, review, now)
            )
            conn.commit()
            new_id = cur.lastrowid
            self._enforce_limit(conn)
            return new_id

    def _enforce_limit(self, conn):
        count = conn.execute("SELECT COUNT(*) FROM restaurants").fetchone()[0]
        if count > MAX_RESTAURANTS:
            to_delete = count - MAX_RESTAURANTS
            oldest = conn.execute(
                "SELECT id FROM restaurants ORDER BY id ASC LIMIT ?", (to_delete,)
            ).fetchall()
            ids = [r["id"] for r in oldest]
            conn.execute(f"DELETE FROM restaurants WHERE id IN ({','.join('?'*len(ids))})", ids)
            conn.execute(f"DELETE FROM likes WHERE restaurant_id IN ({','.join('?'*len(ids))})", ids)
            conn.commit()

    # ── 查詢 ──────────────────────────────────────────────────────────────────
    def get_recent(self, limit: int = 30, category: str = None) -> list[dict]:
        """依時間衰減權重排序，可選擇分類篩選"""
        with self._connect() as conn:
            if category:
                rows = conn.execute(
                    "SELECT * FROM restaurants WHERE category=? ORDER BY id DESC",
                    (category,)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM restaurants ORDER BY id DESC"
                ).fetchall()
            restaurants = [dict(r) for r in rows]

            # 取得所有讚的資料
            likes_rows = conn.execute("SELECT restaurant_id, liked_at FROM likes").fetchall()

        # 計算每家店的時間衰減分數
        now = datetime.now()
        like_map: dict[int, float] = {}
        for like in likes_rows:
            rid = like["restaurant_id"]
            liked_at = datetime.strptime(like["liked_at"], "%Y-%m-%d %H:%M:%S")
            days_ago = max((now - liked_at).total_seconds() / 86400, 0)
            weight = 1.0 / (days_ago + 1)
            like_map[rid] = like_map.get(rid, 0) + weight

        for r in restaurants:
            r["like_score"] = like_map.get(r["id"], 0)
            r["like_count"] = self._like_count(r["id"])

        restaurants.sort(key=lambda x: x["like_score"], reverse=True)
        return restaurants[:limit]

    def _like_count(self, restaurant_id: int) -> int:
        with self._connect() as conn:
            return conn.execute(
                "SELECT COUNT(*) FROM likes WHERE restaurant_id=?", (restaurant_id,)
            ).fetchone()[0]

    def get_by_id(self, restaurant_id: int) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM restaurants WHERE id=?", (restaurant_id,)).fetchone()
            if not row:
                return None
            r = dict(row)
            r["like_count"] = self._like_count(restaurant_id)
            return r

    def get_by_user(self, user_id: str) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM restaurants WHERE user_id=? ORDER BY id DESC", (user_id,)
            ).fetchall()
        return [dict(r) for r in rows]

    def find_by_name(self, name: str) -> Optional[dict]:
        """找同名店家（用於防重複）"""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM restaurants WHERE name=?", (name,)
            ).fetchone()
        return dict(row) if row else None

    # ── 按讚 ──────────────────────────────────────────────────────────────────
    def toggle_like(self, restaurant_id: int, user_id: str) -> str:
        """按讚 / 收回讚，回傳 'liked' 或 'unliked'"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT id FROM likes WHERE restaurant_id=? AND user_id=?",
                (restaurant_id, user_id)
            ).fetchone()
            if existing:
                conn.execute("DELETE FROM likes WHERE restaurant_id=? AND user_id=?",
                             (restaurant_id, user_id))
                conn.commit()
                return "unliked"
            else:
                conn.execute("INSERT INTO likes (restaurant_id, user_id, liked_at) VALUES (?,?,?)",
                             (restaurant_id, user_id, now))
                conn.commit()
                return "liked"

    def has_liked(self, restaurant_id: int, user_id: str) -> bool:
        with self._connect() as conn:
            return conn.execute(
                "SELECT id FROM likes WHERE restaurant_id=? AND user_id=?",
                (restaurant_id, user_id)
            ).fetchone() is not None

    # ── 編輯 ──────────────────────────────────────────────────────────────────
    def update_name(self, rid, user_id, name):
        with self._connect() as conn:
            cur = conn.execute("UPDATE restaurants SET name=? WHERE id=? AND user_id=?", (name, rid, user_id))
            conn.commit()
        return cur.rowcount > 0

    def update_review(self, rid, user_id, review):
        with self._connect() as conn:
            cur = conn.execute("UPDATE restaurants SET review=? WHERE id=? AND user_id=?", (review, rid, user_id))
            conn.commit()
        return cur.rowcount > 0

    def update_image(self, rid, user_id, image_url):
        with self._connect() as conn:
            cur = conn.execute("UPDATE restaurants SET image_url=? WHERE id=? AND user_id=?", (image_url, rid, user_id))
            conn.commit()
        return cur.rowcount > 0

    def update_category(self, rid, user_id, category):
        with self._connect() as conn:
            cur = conn.execute("UPDATE restaurants SET category=? WHERE id=? AND user_id=?", (category, rid, user_id))
            conn.commit()
        return cur.rowcount > 0

    def update_price_range(self, rid, user_id, price_range):
        with self._connect() as conn:
            cur = conn.execute("UPDATE restaurants SET price_range=? WHERE id=? AND user_id=?", (price_range, rid, user_id))
            conn.commit()
        return cur.rowcount > 0

    def delete(self, rid, user_id):
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM restaurants WHERE id=? AND user_id=?", (rid, user_id))
            conn.execute("DELETE FROM likes WHERE restaurant_id=?", (rid,))
            conn.commit()
        return cur.rowcount > 0
