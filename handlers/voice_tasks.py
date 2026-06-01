"""
Обработчик голосовых сообщений — Whisper STT → AI → TTS ответ.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pathlib import Path
from aiogram import Router, F
from aiogram.types import Message, FSInputFile
from loguru import logger

from config import ALLOWED_USERS, TEMP_DIR
from memory import save_task, update_task, add_to_conversation, get_conversation
from voice import speech_to_text, text_to_speech, convert_ogg_to_wav
from ai_client import ask_code_model
from limit_manager import check_limit, track_usage

router = Router()


def is_allowed(user_id: int) -> bool:
    if not ALLOWED_USERS:
        return True
    return user_id in ALLOWED_USERS


@router.message(F.voice)
async def handle_voice(message: Message):
    """Обрабатывает голосовое сообщение."""
    if not is_allowed(message.from_user.id):
        return

    user_id = message.from_user.id

    can_proceed, limit_msg = await check_limit(user_id)
    if not can_proceed:
        await message.answer(limit_msg)
        return

    task_id = await save_task(user_id, "voice", "голосовое сообщение")
    status_msg = await message.answer(
        "🎤 *Голосовое получено!*\n\n"
        "⏳ Распознаю речь (Whisper)...",
        parse_mode="Markdown"
    )

    # Скачиваем голосовое (Telegram отправляет в .ogg)
    ogg_path = str(TEMP_DIR / f"voice_{task_id}.ogg")
    try:
        await message.bot.download(message.voice.file_id, destination=ogg_path)
    except Exception as e:
        await status_msg.edit_text(f"❌ Не могу скачать голосовое: {e}")
        return

    # Конвертируем ogg → wav
    wav_path = await convert_ogg_to_wav(ogg_path)
    Path(ogg_path).unlink(missing_ok=True)

    if not wav_path:
        await status_msg.edit_text(
            "❌ Не могу конвертировать аудио.\n\n"
            "Нужен ffmpeg: https://ffmpeg.org/download.html"
        )
        return

    # Распознаём речь
    recognized_text = await speech_to_text(wav_path)
    Path(wav_path).unlink(missing_ok=True)

    if not recognized_text:
        await status_msg.edit_text(
            "❌ Не удалось распознать речь.\n\n"
            "Проверь что Whisper установлен: `pip install openai-whisper`\n"
            "Или напиши задачу текстом.",
            parse_mode="Markdown"
        )
        return

    await status_msg.edit_text(
        f"🎤 *Распознано:*\n_{recognized_text}_\n\n"
        f"🧠 Обрабатываю задачу...",
        parse_mode="Markdown"
    )

    # Обновляем задачу в БД
    await update_task(task_id, "processing", recognized_text)

    # Отправляем в AI (как обычную текстовую задачу)
    history = await get_conversation(user_id, limit=10)
    await add_to_conversation(user_id, "user", f"[Голос] {recognized_text}")

    system_prompt = (
        "Ты опытный разработчик игр. Отвечай кратко и по делу — "
        "ответ будет озвучен голосом, так что без лишних слов. "
        "Отвечай на русском языке."
    )

    response, tokens_used = await ask_code_model(
        recognized_text,
        system_prompt=system_prompt,
        conversation_history=history,
    )

    await add_to_conversation(user_id, "assistant", response)
    await track_usage(user_id, tokens_used)
    await update_task(task_id, "done", response, tokens_used)

    # Показываем текстовый ответ
    await status_msg.edit_text(
        f"🎤 *Голос:* _{recognized_text}_\n\n"
        f"🤖 *Ответ:*\n{response[:2000]}\n\n"
        f"📊 Токенов: {tokens_used:,}",
        parse_mode="Markdown"
    )

    # Опционально: отправляем голосовой ответ
    tts_path = await text_to_speech(response)
    if tts_path and Path(tts_path).exists():
        try:
            await message.answer_voice(FSInputFile(tts_path))
        except Exception as e:
            logger.warning(f"Не могу отправить голосовой ответ: {e}")
        finally:
            Path(tts_path).unlink(missing_ok=True)
