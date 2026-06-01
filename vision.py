"""
Анализ изображений и видео.
OCR (распознавание текста) + Vision AI (понимание что на картинке).
"""

import os
import asyncio
from pathlib import Path
from loguru import logger
from config import TEMP_DIR


async def analyze_screenshot(image_path: str) -> dict:
    """
    Анализирует скриншот:
    1. OCR — извлекает текст (ошибки, сообщения)
    2. Vision AI — понимает что происходит на экране

    Возвращает словарь с результатами.
    """
    result = {
        "ocr_text": "",       # Текст найденный на изображении
        "vision_desc": "",    # Описание от AI
        "errors_found": [],   # Найденные ошибки
        "tokens_used": 0,
    }

    # Шаг 1: OCR
    ocr_text = await _run_ocr(image_path)
    result["ocr_text"] = ocr_text

    # Извлекаем строки с ошибками
    result["errors_found"] = _extract_errors(ocr_text)

    # Шаг 2: Vision AI (Ollama llava)
    from ai_client import ask_vision_model

    vision_prompt = (
        "Ты анализируешь скриншот игры или ошибки в коде. "
        "Опиши: 1) что видишь на экране, 2) есть ли ошибки или баги, "
        "3) в чём может быть проблема. Отвечай на русском языке."
    )

    vision_desc, tokens = await ask_vision_model(image_path, vision_prompt)
    result["vision_desc"] = vision_desc
    result["tokens_used"] = tokens

    return result


async def _run_ocr(image_path: str) -> str:
    """Запускает Tesseract OCR для извлечения текста из изображения."""
    try:
        import pytesseract
        from PIL import Image

        img = Image.open(image_path)

        # Пробуем распознать на нескольких языках
        text = pytesseract.image_to_string(img, lang="rus+eng")
        return text.strip()

    except ImportError:
        logger.warning("pytesseract не установлен, OCR недоступен")
        return ""
    except Exception as e:
        logger.warning(f"OCR ошибка: {e}")
        return ""


def _extract_errors(text: str) -> list[str]:
    """
    Ищет строки похожие на ошибки в тексте (трейсбэки, Exception и т.д.)
    """
    error_keywords = [
        "error", "exception", "traceback", "failed", "crash",
        "null", "undefined", "cannot", "invalid",
        "ошибка", "исключение", "сбой",
    ]

    errors = []
    for line in text.splitlines():
        line_lower = line.lower()
        if any(kw in line_lower for kw in error_keywords):
            stripped = line.strip()
            if len(stripped) > 5:  # Не пустые строки
                errors.append(stripped)

    return errors[:10]  # Максимум 10 ошибок


async def extract_video_frames(video_path: str, interval_sec: int = 2) -> list[str]:
    """
    Извлекает кадры из видео каждые N секунд.
    Возвращает список путей к изображениям.
    """
    try:
        import cv2
    except ImportError:
        logger.warning("opencv не установлен, анализ видео недоступен")
        return []

    frame_paths = []
    video_name = Path(video_path).stem

    try:
        cap = cv2.VideoCapture(video_path)
        fps = cap.get(cv2.CAP_PROP_FPS)
        frame_interval = int(fps * interval_sec)

        frame_num = 0
        saved = 0

        while cap.isOpened():
            ret, frame = cap.read()
            if not ret:
                break

            if frame_num % frame_interval == 0:
                frame_path = str(TEMP_DIR / f"{video_name}_frame_{saved:04d}.jpg")
                cv2.imwrite(frame_path, frame)
                frame_paths.append(frame_path)
                saved += 1

                # Максимум 10 кадров
                if saved >= 10:
                    break

            frame_num += 1

        cap.release()
        logger.info(f"📹 Извлечено {len(frame_paths)} кадров из видео")

    except Exception as e:
        logger.error(f"Ошибка извлечения кадров: {e}")

    return frame_paths


async def analyze_video(video_path: str) -> dict:
    """
    Анализирует видео с ошибкой/багом:
    1. Извлекает ключевые кадры
    2. Анализирует каждый кадр
    3. Ищет момент проблемы

    Возвращает результаты анализа.
    """
    result = {
        "frames_analyzed": 0,
        "problem_frame": None,
        "description": "",
        "errors_found": [],
        "tokens_used": 0,
    }

    # Извлекаем кадры
    frames = await extract_video_frames(video_path)
    if not frames:
        result["description"] = "Не удалось извлечь кадры из видео"
        return result

    result["frames_analyzed"] = len(frames)

    # Анализируем каждый кадр
    from ai_client import ask_vision_model

    frame_descriptions = []
    error_frame = None
    total_tokens = 0

    for i, frame_path in enumerate(frames):
        prompt = (
            f"Кадр {i+1}/{len(frames)} из видео с багом игры. "
            "Есть ли на этом кадре ошибка, краш или явный баг? "
            "Одним предложением: что видишь?"
        )

        desc, tokens = await ask_vision_model(frame_path, prompt)
        total_tokens += tokens
        frame_descriptions.append(f"Кадр {i+1}: {desc}")

        # Ищем кадр с проблемой
        if any(kw in desc.lower() for kw in ["ошибка", "баг", "краш", "error", "crash", "fail"]):
            if error_frame is None:
                error_frame = (i + 1, desc)

    result["tokens_used"] = total_tokens
    result["problem_frame"] = error_frame

    # Итоговый анализ
    summary_prompt = (
        "Вот описание кадров из видео с багом игры:\n\n"
        + "\n".join(frame_descriptions)
        + "\n\nОпиши: 1) что происходит в видео, 2) в какой момент возникает баг, 3) в чём причина."
    )

    summary, tokens = await ask_vision_model(frames[0], summary_prompt)
    result["description"] = summary
    result["tokens_used"] += tokens

    # Удаляем временные кадры
    for frame_path in frames:
        Path(frame_path).unlink(missing_ok=True)

    return result


def format_screenshot_result(analysis: dict) -> str:
    """Форматирует результат анализа скриншота для показа в Telegram."""
    lines = ["📸 *Анализ скриншота:*\n"]

    if analysis.get("errors_found"):
        lines.append("🔴 *Найденные ошибки:*")
        for err in analysis["errors_found"][:5]:
            lines.append(f"```\n{err}\n```")

    if analysis.get("ocr_text"):
        # Показываем только первые 200 символов текста
        ocr_preview = analysis["ocr_text"][:200]
        if len(analysis["ocr_text"]) > 200:
            ocr_preview += "..."
        lines.append(f"📝 *Текст на экране:*\n```\n{ocr_preview}\n```")

    if analysis.get("vision_desc"):
        lines.append(f"🤖 *AI видит:*\n{analysis['vision_desc']}")

    return "\n\n".join(lines)
