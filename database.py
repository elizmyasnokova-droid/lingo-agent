"""
Database — хранение пользователей, слов, сессий, истории.
"""
import aiosqlite
from datetime import datetime, timedelta
from typing import Optional
from config import DB_PATH


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                user_id       INTEGER PRIMARY KEY,
                username      TEXT,
                first_name    TEXT,
                level         TEXT DEFAULT 'unknown',
                level_score   INTEGER DEFAULT 0,
                native_lang   TEXT DEFAULT 'ru',
                streak_days   INTEGER DEFAULT 0,
                last_practice TEXT,
                created_at    TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS vocabulary (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL,
                word        TEXT NOT NULL,
                translation TEXT NOT NULL,
                example     TEXT,
                category    TEXT,
                ease_factor REAL DEFAULT 2.5,
                interval    INTEGER DEFAULT 1,
                next_review TEXT DEFAULT (datetime('now')),
                times_seen  INTEGER DEFAULT 0,
                times_correct INTEGER DEFAULT 0,
                created_at  TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            );

            CREATE TABLE IF NOT EXISTS sessions (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id      INTEGER NOT NULL,
                session_type TEXT NOT NULL,
                topic        TEXT,
                score        INTEGER DEFAULT 0,
                corrections  INTEGER DEFAULT 0,
                duration_sec INTEGER DEFAULT 0,
                summary      TEXT,
                created_at   TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            );

            CREATE TABLE IF NOT EXISTS chat_history (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER NOT NULL,
                role       TEXT NOT NULL,
                content    TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now'))
            );
        """)
        await db.commit()


# ─── Users ───

async def ensure_user(user_id: int, username: str = None, first_name: str = None):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO users (user_id, username, first_name) VALUES (?,?,?)",
            (user_id, username, first_name)
        )
        await db.commit()


async def get_user(user_id: int) -> Optional[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users WHERE user_id=?", (user_id,)) as c:
            row = await c.fetchone()
    return dict(row) if row else None


async def set_user_level(user_id: int, level: str, score: int = 0):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET level=?, level_score=? WHERE user_id=?",
            (level, score, user_id)
        )
        await db.commit()


async def update_streak(user_id: int):
    user = await get_user(user_id)
    if not user:
        return
    today = datetime.now().date()
    last = user.get("last_practice")
    streak = user.get("streak_days", 0)

    if last:
        try:
            last_date = datetime.fromisoformat(last).date()
            if last_date == today:
                return  # уже занимался сегодня
            elif last_date == today - timedelta(days=1):
                streak += 1  # серия продолжается
            else:
                streak = 1  # серия прервалась
        except Exception:
            streak = 1
    else:
        streak = 1

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET streak_days=?, last_practice=? WHERE user_id=?",
            (streak, datetime.now().isoformat(), user_id)
        )
        await db.commit()


# ─── Vocabulary ───

async def add_word(user_id: int, word: str, translation: str,
                   example: str = None, category: str = None) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """INSERT INTO vocabulary (user_id, word, translation, example, category)
               VALUES (?,?,?,?,?)""",
            (user_id, word, translation, example, category)
        )
        await db.commit()
        return cursor.lastrowid


async def get_words_for_review(user_id: int, limit: int = 10) -> list[dict]:
    """Слова которые пора повторить (по алгоритму интервального повторения)."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT * FROM vocabulary WHERE user_id=?
               AND next_review <= datetime('now')
               ORDER BY next_review ASC LIMIT ?""",
            (user_id, limit)
        ) as c:
            rows = await c.fetchall()
    return [dict(r) for r in rows]


async def get_all_words(user_id: int) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM vocabulary WHERE user_id=? ORDER BY created_at DESC",
            (user_id,)
        ) as c:
            rows = await c.fetchall()
    return [dict(r) for r in rows]


async def update_word_review(word_id: int, correct: bool):
    """
    Алгоритм SM-2 (Spaced Repetition):
    — Правильно: увеличиваем интервал
    — Неправильно: сбрасываем интервал
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM vocabulary WHERE id=?", (word_id,)) as c:
            word = dict(await c.fetchone())

    ef = word["ease_factor"]
    interval = word["interval"]
    times_seen = word["times_seen"] + 1
    times_correct = word["times_correct"] + (1 if correct else 0)

    if correct:
        if interval == 1:
            new_interval = 3
        elif interval == 3:
            new_interval = 7
        else:
            new_interval = round(interval * ef)
        new_ef = max(1.3, ef + 0.1)
    else:
        new_interval = 1
        new_ef = max(1.3, ef - 0.2)

    next_review = (datetime.now() + timedelta(days=new_interval)).isoformat()

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """UPDATE vocabulary SET ease_factor=?, interval=?, next_review=?,
               times_seen=?, times_correct=? WHERE id=?""",
            (new_ef, new_interval, next_review, times_seen, times_correct, word_id)
        )
        await db.commit()


async def get_vocab_stats(user_id: int) -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT COUNT(*) FROM vocabulary WHERE user_id=?", (user_id,)
        ) as c:
            total = (await c.fetchone())[0]
        async with db.execute(
            "SELECT COUNT(*) FROM vocabulary WHERE user_id=? AND next_review > datetime('now')",
            (user_id,)
        ) as c:
            learned = (await c.fetchone())[0]
        async with db.execute(
            "SELECT COUNT(*) FROM vocabulary WHERE user_id=? AND next_review <= datetime('now')",
            (user_id,)
        ) as c:
            due = (await c.fetchone())[0]
    return {"total": total, "learned": learned, "due_today": due}


# ─── Sessions ───

async def save_session(user_id: int, session_type: str, topic: str = None,
                       score: int = 0, corrections: int = 0,
                       duration_sec: int = 0, summary: str = None) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """INSERT INTO sessions (user_id, session_type, topic, score, corrections, duration_sec, summary)
               VALUES (?,?,?,?,?,?,?)""",
            (user_id, session_type, topic, score, corrections, duration_sec, summary)
        )
        await db.commit()
        return cursor.lastrowid


async def get_session_stats(user_id: int) -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT COUNT(*), AVG(score), SUM(corrections) FROM sessions WHERE user_id=?",
            (user_id,)
        ) as c:
            row = await c.fetchone()
            total, avg_score, total_corrections = row[0], row[1] or 0, row[2] or 0
        async with db.execute(
            "SELECT COUNT(*) FROM sessions WHERE user_id=? AND date(created_at)=date('now')",
            (user_id,)
        ) as c:
            today = (await c.fetchone())[0]
    return {
        "total_sessions": total,
        "avg_score": round(avg_score, 1),
        "total_corrections": total_corrections,
        "sessions_today": today,
    }


# ─── Chat history ───

async def get_chat_history(user_id: int, limit: int = 30) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT role, content FROM chat_history WHERE user_id=? ORDER BY created_at DESC LIMIT ?",
            (user_id, limit),
        ) as c:
            rows = await c.fetchall()
    return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]


async def save_message(user_id: int, role: str, content: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO chat_history (user_id, role, content) VALUES (?,?,?)",
            (user_id, role, content)
        )
        await db.commit()


async def clear_session_history(user_id: int):
    """Очистить историю перед новой сессией."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM chat_history WHERE user_id=? AND id NOT IN "
            "(SELECT id FROM chat_history WHERE user_id=? ORDER BY created_at DESC LIMIT 10)",
            (user_id, user_id)
        )
        await db.commit()


async def get_all_users() -> list[int]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id FROM users") as c:
            rows = await c.fetchall()
    return [r[0] for r in rows]
