from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from bot.config import format_days, settings
from bot.db.repo import list_active_plans
from bot.keyboards import main_menu, plans_keyboard, subscription_keyboard
from bot.services.subscriptions import get_active_subscription, get_latest_subscription
from bot.services.users import format_dt, touch_user

router = Router(name="user")

WELCOME = (
    "👋 <b>Добро пожаловать!</b>\n\n"
    "Это бот подписки на закрытый канал «{channel}».\n\n"
    "Что внутри:\n"
    "• закрытые материалы, которых нет в открытом доступе;\n"
    "• доступ на {p1} или {p3};\n"
    "• автоматическая выдача доступа сразу после оплаты.\n\n"
    "Оплата проходит в Telegram Stars ⭐ — без карты и лишних шагов."
)

HELP_TEXT = (
    "❓ <b>Помощь</b>\n\n"
    "<b>Как оплатить?</b>\n"
    "Нажмите «Купить подписку», выберите тариф и подтвердите оплату в Stars.\n\n"
    "<b>Как войти в канал?</b>\n"
    "После оплаты бот пришлёт персональную ссылку — нажмите «Войти в канал». "
    "Ссылка одноразовая.\n\n"
    "<b>Когда закрывается доступ?</b>\n"
    "Автоматически в момент окончания подписки. Продление суммирует срок — "
    "перерыва в доступе не будет.\n\n"
    "<b>Продление</b>\n"
    "Купите любой тариф повторно: дни добавятся к текущему сроку.\n\n"
    "Остались вопросы — напишите администратору."
)


async def _plans_text() -> str:
    plans = await list_active_plans()
    lines = ["💳 <b>Выберите тариф</b>\n"]
    for plan in plans:
        lines.append(
            f"• <b>{plan.title}</b> — {plan.stars_price} ⭐ "
            f"({format_days(plan.duration_days)} доступа)"
        )
    lines.append("\nОплата в Telegram Stars. Доступ выдаётся автоматически.")
    return "\n".join(lines)


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    if message.from_user is None:
        return
    await touch_user(
        message.from_user.id, message.from_user.username, message.from_user.first_name
    )
    plans = await list_active_plans()
    p1 = next((p for p in plans if p.code == "m1"), None)
    p3 = next((p for p in plans if p.code == "m3"), None)

    text = WELCOME.format(
        channel=settings.channel_title,
        p1=format_days(p1.duration_days) if p1 else "месяц",
        p3=format_days(p3.duration_days) if p3 else "три месяца",
    )
    await message.answer(text, reply_markup=main_menu())
    await message.answer(
        await _plans_text(), reply_markup=plans_keyboard(plans)
    )


@router.message(Command("help"))
@router.message(F.text == "❓ Помощь")
async def cmd_help(message: Message) -> None:
    text = HELP_TEXT
    if settings.channel_public_link:
        text += f"\n\nКанал: {settings.channel_public_link}"
    await message.answer(text, reply_markup=main_menu())


@router.message(Command("myid"))
async def cmd_myid(message: Message) -> None:
    """Помогает узнать свой Telegram ID для ADMIN_IDS."""
    if message.from_user is None:
        return
    await message.answer(
        f"Ваш Telegram ID: <code>{message.from_user.id}</code>\n\n"
        "Добавьте его в <code>ADMIN_IDS</code> в файле .env "
        "(несколько ID — через запятую) и перезапустите бота."
    )


@router.message(F.text == "💳 Купить подписку")
@router.message(Command("buy"))
async def cmd_buy(message: Message) -> None:
    plans = await list_active_plans()
    if not plans:
        await message.answer("Тарифы временно недоступны. Попробуйте позже.")
        return
    await message.answer(await _plans_text(), reply_markup=plans_keyboard(plans))


@router.message(F.text == "📅 Моя подписка")
@router.message(Command("sub"))
async def cmd_subscription(message: Message) -> None:
    if message.from_user is None:
        return
    user = await touch_user(
        message.from_user.id, message.from_user.username, message.from_user.first_name
    )
    active = await get_active_subscription(user.id)

    if active is None:
        latest = await get_latest_subscription(user.id)
        if latest is None:
            text = (
                "📅 <b>Моя подписка</b>\n\n"
                "У вас пока нет подписки.\n"
                "Оформите доступ к каналу — это займёт меньше минуты."
            )
        else:
            text = (
                "📅 <b>Моя подписка</b>\n\n"
                "Подписка не активна.\n"
                f"Последний срок истёк: {format_dt(latest.expires_at, settings.timezone)}.\n\n"
                "Продлите доступ, чтобы вернуться в канал."
            )
        await message.answer(
            text, reply_markup=subscription_keyboard(can_join=False, has_active=False)
        )
        return

    text = (
        "📅 <b>Моя подписка</b>\n\n"
        f"Статус: <b>активна</b>\n"
        f"Действует до: <b>{format_dt(active.expires_at, settings.timezone)}</b>\n\n"
        "Продление добавляет дни к текущему сроку."
    )
    await message.answer(
        text, reply_markup=subscription_keyboard(can_join=True, has_active=True)
    )
