from groq import AsyncGroq
import base64
from pathlib import Path
from loguru import logger
from config import GROQ_API_KEY, CODE_MODEL, MAX_CONTEXT_SIZE

_client = None

def get_client():
    global _client
    if _client is None:
        _client = AsyncGroq(api_key=GROQ_API_KEY)
    return _client


async def ask_code_model(
    prompt: str,
    system_prompt: str = None,
    conversation_history: list[dict] = None,
) -> tuple[str, int]:
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    if conversation_history:
        messages.extend(conversation_history[-10:])
    messages.append({"role": "user", "content": prompt[:MAX_CONTEXT_SIZE]})

    try:
        response = await get_client().chat.completions.create(
            model=CODE_MODEL,
            messages=messages,
            max_tokens=4096,
            temperature=0.3,
        )
        answer = response.choices[0].message.content
        tokens = response.usage.total_tokens if response.usage else len(answer)//4
        logger.debug(f"Groq ответил, токенов: {tokens}")
        return answer, tokens
    except Exception as e:
        logger.error(f"Ошибка Groq API: {e}")
        return f"Ошибка: {str(e)}", 0


async def ask_vision_model(image_path: str, prompt: str) -> tuple[str, int]:
    """Groq vision через llama с изображением."""
    try:
        img_bytes = Path(image_path).read_bytes()
        img_b64 = base64.standard_b64encode(img_bytes).decode()
        ext = Path(image_path).suffix.lower()
        mime = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}.get(ext, "image/jpeg")

        response = await get_client().chat.completions.create(
            model="meta-llama/llama-4-scout-17b-16e-instruct",
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{img_b64}"}},
                ],
            }],
            max_tokens=1024,
        )
        answer = response.choices[0].message.content
        tokens = response.usage.total_tokens if response.usage else len(answer)//4
        return answer, tokens
    except Exception as e:
        logger.error(f"Ошибка vision: {e}")
        return f"Ошибка анализа изображения: {str(e)}", 0


def count_tokens_approx(text: str) -> int:
    return len(text) // 4
