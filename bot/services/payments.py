from __future__ import annotations

import json
import uuid

from sqlalchemy import select

from bot.db.models import Payment
from bot.db.repo import session_scope
from bot.utils.time import utcnow


def new_order_id() -> str:
    return uuid.uuid4().hex


async def create_pending_payment(
    user_id: int, plan_id: int, amount: int, order_id: str, provider: str = "stars"
) -> Payment:
    async with session_scope() as session:
        payment = Payment(
            user_id=user_id,
            plan_id=plan_id,
            provider=provider,
            provider_payment_id=f"pending:{order_id}",
            order_id=order_id,
            amount=amount,
            status="pending",
            created_at=utcnow(),
        )
        session.add(payment)
        await session.flush()
        await session.refresh(payment)
        session.expunge(payment)
        return payment


async def find_pending_payment(user_id: int, order_id: str) -> Payment | None:
    async with session_scope() as session:
        payment = await session.scalar(
            select(Payment).where(
                Payment.user_id == user_id,
                Payment.order_id == order_id,
            )
        )
        if payment is not None:
            session.expunge(payment)
        return payment


async def find_payment_by_provider_id(provider_payment_id: str) -> Payment | None:
    async with session_scope() as session:
        payment = await session.scalar(
            select(Payment).where(Payment.provider_payment_id == provider_payment_id)
        )
        if payment is not None:
            session.expunge(payment)
        return payment


async def mark_paid(
    payment_id: int,
    provider_payment_id: str,
    subscription_id: int | None = None,
    raw_payload: dict | None = None,
) -> None:
    async with session_scope() as session:
        payment = await session.get(Payment, payment_id)
        if payment is None:
            return
        payment.status = "paid"
        payment.provider_payment_id = provider_payment_id
        payment.subscription_id = subscription_id
        payment.paid_at = utcnow()
        if raw_payload is not None:
            payment.raw_payload = json.dumps(raw_payload, ensure_ascii=False)


async def mark_failed(payment_id: int) -> None:
    async with session_scope() as session:
        payment = await session.get(Payment, payment_id)
        if payment is not None and payment.status == "pending":
            payment.status = "failed"


async def user_payments(user_id: int, limit: int = 10) -> list[Payment]:
    async with session_scope() as session:
        result = await session.scalars(
            select(Payment)
            .where(Payment.user_id == user_id)
            .order_by(Payment.created_at.desc())
            .limit(limit)
        )
        return list(result)
