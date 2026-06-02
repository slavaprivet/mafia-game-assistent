"""
Обработчик текстовых задач — основной обработчик сообщений.
Анализирует задачу, находит нужный код, отправляет в AI, показывает результат.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import re
from pathlib import Path
from datetime import datetime
from aiogram import Router, F
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from loguru import logger

from config import ALLOWED_USERS
from memory import save_task, update_task, add_to_conversation, get_conversation
from ai_client import ask_code_model, count_tokens_approx, get_user_model, get_model_info
from game_expert import search_in_code, read_relevant_files, load_index, find_related_files, index_game, push_file_to_github
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

    # Детект "подтяни с гитхаб" — обновляем индекс
    text_lower = text.lower()
    if any(kw in text_lower for kw in ["подтяни", "pull", "обнови код", "обнови индекс", "свежак"]):
        from game_expert import index_game
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

    # Определяем текущую модель пользователя
    model_key = get_user_model(user_id)
    model_info = get_model_info(model_key)

    status_msg = await message.answer(
        f"⚙️ Принял задачу\n🔍 Шаг 1/3: ищу связанный код..."
    )

    try:
        # Шаг 1: Ищем связанный код
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

            # Главный файл — world, добавляем его первым если не упомянут другой
            world_files = [
                f["path"] for f in index.get("files", [])
                if "world" in Path(f["path"]).stem.lower()
            ]

            if named_files:
                unique_files = named_files[:3]
            else:
                search_results = await search_in_code(text)
                found = list(dict.fromkeys(r["file"] for r in search_results[:4]))
                # Если нашли только battle/второстепенные — добавляем world
                if world_files and not any("world" in f.lower() for f in found):
                    found = world_files[:1] + [f for f in found if "battle" not in f.lower()][:2]
                unique_files = found[:3]

            # Если вообще ничего не нашли — берём world
            if not unique_files and world_files:
                unique_files = world_files[:1]

            if unique_files:
                context_files = unique_files
                short_names = [Path(f).name for f in unique_files]
                await status_msg.edit_text(
                    f"⚙️ Принял задачу\n"
                    f"📄 Шаг 2/3: читаю {', '.join(short_names)}..."
                )
                code_context = await read_relevant_files(unique_files, max_chars=15000)
            else:
                await status_msg.edit_text(
                    f"⚙️ Принял задачу\n"
                    f"📭 Код не найден — работаю без контекста"
                )
        else:
            await status_msg.edit_text(
                f"⚙️ Принял задачу\n"
                f"📭 Игра не проиндексирована — запусти /index"
            )

        # Шаг 2: Формируем промпт для AI
        index_summary = ""
        if index and not index.get("error"):
            file_names = [f["path"] for f in index.get("files", [])]
            index_summary = (
                f"\n\nТы уже изучил код игры из репозитория {index.get('repo', 'mafiozy')}. "
                f"Файлы в проекте: {', '.join(file_names)}. "
                f"Всего {index.get('file_count', 0)} файлов, {index.get('total_lines', 0)} строк кода."
            )

        system_prompt = (
            "Ты опытный разработчик игр, помощник по разработке игры Мафиози. "
            "Анализируй задачи и ошибки, предлагай конкретные решения с кодом. "
            "Отвечай коротко и по делу, на русском языке. "
            "ВАЖНО: основной файл игры — world (world.html или похожее название). "
            "battle.htm — это второстепенный файл, не трогай его если задача не касается battle явно. "
            "Всегда работай с world-файлом если не указано иное. "
            "Когда предлагаешь изменение кода, форматируй так:\n"
            "БЫЛО:\nстарый код\n\nСТАЛО:\nновый код\n\nФайл: имя_файла"
            + index_summary
        )

        full_prompt = text
        if code_context:
            full_prompt = (
                f"Задача: {text}\n\n"
                f"Контекст проекта (связанный код):\n{code_context}"
            )

        await add_to_conversation(user_id, "user", text)
        history = await get_conversation(user_id, limit=10)

        # Шаг 3: Отправляем в AI — показываем какая модель думает
        await status_msg.edit_text(
            f"⚙️ Принял задачу\n"
            f"{'📄 ' + ', '.join(Path(f).name for f in context_files) if context_files else '📭 без контекста'}\n"
            f"{model_info['emoji']} Шаг 3/3: {model_info['name']} думает..."
        )

        ai_response, tokens_used = await ask_code_model(
            full_prompt,
            system_prompt=system_prompt,
            conversation_history=history[:-1],
            user_id=user_id,
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
                reply_markup=keyboard
            )
        else:
            await status_msg.edit_text(
                ai_response[:4000] + f"\n\n📊 Токенов: {tokens_used:,}",
            )

    except Exception as e:
        logger.error(f"Ошибка обработки задачи {task_id}: {e}")
        await update_task(task_id, "failed")
        await status_msg.edit_text(
            f"❌ Ошибка при обработке задачи:\n{str(e)}\n\nПопробуй ещё раз или переформулируй задачу.",
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
