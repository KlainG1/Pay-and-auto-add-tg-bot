"""Smoke-тест бизнес-логики без Telegram: БД, тарифы, продление, истечение."""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile

os.environ.setdefault("BOT_TOKEN", "0:test")
os.environ["DATABASE_URL"] = (
    "sqlite+aiosqlite:///" + os.path.join(tempfile.mkdtemp(), "smoke.db")
)

from sqlalchemy import select  # noqa: E402

from bot.db.base import SessionFactory, init_db  # noqa: E402
from bot.db.models import InviteLink, Payment, Subscription  # noqa: E402
from bot.db.repo import get_plan_by_code, list_active_plans  # noqa: E402
from bot.db.seed import sync_plans  # noqa: E402
from bot.services.payments import (  # noqa: E402
    create_pending_payment,
    find_pending_payment,
    new_order_id,
)
from bot.services.payments import mark_paid  # noqa: E402
from bot.services.subscriptions import (  # noqa: E402
    activate_subscription,
    due_subscriptions,
    get_active_subscription,
    get_latest_subscription,
    mark_expired,
)
from bot.services.users import touch_user  # noqa: E402


async def main() -> None:
    await init_db()
    await sync_plans()

    plans = await list_active_plans()
    assert len(plans) == 2, plans
    assert [p.code for p in plans] == ["m1", "m3"]

    plan1 = await get_plan_by_code("m1")
    plan3 = await get_plan_by_code("m3")
    assert plan1 and plan3

    user = await touch_user(111, "tester", "Тест")
    assert user.tg_id == 111

    # первая покупка
    sub1 = await activate_subscription(user.id, plan1)
    assert sub1.status == "active"
    first_expires = sub1.expires_at
    print("Подписка 1 мес до:", first_expires)

    # продление суммирует срок
    sub2 = await activate_subscription(user.id, plan3)
    delta_days = (sub2.expires_at - first_expires).total_seconds() / 86400
    assert abs(delta_days - plan3.duration_days) < 1, delta_days
    print("После продления 3 мес до:", sub2.expires_at)

    async with SessionFactory() as session:
        subs = list(await session.scalars(select(Subscription)))
        assert len(subs) == 2, subs
        statuses = {s.status for s in subs}
        assert statuses == {"extended", "active"}, statuses

    active = await get_active_subscription(user.id)
    assert active and active.id == sub2.id

    # платежи: идемпотентность по order_id
    order_id = new_order_id()
    pending = await create_pending_payment(user.id, plan1.id, 300, order_id)
    assert pending.status == "pending"
    found = await find_pending_payment(user.id, order_id)
    assert found and found.id == pending.id
    await mark_paid(pending.id, "charge-1", subscription_id=sub2.id)

    async with SessionFactory() as session:
        paid = await session.scalar(select(Payment).where(Payment.id == pending.id))
        assert paid.status == "paid" and paid.subscription_id == sub2.id

    # истечение: сдвигаем срок в прошлое
    async with SessionFactory() as session:
        sub = await session.get(Subscription, sub2.id)
        from datetime import timedelta

        from bot.utils.time import utcnow

        sub.expires_at = utcnow() - timedelta(minutes=1)
        sub.grace_until = None
        await session.commit()

    due = await due_subscriptions()
    assert len(due) == 1, due
    due_sub, due_tg_id = due[0]
    assert due_tg_id == 111
    await mark_expired(due_sub.id)

    active = await get_active_subscription(user.id)
    assert active is None
    latest = await get_latest_subscription(user.id)
    assert latest and latest.status == "expired"

    # повторная покупка после истечения: срок считается от текущего момента
    from datetime import timedelta as _td

    from bot.utils.time import utcnow as _now

    sub3 = await activate_subscription(user.id, plan1)
    assert sub3.status == "active"
    expected = _now() + _td(days=plan1.duration_days)
    assert abs((sub3.expires_at - expected).total_seconds()) < 120, sub3.expires_at

    async with SessionFactory() as session:
        links = list(await session.scalars(select(InviteLink)))
        assert links == []

    print("SMOKE OK")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except AssertionError as exc:
        print("FAIL:", exc)
        sys.exit(1)
