import sqlite3
import os
from typing import Optional

DB_PATH = os.getenv("CANTEEN_DB_PATH", "canteen.db")
MAX_RESTAURANTS = 10

CATEGORIES = [
    "🍱 便當／快餐",
    "🍜 麵食／湯品",
    "🥗 輕食／沙拉",
    "🧋 飲料／甜點",
    "🍔 西式／速食",
    "🍣 日韓料理",
    "🥞 早午餐",
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
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id    TEXT NOT NULL,
                    name       TEXT NOT NULL,
                    category   TEXT NOT NULL DEFAULT '其他',
                    image_url  TEXT NOT NULL,
                    review     TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
            """)
            # 舊資料庫若沒有 category 欄位，自動加上
            try:
                conn.execute("ALTER TABLE restaurants ADD COLUMN category TEXT NOT NULL DEFAULT '其他'")
            except Exception:
                pass
            conn.commit()

    def add_restaurant(self, user_id, name, category, image_url, review) -> int:
        from datetime import datetime
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO restaurants (user_id, name, category, image_url, review, created_at) VALUES (?,?,?,?,?,?)",
                (user_id, name, category, image_url, review, now)
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
            conn.commit()

    def get_recent(self, limit: int = 10) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, name, category, image_url, review, created_at FROM restaurants ORDER BY id DESC LIMIT ?",
                (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    def get_by_id(self, restaurant_id: int) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM restaurants WHERE id = ?", (restaurant_id,)
            ).fetchone()
        return dict(row) if row else None

    def get_by_user(self, user_id: str) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM restaurants WHERE user_id = ? ORDER BY id DESC",
                (user_id,)
            ).fetchall()
        return [dict(r) for r in rows]

    def update_name(self, restaurant_id: int, user_id: str, name: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE restaurants SET name=? WHERE id=? AND user_id=?",
                (name, restaurant_id, user_id)
            )
            conn.commit()
        return cur.rowcount > 0

    def update_review(self, restaurant_id: int, user_id: str, review: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE restaurants SET review=? WHERE id=? AND user_id=?",
                (review, restaurant_id, user_id)
            )
            conn.commit()
        return cur.rowcount > 0

    def update_image(self, restaurant_id: int, user_id: str, image_url: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE restaurants SET image_url=? WHERE id=? AND user_id=?",
                (image_url, restaurant_id, user_id)
            )
            conn.commit()
        return cur.rowcount > 0

    def update_category(self, restaurant_id: int, user_id: str, category: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE restaurants SET category=? WHERE id=? AND user_id=?",
                (category, restaurant_id, user_id)
            )
            conn.commit()
        return cur.rowcount > 0

    def delete(self, restaurant_id: int, user_id: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM restaurants WHERE id=? AND user_id=?",
                (restaurant_id, user_id)
            )
            conn.commit()
        return cur.rowcount > 0
