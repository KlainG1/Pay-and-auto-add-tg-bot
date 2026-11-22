from __future__ import annotations

from datetime import datetime, timezone

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

from bot.config import settings


async def notify_admins(bot: Bot, text: str) -> None:
    """Отправить сообщение всем администраторам, игнорируя ошибки доставки."""
    for admin_id in settings.admin_id_list:
        try:
            await bot.send_message(admin_id, text)
        except TelegramAPIError:
            continue


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
