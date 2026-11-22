from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import select

from bot.db.base import SessionFactory
from bot.db.models import Plan


@asynccontextmanager
async def session_scope() -> AsyncIterator:
    """Транзакционная сессия: commit при успехе, rollback при ошибке."""
    async with SessionFactory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_plan_by_code(code: str) -> Plan | None:
    async with session_scope() as session:
        return await session.scalar(select(Plan).where(Plan.code == code))


async def list_active_plans() -> list[Plan]:
    async with session_scope() as session:
        result = await session.scalars(
            select(Plan).where(Plan.is_active.is_(True)).order_by(Plan.sort_order)
        )
        return list(result)


async def get_plan_by_id(plan_id: int) -> Plan | None:
    async with session_scope() as session:
        return await session.get(Plan, plan_id)
