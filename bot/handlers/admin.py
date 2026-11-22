from __future__ import annotations

import logging
from datetime import timedelta

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message
from sqlalchemy import func, select

from bot.config import format_days, settings
from bot.db.base import SessionFactory
from bot.db.models import Payment, Subscription, User
from bot.db.repo import get_plan_by_code, list_active_plans
from bot.services.channel import kick_user
from bot.services.subscriptions import (
    activate_subscription,
    get_active_subscription,
    get_latest_subscription,
)
from bot.services.users import format_dt, get_user_by_tg_id, log_action
from bot.utils.time import utcnow

logger = logging.getLogger(__name__)
router = Router(name="admin")


def is_admin(tg_id: int | None) -> bool:
    return tg_id is not None and tg_id in settings.admin_id_list


def admin_only(message: Message) -> bool:
    return is_admin(message.from_user.id if message.from_user else None)


@router.message(Command("admin"))
async def cmd_admin(message: Message) -> None:
    if not admin_only(message):
        return
    text = (
        "🛠 <b>Админ-панель</b>\n\n"
        "<b>Статистика</b>\n"
        "/stats — сводка\n\n"
        "<b>Пользователь</b>\n"
        "/user 123456789 — карточка\n"
        "/grant 123456789 m1 — выдать/продлить\n"
        "/revoke 123456789 — отозвать доступ\n"
        "/kick 123456789 — удалить из канала\n"
        "/block 123456789 — заблокировать бота\n"
        "/unblock 123456789 — разблокировать\n\n"
        "<b>Прочее</b>\n"
        "/plans — тарифы\n"
        "/check — проверить права бота в канале"
    )
    await message.answer(text)


@router.message(Command("stats"))
async def cmd_stats(message: Message) -> None:
    if not admin_only(message):
        return

    now = utcnow()
    day_ago = now - timedelta(days=1)
    week_ago = now - timedelta(days=7)
    month_ago = now - timedelta(days=30)

    async with SessionFactory() as session:
        total_users = await session.scalar(select(func.count(User.id)))
        active_subs = await session.scalar(
            select(func.count(Subscription.id)).where(
                Subscription.status == "active", Subscription.expires_at > now
            )
        )

        async def revenue_since(moment):
            return (
                await session.scalar(
                    select(func.coalesce(func.sum(Payment.amount), 0)).where(
                        Payment.status == "paid", Payment.paid_at >= moment
                    )
                )
                or 0
            )

        day_sum = await revenue_since(day_ago)
        week_sum = await revenue_since(week_ago)
        month_sum = await revenue_since(month_ago)

        paid_total = await session.scalar(
            select(func.count(Payment.id)).where(Payment.status == "paid")
        )
        expired_total = await session.scalar(
            select(func.count(Subscription.id)).where(Subscription.status == "expired")
        )
        expiring_3d = await session.scalar(
            select(func.count(Subscription.id)).where(
                Subscription.status == "active",
                Subscription.expires_at > now,
                Subscription.expires_at <= now + timedelta(days=3),
            )
        )

    text = (
        "📊 <b>Статистика</b>\n\n"
        f"Пользователей: <b>{total_users or 0}</b>\n"
        f"Активных подписок: <b>{active_subs or 0}</b>\n"
        f"Истекают в 3 дня: <b>{expiring_3d or 0}</b>\n"
        f"Всего истекло: <b>{expired_total or 0}</b>\n\n"
        "<b>Выручка (Stars)</b>\n"
        f"За сутки: {day_sum} ⭐\n"
        f"За неделю: {week_sum} ⭐\n"
        f"За 30 дней: {month_sum} ⭐\n\n"
        f"Платежей всего: <b>{paid_total or 0}</b>"
    )
    await message.answer(text)


@router.message(Command("plans"))
async def cmd_plans(message: Message) -> None:
    if not admin_only(message):
        return
    plans = await list_active_plans()
    lines = ["💳 <b>Тарифы</b>\n"]
    for p in plans:
        lines.append(
            f"<code>{p.code}</code> — {p.title}, {p.stars_price} ⭐, "
            f"{format_days(p.duration_days)}"
        )
    lines.append("\nМеняются в .env (PLAN_*) и применяются при старте бота.")
    await message.answer("\n".join(lines))


async def _resolve_user(arg: str) -> User | None:
    arg = arg.strip()
    if arg.startswith("@"):
        from bot.services.users import get_user_by_username

        return await get_user_by_username(arg)
    try:
        return await get_user_by_tg_id(int(arg))
    except ValueError:
        return None


@router.message(Command("user"))
async def cmd_user(message: Message, command: CommandObject) -> None:
    if not admin_only(message):
        return
    if not command.args:
        await message.answer("Использование: /user 123456789 или /user @username")
        return

    user = await _resolve_user(command.args)
    if user is None:
        await message.answer("Пользователь не найден в базе.")
        return

    active = await get_active_subscription(user.id)
    latest = await get_latest_subscription(user.id)

    async with SessionFactory() as session:
        paid_count = await session.scalar(
            select(func.count(Payment.id)).where(
                Payment.user_id == user.id, Payment.status == "paid"
            )
        )
        paid_sum = await session.scalar(
            select(func.coalesce(func.sum(Payment.amount), 0)).where(
                Payment.user_id == user.id, Payment.status == "paid"
            )
        )

    status = "активна" if active else "нет"
    lines = [
        "👤 <b>Карточка пользователя</b>\n",
        f"ID: <code>{user.tg_id}</code>",
        f"Username: @{user.username or '—'}",
        f"Имя: {user.first_name or '—'}",
        f"Заблокирован в боте: {'да' if user.is_blocked else 'нет'}",
        f"Создан: {format_dt(user.created_at, settings.timezone)}",
        "",
        f"Подписка: <b>{status}</b>",
    ]
    if active:
        lines.append(f"До: {format_dt(active.expires_at, settings.timezone)}")
    elif latest:
        lines.append(f"Последняя истекла: {format_dt(latest.expires_at, settings.timezone)}")

    lines.extend(
        [
            "",
            f"Платежей: <b>{paid_count or 0}</b> на <b>{paid_sum or 0} ⭐</b>",
        ]
    )
    await message.answer("\n".join(lines))


@router.message(Command("grant"))
async def cmd_grant(message: Message, command: CommandObject) -> None:
    if not admin_only(message):
        return
    if not command.args or len(command.args.split()) != 2:
        await message.answer("Использование: /grant 123456789 m1")
        return

    tg_raw, code = command.args.split()
    try:
        tg_id = int(tg_raw)
    except ValueError:
        await message.answer("Некорректный tg_id.")
        return

    plan = await get_plan_by_code(code)
    if plan is None:
        await message.answer("Неизвестный тариф. Доступны: m1, m3.")
        return

    user = await get_user_by_tg_id(tg_id)
    if user is None:
        from bot.services.users import touch_user

        user = await touch_user(tg_id)

    sub = await activate_subscription(user.id, plan)
    await log_action(
        "admin_grant",
        actor_tg_id=message.from_user.id if message.from_user else None,
        target_tg_id=tg_id,
        payload=f"plan={code} expires={sub.expires_at.isoformat()}",
    )
    await message.answer(
        f"✅ Выдано: {plan.title} пользователю <code>{tg_id}</code>\n"
        f"До: {format_dt(sub.expires_at, settings.timezone)}"
    )


@router.message(Command("revoke"))
async def cmd_revoke(message: Message, command: CommandObject) -> None:
    if not admin_only(message):
        return
    if not command.args:
        await message.answer("Использование: /revoke 123456789")
        return

    user = await _resolve_user(command.args)
    if user is None:
        await message.answer("Пользователь не найден.")
        return

    active = await get_active_subscription(user.id)
    if active is None:
        await message.answer("Активной подписки нет.")
        return

    from bot.services.subscriptions import cancel_subscription

    await cancel_subscription(active.id)
    ok = await kick_user(message.bot, user.tg_id)
    await log_action(
        "admin_revoke",
        actor_tg_id=message.from_user.id if message.from_user else None,
        target_tg_id=user.tg_id,
    )
    await message.answer(
        "✅ Подписка отменена." + ("" if ok else " ⚠️ Не удалось удалить из канала.")
    )


@router.message(Command("kick"))
async def cmd_kick(message: Message, command: CommandObject) -> None:
    if not admin_only(message):
        return
    if not command.args:
        await message.answer("Использование: /kick 123456789")
        return
    user = await _resolve_user(command.args)
    if user is None:
        await message.answer("Пользователь не найден.")
        return
    ok = await kick_user(message.bot, user.tg_id)
    await log_action(
        "admin_kick",
        actor_tg_id=message.from_user.id if message.from_user else None,
        target_tg_id=user.tg_id,
    )
    await message.answer("✅ Удалён из канала." if ok else "⚠️ Ошибка удаления.")


@router.message(Command("block", "unblock"))
async def cmd_block(message: Message, command: CommandObject) -> None:
    if not admin_only(message):
        return
    if not command.args:
        await message.answer("Использование: /block 123456789")
        return

    blocked = command.command == "block"
    user = await _resolve_user(command.args)
    if user is None:
        await message.answer("Пользователь не найден.")
        return

    from bot.services.users import set_blocked

    await set_blocked(user.tg_id, blocked)
    await message.answer(
        f"✅ Пользователь {user.tg_id} "
        f"{'заблокирован' if blocked else 'разблокирован'}."
    )


@router.message(Command("check"))
async def cmd_check(message: Message) -> None:
    if not admin_only(message):
        return
    from bot.services.channel import channel_is_ready

    ok, detail = await channel_is_ready(message.bot)
    await message.answer(
        "✅ Бот готов к работе." if ok else f"⚠️ Проблема: {detail}"
    )
