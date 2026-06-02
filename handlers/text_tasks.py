"""
Обработчик текстовых задач.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import re
import asyncio
from pathlib import Path
from datetime import datetime
from aiogram import Router, F
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from loguru import logger

from config import ALLOWED_USERS
from memory import save_task, update_task, add_to_conversation, get_conversation
from ai_client import ask_code_model, count_tokens_approx, get_user_model, get_model_info
from game_expert import search_in_code, read_relevant_files, load_index, index_game, push_file_to_github
from limit_manager import check_limit, track_usage
from memory import save_reminder

router = Router()

# task_id -> данные об изменении кода
pending_changes: dict[int, dict] = {}

# user_id -> текущая asyncio.Task (для отмены)
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
            InlineKeyboardButton(text="🌿 Тест-ветка", callback_data=f"branch:{task_id}"),
            InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject:{task_id}"),
        ]
    ])


def _is_reminder_request(text: str) -> tuple[bool, str, datetime | None]:
    text_lower = text.lower()
    if not any(kw in text_lower for kw in ["напомни", "напоминай", "не забудь", "remind"]):
        return False, text, None

    remind_time = None
    if "завтра" in text_lower:
        from datetime import timedelta
        remind_time = datetime.now().replace(hour=9, minute=0) + timedelta(days=1)
    elif "через час" in text_lower or "через 1 час" in text_lower:
        from datetime import timedelta
        remind_time = datetime.now() + timedelta(hours=1)
    elif "через" in text_lower:
        match = re.search(r"через\s+(\d+)\s+(минут|час)", text_lower)
        if match:
            from datetime import timedelta
            amount = int(match.group(1))
            unit = match.group(2)
            if "минут" in unit:
                remind_time = datetime.now() + timedelta(minutes=amount)
            else:
                remind_time = datetime.now() + timedelta(hours=amount)

    return remind_time is not None, text, remind_time


async def _heartbeat(status_msg: Message, base_text: str, task_id: int, interval: int = 5):
    """Каждые N секунд обновляет статус — чтобы было видно что бот жив."""
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
            pass  # Сообщение могло быть удалено — не страшно


def _detect_code_change(response: str) -> bool:
    indicators = ["БЫЛО:", "СТАЛО:", "Файл:", "```python", "```js", "```lua", "```html", "```javascript"]
    return any(ind in response for ind in indicators)


def _parse_code_change(response: str) -> dict:
    change = {"file": None, "old_code": None, "new_code": None, "description": response[:100]}

    file_match = re.search(r"[Фф]айл[:\s]+([^\n]+)", response)
    if file_match:
        change["file"] = file_match.group(1).strip()

    code_blocks = re.findall(r"```(?:\w+)?\n(.*?)```", response, re.DOTALL)
    if len(code_blocks) >= 2:
        change["old_code"] = code_blocks[0].strip()
        change["new_code"] = code_blocks[1].strip()
    elif len(code_blocks) == 1:
        change["new_code"] = code_blocks[0].strip()

    return change


async def _process_task(user_id: int, task_id: int, text: str, status_msg: Message, model_info: dict):
    """Основная логика обработки задачи — запускается как отдельный asyncio.Task."""
    try:
        # Шаг 1: ищем код
        index = load_index()
        context_files = []
        code_context = ""

        if index and not index.get("error"):
            text_lower = text.lower()

            # Ищем файлы по имени в тексте
            named_files = [
                f["path"] for f in index.get("files", [])
                if Path(f["path"]).name.lower() in text_lower
                or Path(f["path"]).stem.lower() in text_lower
            ]

            # world.html — главный файл
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
                    f"⚙️ Принял задачу\n"
                    f"📄 Шаг 2/3: читаю {', '.join(short_names)}...",
                    reply_markup=_stop_keyboard(task_id)
                )
                code_context = await read_relevant_files(unique_files, max_chars=12000, query=text)
            else:
                await status_msg.edit_text(
                    f"⚙️ Принял задачу\n📭 Файлы не найдены, работаю без контекста",
                    reply_markup=_stop_keyboard(task_id)
                )
        else:
            await status_msg.edit_text(
                f"⚙️ Принял задачу\n📭 Игра не проиндексирована — запусти /index",
                reply_markup=_stop_keyboard(task_id)
            )

        # Шаг 2: формируем промпт
        index_summary = ""
        if index and not index.get("error"):
            file_names = [f["path"] for f in index.get("files", [])]
            index_summary = (
                f"\n\nТы изучил код игры из репозитория {index.get('repo', 'mafiozy')}. "
                f"Файлы: {', '.join(file_names)}. "
                f"Всего {index.get('file_count', 0)} файлов, {index.get('total_lines', 0)} строк."
            )

        system_prompt = (
            "Ты разработчик игры Мафиози. Отвечай ОЧЕНЬ кратко — максимум 8 строк. "
            "Только суть + готовый код. Никаких длинных объяснений, теорий и пояснений. "
            "Если нужен код — сразу код, 1-2 предложения что он делает. "
            "Язык: русский. "
            "Структура: world.html — главный файл (открытый мир), "
            "hub.html — хаб, battle.html — бой, creator.html — редактор. "
            "Работай с world.html по умолчанию. "
            "Формат изменения кода:\n"
            "БЫЛО:\n```\nстарый код\n```\nСТАЛО:\n```\nновый код\n```\nФайл: имя"
            + index_summary
        )

        full_prompt = text
        if code_context:
            full_prompt = f"Задача: {text}\n\nКонтекст (связанный код):\n{code_context}"

        await add_to_conversation(user_id, "user", text)
        history = await get_conversation(user_id, limit=10)

        # Шаг 3: AI думает — запускаем пульс чтобы не казалось зависшим
        files_str = ', '.join(Path(f).name for f in context_files) if context_files else 'без контекста'
        thinking_text = (
            f"⚙️ Принял задачу\n"
            f"📄 {files_str}\n"
            f"{model_info['emoji']} {model_info['name']} думает..."
        )
        await status_msg.edit_text(thinking_text, reply_markup=_stop_keyboard(task_id))

        heartbeat = asyncio.create_task(
            _heartbeat(status_msg, thinking_text, task_id, interval=5)
        )
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

        # Показываем результат
        has_change = _detect_code_change(ai_response)
        # Очищаем ответ от символов которые ломают Telegram HTML-парсер
        safe_response = ai_response.replace("<", "&lt;").replace(">", "&gt;")

        if has_change:
            change_info = _parse_code_change(ai_response)
            pending_changes[task_id] = {
                "user_id": user_id,
                "task_id": task_id,
                "change": change_info,
                "full_response": ai_response,
            }
            await status_msg.edit_text(
                safe_response[:4000] + f"\n\n📊 Токенов: {tokens_used:,}",
                reply_markup=_change_keyboard(task_id),
                parse_mode=None
            )
        else:
            await status_msg.edit_text(
                safe_response[:4000] + f"\n\n📊 Токенов: {tokens_used:,}",
                parse_mode=None
            )

    except asyncio.CancelledError:
        logger.info(f"Задача {task_id} отменена пользователем {user_id}")
        raise  # важно пробросить дальше
    except Exception as e:
        logger.error(f"Ошибка задачи {task_id}: {e}")
        await update_task(task_id, "failed")
        await status_msg.edit_text(
            f"❌ Ошибка:\n{str(e)}\n\nПопробуй переформулировать задачу."
        )


@router.callback_query(lambda c: c.data.startswith("stoptask:"))
async def callback_stop_task(callback: CallbackQuery):
    user_id = callback.from_user.id
    task = active_tasks.get(user_id)
    if task and not task.done():
        task.cancel()
        await callback.message.edit_text("🛑 Остановлено. Жду следующую задачу.")
    else:
        await callback.message.edit_reply_markup()
    await callback.answer("Остановлено")


@router.message(F.text)
async def handle_text_task(message: Message):
    if not is_allowed(message.from_user.id):
        return

    user_id = message.from_user.id
    text = message.text.strip()

    # Отменяем предыдущую задачу если ещё идёт
    prev = active_tasks.get(user_id)
    if prev and not prev.done():
        prev.cancel()

    if text.startswith("/"):
        return

    # Детект "подтяни"
    text_lower = text.lower()
    if any(kw in text_lower for kw in ["подтяни", "pull", "обнови код", "обнови индекс", "свежак"]):
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
        return

    # Лимит токенов
    can_proceed, limit_msg = await check_limit(user_id)
    if not can_proceed:
        await message.answer(limit_msg, parse_mode="Markdown")
        return

    # Напоминание
    is_reminder, task_text, remind_time = _is_reminder_request(text)
    if is_reminder and remind_time:
        await save_reminder(user_id, remind_time, task_text)
        await message.answer(
            f"⏰ Напомню!\nЗадача: {task_text}\nКогда: {remind_time.strftime('%d.%m %H:%M')}"
        )
        return

    # Предупреждение об устаревшем индексе (> 2 часов)
    import time as _time
    index_check = load_index()
    if index_check and not index_check.get("error"):
        from config import BASE_DIR
        idx_path = BASE_DIR / "game_index.json"
        if idx_path.exists():
            age_hours = (_time.time() - idx_path.stat().st_mtime) / 3600
            if age_hours > 2:
                await message.answer(
                    f"⚠️ Индекс устарел ({int(age_hours)}ч). Напиши /index чтобы обновить код."
                )

    task_id = await save_task(user_id, "text", text)
    model_key = get_user_model(user_id)
    model_info = get_model_info(model_key)

    status_msg = await message.answer(
        f"⚙️ Принял задачу\n🔍 Шаг 1/3: ищу связанный код...",
        reply_markup=_stop_keyboard(task_id)
    )

    # Запускаем обработку как отдельную задачу
    task = asyncio.create_task(
        _process_task(user_id, task_id, text, status_msg, model_info)
    )
    active_tasks[user_id] = task
