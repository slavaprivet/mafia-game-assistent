"""
Точка входа — запускает Telegram бота.
Запуск: python main.py
"""

import asyncio
import sys
from pathlib import Path
from loguru import logger

# Добавляем корневую папку в путь (для импортов)
sys.path.insert(0, str(Path(__file__).parent))

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from config import BOT_TOKEN, HOURLY_REPORTS, validate_config
from memory import init_db
from game_expert import index_game, load_index


async def check_reminders(bot: Bot):
    """Фоновая задача: проверяет напоминания каждую минуту."""
    from memory import get_pending_reminders, mark_reminder_done

    while True:
        try:
            reminders = await get_pending_reminders()
            for reminder in reminders:
                try:
                    await bot.send_message(
                        reminder["user_id"],
                        f"⏰ *Напоминание:*\n{reminder['message']}",
                        parse_mode="Markdown"
                    )
                    await mark_reminder_done(reminder["id"])
                except Exception as e:
                    logger.error(f"Не могу отправить напоминание {reminder['id']}: {e}")
        except Exception as e:
            logger.error(f"Ошибка проверки напоминаний: {e}")

        await asyncio.sleep(60)  # Проверяем каждую минуту


async def hourly_report(bot: Bot, user_ids: list[int]):
    """Фоновая задача: ежечасный отчёт."""
    from memory import get_stats

    await asyncio.sleep(3600)  # Первый отчёт через час после запуска

    while True:
        try:
            for user_id in user_ids:
                stats = await get_stats(user_id)
                if stats["today_requests"] > 0:
                    await bot.send_message(
                        user_id,
                        f"📊 *Отчёт за час:*\n"
                        f"Запросов сегодня: {stats['today_requests']}\n"
                        f"Токенов сегодня: {stats['today_tokens']:,}\n"
                        f"Изменений кода: {stats['code_changes']}",
                        parse_mode="Markdown"
                    )
        except Exception as e:
            logger.error(f"Ошибка отправки отчёта: {e}")

        await asyncio.sleep(3600)  # Каждый час


async def main():
    """Основная функция запуска бота."""

    # ── Запускаем фоновые задачи ───────────────────────────────────────────
    from config import ALLOWED_USERS
    asyncio.create_task(check_reminders(bot))
    if HOURLY_REPORTS and ALLOWED_USERS:
        asyncio.create_task(hourly_report(bot, ALLOWED_USERS))

    # ── Старт! ─────────────────────────────────────────────────────────────
    me = await bot.get_me()
    logger.info(f"✅ Бот @{me.username} запущен! Жду сообщений...")
    print(f"\n✅ Бот @{me.username} запущен!")
    print(f"   Telegram: https://t.me/{me.username}")
    print(f"   Нажми Ctrl+C для остановки\n")

    # Запускаем polling
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await bot.session.close()
        logger.info("👋 Бот остановлен")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 Бот остановлен")
