"""
Работа с голосом:
- Голос → Текст через Groq Whisper API (поддерживает .ogg напрямую)
- Текст → Голос через gTTS
"""

import asyncio
from pathlib import Path
from loguru import logger

import aiohttp

from config import GROQ_API_KEY, TEMP_DIR

GROQ_TRANSCRIPTION_URL = "https://api.groq.com/openai/v1/audio/transcriptions"


async def speech_to_text(audio_path: str) -> str:
    """
    Преобразует голосовое сообщение в текст через Groq Whisper API.
    Принимает .ogg напрямую — ffmpeg не нужен.
    """
    if not GROQ_API_KEY:
        logger.warning("GROQ_API_KEY не задан — голос недоступен")
        return ""

    try:
        logger.info(f"🎤 Отправляю в Groq Whisper: {audio_path}")
        audio_bytes = Path(audio_path).read_bytes()
        filename = Path(audio_path).name

        form = aiohttp.FormData()
        form.add_field("file", audio_bytes, filename=filename, content_type="audio/ogg")
        form.add_field("model", "whisper-large-v3-turbo")
        form.add_field("language", "ru")
        form.add_field("response_format", "text")

        async with aiohttp.ClientSession() as session:
            async with session.post(
                GROQ_TRANSCRIPTION_URL,
                headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
                data=form,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status == 200:
                    text = (await resp.text()).strip()
                    logger.info(f"✅ Распознано: {text[:80]}")
                    return text
                else:
                    body = await resp.text()
                    logger.error(f"Groq Whisper error {resp.status}: {body}")
                    return ""

    except Exception as e:
        logger.error(f"Ошибка распознавания голоса: {e}")
        return ""


async def text_to_speech(text: str, output_path: str = None) -> str | None:
    """
    Преобразует текст в голосовое сообщение с помощью gTTS.
    Возвращает путь к mp3 или None при ошибке.
    """
    try:
        from gtts import gTTS
    except ImportError:
        logger.warning("gTTS не установлен — TTS недоступен")
        return None

    if output_path is None:
        output_path = str(TEMP_DIR / "tts_response.mp3")

    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _run_gtts, text[:1000], output_path)
        logger.info(f"🔊 TTS готов: {output_path}")
        return output_path
    except Exception as e:
        logger.error(f"Ошибка TTS: {e}")
        return None


def _run_gtts(text: str, output_path: str):
    from gtts import gTTS
    gTTS(text=text, lang="ru", slow=False).save(output_path)
