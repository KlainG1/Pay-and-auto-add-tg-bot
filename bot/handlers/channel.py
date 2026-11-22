from __future__ import annotations

import logging

from aiogram import Bot, Router
from aiogram.types import ChatJoinRequest, ChatMemberUpdated

from bot.config import settings
from bot.notify import notify_admins
from bot.services.channel import mark_user_invites_used
from bot.services.subscriptions import get_active_subscription
from bot.services.users import format_dt, get_user_by_tg_id, log_action, touch_user

logger = logging.getLogger(__name__)
router = Router(name="channel")

MEMBER_STATUSES = {"member", "administrator", "creator"}
LEFT_STATUSES = {"left", "kicked"}


@router.chat_member()
async def on_chat_member(event: ChatMemberUpdated, bot: Bot) -> None:
    """Реакция на вступление/выход из канала."""
    if event.chat.id != settings.channel_id:
        return

    old_status = event.old_chat_member.status
    new_status = event.new_chat_member.status
    user = event.new_chat_member.user

    if user.is_bot:
        return

    joined = new_status in MEMBER_STATUSES and old_status in LEFT_STATUSES
    left = new_status in LEFT_STATUSES and old_status in MEMBER_STATUSES

    if joined:
        await touch_user(user.id, user.username, user.first_name)
        await log_action("channel_join", target_tg_id=user.id)

        # ссылка одноразовая — в БД она больше не рабочая
        await mark_user_invites_used(user.id)

        db_user = await get_user_by_tg_id(user.id)
        active = await get_active_subscription(db_user.id) if db_user else None
        if active is None:
            await notify_admins(
                bot,
                f"ℹ️ В канал вошёл пользователь без активной подписки: "
                f"{user.id} (@{user.username or '—'}). "
                "Проверьте вручную — бот его не удалял.",
            )
            return

        try:
            await bot.send_message(
                user.id,
                "🎉 <b>Вы в канале!</b>\n\n"
                f"Подписка действует до "
                f"<b>{format_dt(active.expires_at, settings.timezone)}</b>.\n"
                "За день до окончания пришлём напоминание.",
            )
        except Exception:
            logger.info("Не удалось отправить приветствие %s", user.id)

    elif left:
        await log_action("channel_leave", target_tg_id=user.id)

        # "kicked" — это бан ботом или админом, уведомлять не нужно
        if new_status == "kicked":
            return

        db_user = await get_user_by_tg_id(user.id)
        if db_user is None:
            return
        active = await get_active_subscription(db_user.id)
        if active is not None:
            await notify_admins(
                bot,
                f"ℹ️ Пользователь {user.id} (@{user.username or '—'}) "
                "сам вышел из канала, подписка ещё активна.",
            )


@router.chat_join_request()
async def on_join_request(event: ChatJoinRequest, bot: Bot) -> None:
    """Режим заявок: одобряем только тех, у кого есть активная подписка."""
    if event.chat.id != settings.channel_id:
        return

    user = event.from_user
    await touch_user(user.id, user.username, user.first_name)
    db_user = await get_user_by_tg_id(user.id)
    active = await get_active_subscription(db_user.id) if db_user else None

    if active is None:
        try:
            await bot.decline_chat_join_request(settings.channel_id, user.id)
        except Exception:
            logger.exception("Не удалось отклонить заявку %s", user.id)
        try:
            await bot.send_message(
                user.id,
                "Заявка отклонена: активной подписки не найдено.\n"
                "Оформите подписку и попробуйте снова.",
            )
        except Exception:
            pass
        return

    try:
        await bot.approve_chat_join_request(settings.channel_id, user.id)
    except Exception:
        logger.exception("Не удалось одобрить заявку %s", user.id)
        return

    await log_action("channel_join_request_approved", target_tg_id=user.id)
    try:
        await bot.send_message(
            user.id,
            "🎉 <b>Заявка одобрена!</b>\n\n"
            f"Подписка действует до "
            f"<b>{format_dt(active.expires_at, settings.timezone)}</b>.",
        )
    except Exception:
        pass
