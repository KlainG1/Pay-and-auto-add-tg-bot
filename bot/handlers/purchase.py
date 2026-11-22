from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
)

from bot.config import settings
from bot.db.repo import get_plan_by_code, list_active_plans
from bot.keyboards import join_keyboard, plans_keyboard
from bot.notify import notify_admins
from bot.services.channel import ChannelAccessError, is_member
from bot.services.messaging import access_granted_text, get_or_create_invite
from bot.services.payments import (
    create_pending_payment,
    find_pending_payment,
    mark_paid,
    new_order_id,
)
from bot.services.subscriptions import activate_subscription
from bot.services.users import format_dt, get_user_by_tg_id, log_action, touch_user
from bot.utils.time import utcnow

logger = logging.getLogger(__name__)
router = Router(name="purchase")


@router.callback_query(F.data == "buy_menu")
async def cb_buy_menu(callback: CallbackQuery) -> None:
    plans = await list_active_plans()
    if not plans:
        await callback.answer("Тарифы временно недоступны", show_alert=True)
        return
    if callback.message is not None:
        await callback.message.edit_text(
            "💳 <b>Выберите тариф</b>\n\nОплата в Telegram Stars ⭐",
            reply_markup=plans_keyboard(plans),
        )
    await callback.answer()


@router.callback_query(F.data.startswith("buy:"))
async def cb_buy_plan(callback: CallbackQuery) -> None:
    if callback.from_user is None or callback.message is None:
        await callback.answer()
        return

    code = (callback.data or "").split(":", 1)[1]
    plan = await get_plan_by_code(code)
    if plan is None or not plan.is_active:
        await callback.answer("Этот тариф больше недоступен", show_alert=True)
        return

    user = await touch_user(
        callback.from_user.id,
        callback.from_user.username,
        callback.from_user.first_name,
    )
    order_id = new_order_id()
    await create_pending_payment(user.id, plan.id, plan.stars_price, order_id)

    # Telegram ограничивает длину payload — кладём компактно
    payload = f"{callback.from_user.id}|{plan.code}|{order_id}"

    try:
        await callback.message.answer_invoice(
            title=f"Доступ в «{settings.channel_title}» — {plan.title}",
            description=(
                f"Подписка на закрытый канал на {plan.title}. "
                "Доступ выдаётся автоматически после оплаты."
            ),
            payload=payload,
            provider_token="",  # для Stars обязателен пустой
            currency="XTR",
            prices=[LabeledPrice(label=plan.title, amount=plan.stars_price)],
        )
    except TelegramAPIError as exc:
        logger.exception("Не удалось выставить счёт")
        await callback.answer(
            f"Не удалось создать счёт: {exc.message}", show_alert=True
        )
        return

    await callback.answer()


@router.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery) -> None:
    """
    Валидируем заказ до списания средств.
    payload: tg_id|plan_code|order_id
    """
    try:
        tg_id_raw, code, order_id = (query.invoice_payload or "").split("|", 2)
        tg_id = int(tg_id_raw)
    except ValueError:
        await query.answer(ok=False, error_message="Некорректные данные заказа.")
        return

    if tg_id != query.from_user.id:
        await query.answer(ok=False, error_message="Заказ оформлен другим пользователем.")
        return

    plan = await get_plan_by_code(code)
    if plan is None or not plan.is_active:
        await query.answer(ok=False, error_message="Тариф недоступен. Выберите другой.")
        return

    user = await get_user_by_tg_id(tg_id)
    if user is None:
        await query.answer(ok=False, error_message="Профиль не найден. Нажмите /start.")
        return
    if user.is_blocked:
        await query.answer(ok=False, error_message="Оплата недоступна для вашего аккаунта.")
        return

    if query.total_amount != plan.stars_price:
        await query.answer(
            ok=False, error_message="Цена изменилась. Выберите тариф заново."
        )
        return

    pending = await find_pending_payment(user.id, order_id)
    if pending is None:
        await query.answer(ok=False, error_message="Заказ не найден. Создайте новый.")
        return
    if pending.status != "pending":
        await query.answer(ok=False, error_message="Этот заказ уже обработан.")
        return

    await query.answer(ok=True)


@router.message(F.successful_payment)
async def on_successful_payment(message: Message, bot: Bot) -> None:
    payment_info = message.successful_payment
    if payment_info is None or message.from_user is None:
        return

    tg_id = message.from_user.id
    try:
        _, code, order_id = (payment_info.invoice_payload or "").split("|", 2)
    except ValueError:
        await notify_admins(
            bot,
            f"⚠️ Оплата без корректного payload от {tg_id}: "
            f"{payment_info.telegram_payment_charge_id}",
        )
        return

    user = await touch_user(
        tg_id, message.from_user.username, message.from_user.first_name
    )
    plan = await get_plan_by_code(code)
    if plan is None:
        await notify_admins(bot, f"⚠️ Оплата по неизвестному тарифу {code} от {tg_id}")
        return

    pending = await find_pending_payment(user.id, order_id)
    if pending is None:
        await notify_admins(
            bot, f"⚠️ Оплата без заказа: user={tg_id}, order={order_id}"
        )
        return

    if pending.status == "paid":
        # повторная доставка апдейта — не продлеваем второй раз
        logger.info("Дубликат successful_payment order=%s", order_id)
        return

    subscription = await activate_subscription(user.id, plan)
    await mark_paid(
        pending.id,
        provider_payment_id=payment_info.telegram_payment_charge_id,
        subscription_id=subscription.id,
        raw_payload={
            "amount": payment_info.total_amount,
            "currency": payment_info.currency,
            "charge_id": payment_info.telegram_payment_charge_id,
            "provider_charge_id": payment_info.provider_payment_charge_id,
            "paid_at": utcnow().isoformat(),
        },
    )
    await log_action(
        "payment_success",
        actor_tg_id=tg_id,
        target_tg_id=tg_id,
        payload=f"plan={plan.code} amount={payment_info.total_amount} order={order_id}",
    )

    # уже в канале — продление без повторного входа
    if await is_member(bot, tg_id):
        await message.answer(
            "✅ <b>Подписка продлена!</b>\n\n"
            f"Вы уже в канале, доступ действует до "
            f"<b>{format_dt(subscription.expires_at, settings.timezone)}</b>.",
        )
        await notify_admins(
            bot,
            f"💰 Продление: {plan.title} за {payment_info.total_amount} ⭐\n"
            f"Пользователь: {tg_id} (@{message.from_user.username or '—'})",
        )
        return

    try:
        invite = await get_or_create_invite(bot, user.id, subscription.id, tg_id)
    except ChannelAccessError as exc:
        await message.answer(
            "✅ Оплата получена, подписка активна.\n\n"
            "Но сейчас не удаётся создать ссылку на канал — "
            "администратор уже уведомлён. Нажмите «Моя подписка» позже."
        )
        await notify_admins(
            bot,
            f"🚨 Оплата прошла, но инвайт не создан.\n"
            f"user={tg_id}, order={order_id}, ошибка: {exc}",
        )
        return

    await message.answer(
        access_granted_text(subscription.expires_at),
        reply_markup=join_keyboard(invite),
    )
    await notify_admins(
        bot,
        f"💰 Новая оплата: {plan.title} за {payment_info.total_amount} ⭐\n"
        f"Пользователь: {tg_id} (@{message.from_user.username or '—'})",
    )


@router.callback_query(F.data == "get_link")
async def cb_get_link(callback: CallbackQuery, bot: Bot) -> None:
    if callback.from_user is None:
        await callback.answer()
        return

    user = await get_user_by_tg_id(callback.from_user.id)
    if user is None:
        await callback.answer("Нажмите /start", show_alert=True)
        return

    from bot.services.subscriptions import get_active_subscription

    active = await get_active_subscription(user.id)
    if active is None:
        await callback.answer("Активной подписки нет", show_alert=True)
        return

    try:
        invite = await get_or_create_invite(bot, user.id, active.id, user.tg_id)
    except ChannelAccessError:
        await callback.answer(
            "Не удалось создать ссылку. Администратор уведомлён.", show_alert=True
        )
        await notify_admins(bot, f"🚨 Не удалось создать инвайт для {user.tg_id}")
        return

    if callback.message is not None:
        await callback.message.answer(
            access_granted_text(active.expires_at), reply_markup=join_keyboard(invite)
        )
    await callback.answer()


@router.message(Command("cancel"))
async def cmd_cancel(message: Message) -> None:
    await message.answer("Ок. Выберите действие в меню.")
