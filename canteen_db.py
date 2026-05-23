# canteen_db.py
# SQLite 資料庫：儲存分享的餐廳資料

import sqlite3
import os
from datetime import datetime
from typing import Optional


DB_PATH = os.getenv("CANTEEN_DB_PATH", "canteen.db")


class CanteenDB:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._init_db()

    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row  # 讓結果可以用欄位名稱存取
        return conn

    def _init_db(self):
        """建立資料表（如果不存在）"""
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS restaurants (
                    id        INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id   TEXT NOT NULL,
                    name      TEXT NOT NULL,
                    image_url TEXT NOT NULL,
                    review    TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
            """)
            conn.commit()

    def add_restaurant(
        self,
        user_id: str,
        name: str,
        image_url: str,
        review: str
    ) -> int:
        """新增一筆餐廳分享，回傳 row id"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._connect() as conn:
            cur = conn.execute(
                """INSERT INTO restaurants (user_id, name, image_url, review, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (user_id, name, image_url, review, now)
            )
            conn.commit()
            return cur.lastrowid

    def get_recent(self, limit: int = 10) -> list[dict]:
        """取得最新 N 筆，依建立時間降冪排列"""
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT id, name, image_url, review, created_at
                   FROM restaurants
                   ORDER BY id DESC
                   LIMIT ?""",
                (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    def get_by_id(self, restaurant_id: int) -> Optional[dict]:
        """用 id 取得單筆資料"""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM restaurants WHERE id = ?",
                (restaurant_id,)
            ).fetchone()
        return dict(row) if row else None

    def count(self) -> int:
        with self._connect() as conn:
            return conn.execute("SELECT COUNT(*) FROM restaurants").fetchone()[0]
