from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

from bot.config import settings
from bot.notify import notify_admins
from bot.services.channel import is_member, kick_user, revoke_invites
from bot.services.messaging import expired_text, reminder_text
from bot.services.subscriptions import (
    days_left_ceil,
    due_subscriptions,
    mark_expired,
    mark_reminded,
    subscriptions_for_reminders,
)
from bot.services.users import log_action

logger = logging.getLogger(__name__)


async def expire_subscriptions(bot: Bot) -> None:
    """Снимает доступ у пользователей с истёкшей подпиской."""
    subs = await due_subscriptions()
    if not subs:
        return

    logger.info("Обработка истёкших подписок: %s", len(subs))
    for sub, tg_id in subs:
        if await is_member(bot, tg_id):
            if not await kick_user(bot, tg_id):
                await notify_admins(
                    bot,
                    f"🚨 Не удалось удалить {tg_id} из канала "
                    "после истечения подписки. Проверьте права бота.",
                )
                continue

        await mark_expired(sub.id)
        await revoke_invites(bot, sub.id)
        await log_action("subscription_expired", target_tg_id=tg_id)

        try:
            await bot.send_message(tg_id, expired_text())
        except TelegramAPIError:
            logger.info("Не удалось уведомить %s об истечении", tg_id)


async def send_reminders(bot: Bot) -> None:
    """
    Напоминания за N и M дней до окончания.

    Окна не пересекаются, поэтому оба напоминания не приходят одновременно:
    первое — когда осталось от M до N суток, второе — когда осталось меньше M.
    В тексте указывается фактический остаток (с округлением вверх).
    """
    near = max(settings.reminder_days_2, 1)
    far = max(settings.reminder_days_1, near)

    windows = [
        (near, far, "3d"),
        (0, near, "1d"),
    ]
    for days_from, days_to, flag in windows:
        for sub, tg_id in await subscriptions_for_reminders(days_from, days_to, flag):
            try:
                await bot.send_message(
                    tg_id, reminder_text(days_left_ceil(sub.expires_at), sub.expires_at)
                )
            except TelegramAPIError:
                logger.info("Напоминание не доставлено %s", tg_id)
            await mark_reminded(sub.id, flag)
