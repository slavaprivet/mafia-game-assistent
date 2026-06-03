"""
Точка входа — запускает Telegram бота.
Запуск: python main.py
"""

import asyncio
import sys
from pathlib import Path
from loguru import logger

sys.path.insert(0, str(Path(__file__).parent))

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from config import BOT_TOKEN, HOURLY_REPORTS, ALLOWED_USERS, validate_config
from memory import init_db


async def check_reminders(bot: Bot):
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
        await asyncio.sleep(60)


async def hourly_report(bot: Bot, user_ids: list):
    from memory import get_stats
    await asyncio.sleep(3600)
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
        await asyncio.sleep(3600)


async def main():
    validate_config()
    await init_db()

    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )
    dp = Dispatcher()

    from handlers import commands, text_tasks, media_tasks, callbacks
    dp.include_router(commands.router)
    dp.include_router(text_tasks.router)
    dp.include_router(media_tasks.router)
    dp.include_router(callbacks.router)

    asyncio.create_task(check_reminders(bot))
    if HOURLY_REPORTS and ALLOWED_USERS:
        asyncio.create_task(hourly_report(bot, ALLOWED_USERS))

    # Автоматически индексируем игру при старте
    try:
        from game_expert import index_game
        logger.info("📚 Индексирую игру с GitHub...")
        index = await index_game()
        if index.get("error"):
            logger.warning(f"Индекс не загружен: {index['error']}")
        else:
            logger.info(f"✅ Игра проиндексирована: {index['file_count']} файлов, {index['total_lines']} строк")
    except Exception as e:
        logger.error(f"Ошибка автоиндексации: {e}")

    me = await bot.get_me()
    logger.info(f"✅ Бот @{me.username} запущен!")

    # Уведомляем владельца о запуске
    for uid in ALLOWED_USERS:
        try:
            await bot.send_message(
                uid,
                "🚀 <b>Бот запущен на Railway</b>\n\n"
                "Если получил это сообщение дважды — у тебя запущена локальная копия. "
                "Останови её, иначе бот работать не будет."
            )
        except Exception:
            pass

    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    except Exception as e:
        if "Conflict" in str(e):
            for uid in ALLOWED_USERS:
                try:
                    await bot.send_message(uid, "⚠️ <b>Конфликт!</b> Бот запущен в двух местах одновременно. Останови локальную копию.")
                except Exception:
                    pass
        raise
    finally:
        await bot.session.close()
        logger.info("👋 Бот остановлен")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 Бот остановлен")
