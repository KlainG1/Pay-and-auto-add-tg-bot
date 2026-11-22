from __future__ import annotations

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from aiogram import Bot

from bot.config import settings
from bot.scheduler.jobs import expire_subscriptions, send_reminders

logger = logging.getLogger(__name__)


def build_scheduler(bot: Bot) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=settings.timezone)

    async def _expire() -> None:
        try:
            await expire_subscriptions(bot)
        except Exception:
            logger.exception("Ошибка задачи expire_subscriptions")

    async def _remind() -> None:
        try:
            await send_reminders(bot)
        except Exception:
            logger.exception("Ошибка задачи send_reminders")

    scheduler.add_job(
        _expire,
        trigger=IntervalTrigger(minutes=5),
        id="expire_subscriptions",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        _remind,
        trigger=IntervalTrigger(hours=1),
        id="send_reminders",
        max_instances=1,
        coalesce=True,
    )
    return scheduler
