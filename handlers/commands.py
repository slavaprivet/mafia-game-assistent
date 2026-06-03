"""
Обработчики команд — всё через кнопки, минимум текстовых команд.
"""

import html
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from loguru import logger

from config import ALLOWED_USERS, GITHUB_REPO, GITHUB_BRANCH
from memory import (get_stats, get_last_changes, get_reminders, clear_conversation,
                    get_changes_with_rollback, get_todos, save_todo, mark_todo_done,
                    get_all_knowledge)
from limit_manager import get_limit_status
from game_expert import format_index_message, index_game, search_in_code, _fetch_file, load_index
from teacher import explain_error, suggest_best_practices
from ai_client import AVAILABLE_MODELS, get_user_model, set_user_model

router = Router()

GITHUB_PAGES_BASE = f"https://{GITHUB_REPO.split('/')[0]}.github.io/{GITHUB_REPO.split('/')[1]}"


def is_allowed(user_id: int) -> bool:
    if not ALLOWED_USERS:
        return True
    return user_id in ALLOWED_USERS


def _main_menu() -> InlineKeyboardMarkup:
    """Главное меню — все действия кнопками."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🔄 Обновить код", callback_data="menu:index"),
            InlineKeyboardButton(text="🎮 Открыть игру", callback_data="menu:open_game"),
        ],
        [
            InlineKeyboardButton(text="📋 История изменений", callback_data="menu:changes"),
            InlineKeyboardButton(text="↩️ Откатить", callback_data="menu:rollback"),
        ],
        [
            InlineKeyboardButton(text="🔍 Поиск в коде", callback_data="menu:search"),
            InlineKeyboardButton(text="📝 Список задач", callback_data="menu:todo"),
        ],
        [
            InlineKeyboardButton(text="🧠 Память бота", callback_data="menu:knowledge"),
            InlineKeyboardButton(text="🤖 Нейросеть", callback_data="menu:model"),
        ],
        [
            InlineKeyboardButton(text="📊 Статистика", callback_data="menu:stats"),
            InlineKeyboardButton(text="🧹 Очистить контекст", callback_data="menu:clear"),
        ],
    ])


# ── /start и /menu ────────────────────────────────────────────────────────────

@router.message(Command("start"))
@router.message(Command("menu"))
async def cmd_start(message: Message):
    if not is_allowed(message.from_user.id):
        await message.answer("🚫 Доступ запрещён.")
        return

    from game_expert import get_project_summary
    project_info = get_project_summary()

    current_model = get_user_model(message.from_user.id)
    model_info = AVAILABLE_MODELS.get(current_model, {})

    await message.answer(
        f"👋 Привет! Я твой AI-разработчик игры «Мафиози».\n\n"
        f"{project_info}\n\n"
        f"🤖 Нейросеть: {model_info.get('emoji','')} {model_info.get('name','')}\n\n"
        f"Просто пиши задачу — или выбери действие:",
        reply_markup=_main_menu()
    )


# ── Обработчик всех кнопок меню ───────────────────────────────────────────────

@router.callback_query(lambda c: c.data.startswith("menu:"))
async def callback_menu(callback: CallbackQuery):
    if not is_allowed(callback.from_user.id):
        await callback.answer("🚫 Доступ запрещён")
        return

    action = callback.data.split(":")[1]
    await callback.answer()

    # ── Обновить код ──────────────────────────────────────────────────────────
    if action == "index":
        msg = await callback.message.answer("🔄 Обновляю код с GitHub...")
        index = await index_game()
        if index.get("error"):
            await msg.edit_text(f"❌ Ошибка: {index['error']}")
        else:
            await msg.edit_text(
                f"✅ Код обновлён!\n\n"
                f"📁 Файлов: {index['file_count']}\n"
                f"📝 Строк: {index['total_lines']:,}\n"
                f"🔧 Функций: {len(index['functions'])}",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="◀️ Меню", callback_data="menu:back")
                ]])
            )

    # ── Открыть игру ──────────────────────────────────────────────────────────
    elif action == "open_game":
        game_url = f"{GITHUB_PAGES_BASE}/world.html"
        await callback.message.answer(
            f"🎮 Открыть игру:\n{game_url}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="🎮 Открыть", url=game_url),
                InlineKeyboardButton(text="◀️ Меню", callback_data="menu:back"),
            ]])
        )

    # ── История изменений ─────────────────────────────────────────────────────
    elif action == "changes":
        changes = await get_changes_with_rollback(callback.from_user.id, limit=8)
        if not changes:
            await callback.message.answer(
                "📋 Изменений пока нет.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="◀️ Меню", callback_data="menu:back")
                ]])
            )
            return
        await callback.message.answer("📋 История изменений:")
        for ch in changes:
            date = ch["changed_at"][:16].replace("T", " ")
            status = "↩️ откатано" if ch["status"] == "rolled_back" else "✅ применено"
            text = f"{status} | {date}\n📄 {ch['file_path']}\n{ch['description'][:60]}"
            kb = None
            if ch["status"] != "rolled_back" and ch.get("has_rollback"):
                kb = InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="↩️ Откатить", callback_data=f"rollback:{ch['id']}")
                ]])
            await callback.message.answer(text, reply_markup=kb)

    # ── Откатить последнее ────────────────────────────────────────────────────
    elif action == "rollback":
        changes = await get_changes_with_rollback(callback.from_user.id, limit=5)
        active = [c for c in changes if c["status"] != "rolled_back" and c.get("has_rollback")]
        if not active:
            await callback.message.answer(
                "❌ Нет изменений для отката.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="◀️ Меню", callback_data="menu:back")
                ]])
            )
            return
        await callback.message.answer("↩️ Выбери что откатить:")
        for ch in active[:5]:
            date = ch["changed_at"][:16].replace("T", " ")
            await callback.message.answer(
                f"📄 {ch['file_path']}\n{date} — {ch['description'][:50]}",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="↩️ Откатить это", callback_data=f"rollback:{ch['id']}")
                ]])
            )

    # ── Поиск в коде ─────────────────────────────────────────────────────────
    elif action == "search":
        await callback.message.answer(
            "🔍 Напиши что найти в коде:\n\n"
            "Примеры:\n"
            "• <code>найди код fireBullet</code>\n"
            "• <code>найди функцию spawnNpc</code>\n"
            "• <code>где функция initNpcs</code>"
        )

    # ── Список задач ──────────────────────────────────────────────────────────
    elif action == "todo":
        todos = await get_todos(callback.from_user.id)
        if not todos:
            await callback.message.answer(
                "📝 Список задач пуст.\n\nНапиши: <code>добавь задачу починить стрельбу</code>",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="◀️ Меню", callback_data="menu:back")
                ]])
            )
            return
        await callback.message.answer("📝 Твои задачи:")
        for t in todos:
            await callback.message.answer(
                f"• {t['text']}",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="✅ Готово", callback_data=f"todo_done:{t['id']}")
                ]])
            )

    # ── Память бота (знания об игре) ──────────────────────────────────────────
    elif action == "knowledge":
        knowledge = await get_all_knowledge(limit=20)
        if not knowledge:
            await callback.message.answer(
                "🧠 Память пока пуста.\n\n"
                "Каждый раз когда нажимаешь «✅ Добавить в игру» — бот запоминает рабочий код.\n"
                "Со временем будет знать твою игру изнутри.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="◀️ Меню", callback_data="menu:back")
                ]])
            )
            return
        lines = [f"🧠 Бот знает {len(knowledge)} приёмов:\n"]
        for k in knowledge:
            date = k["created_at"][:10]
            func = f" → {k['func_name']}" if k.get("func_name") else ""
            lines.append(f"• {date} {k['topic'][:50]}{func}")
        await callback.message.answer(
            "\n".join(lines),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="◀️ Меню", callback_data="menu:back")
            ]])
        )

    # ── Выбор нейросети ───────────────────────────────────────────────────────
    elif action == "model":
        current = get_user_model(callback.from_user.id)
        buttons = []
        for key, info in AVAILABLE_MODELS.items():
            mark = " ✅" if key == current else ""
            buttons.append([InlineKeyboardButton(
                text=f"{info['emoji']} {info['name']}{mark}",
                callback_data=f"setmodel:{key}"
            )])
        buttons.append([InlineKeyboardButton(text="◀️ Меню", callback_data="menu:back")])
        await callback.message.answer(
            "🤖 Выбери нейросеть:\n(при лимите — автопереключение на следующую)",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
        )

    # ── Статистика ────────────────────────────────────────────────────────────
    elif action == "stats":
        stats = await get_stats(callback.from_user.id)
        knowledge = await get_all_knowledge(limit=1)
        current = get_user_model(callback.from_user.id)
        model_info = AVAILABLE_MODELS.get(current, {})
        await callback.message.answer(
            f"📊 Статистика\n\n"
            f"Задач всего: {stats['total_tasks']}\n"
            f"Изменений кода: {stats['code_changes']}\n"
            f"Токенов сегодня: {stats['today_tokens']:,}\n"
            f"Запросов сегодня: {stats['today_requests']}\n"
            f"Знаний накоплено: {stats.get('knowledge_count', len(knowledge))}\n\n"
            f"🤖 Нейросеть: {model_info.get('emoji','')} {model_info.get('name','')}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="◀️ Меню", callback_data="menu:back")
            ]])
        )

    # ── Очистить контекст ─────────────────────────────────────────────────────
    elif action == "clear":
        await clear_conversation(callback.from_user.id)
        await callback.message.answer(
            "🧹 Контекст разговора очищен.\nТеперь бот не помнит предыдущие сообщения.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="◀️ Меню", callback_data="menu:back")
            ]])
        )

    # ── Назад в меню ─────────────────────────────────────────────────────────
    elif action == "back":
        current_model = get_user_model(callback.from_user.id)
        model_info = AVAILABLE_MODELS.get(current_model, {})
        from game_expert import get_project_summary
        project_info = get_project_summary()
        await callback.message.answer(
            f"👋 Главное меню\n\n"
            f"{project_info}\n\n"
            f"🤖 Нейросеть: {model_info.get('emoji','')} {model_info.get('name','')}\n\n"
            f"Просто пиши задачу — или выбери действие:",
            reply_markup=_main_menu()
        )


# ── Смена модели ──────────────────────────────────────────────────────────────

@router.callback_query(lambda c: c.data.startswith("setmodel:"))
async def callback_set_model(callback: CallbackQuery):
    model_key = callback.data.split(":")[1]
    set_user_model(callback.from_user.id, model_key)
    info = AVAILABLE_MODELS.get(model_key, {})
    await callback.answer(f"✅ {info.get('name', model_key)}")

    current = get_user_model(callback.from_user.id)
    buttons = []
    for key, m in AVAILABLE_MODELS.items():
        mark = " ✅" if key == current else ""
        buttons.append([InlineKeyboardButton(
            text=f"{m['emoji']} {m['name']}{mark}",
            callback_data=f"setmodel:{key}"
        )])
    buttons.append([InlineKeyboardButton(text="◀️ Меню", callback_data="menu:back")])
    try:
        await callback.message.edit_reply_markup(
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
        )
    except Exception:
        pass


# ── TODO готово ───────────────────────────────────────────────────────────────

@router.callback_query(lambda c: c.data.startswith("todo_done:"))
async def callback_todo_done(callback: CallbackQuery):
    todo_id = int(callback.data.split(":")[1])
    await mark_todo_done(todo_id)
    await callback.answer("✅ Готово!")
    await callback.message.edit_reply_markup()


# ── Старые команды (оставляем для совместимости) ──────────────────────────────

@router.message(Command("help"))
async def cmd_help(message: Message):
    if not is_allowed(message.from_user.id):
        return
    await message.answer(
        "📖 Просто пиши задачу текстом — бот поймёт.\n\n"
        "Или нажми /menu чтобы открыть меню с кнопками.",
        reply_markup=_main_menu()
    )


@router.message(Command("stats"))
async def cmd_stats(message: Message):
    if not is_allowed(message.from_user.id):
        return
    stats = await get_stats(message.from_user.id)
    current = get_user_model(message.from_user.id)
    model_info = AVAILABLE_MODELS.get(current, {})
    await message.answer(
        f"📊 Статистика\n\n"
        f"Задач: {stats['total_tasks']}\n"
        f"Изменений: {stats['code_changes']}\n"
        f"Токенов сегодня: {stats['today_tokens']:,}\n\n"
        f"🤖 {model_info.get('emoji','')} {model_info.get('name','')}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="◀️ Меню", callback_data="menu:back")
        ]])
    )


@router.message(Command("changes"))
async def cmd_changes(message: Message):
    if not is_allowed(message.from_user.id):
        return
    changes = await get_changes_with_rollback(message.from_user.id, limit=8)
    if not changes:
        await message.answer("📋 Изменений пока нет.")
        return
    for ch in changes:
        date = ch["changed_at"][:16].replace("T", " ")
        status = "↩️ откатано" if ch["status"] == "rolled_back" else "✅ применено"
        kb = None
        if ch["status"] != "rolled_back" and ch.get("has_rollback"):
            kb = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="↩️ Откатить", callback_data=f"rollback:{ch['id']}")
            ]])
        await message.answer(f"{status} | {date}\n📄 {ch['file_path']}\n{ch['description'][:60]}", reply_markup=kb)


@router.message(Command("model"))
async def cmd_model(message: Message):
    if not is_allowed(message.from_user.id):
        return
    current = get_user_model(message.from_user.id)
    buttons = []
    for key, info in AVAILABLE_MODELS.items():
        mark = " ✅" if key == current else ""
        buttons.append([InlineKeyboardButton(
            text=f"{info['emoji']} {info['name']}{mark}",
            callback_data=f"setmodel:{key}"
        )])
    await message.answer(
        "🤖 Выбери нейросеть:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )


@router.message(Command("index"))
async def cmd_index(message: Message):
    if not is_allowed(message.from_user.id):
        return
    msg = await message.answer("🔄 Индексирую...")
    index = await index_game()
    if index.get("error"):
        await msg.edit_text(f"❌ {index['error']}")
    else:
        await msg.edit_text(
            f"✅ Готово! {index['file_count']} файлов, {index['total_lines']:,} строк",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="◀️ Меню", callback_data="menu:back")
            ]])
        )


@router.message(Command("clear"))
async def cmd_clear(message: Message):
    if not is_allowed(message.from_user.id):
        return
    await clear_conversation(message.from_user.id)
    await message.answer("🧹 Контекст очищен.")


@router.message(Command("find"))
async def cmd_find(message: Message):
    if not is_allowed(message.from_user.id):
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("🔍 Использование: /find имя_функции")
        return
    query = parts[1].strip()
    msg = await message.answer(f"🔍 Ищу {query}...")
    index = load_index()
    if not index:
        await msg.edit_text("❌ Сначала обнови код")
        return
    found_blocks = []
    for file_info in index.get("files", [])[:20]:
        content = await _fetch_file(file_info["path"])
        if not content:
            continue
        lines = content.splitlines()
        for i, line in enumerate(lines):
            if query.lower() in line.lower():
                start = max(0, i - 3)
                end = min(len(lines), i + 15)
                snippet = html.escape("\n".join(lines[start:end])[:800])
                found_blocks.append(f"📄 <code>{html.escape(file_info['path'])}:{i+1}</code>\n<pre>{snippet}</pre>")
                if len(found_blocks) >= 3:
                    break
        if len(found_blocks) >= 3:
            break
    if not found_blocks:
        await msg.edit_text(f"😶 {query} не найдено")
        return
    await msg.edit_text(f"🔍 {query}:\n\n" + "\n\n".join(found_blocks))
