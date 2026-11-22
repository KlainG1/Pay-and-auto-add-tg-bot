from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand

from bot.config import settings
from bot.db.base import init_db
from bot.db.seed import sync_plans
from bot.handlers import admin, channel, purchase, user
from bot.logging_setup import setup_logging
from bot.notify import notify_admins
from bot.scheduler.setup import build_scheduler
from bot.services.channel import channel_is_ready

logger = logging.getLogger(__name__)


async def set_commands(bot: Bot) -> None:
    await bot.set_my_commands(
        [
            BotCommand(command="start", description="Начать"),
            BotCommand(command="buy", description="Купить подписку"),
            BotCommand(command="sub", description="Моя подписка"),
            BotCommand(command="help", description="Помощь"),
            BotCommand(command="myid", description="Узнать свой Telegram ID"),
        ]
    )


async def main() -> None:
    setup_logging(settings.log_level)

    problems = settings.validate_startup()
    for problem in problems:
        logger.warning("Конфигурация: %s", problem)
    if not settings.bot_token:
        raise SystemExit("BOT_TOKEN не задан. Заполните .env (см. .env.example).")

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()

    dp.include_router(admin.router)
    dp.include_router(purchase.router)
    dp.include_router(channel.router)
    dp.include_router(user.router)

    await init_db()
    await sync_plans()

    scheduler = None
    try:
        await set_commands(bot)

        ok, detail = await channel_is_ready(bot)
        if not ok:
            logger.error("Канал не готов: %s", detail)
            await notify_admins(bot, f"⚠️ Бот запущен, но канал не готов:\n{detail}")
        else:
            logger.info("Канал готов к работе")

        scheduler = build_scheduler(bot)
        scheduler.start()

        logger.info("Бот запускается (polling)")
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        if scheduler is not None:
            scheduler.shutdown(wait=False)
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit) as exc:
        if isinstance(exc, SystemExit) and exc.code:
            print(exc.code)
