"""
Работа с голосом:
- Голос → Текст (Whisper)
- Текст → Голос (gTTS)
"""

import asyncio
import os
from pathlib import Path
from loguru import logger
from config import TEMP_DIR


async def speech_to_text(audio_path: str) -> str:
    """
    Преобразует голосовое сообщение в текст с помощью Whisper.
    Возвращает распознанный текст.
    """
    try:
        import whisper
    except ImportError:
        logger.warning("openai-whisper не установлен")
        return ""

    try:
        logger.info(f"🎤 Распознаю голос: {audio_path}")

        # Запускаем Whisper в отдельном потоке (CPU-intensive)
        loop = asyncio.get_event_loop()
        text = await loop.run_in_executor(None, _run_whisper, audio_path)

        logger.info(f"✅ Распознано: {text[:50]}...")
        return text

    except Exception as e:
        logger.error(f"Ошибка распознавания голоса: {e}")
        return ""


def _run_whisper(audio_path: str) -> str:
    """Синхронная функция для запуска Whisper (запускается в executor)."""
    import whisper

    # Загружаем модель (small — компромисс скорость/качество)
    # При первом запуске скачает модель (~460 MB)
    model = whisper.load_model("small")
    result = model.transcribe(audio_path, language="ru")
    return result["text"].strip()


async def text_to_speech(text: str, output_path: str = None) -> str | None:
    """
    Преобразует текст в голосовое сообщение с помощью gTTS.
    Возвращает путь к аудио файлу или None при ошибке.
    """
    try:
        from gtts import gTTS
    except ImportError:
        logger.warning("gTTS не установлен, TTS недоступен")
        return None

    if output_path is None:
        output_path = str(TEMP_DIR / "tts_response.mp3")

    try:
        # Ограничиваем длину текста (TTS не любит очень длинные тексты)
        text_for_tts = text[:1000]

        # Запускаем в executor (блокирующий I/O)
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _run_gtts, text_for_tts, output_path)

        logger.info(f"🔊 TTS готов: {output_path}")
        return output_path

    except Exception as e:
        logger.error(f"Ошибка TTS: {e}")
        return None


def _run_gtts(text: str, output_path: str):
    """Синхронная функция gTTS."""
    from gtts import gTTS
    tts = gTTS(text=text, lang="ru", slow=False)
    tts.save(output_path)


async def convert_ogg_to_wav(ogg_path: str) -> str | None:
    """
    Конвертирует .ogg (Telegram voice) в .wav для Whisper.
    Требует ffmpeg.
    """
    wav_path = ogg_path.replace(".ogg", ".wav")

    try:
        # Запускаем ffmpeg
        process = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-i", ogg_path, wav_path,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await process.wait()

        if process.returncode == 0 and Path(wav_path).exists():
            return wav_path
        else:
            logger.error("ffmpeg не смог конвертировать файл")
            return None

    except FileNotFoundError:
        logger.error("ffmpeg не установлен! Голосовые сообщения не работают.")
        return None
    except Exception as e:
        logger.error(f"Ошибка конвертации: {e}")
        return None
