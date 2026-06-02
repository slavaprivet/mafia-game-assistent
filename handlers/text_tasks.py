"""
Обработчик текстовых задач + распознавание естественного языка.
Понимает команды без слешей — на ломаном тексте.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import re
import time
import asyncio
from pathlib import Path
from datetime import datetime
from aiogram import Router, F
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from loguru import logger

from config import ALLOWED_USERS, BASE_DIR
from memory import (
    save_task, update_task, add_to_conversation, get_conversation,
    save_reminder, get_todos, save_todo, get_changes_with_rollback
)
from ai_client import ask_code_model, get_user_model, get_model_info, AVAILABLE_MODELS, set_user_model
from game_expert import search_in_code, read_relevant_files, load_index, index_game, _fetch_file
from limit_manager import check_limit, track_usage

router = Router()

pending_changes: dict[int, dict] = {}
active_tasks: dict[int, asyncio.Task] = {}


def is_allowed(user_id: int) -> bool:
    if not ALLOWED_USERS:
        return True
    return user_id in ALLOWED_USERS


def _stop_keyboard(task_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🛑 Стоп", callback_data=f"stoptask:{task_id}")
    ]])


def _change_keyboard(task_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Применить", callback_data=f"apply:{task_id}"),
            InlineKeyboardButton(text="📝 Показать файл", callback_data=f"showfile:{task_id}"),
        ],
        [
            InlineKeyboardButton(text="🌿 Тест", callback_data=f"branch:{task_id}"),
            InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject:{task_id}"),
        ]
    ])


def _is_reminder_request(text: str) -> tuple[bool, str, datetime | None]:
    t = text.lower()
    if not any(kw in t for kw in ["напомни", "напоминай", "не забудь", "remind"]):
        return False, text, None
    remind_time = None
    if "завтра" in t:
        from datetime import timedelta
        remind_time = datetime.now().replace(hour=9, minute=0) + timedelta(days=1)
    elif "через час" in t or "через 1 час" in t:
        from datetime import timedelta
        remind_time = datetime.now() + timedelta(hours=1)
    elif "через" in t:
        match = re.search(r"через\s+(\d+)\s+(минут|час)", t)
        if match:
            from datetime import timedelta
            amount = int(match.group(1))
            unit = match.group(2)
            delta = timedelta(minutes=amount) if "минут" in unit else timedelta(hours=amount)
            remind_time = datetime.now() + delta
    return remind_time is not None, text, remind_time


async def _handle_nlp(message: Message, user_id: int, text: str) -> bool:
    """
    Распознаёт естественные команды без слешей.
    Возвращает True если команда обработана и в AI идти не нужно.
    """
    t = text.lower().strip()

    # ── ПОИСК КОДА ────────────────────────────────────────
    find_kws = ["найди", "где находится", "покажи код", "find ", "где функция", "найти"]
    if any(kw in t for kw in find_kws):
        # Убираем ключевые слова, оставляем запрос
        query = t
        for kw in find_kws:
            query = query.replace(kw, "")
        query = re.sub(r"функцию|функция|код|в коде", "", query).strip(" .,?")
        if not query:
            return False  # неясно что искать — отдаём AI
        msg = await message.answer(f"🔍 Ищу: {query}")
        index = load_index()
        if not index:
            await msg.edit_text("❌ Сначала запусти индексацию: напиши 'обнови код'")
            return True
        found = []
        for fi in index.get("files", [])[:20]:
            content = await _fetch_file(fi["path"])
            if not content:
                continue
            lines = content.splitlines()
            for i, line in enumerate(lines):
                if query in line.lower():
                    start, end = max(0, i-2), min(len(lines), i+12)
                    snippet = "\n".join(lines[start:end])[:600]
                    found.append(f"📄 {fi['path']}:{i+1}\n<pre>{snippet}</pre>")
                    if len(found) >= 3:
                        break
            if len(found) >= 3:
                break
        if found:
            await msg.edit_text(f"🔍 {query}:\n\n" + "\n\n".join(found))
        else:
            await msg.edit_text(f"😶 '{query}' не найдено в коде.")
        return True

    # ── ИСТОРИЯ ИЗМЕНЕНИЙ ──────────────────────────────────
    changes_kws = ["что менял", "история", "мои изменения", "что изменил", "последние правки",
                   "что ты делал", "что сделал", "покажи изменения"]
    if any(kw in t for kw in changes_kws):
        changes = await get_changes_with_rollback(user_id, limit=6)
        if not changes:
            await message.answer("📋 Изменений пока нет.")
            return True
        await message.answer("📋 Последние изменения:")
        for ch in changes:
            date = ch["changed_at"][:16].replace("T", " ")
            icon = "↩️" if ch["status"] == "rolled_back" else "✅"
            kb = None
            if ch["status"] != "rolled_back" and ch.get("has_rollback"):
                kb = InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="↩️ Откатить", callback_data=f"rollback:{ch['id']}")
                ]])
            await message.answer(
                f"{icon} {date} — {ch['file_path']}\n{ch['description'][:60]}",
                reply_markup=kb
            )
        return True

    # ── TODO ДОБАВИТЬ ──────────────────────────────────────
    add_todo_kws = ["добавь задачу", "запомни задачу", "добавь в список", "запомни что надо",
                    "добавь в тудушку", "в список задач"]
    if any(kw in t for kw in add_todo_kws):
        todo_text = text
        for kw in add_todo_kws:
            todo_text = re.sub(kw, "", todo_text, flags=re.IGNORECASE).strip(" :.,")
        if todo_text:
            await save_todo(user_id, todo_text)
            await message.answer(f"✅ Добавил в список: {todo_text}")
        else:
            await message.answer("Что добавить? Напиши: 'добавь задачу починить анимацию'")
        return True

    # ── TODO СПИСОК ───────────────────────────────────────
    list_kws = ["что надо сделать", "список задач", "мои задачи", "что в списке",
                "тудушки", "todo", "мой список"]
    if any(kw in t for kw in list_kws):
        todos = await get_todos(user_id)
        if not todos:
            await message.answer("📝 Список задач пуст.\nДобавь: 'добавь задачу починить стрельбу'")
            return True
        await message.answer("📝 Твои задачи:")
        for todo in todos:
            kb = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="✅ Готово", callback_data=f"todo_done:{todo['id']}")
            ]])
            await message.answer(f"• {todo['text']}", reply_markup=kb)
        return True

    # ── ОТКАТ ─────────────────────────────────────────────
    rollback_kws = ["откати", "отмени изменение", "верни как было", "rollback", "отменить последнее"]
    if any(kw in t for kw in rollback_kws):
        changes = await get_changes_with_rollback(user_id, limit=3)
        active = [c for c in changes if c["status"] != "rolled_back" and c.get("has_rollback")]
        if not active:
            await message.answer("❌ Нет изменений для отката.")
            return True
        last = active[0]
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text=f"↩️ Откатить {last['file_path']}", callback_data=f"rollback:{last['id']}")
        ]])
        await message.answer(
            f"Последнее изменение:\n{last['file_path']} — {last['description'][:60]}\n\nОткатить?",
            reply_markup=kb
        )
        return True

    # ── СМЕНА МОДЕЛИ ──────────────────────────────────────
    model_kws = ["смени модель", "поменяй модель", "какая модель", "список моделей",
                 "модели", "выбери модель", "переключи модель"]
    if any(kw in t for kw in model_kws):
        current = get_user_model(user_id)
        buttons = []
        for key, info in AVAILABLE_MODELS.items():
            mark = " ✅" if key == current else ""
            buttons.append([InlineKeyboardButton(
                text=f"{info['emoji']} {info['name']}{mark}",
                callback_data=f"setmodel:{key}"
            )])
        await message.answer(
            "🤖 Выбери AI модель:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
        )
        return True

    # ── СТАТИСТИКА ────────────────────────────────────────
    stats_kws = ["статистика", "сколько токенов", "мои токены", "сколько запросов"]
    if any(kw in t for kw in stats_kws):
        from memory import get_stats
        stats = await get_stats(user_id)
        await message.answer(
            f"📊 Статистика\n\n"
            f"Задач всего: {stats['total_tasks']}\n"
            f"Изменений кода: {stats['code_changes']}\n"
            f"Токенов сегодня: {stats['today_tokens']:,}\n"
            f"Запросов сегодня: {stats['today_requests']}"
        )
        return True

    # ── ОЧИСТИТЬ КОНТЕКСТ ─────────────────────────────────
    clear_kws = ["очисти контекст", "забудь всё", "забудь историю", "новый разговор", "сброс контекста"]
    if any(kw in t for kw in clear_kws):
        from memory import clear_conversation
        await clear_conversation(user_id)
        await message.answer("🧹 Контекст разговора очищен. Начинаем заново.")
        return True

    # ── ОБНОВИТЬ КОД ──────────────────────────────────────
    pull_kws = ["подтяни", "обнови код", "обнови индекс", "свежак", "скачай код", "загрузи код"]
    if any(kw in t for kw in pull_kws):
        msg = await message.answer("🔄 Подтягиваю свежий код с GitHub...")
        index = await index_game()
        if index.get("error"):
            await msg.edit_text(f"❌ Ошибка: {index['error']}")
        else:
            await msg.edit_text(
                f"✅ Код обновлён!\n\n"
                f"Файлов: {index['file_count']}\n"
                f"Строк: {index['total_lines']:,}\n"
                f"Функций: {len(index['functions'])}"
            )
        return True

    return False  # не распознали — идём в AI


async def _heartbeat(status_msg: Message, base_text: str, task_id: int, interval: int = 5):
    elapsed = 0
    while True:
        await asyncio.sleep(interval)
        elapsed += interval
        try:
            await status_msg.edit_text(
                f"{base_text}\n⏱ {elapsed} сек...",
                reply_markup=_stop_keyboard(task_id)
            )
        except Exception:
            pass


def _detect_code_change(response: str) -> bool:
    indicators = ["БЫЛО:", "СТАЛО:", "Файл:", "```python", "```js", "```lua", "```html", "```javascript", "```css"]
    return any(ind in response for ind in indicators)


def _parse_code_change(response: str) -> dict:
    change = {"file": None, "old_code": None, "new_code": None, "description": response[:100]}
    file_match = re.search(r"[Фф]айл[:\s]+([^\n]+)", response)
    if file_match:
        change["file"] = file_match.group(1).strip().rstrip(".")
    code_blocks = re.findall(r"```(?:\w+)?\n(.*?)```", response, re.DOTALL)
    if len(code_blocks) >= 2:
        change["old_code"] = code_blocks[0].strip()
        change["new_code"] = code_blocks[1].strip()
    elif len(code_blocks) == 1:
        change["new_code"] = code_blocks[0].strip()
    return change


async def _process_task(user_id: int, task_id: int, text: str, status_msg: Message, model_info: dict):
    try:
        index = load_index()
        context_files = []
        code_context = ""

        if index and not index.get("error"):
            text_lower = text.lower()

            named_files = [
                f["path"] for f in index.get("files", [])
                if Path(f["path"]).name.lower() in text_lower
                or Path(f["path"]).stem.lower() in text_lower
            ]
            world_files = [
                f["path"] for f in index.get("files", [])
                if "world" in Path(f["path"]).stem.lower()
            ]

            if named_files:
                unique_files = named_files[:3]
            else:
                search_results = await search_in_code(text)
                found = list(dict.fromkeys(r["file"] for r in search_results[:4]))
                if world_files and not any("world" in f.lower() for f in found):
                    found = world_files[:1] + [f for f in found if "battle" not in f.lower()][:2]
                unique_files = found[:3]

            if not unique_files and world_files:
                unique_files = world_files[:1]

            if unique_files:
                context_files = unique_files
                short_names = [Path(f).name for f in unique_files]
                await status_msg.edit_text(
                    f"⚙️ Принял\n📄 Читаю: {', '.join(short_names)}...",
                    reply_markup=_stop_keyboard(task_id)
                )
                code_context = await read_relevant_files(unique_files, max_chars=12000, query=text)
            else:
                await status_msg.edit_text(
                    "⚙️ Принял\n📭 Файлы не найдены, работаю без контекста",
                    reply_markup=_stop_keyboard(task_id)
                )
        else:
            await status_msg.edit_text(
                "⚙️ Принял\n📭 Код не проиндексирован — напиши 'обнови код'",
                reply_markup=_stop_keyboard(task_id)
            )

        index_summary = ""
        if index and not index.get("error"):
            file_names = [f["path"] for f in index.get("files", [])]
            index_summary = (
                f"\n\nКод игры из репозитория {index.get('repo', 'mafiozy')}: "
                f"{', '.join(file_names)}. "
                f"{index.get('file_count', 0)} файлов, {index.get('total_lines', 0)} строк."
            )

        system_prompt = (
            "Ты разработчик игры Мафиози. Отвечай КРАТКО — максимум 8 строк. "
            "Только суть + готовый код. Без длинных объяснений. "
            "Язык: русский. "
            "Структура проекта: world.html — ГЛАВНЫЙ файл (открытый мир), "
            "hub.html — хаб, battle.html — бой, creator.html — редактор. "
            "Работай с world.html по умолчанию если не указано иное. "
            "Формат изменения:\n"
            "БЫЛО:\n```\nстарый код\n```\nСТАЛО:\n```\nновый код\n```\nФайл: имя_файла"
            + index_summary
        )

        full_prompt = text
        if code_context:
            full_prompt = f"Задача: {text}\n\nКод из проекта:\n{code_context}"

        await add_to_conversation(user_id, "user", text)
        history = await get_conversation(user_id, limit=8)

        files_str = ', '.join(Path(f).name for f in context_files) if context_files else 'без контекста'
        thinking_text = (
            f"⚙️ Принял\n"
            f"📄 {files_str}\n"
            f"{model_info['emoji']} {model_info['name']} думает..."
        )
        await status_msg.edit_text(thinking_text, reply_markup=_stop_keyboard(task_id))

        heartbeat = asyncio.create_task(_heartbeat(status_msg, thinking_text, task_id))
        try:
            ai_response, tokens_used = await ask_code_model(
                full_prompt,
                system_prompt=system_prompt,
                conversation_history=history[:-1],
                user_id=user_id,
            )
        finally:
            heartbeat.cancel()

        await add_to_conversation(user_id, "assistant", ai_response)
        await track_usage(user_id, tokens_used)
        await update_task(task_id, "done", ai_response, tokens_used, context_files)

        # Экранируем < > чтобы не ломать HTML-парсер Telegram
        safe = ai_response.replace("<", "&lt;").replace(">", "&gt;")
        footer = f"\n\n📊 {tokens_used:,} токенов"

        has_change = _detect_code_change(ai_response)
        if has_change:
            change_info = _parse_code_change(ai_response)
            pending_changes[task_id] = {
                "user_id": user_id,
                "task_id": task_id,
                "change": change_info,
                "full_response": ai_response,
            }
            await status_msg.edit_text(
                safe[:4000] + footer,
                reply_markup=_change_keyboard(task_id),
                parse_mode=None
            )
        else:
            await status_msg.edit_text(safe[:4000] + footer, parse_mode=None)

    except asyncio.CancelledError:
        logger.info(f"Задача {task_id} отменена — user {user_id}")
        raise
    except Exception as e:
        logger.error(f"Ошибка задачи {task_id}: {e}")
        await update_task(task_id, "failed")
        try:
            await status_msg.edit_text(f"❌ Ошибка: {e}\n\nПереформулируй задачу.")
        except Exception:
            pass


@router.callback_query(lambda c: c.data.startswith("stoptask:"))
async def callback_stop_task(callback: CallbackQuery):
    user_id = callback.from_user.id
    task = active_tasks.get(user_id)
    if task and not task.done():
        task.cancel()
        await callback.message.edit_text("🛑 Остановлено.")
    else:
        await callback.message.edit_reply_markup()
    await callback.answer("Остановлено")


@router.callback_query(lambda c: c.data.startswith("setmodel:"))
async def callback_set_model(callback: CallbackQuery):
    model_key = callback.data.split(":")[1]
    set_user_model(callback.from_user.id, model_key)
    info = AVAILABLE_MODELS.get(model_key, {})
    await callback.answer(f"Переключился на {info.get('name', model_key)}")
    current = get_user_model(callback.from_user.id)
    buttons = []
    for key, m in AVAILABLE_MODELS.items():
        mark = " ✅" if key == current else ""
        buttons.append([InlineKeyboardButton(
            text=f"{m['emoji']} {m['name']}{mark}",
            callback_data=f"setmodel:{key}"
        )])
    try:
        await callback.message.edit_reply_markup(
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
        )
    except Exception:
        pass


@router.callback_query(lambda c: c.data.startswith("todo_done:"))
async def callback_todo_done(callback: CallbackQuery):
    from memory import mark_todo_done
    todo_id = int(callback.data.split(":")[1])
    await mark_todo_done(todo_id)
    await callback.answer("✅ Готово!")
    await callback.message.edit_reply_markup()


@router.message(F.text)
async def handle_text_task(message: Message):
    if not is_allowed(message.from_user.id):
        return

    user_id = message.from_user.id
    text = message.text.strip()

    # Отменяем предыдущую задачу
    prev = active_tasks.get(user_id)
    if prev and not prev.done():
        prev.cancel()

    if text.startswith("/"):
        return

    # Напоминание
    is_reminder, task_text, remind_time = _is_reminder_request(text)
    if is_reminder and remind_time:
        await save_reminder(user_id, remind_time, task_text)
        await message.answer(
            f"⏰ Напомню!\nЗадача: {task_text}\nКогда: {remind_time.strftime('%d.%m %H:%M')}"
        )
        return

    # Пробуем распознать как команду на естественном языке
    if await _handle_nlp(message, user_id, text):
        return

    # Лимит токенов
    can_proceed, limit_msg = await check_limit(user_id)
    if not can_proceed:
        await message.answer(limit_msg)
        return

    # Предупреждение об устаревшем индексе (> 3 часов)
    try:
        idx_path = BASE_DIR / "game_index.json"
        if idx_path.exists():
            age_hours = (time.time() - idx_path.stat().st_mtime) / 3600
            if age_hours > 3:
                await message.answer(
                    f"⚠️ Код устарел ({int(age_hours)}ч). Напиши 'обнови код' чтобы обновить."
                )
    except Exception:
        pass

    task_id = await save_task(user_id, "text", text)
    model_key = get_user_model(user_id)
    model_info = get_model_info(model_key)

    status_msg = await message.answer(
        "⚙️ Принял\n🔍 Ищу связанный код...",
        reply_markup=_stop_keyboard(task_id)
    )

    task = asyncio.create_task(_process_task(user_id, task_id, text, status_msg, model_info))
    active_tasks[user_id] = task
