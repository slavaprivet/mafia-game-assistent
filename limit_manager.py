"""
Менеджер лимитов токенов — следит за расходом и предупреждает.
"""

from loguru import logger
from config import DAILY_TOKEN_LIMIT, TOKEN_WARN_LEVELS
from memory import get_today_tokens, add_tokens


async def track_usage(user_id: int, tokens_used: int) -> str | None:
    """
    Записывает использованные токены и возвращает предупреждение
    если приближаемся к лимиту. Возвращает None если всё в порядке.
    """
    if tokens_used <= 0:
        return None

    # Сохраняем в базу
    await add_tokens(user_id, tokens_used)

    # Проверяем лимит
    if DAILY_TOKEN_LIMIT <= 0:
        return None  # Лимит отключён

    total_today, _ = await get_today_tokens(user_id)
    percent = (total_today / DAILY_TOKEN_LIMIT) * 100

    # Проверяем уровни предупреждений
    for level in sorted(TOKEN_WARN_LEVELS, reverse=True):
        if percent >= level:
            remaining = DAILY_TOKEN_LIMIT - total_today
            if level == 100:
                return (
                    f"🚫 *Дневной лимит исчерпан!*\n"
                    f"Использовано: {total_today:,} / {DAILY_TOKEN_LIMIT:,} токенов\n"
                    f"Лимит сбрасывается в 00:00 МСК"
                )
            else:
                return (
                    f"⚠️ *Использовано {level}% токенов на сегодня*\n"
                    f"Осталось: {remaining:,} токенов"
                )

    return None


async def check_limit(user_id: int) -> tuple[bool, str]:
    """
    Проверяет можно ли выполнить запрос (не превышен ли лимит).
    Возвращает (можно_ли, сообщение).
    """
    if DAILY_TOKEN_LIMIT <= 0:
        return True, ""

    total_today, _ = await get_today_tokens(user_id)
    if total_today >= DAILY_TOKEN_LIMIT:
        return False, (
            f"🚫 Дневной лимит {DAILY_TOKEN_LIMIT:,} токенов исчерпан.\n"
            f"Сброс в 00:00 МСК."
        )
    return True, ""


async def get_limit_status(user_id: int) -> str:
    """Возвращает статус лимитов для команды /limits."""
    total_today, requests_today = await get_today_tokens(user_id)

    if DAILY_TOKEN_LIMIT <= 0:
        return (
            f"📊 *Лимиты токенов*\n\n"
            f"Сегодня использовано: {total_today:,} токенов\n"
            f"Запросов: {requests_today}\n"
            f"Дневной лимит: ∞ (не задан)"
        )

    remaining = max(0, DAILY_TOKEN_LIMIT - total_today)
    percent = min(100, (total_today / DAILY_TOKEN_LIMIT) * 100)

    # Прогресс бар
    filled = int(percent / 10)
    bar = "█" * filled + "░" * (10 - filled)

    return (
        f"📊 *Лимиты токенов*\n\n"
        f"`{bar}` {percent:.1f}%\n\n"
        f"Использовано сегодня: {total_today:,}\n"
        f"Осталось: {remaining:,}\n"
        f"Лимит в день: {DAILY_TOKEN_LIMIT:,}\n"
        f"Запросов сегодня: {requests_today}"
    )
