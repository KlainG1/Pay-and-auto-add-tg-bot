from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import or_, select

from bot.config import settings
from bot.db.models import Plan, Subscription, User
from bot.db.repo import session_scope
from bot.utils.time import ensure_aware, utcnow

ACTIVE_STATUS = "active"
RENEWED_STATUS = "extended"
EXPIRED_STATUS = "expired"
CANCELED_STATUS = "canceled"


def _access_deadline(sub: Subscription) -> datetime:
    """До какого момента доступ реально сохраняется (с учётом grace)."""
    grace = ensure_aware(sub.grace_until)
    expires = ensure_aware(sub.expires_at) or utcnow()
    return max(grace, expires) if grace else expires


async def get_active_subscription(user_id: int) -> Subscription | None:
    """Действующая подписка: срок не истёк либо активен grace period."""
    now = utcnow()
    async with session_scope() as session:
        sub = await session.scalar(
            select(Subscription)
            .where(
                Subscription.user_id == user_id,
                Subscription.status == ACTIVE_STATUS,
                or_(
                    Subscription.expires_at > now,
                    Subscription.grace_until > now,
                ),
            )
            .order_by(Subscription.expires_at.desc())
        )
        if sub is not None:
            session.expunge(sub)
        return sub


async def get_latest_subscription(user_id: int) -> Subscription | None:
    """Последняя созданная подписка пользователя."""
    async with session_scope() as session:
        sub = await session.scalar(
            select(Subscription)
            .where(Subscription.user_id == user_id)
            .order_by(Subscription.id.desc())
        )
        if sub is not None:
            session.expunge(sub)
        return sub


async def activate_subscription(
    user_id: int, plan: Plan, *, grace_hours: int | None = None
) -> Subscription:
    """
    Создаёт подписку или продлевает действующую.

    Новый срок = max(now, текущий срок) + duration_days.
    Старые активные подписки переводятся в статус «extended»,
    их остаток срока переносится в новую — доступ не «теряется».
    """
    grace_hours = settings.grace_hours if grace_hours is None else grace_hours
    now = utcnow()

    async with session_scope() as session:
        active = list(
            await session.scalars(
                select(Subscription)
                .where(
                    Subscription.user_id == user_id,
                    Subscription.status == ACTIVE_STATUS,
                )
                .order_by(Subscription.expires_at.desc())
            )
        )

        base = now
        for sub in active:
            base = max(base, _access_deadline(sub))
            sub.status = RENEWED_STATUS

        expires_at = base + timedelta(days=plan.duration_days)
        subscription = Subscription(
            user_id=user_id,
            plan_id=plan.id,
            status=ACTIVE_STATUS,
            started_at=now,
            expires_at=expires_at,
            grace_until=(
                expires_at + timedelta(hours=grace_hours) if grace_hours else None
            ),
            notified_3d=False,
            notified_1d=False,
            created_at=now,
        )
        session.add(subscription)
        await session.flush()
        await session.refresh(subscription)
        session.expunge(subscription)
        return subscription


async def cancel_subscription(subscription_id: int) -> None:
    async with session_scope() as session:
        sub = await session.get(Subscription, subscription_id)
        if sub is not None:
            sub.status = CANCELED_STATUS


async def due_subscriptions() -> list[tuple[Subscription, int]]:
    """
    Подписки, у которых истёк срок с учётом grace period.
    Возвращает пары (подписка, tg_id) — объекты отсоединены от сессии.
    """
    now = utcnow()
    async with session_scope() as session:
        rows = await session.execute(
            select(Subscription, User.tg_id)
            .join(User, User.id == Subscription.user_id)
            .where(
                Subscription.status == ACTIVE_STATUS,
                Subscription.expires_at <= now,
            )
        )
        result: list[tuple[Subscription, int]] = []
        for sub, tg_id in rows.all():
            if _access_deadline(sub) > now:
                continue
            session.expunge(sub)
            result.append((sub, tg_id))
        return result


async def mark_expired(subscription_id: int) -> None:
    async with session_scope() as session:
        sub = await session.get(Subscription, subscription_id)
        if sub is not None and sub.status == ACTIVE_STATUS:
            sub.status = EXPIRED_STATUS


async def subscriptions_for_reminders(
    days_from: int, days_to: int, flag: str
) -> list[tuple[Subscription, int]]:
    """
    Подписки, до окончания которых осталось от `days_from` до `days_to` суток
    и по которым напоминание ещё не отправляли.
    """
    now = utcnow()
    lower = now + timedelta(days=days_from)
    upper = now + timedelta(days=days_to)
    flag_column = (
        Subscription.notified_3d if flag == "3d" else Subscription.notified_1d
    )

    async with session_scope() as session:
        rows = await session.execute(
            select(Subscription, User.tg_id)
            .join(User, User.id == Subscription.user_id)
            .where(
                Subscription.status == ACTIVE_STATUS,
                Subscription.expires_at > lower,
                Subscription.expires_at <= upper,
                flag_column.is_(False),
            )
        )
        result = [(sub, tg_id) for sub, tg_id in rows.all()]
        for sub, _ in result:
            session.expunge(sub)
        return result


async def mark_reminded(subscription_id: int, flag: str) -> None:
    async with session_scope() as session:
        sub = await session.get(Subscription, subscription_id)
        if sub is None:
            return
        if flag == "3d":
            sub.notified_3d = True
        else:
            sub.notified_1d = True


def days_left(expires_at: datetime) -> int:
    """Сколько полных суток осталось до окончания."""
    expires = ensure_aware(expires_at) or expires_at
    delta = expires - utcnow()
    return max(0, int(delta.total_seconds() // 86400))


def days_left_ceil(expires_at: datetime) -> int:
    """Сколько суток осталось с округлением вверх (для текстов)."""
    expires = ensure_aware(expires_at) or expires_at
    seconds = (expires - utcnow()).total_seconds()
    return max(1, int(-(-seconds // 86400)))
