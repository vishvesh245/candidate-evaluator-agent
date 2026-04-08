import aiosqlite
import json
from datetime import datetime
from typing import Optional

DB_PATH = "applications.db"


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS applications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                sender_name TEXT,
                status TEXT NOT NULL,  -- 'pass', 'fail', 'incomplete'
                score REAL,
                result_json TEXT,
                created_at TEXT NOT NULL
            )
        """)
        await db.commit()


async def is_duplicate(email: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT id FROM applications WHERE email = ?", (email.lower(),)
        ) as cursor:
            row = await cursor.fetchone()
            return row is not None


async def save_application(
    email: str,
    sender_name: str,
    status: str,
    score: Optional[float] = None,
    result: Optional[dict] = None,
):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT OR REPLACE INTO applications (email, sender_name, status, score, result_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                email.lower(),
                sender_name,
                status,
                score,
                json.dumps(result) if result else None,
                datetime.utcnow().isoformat(),
            ),
        )
        await db.commit()


async def get_all_applications() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM applications ORDER BY created_at DESC"
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]
