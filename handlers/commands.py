"""
Обработчики команд: /start, /help, /stats, /limits, /git, /index, /search, /reminders, /clear
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from loguru import logger

from config import ALLOWED_USERS
from memory import get_stats, get_last_changes, get_reminders, clear_conversation
from limit_manager import get_limit_status
from git_manager import format_git_status, is_git_repo
from game_expert import format_index_message, index_game, search_in_code
from teacher import explain_error, suggest_best_practices
from ai_client import AVAILABLE_MODELS, get_user_model, set_user_model

router = Router()


def is_allowed(user_id: int) -> bool:
    """Проверяет есть ли пользователь в списке разрешённых."""
    if not ALLOWED_USERS:
        return True  # Если список пуст — разрешаем всем
    return user_id in ALLOWED_USERS


@router.message(Command("start"))
async def cmd_start(message: Message):
    """Приветственное сообщение."""
    if not is_allowed(message.from_user.id):
        await message.answer("🚫 Доступ запрещён.")
        return

    from game_expert import get_project_summary
    project_info = get_project_summary()

    await message.answer(
        f"👋 Привет! Я твой AI-разработчик игры.\n\n"
        f"{project_info}\n\n"
        f"📤 *Что я умею:*\n"
        f"• Текст — пиши задачу, я разберусь\n"
        f"• 📸 Скриншот — покажи ошибку, я найду причину\n"
        f"• 🎤 Голосовое — говори задачу голосом\n"
        f"• 📹 Видео — покажи баг в движении\n"
        f"• 📄 Файл/лог — скину после разбора\n\n"
        f"📋 *Команды:*\n"
        f"/help — справка\n"
        f"/stats — статистика\n"
        f"/limits — токены\n"
        f"/git — статус репозитория\n"
        f"/index — переиндексировать игру\n"
        f"/search — поиск по коду\n"
        f"/reminders — напоминания\n"
        f"/explain — объяснить ошибку\n"
        f"/review — ревью кода\n"
        f"/clear — очистить контекст разговора",
        parse_mode="Markdown"
    )


@router.message(Command("help"))
async def cmd_help(message: Message):
    """Справка по использованию."""
    if not is_allowed(message.from_user.id):
        return

    await message.answer(
        "📖 *Как пользоваться ботом:*\n\n"
        "*Текстовые задачи:*\n"
        "Просто напиши что нужно. Например:\n"
        "• `game crash when open inventory`\n"
        "• `добавь систему сохранений`\n"
        "• `почему падает FPS в пещере?`\n\n"
        "*Скриншоты:*\n"
        "Прикрепи скриншот ошибки — бот прочитает текст\n"
        "и спросит AI что не так\n\n"
        "*Голос:*\n"
        "Отправь голосовое — Whisper распознает и обработает\n\n"
        "*Видео:*\n"
        "Запись бага — бот разберёт по кадрам\n\n"
        "*Работа с кодом:*\n"
        "Когда бот предлагает изменение — появятся кнопки:\n"
        "✅ Применить | 📝 Показать файл | ❌ Отклонить\n\n"
        "*Поиск по коду:*\n"
        "`/search текстура_огня` — найдёт все вхождения\n\n"
        "*Напоминания:*\n"
        "`напомни завтра починить сундуки` — запомню и напомню\n\n"
        "*Ревью и объяснения:*\n"
        "`/explain NullReferenceException` — объясню ошибку просто\n"
        "`/review def my_func(): ...` — скажу что улучшить",
        parse_mode="Markdown"
    )


@router.message(Command("stats"))
async def cmd_stats(message: Message):
    """Статистика работы бота."""
    if not is_allowed(message.from_user.id):
        return

    stats = await get_stats(message.from_user.id)
    changes = await get_last_changes(message.from_user.id, limit=3)

    lines = [
        "📊 *Статистика*\n",
        f"Всего задач: {stats['total_tasks']}",
        f"Изменений кода: {stats['code_changes']}",
        f"Токенов всего: {stats['total_tokens']:,}",
        f"Сегодня токенов: {stats['today_tokens']:,}",
        f"Запросов сегодня: {stats['today_requests']}",
    ]

    if changes:
        lines.append("\n📝 *Последние изменения:*")
        for ch in changes:
            status_icon = "✅" if ch["status"] == "applied" else "↩️"
            lines.append(f"{status_icon} `{ch['file_path']}` — {ch['description'][:40]}")

    await message.answer("\n".join(lines), parse_mode="Markdown")


@router.message(Command("limits"))
async def cmd_limits(message: Message):
    """Показывает использование токенов."""
    if not is_allowed(message.from_user.id):
        return

    status = await get_limit_status(message.from_user.id)
    await message.answer(status, parse_mode="Markdown")


@router.message(Command("git"))
async def cmd_git(message: Message):
    """Показывает статус Git репозитория."""
    if not is_allowed(message.from_user.id):
        return

    if not is_git_repo():
        await message.answer(
            "📭 Папка `game_repo/` не является Git репозиторием.\n\n"
            "Чтобы инициализировать:\n"
            "`cd game_repo && git init`",
            parse_mode="Markdown"
        )
        return

    status = format_git_status()
    await message.answer(status, parse_mode="Markdown")


@router.message(Command("index"))
async def cmd_index(message: Message):
    """Переиндексирует код игры."""
    if not is_allowed(message.from_user.id):
        return

    msg = await message.answer("📚 Индексирую игру... подожди немного")

    index = await index_game()

    if index.get("error"):
        await msg.edit_text(
            f"❌ {index['error']}\n\n"
            f"Положи код игры в папку `game_repo/` рядом с ботом.",
            parse_mode="Markdown"
        )
    else:
        await msg.edit_text(
            f"✅ *Игра проиндексирована!*\n\n"
            f"📁 Файлов: {index['file_count']}\n"
            f"📝 Строк: {index['total_lines']:,}\n"
            f"🔧 Функций: {len(index['functions'])}\n"
            f"🏗 Классов: {len(index['classes'])}\n\n"
            f"Теперь я знаю структуру твоей игры.",
            parse_mode="Markdown"
        )


@router.message(Command("search"))
async def cmd_search(message: Message):
    """Поиск по коду игры."""
    if not is_allowed(message.from_user.id):
        return

    # Получаем поисковый запрос
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer(
            "🔍 *Поиск по коду*\n\n"
            "Использование: `/search текст_для_поиска`\n\n"
            "Примеры:\n"
            "`/search inventory`\n"
            "`/search def save_game`\n"
            "`/search NullReferenceException`",
            parse_mode="Markdown"
        )
        return

    query = parts[1]
    msg = await message.answer(f"🔍 Ищу `{query}`...", parse_mode="Markdown")

    results = await search_in_code(query)

    if not results:
        await msg.edit_text(f"😶 Ничего не найдено по запросу `{query}`", parse_mode="Markdown")
        return

    lines = [f"🔍 *Результаты поиска '{query}':*\n"]
    for r in results[:10]:
        lines.append(f"`{r['file']}:{r['line_num']}`")
        lines.append(f"  `{r['line_text'][:60]}`")

    if len(results) > 10:
        lines.append(f"\n...и ещё {len(results) - 10} совпадений")

    await msg.edit_text("\n".join(lines), parse_mode="Markdown")


@router.message(Command("reminders"))
async def cmd_reminders(message: Message):
    """Показывает список активных напоминаний."""
    if not is_allowed(message.from_user.id):
        return

    reminders = await get_reminders(message.from_user.id)

    if not reminders:
        await message.answer("📅 Нет активных напоминаний.")
        return

    lines = ["📅 *Напоминания:*\n"]
    for r in reminders:
        remind_date = r["remind_at"][:16].replace("T", " ")
        lines.append(f"🔔 {remind_date} — {r['message']}")

    await message.answer("\n".join(lines), parse_mode="Markdown")


@router.message(Command("explain"))
async def cmd_explain(message: Message):
    """Объясняет ошибку простым языком."""
    if not is_allowed(message.from_user.id):
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer(
            "🎓 *Объяснение ошибки*\n\n"
            "Использование: `/explain текст ошибки`\n\n"
            "Пример:\n"
            "`/explain NullReferenceException: Object reference not set`",
            parse_mode="Markdown"
        )
        return

    error_text = parts[1]
    msg = await message.answer("🎓 Разбираю ошибку...", parse_mode="Markdown")

    explanation, tokens = await explain_error(error_text)

    await msg.edit_text(
        explanation[:4000] + f"\n\n📊 Токенов: {tokens:,}",
        parse_mode="Markdown"
    )


@router.message(Command("review"))
async def cmd_review(message: Message):
    """Анализирует код и предлагает улучшения."""
    if not is_allowed(message.from_user.id):
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer(
            "🔬 *Ревью кода*\n\n"
            "Использование: `/review код`\n\n"
            "Или отправь файл с подписью `/review` — я проанализирую его.",
            parse_mode="Markdown"
        )
        return

    code = parts[1]
    # Определяем язык по первой строке если есть markdown-блок
    language = "python"
    if code.startswith("```"):
        first_line = code.split("\n")[0].strip("`").strip()
        if first_line:
            language = first_line

    msg = await message.answer("🔬 Анализирую код...", parse_mode="Markdown")

    feedback, tokens = await suggest_best_practices(code, language)

    await msg.edit_text(
        feedback[:4000] + f"\n\n📊 Токенов: {tokens:,}",
        parse_mode="Markdown"
    )


@router.message(Command("clear"))
async def cmd_clear(message: Message):
    """Очищает историю разговора (контекст для AI)."""
    if not is_allowed(message.from_user.id):
        return

    await clear_conversation(message.from_user.id)
    await message.answer(
        "🧹 Контекст разговора очищен.\n"
        "Теперь я не помню предыдущие сообщения этой сессии."
    )


@router.message(Command("model"))
async def cmd_model(message: Message):
    """Выбор AI модели."""
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
        "🤖 Выбери AI модель:\n(все бесплатные, при лимите автопереключение)",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )


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

    await callback.message.edit_reply_markup(
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )


@router.message(Command("testmodels"))
async def cmd_testmodels(message: Message):
    """Проверяет какие модели OpenRouter реально доступны."""
    if not is_allowed(message.from_user.id):
        return

    import aiohttp
    from config import OPENROUTER_API_KEY

    msg = await message.answer("🔍 Проверяю доступные бесплатные модели на OpenRouter...")

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                "https://openrouter.ai/api/v1/models",
                headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}"},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                data = await resp.json()

        if resp.status != 200:
            await msg.edit_text(f"❌ Ошибка API: {resp.status}\n{data}")
            return

        free_models = [
            m for m in data.get("data", [])
            if ":free" in m.get("id", "") or m.get("pricing", {}).get("prompt") == "0"
        ]

        if not free_models:
            await msg.edit_text("😶 Бесплатных моделей не найдено.")
            return

        lines = [f"✅ Доступных бесплатных моделей: {len(free_models)}\n"]
        for m in free_models[:20]:
            lines.append(f"• `{m['id']}`")
        if len(free_models) > 20:
            lines.append(f"\n...и ещё {len(free_models) - 20}")

        await msg.edit_text("\n".join(lines), parse_mode="Markdown")

    except Exception as e:
        await msg.edit_text(f"❌ Ошибка: {e}")


@router.message(Command("pull"))
async def cmd_pull(message: Message):
    """Подтягивает свежий код с GitHub и переиндексирует."""
    if not is_allowed(message.from_user.id):
        return

    msg = await message.answer("🔄 Подтягиваю свежий код с GitHub...")
    index = await index_game()

    if index.get("error"):
        await msg.edit_text(f"❌ Ошибка: {index['error']}")
    else:
        await msg.edit_text(
            f"✅ Код обновлён с GitHub!\n\n"
            f"Файлов: {index['file_count']}\n"
            f"Строк: {index['total_lines']:,}\n"
            f"Функций: {len(index['functions'])}"
        )
