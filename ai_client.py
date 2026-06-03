"""
AI клиент с несколькими прямыми провайдерами:
  1. Gemini  — Google AI Studio (прямой, бесплатный)
  2. Groq    — быстрый Llama (прямой, бесплатный)
  3. Cerebras — сверхбыстрый Llama (прямой, бесплатный)
  4. DeepSeek — умный и дешёвый (прямой)
  5. OpenRouter — резерв когда всё остальное упало
"""

import aiohttp
import base64
from pathlib import Path
from loguru import logger
from config import OPENROUTER_API_KEY, GROQ_API_KEY, GEMINI_API_KEY, CEREBRAS_API_KEY, DEEPSEEK_API_KEY, MAX_CONTEXT_SIZE

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
CEREBRAS_URL = "https://api.cerebras.ai/v1/chat/completions"
DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"

AVAILABLE_MODELS = {
    # === ПРЯМЫЕ ПРОВАЙДЕРЫ (приоритет) ===
    "gemini": {
        "id": "gemini-2.0-flash",
        "name": "Gemini 2.0 Flash",
        "emoji": "✨",
        "provider": "gemini",
    },
    "gemini-pro": {
        "id": "gemini-1.5-pro",
        "name": "Gemini 1.5 Pro",
        "emoji": "🌟",
        "provider": "gemini",
    },
    "cerebras": {
        "id": "llama-3.3-70b",
        "name": "Llama 3.3 70B (Cerebras)",
        "emoji": "⚡",
        "provider": "cerebras",
    },
    "deepseek-direct": {
        "id": "deepseek-chat",
        "name": "DeepSeek V3 (прямой)",
        "emoji": "🔵",
        "provider": "deepseek",
    },
    "llama": {
        "id": "llama-3.3-70b-versatile",
        "name": "Llama 3.3 70B (Groq)",
        "emoji": "🦙",
        "provider": "groq",
    },
    "llama-fast": {
        "id": "llama-3.1-8b-instant",
        "name": "Llama 3.1 8B (Groq)",
        "emoji": "🐇",
        "provider": "groq",
    },
    # === OPENROUTER (резерв) ===
    "gpt": {
        "id": "openai/gpt-oss-120b:free",
        "name": "GPT OSS 120B",
        "emoji": "🟢",
        "provider": "openrouter",
    },
    "qwen": {
        "id": "qwen/qwen3-235b-a22b:free",
        "name": "Qwen3 235B",
        "emoji": "🟣",
        "provider": "openrouter",
    },
    "deepseek": {
        "id": "deepseek/deepseek-chat:free",
        "name": "DeepSeek V3 (OR)",
        "emoji": "🔵",
        "provider": "openrouter",
    },
    "gemma": {
        "id": "google/gemma-4-31b-it:free",
        "name": "Gemma 4 31B",
        "emoji": "♊",
        "provider": "openrouter",
    },
    "nvidia": {
        "id": "nvidia/nemotron-3-super-120b-a12b:free",
        "name": "Nemotron 120B",
        "emoji": "🟦",
        "provider": "openrouter",
    },
    "claude": {
        "id": "anthropic/claude-3.5-haiku",
        "name": "Claude 3.5 Haiku",
        "emoji": "🔶",
        "provider": "openrouter",
    },
    "chatgpt": {
        "id": "openai/gpt-4o-mini",
        "name": "ChatGPT 4o mini",
        "emoji": "🤖",
        "provider": "openrouter",
    },
}

# Прямые сначала → OpenRouter в конце как резерв
FALLBACK_ORDER = [
    "gemini", "cerebras", "llama", "deepseek-direct",
    "llama-fast", "gpt", "qwen", "deepseek", "gemma", "nvidia",
]

_user_models: dict[int, str] = {}


def get_user_model(user_id: int) -> str:
    return _user_models.get(user_id, "gpt")


def set_user_model(user_id: int, model_key: str):
    if model_key in AVAILABLE_MODELS:
        _user_models[user_id] = model_key


def get_model_info(model_key: str) -> dict:
    return AVAILABLE_MODELS.get(model_key, AVAILABLE_MODELS["gpt"])


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
            timeout=aiohttp.ClientTimeout(total=25)
        ) as resp:
            data = await resp.json()
            if resp.status == 429:
                raise RateLimitError(f"Rate limit на {model_id}")
            if resp.status != 200:
                error = data.get("error", {}).get("message", str(data))
                raise Exception(f"OpenRouter {resp.status}: {error}")
            choices = data.get("choices") or []
            if not choices:
                raise Exception(f"Пустой ответ от {model_id}")
            answer = choices[0].get("message", {}).get("content", "")
            if not answer:
                raise Exception(f"Пустой content от {model_id}")
            tokens = data.get("usage", {}).get("total_tokens", 0)
            return answer, tokens


async def _call_gemini(model_id: str, messages: list, system_prompt: str = None) -> tuple[str, int]:
    if not GEMINI_API_KEY:
        raise Exception("GEMINI_API_KEY не задан")

    msgs = []
    if system_prompt:
        msgs.append({"role": "system", "content": system_prompt})
    msgs.extend(messages)

    headers = {
        "Authorization": f"Bearer {GEMINI_API_KEY}",
        "Content-Type": "application/json",
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(
            GEMINI_URL,
            json={"model": model_id, "messages": msgs, "max_tokens": 4096},
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=25)
        ) as resp:
            data = await resp.json()
            if resp.status == 429:
                raise RateLimitError(f"Gemini rate limit на {model_id}")
            if resp.status != 200:
                error = data.get("error", {}).get("message", str(data))
                raise Exception(f"Gemini {resp.status}: {error}")
            choices = data.get("choices") or []
            if not choices:
                raise Exception(f"Пустой ответ от Gemini {model_id}")
            answer = choices[0].get("message", {}).get("content", "")
            if not answer:
                raise Exception(f"Пустой content от Gemini {model_id}")
            tokens = data.get("usage", {}).get("total_tokens", 0)
            return answer, tokens


async def _call_cerebras(model_id: str, messages: list, system_prompt: str = None) -> tuple[str, int]:
    if not CEREBRAS_API_KEY:
        raise Exception("CEREBRAS_API_KEY не задан")

    msgs = []
    if system_prompt:
        msgs.append({"role": "system", "content": system_prompt})
    msgs.extend(messages)

    headers = {
        "Authorization": f"Bearer {CEREBRAS_API_KEY}",
        "Content-Type": "application/json",
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(
            CEREBRAS_URL,
            json={"model": model_id, "messages": msgs, "max_tokens": 4096},
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=20)
        ) as resp:
            data = await resp.json()
            if resp.status == 429:
                raise RateLimitError(f"Cerebras rate limit на {model_id}")
            if resp.status != 200:
                error = data.get("error", {}).get("message", str(data))
                raise Exception(f"Cerebras {resp.status}: {error}")
            choices = data.get("choices") or []
            if not choices:
                raise Exception(f"Пустой ответ от Cerebras {model_id}")
            answer = choices[0].get("message", {}).get("content", "")
            if not answer:
                raise Exception(f"Пустой content от Cerebras {model_id}")
            tokens = data.get("usage", {}).get("total_tokens", 0)
            return answer, tokens


async def _call_deepseek(model_id: str, messages: list, system_prompt: str = None) -> tuple[str, int]:
    if not DEEPSEEK_API_KEY:
        raise Exception("DEEPSEEK_API_KEY не задан")

    msgs = []
    if system_prompt:
        msgs.append({"role": "system", "content": system_prompt})
    msgs.extend(messages)

    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(
            DEEPSEEK_URL,
            json={"model": model_id, "messages": msgs, "max_tokens": 4096},
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=30)
        ) as resp:
            data = await resp.json()
            if resp.status == 429:
                raise RateLimitError(f"DeepSeek rate limit на {model_id}")
            if resp.status != 200:
                error = data.get("error", {}).get("message", str(data))
                raise Exception(f"DeepSeek {resp.status}: {error}")
            choices = data.get("choices") or []
            if not choices:
                raise Exception(f"Пустой ответ от DeepSeek {model_id}")
            answer = choices[0].get("message", {}).get("content", "")
            if not answer:
                raise Exception(f"Пустой content от DeepSeek {model_id}")
            tokens = data.get("usage", {}).get("total_tokens", 0)
            return answer, tokens


async def _call_groq(model_id: str, messages: list, system_prompt: str = None) -> tuple[str, int]:
    if not GROQ_API_KEY:
        raise Exception("GROQ_API_KEY не задан")

    msgs = []
    if system_prompt:
        msgs.append({"role": "system", "content": system_prompt})
    msgs.extend(messages)

    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json",
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(
            GROQ_URL,
            json={"model": model_id, "messages": msgs, "max_tokens": 4096},
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=30)
        ) as resp:
            data = await resp.json()
            if resp.status == 429:
                raise RateLimitError(f"Groq rate limit на {model_id}")
            if resp.status != 200:
                error = data.get("error", {}).get("message", str(data))
                raise Exception(f"Groq {resp.status}: {error}")
            choices = data.get("choices") or []
            if not choices:
                raise Exception(f"Пустой ответ от Groq {model_id}")
            answer = choices[0].get("message", {}).get("content", "")
            if not answer:
                raise Exception(f"Пустой content от Groq {model_id}")
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
            if model["provider"] == "gemini":
                answer, tokens = await _call_gemini(model["id"], messages, system_prompt)
            elif model["provider"] == "cerebras":
                answer, tokens = await _call_cerebras(model["id"], messages, system_prompt)
            elif model["provider"] == "deepseek":
                answer, tokens = await _call_deepseek(model["id"], messages, system_prompt)
            elif model["provider"] == "groq":
                answer, tokens = await _call_groq(model["id"], messages, system_prompt)
            else:
                answer, tokens = await _call_openrouter(model["id"], messages, system_prompt)

            if model_key != preferred:
                answer = f"[{model['emoji']} {model['name']}]\n\n{answer}"
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
