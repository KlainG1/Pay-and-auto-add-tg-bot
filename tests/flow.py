"""Интеграционный тест доступа: ссылка, вступление, истечение, кик.

Telegram не вызывается — используется заглушка Bot.
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from datetime import timedelta
from types import SimpleNamespace

os.environ.setdefault("BOT_TOKEN", "0:test")
os.environ["DATABASE_URL"] = (
    "sqlite+aiosqlite:///" + os.path.join(tempfile.mkdtemp(), "flow.db")
)
os.environ.setdefault("CHANNEL_ID", "-1001234567890")
os.environ.setdefault("ADMIN_IDS", "999")

from sqlalchemy import select  # noqa: E402

from bot.db.base import SessionFactory, init_db  # noqa: E402
from bot.db.models import InviteLink, Subscription  # noqa: E402
from bot.db.repo import get_plan_by_code  # noqa: E402
from bot.db.seed import sync_plans  # noqa: E402
from bot.scheduler.jobs import expire_subscriptions, send_reminders  # noqa: E402
from bot.services.messaging import get_or_create_invite  # noqa: E402
from bot.services.subscriptions import (  # noqa: E402
    activate_subscription,
    get_active_subscription,
)
from bot.services.users import touch_user  # noqa: E402
from bot.utils.time import utcnow  # noqa: E402


class FakeBot:
    def __init__(self) -> None:
        self.members: set[int] = set()
        self.messages: list[tuple[int, str]] = []
        self.links: dict[str, dict] = {}
        self.revoked: list[str] = []
        self.kicks: list[int] = []
        self._seq = 0

    async def me(self):
        return SimpleNamespace(id=1)

    async def create_chat_invite_link(
        self, chat_id, name=None, expire_date=None, member_limit=None
    ):
        self._seq += 1
        link = f"https://t.me/+test{self._seq}"
        self.links[link] = {
            "name": name,
            "member_limit": member_limit,
            "expire_date": expire_date,
        }
        return SimpleNamespace(invite_link=link, name=name)

    async def revoke_chat_invite_link(self, chat_id, invite_link):
        self.revoked.append(invite_link)
        return True

    async def get_chat_member(self, chat_id, user_id):
        status = "member" if user_id in self.members else "left"
        return SimpleNamespace(status=status, can_invite_users=True, can_restrict_members=True)

    async def ban_chat_member(self, chat_id, user_id, **kwargs):
        self.members.discard(user_id)
        self.kicks.append(user_id)
        return True

    async def unban_chat_member(self, chat_id, user_id, **kwargs):
        return True

    async def send_message(self, chat_id, text, **kwargs):
        self.messages.append((chat_id, text))
        return SimpleNamespace(message_id=1)


async def main() -> None:
    await init_db()
    await sync_plans()
    bot = FakeBot()

    plan = await get_plan_by_code("m1")
    assert plan is not None

    user = await touch_user(555, "buyer", "Покупатель")
    sub = await activate_subscription(user.id, plan)

    # 1. Ссылка создаётся один раз и переиспользуется
    link1 = await get_or_create_invite(bot, user.id, sub.id, user.tg_id)
    link2 = await get_or_create_invite(bot, user.id, sub.id, user.tg_id)
    assert link1 == link2, (link1, link2)
    assert bot.links[link1]["member_limit"] == 1
    assert bot.links[link1]["expire_date"] is not None
    print("Ссылка:", link1, bot.links[link1])

    # 2. Вступление по ссылке
    bot.members.add(user.tg_id)

    # 3. Срок не истёк — кика нет
    await expire_subscriptions(bot)
    assert bot.kicks == [], bot.kicks
    assert await get_active_subscription(user.id) is not None

    # 4. Истечение срока
    async with SessionFactory() as session:
        db_sub = await session.get(Subscription, sub.id)
        db_sub.expires_at = utcnow() - timedelta(minutes=1)
        db_sub.grace_until = None
        await session.commit()

    await expire_subscriptions(bot)
    assert bot.kicks == [user.tg_id], bot.kicks
    assert user.tg_id not in bot.members
    assert await get_active_subscription(user.id) is None
    assert link1 in bot.revoked, bot.revoked
    assert any(chat == user.tg_id for chat, _ in bot.messages)

    async with SessionFactory() as session:
        db_sub = await session.get(Subscription, sub.id)
        assert db_sub.status == "expired", db_sub.status
        links = list(await session.scalars(select(InviteLink)))
        assert links[0].revoked_at is not None

    # 5. Продление после истечения выдаёт новую ссылку
    sub2 = await activate_subscription(user.id, plan)
    link3 = await get_or_create_invite(bot, user.id, sub2.id, user.tg_id)
    assert link3 != link1
    print("Новая ссылка после продления:", link3)

    # 6. Напоминания: за 2 дня — одно, за 12 часов — второе
    async with SessionFactory() as session:
        db_sub = await session.get(Subscription, sub2.id)
        db_sub.expires_at = utcnow() + timedelta(days=2)
        await session.commit()

    bot.messages.clear()
    await send_reminders(bot)
    assert len(bot.messages) == 1, bot.messages
    assert "2 дня" in bot.messages[0][1], bot.messages[0][1]

    bot.messages.clear()
    await send_reminders(bot)
    assert bot.messages == [], "напоминание не должно дублироваться"

    async with SessionFactory() as session:
        db_sub = await session.get(Subscription, sub2.id)
        db_sub.expires_at = utcnow() + timedelta(hours=12)
        await session.commit()

    await send_reminders(bot)
    assert len(bot.messages) == 1, bot.messages
    assert "1 день" in bot.messages[0][1], bot.messages[0][1]

    print("FLOW OK")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except AssertionError as exc:
        print("FAIL:", exc)
        sys.exit(1)
