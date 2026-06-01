"""
Обработчик медиафайлов — скриншоты, видео.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pathlib import Path
from aiogram import Router, F
from aiogram.types import Message
from loguru import logger

from config import ALLOWED_USERS, TEMP_DIR
from memory import save_task, update_task, add_to_conversation
from vision import analyze_screenshot, analyze_video, format_screenshot_result
from ai_client import ask_code_model
from game_expert import search_in_code, read_relevant_files
from limit_manager import check_limit, track_usage

router = Router()


def is_allowed(user_id: int) -> bool:
    if not ALLOWED_USERS:
        return True
    return user_id in ALLOWED_USERS


@router.message(F.photo)
async def handle_photo(message: Message):
    """Обрабатывает скриншот — OCR + Vision AI."""
    if not is_allowed(message.from_user.id):
        return

    user_id = message.from_user.id

    # Проверяем лимит
    can_proceed, limit_msg = await check_limit(user_id)
    if not can_proceed:
        await message.answer(limit_msg)
        return

    # Подпись к фото (если есть)
    caption = message.caption or "Проанализируй этот скриншот"

    task_id = await save_task(user_id, "photo", caption)
    status_msg = await message.answer(
        "📸 *Получил скриншот!*\n\n"
        "🔍 Запускаю OCR (распознавание текста)...",
        parse_mode="Markdown"
    )

    # Скачиваем фото
    photo = message.photo[-1]  # Берём самое большое
    file_path = str(TEMP_DIR / f"screenshot_{task_id}.jpg")

    try:
        await message.bot.download(photo.file_id, destination=file_path)
    except Exception as e:
        await status_msg.edit_text(f"❌ Не могу скачать фото: {e}")
        return

    # Анализируем скриншот
    await status_msg.edit_text(
        "📸 *Анализирую скриншот...*\n\n"
        "🔍 OCR читает текст...\n"
        "🤖 Vision AI смотрит на картинку...",
        parse_mode="Markdown"
    )

    try:
        analysis = await analyze_screenshot(file_path)
        tokens_used = analysis.get("tokens_used", 0)

        # Собираем весь контекст об ошибке
        error_context = ""
        if analysis.get("ocr_text"):
            error_context += f"Текст на скриншоте:\n{analysis['ocr_text']}\n\n"
        if analysis.get("vision_desc"):
            error_context += f"Описание от AI:\n{analysis['vision_desc']}\n\n"

        # Ищем связанный код
        code_context = ""
        if analysis.get("errors_found"):
            search_query = " ".join(analysis["errors_found"][:3])
            results = await search_in_code(search_query)
            if results:
                files = list(dict.fromkeys(r["file"] for r in results[:3]))
                code_context = await read_relevant_files(files)

        # Дополнительный анализ от code модели
        if error_context:
            await status_msg.edit_text(
                "📸 *Скриншот проанализирован*\n\n"
                "🧠 Ищу решение в коде...",
                parse_mode="Markdown"
            )

            prompt = (
                f"Пользователь прислал скриншот с проблемой.\n\n"
                f"Описание скриншота:\n{error_context}\n"
                f"Дополнительная подпись: {caption}\n"
            )
            if code_context:
                prompt += f"\nСвязанный код:\n{code_context}"
            prompt += "\nЧто случилось и как починить?"

            solution, code_tokens = await ask_code_model(prompt)
            tokens_used += code_tokens

            await add_to_conversation(user_id, "user", f"[Скриншот] {caption}")
            await add_to_conversation(user_id, "assistant", solution)

            await track_usage(user_id, tokens_used)
            await update_task(task_id, "done", solution, tokens_used)

            # Форматируем итоговый ответ
            screenshot_info = format_screenshot_result(analysis)
            full_response = (
                f"{screenshot_info}\n\n"
                f"💡 *Решение:*\n{solution[:2000]}\n\n"
                f"📊 Токенов: {tokens_used:,}"
            )

            await status_msg.edit_text(full_response[:4096], parse_mode="Markdown")
        else:
            # Не удалось извлечь текст — показываем что видит Vision
            result_text = format_screenshot_result(analysis)
            await status_msg.edit_text(
                result_text + "\n\n💬 Опиши проблему текстом — я разберусь!",
                parse_mode="Markdown"
            )

    except Exception as e:
        logger.error(f"Ошибка анализа скриншота: {e}")
        await status_msg.edit_text(f"❌ Ошибка анализа: {e}")
    finally:
        # Удаляем временный файл
        Path(file_path).unlink(missing_ok=True)


@router.message(F.video | F.video_note)
async def handle_video(message: Message):
    """Обрабатывает видео с багом."""
    if not is_allowed(message.from_user.id):
        return

    user_id = message.from_user.id

    can_proceed, limit_msg = await check_limit(user_id)
    if not can_proceed:
        await message.answer(limit_msg)
        return

    caption = message.caption or "Видео с багом"
    task_id = await save_task(user_id, "video", caption)

    status_msg = await message.answer(
        "📹 *Получил видео!*\n\n"
        "⏳ Скачиваю и извлекаю кадры...",
        parse_mode="Markdown"
    )

    # Скачиваем видео
    video = message.video or message.video_note
    video_path = str(TEMP_DIR / f"video_{task_id}.mp4")

    try:
        await message.bot.download(video.file_id, destination=video_path)
    except Exception as e:
        await status_msg.edit_text(f"❌ Не могу скачать видео: {e}")
        return

    try:
        await status_msg.edit_text(
            "📹 *Анализирую видео...*\n\n"
            "🎞 Разбиваю на кадры каждые 2 секунды...\n"
            "🤖 AI ищет момент бага...",
            parse_mode="Markdown"
        )

        analysis = await analyze_video(video_path)
        tokens_used = analysis.get("tokens_used", 0)

        await track_usage(user_id, tokens_used)
        await update_task(task_id, "done", analysis.get("description", ""), tokens_used)

        # Формируем ответ
        lines = [f"📹 *Анализ видео ({analysis['frames_analyzed']} кадров):*\n"]

        if analysis.get("problem_frame"):
            frame_num, frame_desc = analysis["problem_frame"]
            lines.append(f"🎯 *Момент бага:* кадр {frame_num}")
            lines.append(f"_{frame_desc}_\n")

        if analysis.get("description"):
            lines.append(f"💡 *Вывод AI:*\n{analysis['description']}")

        lines.append(f"\n📊 Токенов: {tokens_used:,}")

        await status_msg.edit_text("\n".join(lines)[:4096], parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Ошибка анализа видео: {e}")
        await status_msg.edit_text(f"❌ Ошибка анализа видео: {e}")
    finally:
        Path(video_path).unlink(missing_ok=True)


@router.message(F.document)
async def handle_document(message: Message):
    """Обрабатывает документы (логи, код, текстовые файлы)."""
    if not is_allowed(message.from_user.id):
        return

    user_id = message.from_user.id

    can_proceed, limit_msg = await check_limit(user_id)
    if not can_proceed:
        await message.answer(limit_msg)
        return

    doc = message.document

    # Только текстовые файлы
    allowed_mimes = [
        "text/plain", "text/x-python", "application/json",
        "text/html", "text/css", "text/javascript",
    ]
    allowed_exts = [".py", ".js", ".ts", ".lua", ".txt", ".log", ".json", ".yaml", ".yml", ".cs"]

    file_ext = Path(doc.file_name or "").suffix.lower()
    is_text = (doc.mime_type in allowed_mimes) or (file_ext in allowed_exts)

    if not is_text:
        await message.answer(
            "📎 Могу обрабатывать только текстовые файлы:\n"
            "`.py .js .ts .lua .txt .log .json .yaml .cs`",
            parse_mode="Markdown"
        )
        return

    task_id = await save_task(user_id, "document", doc.file_name or "unknown")
    status_msg = await message.answer(
        f"📄 *Получил файл:* `{doc.file_name}`\n\n"
        "📖 Читаю и анализирую...",
        parse_mode="Markdown"
    )

    # Скачиваем файл
    file_path = str(TEMP_DIR / f"doc_{task_id}{file_ext}")
    try:
        await message.bot.download(doc.file_id, destination=file_path)
        content = Path(file_path).read_text(encoding="utf-8", errors="ignore")
    except Exception as e:
        await status_msg.edit_text(f"❌ Не могу прочитать файл: {e}")
        return
    finally:
        Path(file_path).unlink(missing_ok=True)

    caption = message.caption or f"Проанализируй файл {doc.file_name}"

    # Если это лог — ищем ошибки
    if file_ext == ".log" or "log" in (doc.file_name or "").lower():
        prompt = (
            f"Это лог файл: {doc.file_name}\n\n"
            f"Содержимое (последние 3000 символов):\n{content[-3000:]}\n\n"
            f"Найди ошибки и критические проблемы. Объясни что случилось."
        )
    else:
        prompt = (
            f"Файл: {doc.file_name}\n\n"
            f"Содержимое:\n{content[:4000]}\n\n"
            f"Задача пользователя: {caption}"
        )

    response, tokens_used = await ask_code_model(prompt)

    await track_usage(user_id, tokens_used)
    await update_task(task_id, "done", response, tokens_used)
    await add_to_conversation(user_id, "user", f"[Файл: {doc.file_name}] {caption}")
    await add_to_conversation(user_id, "assistant", response)

    await status_msg.edit_text(
        response[:4000] + f"\n\n📊 Токенов: {tokens_used:,}",
        parse_mode="Markdown"
    )
