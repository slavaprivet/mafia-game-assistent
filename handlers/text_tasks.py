"""
Обработчик текстовых задач — основной обработчик сообщений.
Анализирует задачу, находит нужный код, отправляет в AI, показывает результат.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import re
from datetime import datetime
from aiogram import Router, F
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from loguru import logger

from config import ALLOWED_USERS
from memory import save_task, update_task, add_to_conversation, get_conversation
from ai_client import ask_code_model, count_tokens_approx
from game_expert import search_in_code, read_relevant_files, load_index, find_related_files
from limit_manager import check_limit, track_usage
from memory import save_reminder

router = Router()

# Хранилище ожидающих изменений (task_id -> данные об изменении)
# Используем простой dict (в памяти, не персистентно)
pending_changes: dict[int, dict] = {}


def is_allowed(user_id: int) -> bool:
    if not ALLOWED_USERS:
        return True
    return user_id in ALLOWED_USERS


def _is_reminder_request(text: str) -> tuple[bool, str, datetime | None]:
    """
    Проверяет похоже ли сообщение на просьбу о напоминании.
    Возвращает (это_напоминание, текст_задачи, когда_напомнить).
    """
    text_lower = text.lower()
    reminder_keywords = ["напомни", "напоминай", "не забудь", "remind"]

    if not any(kw in text_lower for kw in reminder_keywords):
        return False, text, None

    # Простой парсинг времени
    remind_time = None
    task_text = text

    if "завтра" in text_lower:
        from datetime import timedelta
        remind_time = datetime.now().replace(hour=9, minute=0) + timedelta(days=1)
    elif "через час" in text_lower or "через 1 час" in text_lower:
        from datetime import timedelta
        remind_time = datetime.now() + timedelta(hours=1)
    elif "через" in text_lower:
        # Ищем "через N минут/часов"
        match = re.search(r"через\s+(\d+)\s+(минут|час)", text_lower)
        if match:
            from datetime import timedelta
            amount = int(match.group(1))
            unit = match.group(2)
            if "минут" in unit:
                remind_time = datetime.now() + timedelta(minutes=amount)
            else:
                remind_time = datetime.now() + timedelta(hours=amount)

    return remind_time is not None, task_text, remind_time


@router.message(F.text)
async def handle_text_task(message: Message):
    """Основной обработчик текстовых сообщений."""
    if not is_allowed(message.from_user.id):
        return

    user_id = message.from_user.id
    text = message.text.strip()

    # Игнорируем команды (они обрабатываются в commands.py)
    if text.startswith("/"):
        return

    # Проверяем лимит токенов
    can_proceed, limit_msg = await check_limit(user_id)
    if not can_proceed:
        await message.answer(limit_msg, parse_mode="Markdown")
        return

    # Проверяем на напоминание
    is_reminder, task_text, remind_time = _is_reminder_request(text)
    if is_reminder and remind_time:
        await save_reminder(user_id, remind_time, task_text)
        await message.answer(
            f"⏰ Напомню!\n"
            f"Задача: {task_text}\n"
            f"Когда: {remind_time.strftime('%d.%m %H:%M')}",
        )
        return

    # Сохраняем задачу в БД
    task_id = await save_task(user_id, "text", text)

    # Отправляем статусное сообщение
    status_msg = await message.answer(
        f"🎯 *Задача принята!*\n\n"
        f"🔍 Ищу связанный код...",
        parse_mode="Markdown"
    )

    try:
        # Шаг 1: Ищем связанный код
        index = load_index()
        context_files = []
        code_context = ""

        if index and not index.get("error"):
            # Ищем файлы по ключевым словам из задачи
            search_results = await search_in_code(text)

            if search_results:
                # Берём уникальные файлы
                unique_files = list(dict.fromkeys(r["file"] for r in search_results[:4]))
                context_files = unique_files

                await status_msg.edit_text(
                    f"🎯 *Задача принята!*\n\n"
                    f"📂 Нашёл {len(unique_files)} связанных файлов:\n"
                    + "\n".join(f"• `{f}`" for f in unique_files)
                    + "\n\n🧠 Отправляю в AI...",
                    parse_mode="Markdown"
                )

                # Читаем содержимое найденных файлов
                code_context = await read_relevant_files(unique_files)
            else:
                await status_msg.edit_text(
                    f"🎯 *Задача принята!*\n\n"
                    f"📭 В коде не нашёл прямых совпадений.\n"
                    f"🧠 Анализирую задачу через AI...",
                    parse_mode="Markdown"
                )
        else:
            await status_msg.edit_text(
                f"🎯 *Задача принята!*\n\n"
                f"📭 Игра не проиндексирована (/index)\n"
                f"🧠 Работаю без контекста кода...",
                parse_mode="Markdown"
            )

        # Шаг 2: Формируем промпт для AI
        system_prompt = (
            "Ты опытный разработчик игр. Анализируй задачи и ошибки, "
            "предлагай конкретные решения с кодом. "
            "Всегда объясняй ЧТО делает код и ПОЧЕМУ именно так. "
            "Отвечай на русском языке. "
            "Когда предлагаешь изменение кода, форматируй так:\n"
            "❌ БЫЛО:\n```\nстарый код\n```\n"
            "✅ СТАЛО:\n```\nновый код\n```\n"
            "📁 Файл: имя_файла.py"
        )

        # Добавляем контекст кода если есть
        full_prompt = text
        if code_context:
            full_prompt = (
                f"Задача: {text}\n\n"
                f"Контекст проекта (связанный код):\n{code_context}"
            )

        # Добавляем сообщение в историю разговора
        await add_to_conversation(user_id, "user", text)

        # Получаем историю для контекста
        history = await get_conversation(user_id, limit=10)

        # Шаг 3: Отправляем в AI
        ai_response, tokens_used = await ask_code_model(
            full_prompt,
            system_prompt=system_prompt,
            conversation_history=history[:-1],  # Без последнего (мы уже в промпте)
        )

        # Сохраняем ответ в историю
        await add_to_conversation(user_id, "assistant", ai_response)

        # Шаг 4: Обновляем статистику
        await track_usage(user_id, tokens_used)
        await update_task(task_id, "done", ai_response, tokens_used, context_files)

        # Шаг 5: Проверяем есть ли предложение изменения кода
        has_code_change = _detect_code_change(ai_response)

        if has_code_change:
            # Парсим изменение
            change_info = _parse_code_change(ai_response)
            pending_changes[task_id] = {
                "user_id": user_id,
                "task_id": task_id,
                "change": change_info,
                "full_response": ai_response,
            }

            # Показываем с кнопками
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text="✅ Применить", callback_data=f"apply:{task_id}"),
                    InlineKeyboardButton(text="📝 Показать файл", callback_data=f"showfile:{task_id}"),
                ],
                [
                    InlineKeyboardButton(text="🌿 В тест-ветку", callback_data=f"branch:{task_id}"),
                    InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject:{task_id}"),
                ]
            ])

            await status_msg.edit_text(
                ai_response[:4000] + f"\n\n📊 Токенов: {tokens_used:,}",
                parse_mode="Markdown",
                reply_markup=keyboard
            )
        else:
            # Просто текстовый ответ без изменений кода
            await status_msg.edit_text(
                ai_response[:4000] + f"\n\n📊 Токенов: {tokens_used:,}",
                parse_mode="Markdown"
            )

    except Exception as e:
        logger.error(f"Ошибка обработки задачи {task_id}: {e}")
        await update_task(task_id, "failed")
        await status_msg.edit_text(
            f"❌ Ошибка при обработке задачи:\n`{str(e)}`\n\n"
            f"Попробуй ещё раз или переформулируй задачу.",
            parse_mode="Markdown"
        )


def _detect_code_change(response: str) -> bool:
    """Проверяет есть ли в ответе предложение изменения кода."""
    indicators = ["❌ БЫЛО", "✅ СТАЛО", "📁 Файл", "```python", "```js", "```lua"]
    return any(ind in response for ind in indicators)


def _parse_code_change(response: str) -> dict:
    """Пытается извлечь информацию об изменении кода из ответа AI."""
    change = {
        "file": None,
        "old_code": None,
        "new_code": None,
        "description": response[:100],
    }

    # Ищем имя файла
    file_match = re.search(r"📁 Файл[:\s]+([^\n]+)", response)
    if file_match:
        change["file"] = file_match.group(1).strip()

    # Извлекаем блоки кода
    code_blocks = re.findall(r"```(?:\w+)?\n(.*?)```", response, re.DOTALL)
    if len(code_blocks) >= 2:
        change["old_code"] = code_blocks[0].strip()
        change["new_code"] = code_blocks[1].strip()
    elif len(code_blocks) == 1:
        change["new_code"] = code_blocks[0].strip()

    return change
