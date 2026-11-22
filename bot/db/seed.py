from __future__ import annotations

import logging

from sqlalchemy import select

from bot.config import settings
from bot.db.base import SessionFactory
from bot.db.models import Plan

logger = logging.getLogger(__name__)


def _plan_specs() -> list[dict]:
    return [
        {
            "code": "m1",
            "title": "1 месяц",
            "duration_days": settings.plan_1m_days,
            "stars_price": settings.plan_1m_stars,
            "sort_order": 1,
        },
        {
            "code": "m3",
            "title": "3 месяца",
            "duration_days": settings.plan_3m_days,
            "stars_price": settings.plan_3m_stars,
            "sort_order": 2,
        },
    ]


async def sync_plans() -> None:
    """Создаёт тарифы из конфига или обновляет их параметры."""
    async with SessionFactory() as session:
        for spec in _plan_specs():
            plan = await session.scalar(select(Plan).where(Plan.code == spec["code"]))
            if plan is None:
                session.add(Plan(**spec))
                logger.info("Создан тариф %s", spec["code"])
                continue

            changed = False
            for field in ("title", "duration_days", "stars_price", "sort_order"):
                if getattr(plan, field) != spec[field]:
                    setattr(plan, field, spec[field])
                    changed = True
            if not plan.is_active:
                plan.is_active = True
                changed = True
            if changed:
                logger.info("Обновлён тариф %s", spec["code"])

        await session.commit()
