from __future__ import annotations

import logging
from datetime import timedelta

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from sqlalchemy import select

from bot.config import settings
from bot.db.models import InviteLink
from bot.db.repo import session_scope
from bot.utils.time import ensure_aware, utcnow

logger = logging.getLogger(__name__)


class ChannelAccessError(RuntimeError):
    pass


async def create_invite(
    bot: Bot, user_id: int, subscription_id: int, tg_id: int
) -> InviteLink:
    """Создаёт персональную одноразовую ссылку на канал."""
    expire_at = utcnow() + timedelta(hours=settings.invite_ttl_hours)
    try:
        link = await bot.create_chat_invite_link(
            chat_id=settings.channel_id,
            name=f"u{tg_id}"[:32],
            expire_date=int(expire_at.timestamp()),
            member_limit=1,
        )
    except TelegramAPIError as exc:
        logger.exception("Не удалось создать инвайт-ссылку")
        raise ChannelAccessError(
            "Бот не может создать ссылку. Проверьте права администратора в канале."
        ) from exc

    async with session_scope() as session:
        record = InviteLink(
            subscription_id=subscription_id,
            user_id=user_id,
            link=link.invite_link,
            name=link.name,
            expire_at=expire_at,
        )
        session.add(record)
        await session.flush()
        await session.refresh(record)
        session.expunge(record)
        return record


async def get_usable_invite(subscription_id: int) -> InviteLink | None:
    """Живая неиспользованная ссылка по подписке."""
    now = utcnow()
    async with session_scope() as session:
        record = await session.scalar(
            select(InviteLink)
            .where(
                InviteLink.subscription_id == subscription_id,
                InviteLink.used_at.is_(None),
                InviteLink.revoked_at.is_(None),
            )
            .order_by(InviteLink.id.desc())
        )
        if record is None:
            return None
        expire = ensure_aware(record.expire_at)
        if expire is not None and expire <= now:
            return None
        session.expunge(record)
        return record


async def revoke_invites(bot: Bot, subscription_id: int) -> None:
    """Отзывает все неиспользованные ссылки подписки."""
    async with session_scope() as session:
        records = list(
            await session.scalars(
                select(InviteLink).where(
                    InviteLink.subscription_id == subscription_id,
                    InviteLink.used_at.is_(None),
                    InviteLink.revoked_at.is_(None),
                )
            )
        )
        for record in records:
            try:
                await bot.revoke_chat_invite_link(settings.channel_id, record.link)
            except TelegramAPIError:
                logger.warning("Не удалось отозвать ссылку %s", record.link)
            record.revoked_at = utcnow()


async def revoke_stale_user_invites(
    bot: Bot, user_id: int, keep_subscription_id: int
) -> None:
    """Гасит неиспользованные ссылки пользователя от прошлых подписок."""
    async with session_scope() as session:
        records = list(
            await session.scalars(
                select(InviteLink).where(
                    InviteLink.user_id == user_id,
                    InviteLink.subscription_id != keep_subscription_id,
                    InviteLink.used_at.is_(None),
                    InviteLink.revoked_at.is_(None),
                )
            )
        )
        for record in records:
            try:
                await bot.revoke_chat_invite_link(settings.channel_id, record.link)
            except TelegramAPIError:
                logger.warning("Не удалось отозвать ссылку %s", record.link)
            record.revoked_at = utcnow()


async def mark_invite_used(link: str, tg_id: int) -> InviteLink | None:
    async with session_scope() as session:
        record = await session.scalar(select(InviteLink).where(InviteLink.link == link))
        if record is None:
            return None
        record.used_at = utcnow()
        session.expunge(record)
        return record


async def mark_user_invites_used(user_id: int) -> None:
    """
    Помечает ссылки пользователя использованными после вступления.
    Telegram сам гасит ссылку с member_limit=1, но в БД это нужно отразить,
    иначе бот будет считать её рабочей и выдавать повторно.
    """
    async with session_scope() as session:
        records = list(
            await session.scalars(
                select(InviteLink).where(
                    InviteLink.user_id == user_id,
                    InviteLink.used_at.is_(None),
                )
            )
        )
        for record in records:
            record.used_at = utcnow()


async def is_member(bot: Bot, tg_id: int) -> bool:
    try:
        member = await bot.get_chat_member(settings.channel_id, tg_id)
    except TelegramAPIError:
        logger.warning("Не удалось получить статус участника %s", tg_id)
        return False
    return member.status in {"creator", "administrator", "member"}


async def kick_user(bot: Bot, tg_id: int) -> bool:
    """
    Удаляет пользователя из канала и сразу снимает бан,
    чтобы он мог вернуться по новой ссылке после продления.
    """
    try:
        await bot.ban_chat_member(settings.channel_id, tg_id)
        await bot.unban_chat_member(settings.channel_id, tg_id, only_if_banned=True)
    except TelegramAPIError:
        logger.exception("Не удалось удалить пользователя %s из канала", tg_id)
        return False
    return True


async def channel_is_ready(bot: Bot) -> tuple[bool, str]:
    """Проверка прав бота при старте."""
    try:
        me = await bot.me()
        member = await bot.get_chat_member(settings.channel_id, me.id)
    except TelegramAPIError as exc:
        return False, f"Нет доступа к каналу: {exc.message}"

    if member.status != "administrator":
        return False, "Бот не является администратором канала"

    can_invite = getattr(member, "can_invite_users", False)
    can_restrict = getattr(member, "can_restrict_members", False)
    missing = []
    if not can_invite:
        missing.append("«Приглашать по ссылке»")
    if not can_restrict:
        missing.append("«Блокировать участников»")
    if missing:
        return False, "Не хватает прав: " + ", ".join(missing)
    return True, "ok"
