from __future__ import annotations

from datetime import datetime

from sqlalchemy import select

from bot.db.models import AuditLog, User
from bot.db.repo import session_scope
from bot.utils.time import utcnow


async def touch_user(
    tg_id: int,
    username: str | None = None,
    first_name: str | None = None,
) -> User:
    """Создаёт пользователя или обновляет его профиль и last_seen_at."""
    async with session_scope() as session:
        user = await session.scalar(select(User).where(User.tg_id == tg_id))
        if user is None:
            user = User(
                tg_id=tg_id,
                username=username,
                first_name=first_name,
                created_at=utcnow(),
                last_seen_at=utcnow(),
            )
            session.add(user)
            await session.flush()
        else:
            user.username = username
            user.first_name = first_name
            user.last_seen_at = utcnow()
        await session.refresh(user)
        session.expunge(user)
        return user


async def get_user_by_tg_id(tg_id: int) -> User | None:
    async with session_scope() as session:
        user = await session.scalar(select(User).where(User.tg_id == tg_id))
        if user is not None:
            session.expunge(user)
        return user


async def get_user_by_username(username: str) -> User | None:
    normalized = username.lstrip("@").lower()
    async with session_scope() as session:
        user = await session.scalar(
            select(User).where(User.username.ilike(normalized))
        )
        if user is not None:
            session.expunge(user)
        return user


async def set_blocked(tg_id: int, blocked: bool) -> None:
    async with session_scope() as session:
        user = await session.scalar(select(User).where(User.tg_id == tg_id))
        if user is not None:
            user.is_blocked = blocked


async def log_action(
    action: str,
    actor_tg_id: int | None = None,
    target_tg_id: int | None = None,
    payload: str | None = None,
) -> None:
    async with session_scope() as session:
        session.add(
            AuditLog(
                action=action,
                actor_tg_id=actor_tg_id,
                target_tg_id=target_tg_id,
                payload=payload,
                created_at=utcnow(),
            )
        )


def format_dt(value: datetime | None, tz_name: str) -> str:
    from zoneinfo import ZoneInfo

    if value is None:
        return "—"
    if value.tzinfo is None:
        value = value.replace(tzinfo=ZoneInfo("UTC"))
    return value.astimezone(ZoneInfo(tz_name)).strftime("%d.%m.%Y %H:%M")
