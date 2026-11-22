from __future__ import annotations

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

from bot.config import format_days
from bot.db.models import Plan


def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="💳 Купить подписку"), KeyboardButton(text="📅 Моя подписка")],
            [KeyboardButton(text="❓ Помощь")],
        ],
        resize_keyboard=True,
    )


def plans_keyboard(plans: list[Plan]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for plan in plans:
        rows.append(
            [
                InlineKeyboardButton(
                    text=(
                        f"{plan.title} — {plan.stars_price} ⭐ "
                        f"({format_days(plan.duration_days)})"
                    ),
                    callback_data=f"buy:{plan.code}",
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def join_keyboard(url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🔑 Войти в канал", url=url)]]
    )


def subscription_keyboard(can_join: bool, has_active: bool) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if has_active:
        rows.append(
            [InlineKeyboardButton(text="🔄 Продлить подписку", callback_data="buy_menu")]
        )
        if can_join:
            rows.append(
                [
                    InlineKeyboardButton(
                        text="🔗 Получить ссылку на канал",
                        callback_data="get_link",
                    )
                ]
            )
    else:
        rows.append(
            [InlineKeyboardButton(text="💳 Оформить подписку", callback_data="buy_menu")]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def back_to_plans() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="« К тарифам", callback_data="buy_menu")]
        ]
    )
