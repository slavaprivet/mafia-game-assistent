"""
Клиент для работы с AI через OpenRouter.
Поддерживает несколько моделей с автоматическим fallback при лимите.
"""

import aiohttp
import base64
from pathlib import Path
from loguru import logger
from config import OPENROUTER_API_KEY, MAX_CONTEXT_SIZE

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

AVAILABLE_MODELS = {
    "deepseek": {
        "id": "deepseek/deepseek-chat-v3-0324:free",
        "name": "DeepSeek V3",
        "emoji": "🔵",
    },
    "llama": {
        "id": "meta-llama/llama-3.3-70b-instruct:free",
        "name": "Llama 3.3 70B",
        "emoji": "🦙",
    },
    "gemini": {
        "id": "google/gemini-flash-1.5:free",
        "name": "Gemini Flash",
        "emoji": "♊",
    },
    "mistral": {
        "id": "mistralai/mistral-7b-instruct:free",
        "name": "Mistral 7B",
        "emoji": "🌬",
    },
}

FALLBACK_ORDER = ["deepseek", "llama", "gemini", "mistral"]

_user_models: dict[int, str] = {}


def get_user_model(user_id: int) -> str:
    return _user_models.get(user_id, "deepseek")


def set_user_model(user_id: int, model_key: str):
    if model_key in AVAILABLE_MODELS:
        _user_models[user_id] = model_key


def get_model_info(model_key: str) -> dict:
    return AVAILABLE_MODELS.get(model_key, AVAILABLE_MODELS["deepseek"])


class RateLimitError(Exception):
    pass


async def _call_openrouter(model_id: str, messages: list, system_prompt: str = None) -> tuple[str, int]:
    msgs = []
    if system_prompt:
        msgs.append({"role": "system", "content": system_prompt})
    msgs.extend(messages)

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/slavaprivet/mafia-game-assistent",
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(
            OPENROUTER_URL,
            json={"model": model_id, "messages": msgs, "max_tokens": 4096},
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=60)
        ) as resp:
            data = await resp.json()
            if resp.status == 429:
                raise RateLimitError(f"Rate limit на {model_id}")
            if resp.status != 200:
                error = data.get("error", {}).get("message", str(data))
                raise Exception(f"OpenRouter {resp.status}: {error}")
            answer = data["choices"][0]["message"]["content"]
            tokens = data.get("usage", {}).get("total_tokens", 0)
            return answer, tokens


async def ask_code_model(
    prompt: str,
    system_prompt: str = None,
    conversation_history: list = None,
    user_id: int = 0,
) -> tuple[str, int]:
    messages = []
    if conversation_history:
        messages.extend(conversation_history[-10:])
    messages.append({"role": "user", "content": prompt[:MAX_CONTEXT_SIZE]})

    preferred = get_user_model(user_id)
    order = [preferred] + [m for m in FALLBACK_ORDER if m != preferred]

    last_error = None
    for model_key in order:
        model = AVAILABLE_MODELS[model_key]
        try:
            logger.debug(f"Пробую {model['name']} для user {user_id}")
            answer, tokens = await _call_openrouter(model["id"], messages, system_prompt)
            if model_key != preferred:
                answer = f"[{model['emoji']} Переключился на {model['name']} — лимит основной]\n\n{answer}"
            return answer, tokens
        except RateLimitError as e:
            logger.warning(f"Лимит на {model['name']}, следующая...")
            last_error = e
        except Exception as e:
            logger.error(f"Ошибка {model['name']}: {e}")
            last_error = e

    return f"❌ Все модели недоступны: {last_error}", 0


async def ask_vision_model(image_path: str, prompt: str) -> tuple[str, int]:
    try:
        image_bytes = Path(image_path).read_bytes()
        image_b64 = base64.standard_b64encode(image_bytes).decode()
        ext = Path(image_path).suffix.lower()
        mime_map = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}
        media_type = mime_map.get(ext, "image/jpeg")
    except Exception as e:
        return f"❌ Ошибка чтения изображения: {e}", 0

    messages = [{"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": f"data:{media_type};base64,{image_b64}"}},
        {"type": "text", "text": prompt},
    ]}]

    try:
        return await _call_openrouter("google/gemini-flash-1.5", messages)
    except Exception as e:
        return f"❌ Ошибка анализа изображения: {e}", 0


def count_tokens_approx(text: str) -> int:
    return len(text) // 4
