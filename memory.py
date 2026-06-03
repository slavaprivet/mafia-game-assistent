"""
Память бота — хранит историю задач, изменений, напоминания.
Использует SQLite (не требует Redis или отдельного сервера).
"""

import json
import aiosqlite
from datetime import datetime
from loguru import logger
from config import DB_PATH


async def _fetchone(db, query, params=()):
    cur = await db.execute(query, params)
    return await cur.fetchone()


async def _fetchall(db, query, params=()):
    cur = await db.execute(query, params)
    return await cur.fetchall()


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                task_type TEXT NOT NULL,
                task_text TEXT,
                status TEXT DEFAULT 'pending',
                result TEXT,
                tokens_used INTEGER DEFAULT 0,
                files_changed TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS code_changes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id INTEGER,
                changed_at TEXT NOT NULL,
                file_path TEXT NOT NULL,
                branch TEXT,
                commit_hash TEXT,
                description TEXT,
                diff TEXT,
                status TEXT DEFAULT 'applied'
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS token_stats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                tokens_used INTEGER DEFAULT 0,
                requests_count INTEGER DEFAULT 0
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS reminders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                remind_at TEXT NOT NULL,
                message TEXT NOT NULL,
                done INTEGER DEFAULT 0
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS conversation (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS rollbacks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                change_id INTEGER NOT NULL,
                file_path TEXT NOT NULL,
                old_content TEXT NOT NULL,
                saved_at TEXT NOT NULL
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS user_prefs (
                user_id INTEGER NOT NULL,
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                PRIMARY KEY (user_id, key)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS todos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                file_path TEXT,
                line_num INTEGER,
                text TEXT NOT NULL,
                done INTEGER DEFAULT 0,
                created_at TEXT NOT NULL
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS game_knowledge (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                topic TEXT NOT NULL,
                request TEXT NOT NULL,
                working_code TEXT NOT NULL,
                func_name TEXT,
                file_path TEXT,
                model TEXT,
                created_at TEXT NOT NULL
            )
        """)
        await db.commit()
        logger.info("📦 База данных инициализирована")


async def save_task(user_id: int, task_type: str, task_text: str) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "INSERT INTO tasks (user_id, created_at, task_type, task_text, status) VALUES (?, ?, ?, ?, 'processing')",
            (user_id, datetime.now().isoformat(), task_type, task_text)
        )
        await db.commit()
        return cursor.lastrowid


async def update_task(task_id: int, status: str, result: str = None,
                      tokens_used: int = 0, files_changed: list = None):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE tasks SET status=?, result=?, tokens_used=?, files_changed=? WHERE id=?",
            (status, result, tokens_used, json.dumps(files_changed or []), task_id)
        )
        await db.commit()


async def save_code_change(task_id: int, file_path: str, branch: str,
                           commit_hash: str, description: str, diff: str) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """INSERT INTO code_changes
               (task_id, changed_at, file_path, branch, commit_hash, description, diff)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (task_id, datetime.now().isoformat(), file_path, branch, commit_hash, description, diff)
        )
        await db.commit()
        return cursor.lastrowid


async def add_tokens(user_id: int, tokens: int):
    today = datetime.now().strftime("%Y-%m-%d")
    async with aiosqlite.connect(DB_PATH) as db:
        row = await _fetchone(db,
            "SELECT id, tokens_used, requests_count FROM token_stats WHERE date=? AND user_id=?",
            (today, user_id)
        )
        if row:
            await db.execute(
                "UPDATE token_stats SET tokens_used=tokens_used+?, requests_count=requests_count+1 WHERE id=?",
                (tokens, row[0])
            )
        else:
            await db.execute(
                "INSERT INTO token_stats (date, user_id, tokens_used, requests_count) VALUES (?, ?, ?, 1)",
                (today, user_id, tokens)
            )
        await db.commit()


async def get_today_tokens(user_id: int) -> tuple:
    today = datetime.now().strftime("%Y-%m-%d")
    async with aiosqlite.connect(DB_PATH) as db:
        row = await _fetchone(db,
            "SELECT tokens_used, requests_count FROM token_stats WHERE date=? AND user_id=?",
            (today, user_id)
        )
        if row:
            return row[0], row[1]
        return 0, 0


async def get_stats(user_id: int) -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        row = await _fetchone(db,
            "SELECT COUNT(*), SUM(tokens_used) FROM tasks WHERE user_id=?",
            (user_id,)
        )
        total_tasks = row[0] if row else 0
        total_tokens = row[1] if row and row[1] else 0

        today = datetime.now().strftime("%Y-%m-%d")
        row = await _fetchone(db,
            "SELECT tokens_used, requests_count FROM token_stats WHERE date=? AND user_id=?",
            (today, user_id)
        )
        today_tokens = row[0] if row else 0
        today_requests = row[1] if row else 0

        row = await _fetchone(db,
            "SELECT COUNT(*) FROM code_changes cc JOIN tasks t ON cc.task_id=t.id WHERE t.user_id=?",
            (user_id,)
        )
        code_changes = row[0] if row else 0

        return {
            "total_tasks": total_tasks,
            "total_tokens": total_tokens,
            "today_tokens": today_tokens,
            "today_requests": today_requests,
            "code_changes": code_changes,
        }


async def add_to_conversation(user_id: int, role: str, content: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO conversation (user_id, role, content, created_at) VALUES (?, ?, ?, ?)",
            (user_id, role, content, datetime.now().isoformat())
        )
        await db.commit()


async def get_conversation(user_id: int, limit: int = 20) -> list:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await _fetchall(db,
            "SELECT role, content FROM conversation WHERE user_id=? ORDER BY created_at DESC LIMIT ?",
            (user_id, limit)
        )
        return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]


async def clear_conversation(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM conversation WHERE user_id=?", (user_id,))
        await db.commit()


async def get_last_changes(user_id: int, limit: int = 5) -> list:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await _fetchall(db,
            """SELECT cc.file_path, cc.changed_at, cc.description, cc.branch, cc.status
               FROM code_changes cc
               JOIN tasks t ON cc.task_id = t.id
               WHERE t.user_id = ?
               ORDER BY cc.changed_at DESC LIMIT ?""",
            (user_id, limit)
        )
        return [dict(r) for r in rows]


async def save_reminder(user_id: int, remind_at: datetime, message: str) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "INSERT INTO reminders (user_id, remind_at, message) VALUES (?, ?, ?)",
            (user_id, remind_at.isoformat(), message)
        )
        await db.commit()
        return cursor.lastrowid


async def get_pending_reminders() -> list:
    now = datetime.now().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await _fetchall(db,
            "SELECT * FROM reminders WHERE remind_at <= ? AND done=0",
            (now,)
        )
        return [dict(r) for r in rows]


async def mark_reminder_done(reminder_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE reminders SET done=1 WHERE id=?", (reminder_id,))
        await db.commit()


# ─── Откаты ───────────────────────────────────────────────

async def save_rollback(change_id: int, file_path: str, old_content: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO rollbacks (change_id, file_path, old_content, saved_at) VALUES (?, ?, ?, ?)",
            (change_id, file_path, old_content, datetime.now().isoformat())
        )
        await db.commit()


async def get_rollback(change_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        row = await _fetchone(db,
            "SELECT * FROM rollbacks WHERE change_id=?", (change_id,)
        )
        return dict(row) if row else None


async def get_changes_with_rollback(user_id: int, limit: int = 8) -> list:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await _fetchall(db,
            """SELECT cc.id, cc.file_path, cc.changed_at, cc.description, cc.status,
                      (SELECT 1 FROM rollbacks r WHERE r.change_id=cc.id) as has_rollback
               FROM code_changes cc
               JOIN tasks t ON cc.task_id = t.id
               WHERE t.user_id = ?
               ORDER BY cc.changed_at DESC LIMIT ?""",
            (user_id, limit)
        )
        return [dict(r) for r in rows]


async def mark_change_rolled_back(change_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE code_changes SET status='rolled_back' WHERE id=?", (change_id,)
        )
        await db.commit()


# ─── Предпочтения пользователя ───────────────────────────

async def save_pref(user_id: int, key: str, value: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO user_prefs (user_id, key, value) VALUES (?, ?, ?)",
            (user_id, key, value)
        )
        await db.commit()


async def get_pref(user_id: int, key: str, default: str = "") -> str:
    async with aiosqlite.connect(DB_PATH) as db:
        row = await _fetchone(db,
            "SELECT value FROM user_prefs WHERE user_id=? AND key=?", (user_id, key)
        )
        return row[0] if row else default


# ─── TODO-список ─────────────────────────────────────────

async def save_todo(user_id: int, text: str, file_path: str = "", line_num: int = 0):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO todos (user_id, file_path, line_num, text, created_at) VALUES (?, ?, ?, ?, ?)",
            (user_id, file_path, line_num, text, datetime.now().isoformat())
        )
        await db.commit()


async def get_todos(user_id: int) -> list:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await _fetchall(db,
            "SELECT * FROM todos WHERE user_id=? AND done=0 ORDER BY created_at DESC",
            (user_id,)
        )
        return [dict(r) for r in rows]


async def mark_todo_done(todo_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE todos SET done=1 WHERE id=?", (todo_id,))
        await db.commit()


async def get_reminders(user_id: int) -> list:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await _fetchall(db,
            "SELECT * FROM reminders WHERE user_id=? AND done=0 ORDER BY remind_at",
            (user_id,)
        )
        return [dict(r) for r in rows]


# ─── Знания об игре (обучение на успешных изменениях) ─────

async def save_game_knowledge(topic: str, request: str, working_code: str,
                               func_name: str = "", file_path: str = "", model: str = ""):
    """Сохраняет рабочий пример кода — бот учится на своих успехах."""
    async with aiosqlite.connect(DB_PATH) as db:
        # Не дублируем — если такой топик уже есть, обновляем
        await db.execute("""
            INSERT INTO game_knowledge (topic, request, working_code, func_name, file_path, model, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (topic, request[:200], working_code[:2000], func_name, file_path, model, datetime.now().isoformat()))
        await db.commit()


async def get_relevant_knowledge(query: str, limit: int = 3) -> list[dict]:
    """Ищет похожие рабочие примеры по ключевым словам."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        # Простой поиск по словам из запроса
        words = [w for w in query.lower().split() if len(w) > 3]
        if not words:
            return []
        conditions = " OR ".join(["LOWER(topic) LIKE ? OR LOWER(request) LIKE ?" for _ in words])
        params = []
        for w in words:
            params.extend([f"%{w}%", f"%{w}%"])
        params.append(limit)
        rows = await _fetchall(db,
            f"SELECT * FROM game_knowledge WHERE {conditions} ORDER BY created_at DESC LIMIT ?",
            params
        )
        return [dict(r) for r in rows]


async def get_all_knowledge(limit: int = 20) -> list[dict]:
    """Возвращает все накопленные знания."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await _fetchall(db,
            "SELECT * FROM game_knowledge ORDER BY created_at DESC LIMIT ?", (limit,)
        )
        return [dict(r) for r in rows]
