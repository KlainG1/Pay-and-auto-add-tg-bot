from __future__ import annotations

from datetime import datetime

from aiogram import Bot

from bot.config import format_days, settings
from bot.services.channel import (
    create_invite,
    get_usable_invite,
    revoke_stale_user_invites,
)
from bot.services.users import format_dt


async def get_or_create_invite(
    bot: Bot, user_id: int, subscription_id: int, tg_id: int
) -> str:
    existing = await get_usable_invite(subscription_id)
    if existing is not None:
        return existing.link

    # ссылки от прошлых подписок больше не должны работать
    await revoke_stale_user_invites(bot, user_id, subscription_id)

    record = await create_invite(bot, user_id, subscription_id, tg_id)
    return record.link


def access_granted_text(expires_at: datetime) -> str:
    return (
        "✅ <b>Доступ активирован!</b>\n\n"
        f"Подписка действует до <b>{format_dt(expires_at, settings.timezone)}</b>.\n\n"
        "Нажмите кнопку ниже, чтобы войти в канал. "
        "Ссылка персональная и действует один раз — не передавайте её другим."
    )


def reminder_text(days: int, expires_at: datetime) -> str:
    return (
        f"⏳ <b>Подписка заканчивается через {format_days(days)}</b>\n\n"
        f"Доступ к «{settings.channel_title}» закроется "
        f"<b>{format_dt(expires_at, settings.timezone)}</b>.\n\n"
        "Продлите подписку, чтобы остаться в канале без перерыва."
    )


def expired_text() -> str:
    return (
        "🔒 <b>Подписка закончилась</b>\n\n"
        f"Доступ к «{settings.channel_title}» закрыт, вы удалены из канала.\n\n"
        "Оформить подписку заново можно в любой момент — "
        "ваша история платежей сохранится."
    )
