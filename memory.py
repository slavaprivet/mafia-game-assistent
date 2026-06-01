"""
Память бота — хранит историю задач, изменений, напоминания.
Использует SQLite (не требует Redis или отдельного сервера).
"""

import json
import aiosqlite
from datetime import datetime
from loguru import logger
from config import DB_PATH


async def init_db():
    """Создаёт таблицы в базе данных при первом запуске."""
    async with aiosqlite.connect(DB_PATH) as db:
        # История задач
        await db.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                task_type TEXT NOT NULL,       -- text/photo/voice/video
                task_text TEXT,                -- описание задачи
                status TEXT DEFAULT 'pending', -- pending/processing/done/failed
                result TEXT,                   -- результат (JSON)
                tokens_used INTEGER DEFAULT 0,
                files_changed TEXT             -- список изменённых файлов (JSON)
            )
        """)

        # История изменений кода
        await db.execute("""
            CREATE TABLE IF NOT EXISTS code_changes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id INTEGER,
                changed_at TEXT NOT NULL,
                file_path TEXT NOT NULL,
                branch TEXT,
                commit_hash TEXT,
                description TEXT,
                diff TEXT,                     -- что именно изменилось
                status TEXT DEFAULT 'applied'  -- applied/rolled_back
            )
        """)

        # Статистика использования токенов
        await db.execute("""
            CREATE TABLE IF NOT EXISTS token_stats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,            -- YYYY-MM-DD
                user_id INTEGER NOT NULL,
                tokens_used INTEGER DEFAULT 0,
                requests_count INTEGER DEFAULT 0
            )
        """)

        # Напоминания
        await db.execute("""
            CREATE TABLE IF NOT EXISTS reminders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                remind_at TEXT NOT NULL,       -- когда напомнить (ISO формат)
                message TEXT NOT NULL,
                done INTEGER DEFAULT 0
            )
        """)

        # История разговора (контекст для AI)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS conversation (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                role TEXT NOT NULL,            -- user/assistant
                content TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)

        await db.commit()
        logger.info("📦 База данных инициализирована")


async def save_task(user_id: int, task_type: str, task_text: str) -> int:
    """Сохраняет новую задачу и возвращает её ID."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "INSERT INTO tasks (user_id, created_at, task_type, task_text, status) VALUES (?, ?, ?, ?, 'processing')",
            (user_id, datetime.now().isoformat(), task_type, task_text)
        )
        await db.commit()
        return cursor.lastrowid


async def update_task(task_id: int, status: str, result: str = None,
                      tokens_used: int = 0, files_changed: list = None):
    """Обновляет статус задачи после выполнения."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """UPDATE tasks SET status=?, result=?, tokens_used=?, files_changed=?
               WHERE id=?""",
            (
                status,
                result,
                tokens_used,
                json.dumps(files_changed or []),
                task_id
            )
        )
        await db.commit()


async def save_code_change(task_id: int, file_path: str, branch: str,
                           commit_hash: str, description: str, diff: str):
    """Сохраняет запись об изменении кода."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO code_changes
               (task_id, changed_at, file_path, branch, commit_hash, description, diff)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (task_id, datetime.now().isoformat(), file_path,
             branch, commit_hash, description, diff)
        )
        await db.commit()


async def add_tokens(user_id: int, tokens: int):
    """Добавляет использованные токены в статистику."""
    today = datetime.now().strftime("%Y-%m-%d")
    async with aiosqlite.connect(DB_PATH) as db:
        # Проверяем есть ли запись за сегодня
        row = await db.execute_fetchone(
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


async def get_today_tokens(user_id: int) -> tuple[int, int]:
    """Возвращает (токены за сегодня, количество запросов)."""
    today = datetime.now().strftime("%Y-%m-%d")
    async with aiosqlite.connect(DB_PATH) as db:
        row = await db.execute_fetchone(
            "SELECT tokens_used, requests_count FROM token_stats WHERE date=? AND user_id=?",
            (today, user_id)
        )
        if row:
            return row[0], row[1]
        return 0, 0


async def get_stats(user_id: int) -> dict:
    """Возвращает полную статистику пользователя."""
    async with aiosqlite.connect(DB_PATH) as db:
        # Общее количество задач
        row = await db.execute_fetchone(
            "SELECT COUNT(*), SUM(tokens_used) FROM tasks WHERE user_id=?",
            (user_id,)
        )
        total_tasks = row[0] if row else 0
        total_tokens = row[1] if row and row[1] else 0

        # Задачи за сегодня
        today = datetime.now().strftime("%Y-%m-%d")
        row = await db.execute_fetchone(
            "SELECT tokens_used, requests_count FROM token_stats WHERE date=? AND user_id=?",
            (today, user_id)
        )
        today_tokens = row[0] if row else 0
        today_requests = row[1] if row else 0

        # Количество изменений кода
        row = await db.execute_fetchone(
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
    """Добавляет сообщение в историю разговора."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO conversation (user_id, role, content, created_at) VALUES (?, ?, ?, ?)",
            (user_id, role, content, datetime.now().isoformat())
        )
        await db.commit()


async def get_conversation(user_id: int, limit: int = 20) -> list[dict]:
    """Возвращает последние N сообщений разговора."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await db.execute_fetchall(
            """SELECT role, content FROM conversation
               WHERE user_id=?
               ORDER BY created_at DESC LIMIT ?""",
            (user_id, limit)
        )
        # Разворачиваем — новые внизу
        return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]


async def clear_conversation(user_id: int):
    """Очищает историю разговора."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM conversation WHERE user_id=?", (user_id,))
        await db.commit()


async def get_last_changes(user_id: int, limit: int = 5) -> list[dict]:
    """Возвращает последние изменения кода."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await db.execute_fetchall(
            """SELECT cc.file_path, cc.changed_at, cc.description, cc.branch, cc.status
               FROM code_changes cc
               JOIN tasks t ON cc.task_id = t.id
               WHERE t.user_id = ?
               ORDER BY cc.changed_at DESC LIMIT ?""",
            (user_id, limit)
        )
        return [dict(r) for r in rows]


async def save_reminder(user_id: int, remind_at: datetime, message: str) -> int:
    """Сохраняет напоминание и возвращает его ID."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "INSERT INTO reminders (user_id, remind_at, message) VALUES (?, ?, ?)",
            (user_id, remind_at.isoformat(), message)
        )
        await db.commit()
        return cursor.lastrowid


async def get_pending_reminders() -> list[dict]:
    """Возвращает все напоминания которые пора показать."""
    now = datetime.now().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await db.execute_fetchall(
            "SELECT * FROM reminders WHERE remind_at <= ? AND done=0",
            (now,)
        )
        return [dict(r) for r in rows]


async def mark_reminder_done(reminder_id: int):
    """Отмечает напоминание как выполненное."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE reminders SET done=1 WHERE id=?", (reminder_id,))
        await db.commit()


async def get_reminders(user_id: int) -> list[dict]:
    """Возвращает все активные напоминания пользователя."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await db.execute_fetchall(
            "SELECT * FROM reminders WHERE user_id=? AND done=0 ORDER BY remind_at",
            (user_id,)
        )
        return [dict(r) for r in rows]
