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
                status TEXT NOT NULL,
                score REAL,
                result_json TEXT,
                subject TEXT,
                body_text TEXT,
                github_url TEXT,
                portfolio_url TEXT,
                has_resume INTEGER DEFAULT 0,
                created_at TEXT NOT NULL
            )
        """)
        await db.commit()


async def is_duplicate(email: str) -> bool:
    """Only block if already evaluated (pass/fail), not if previously incomplete."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT id FROM applications WHERE email = ? AND status IN ('pass', 'fail')",
            (email.lower(),),
        ) as cursor:
            row = await cursor.fetchone()
            return row is not None


async def save_application(
    email: str,
    sender_name: str,
    status: str,
    score: Optional[float] = None,
    result: Optional[dict] = None,
    subject: str = "",
    body_text: str = "",
    github_url: Optional[str] = None,
    portfolio_url: Optional[str] = None,
    has_resume: bool = False,
):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT OR REPLACE INTO applications
              (email, sender_name, status, score, result_json, subject, body_text, github_url, portfolio_url, has_resume, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                email.lower(),
                sender_name,
                status,
                score,
                json.dumps(result) if result else None,
                subject,
                body_text,
                github_url,
                portfolio_url,
                1 if has_resume else 0,
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
