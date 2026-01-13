from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton


def presets_keyboard(presets: list[dict]) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text=preset["title"], callback_data=f"preset:{preset['id']}")]
        for preset in presets
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def review_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Утвердить", callback_data="review:approve")],
            [InlineKeyboardButton(text="✏️ Правка", callback_data="review:edit")],
            [InlineKeyboardButton(text="🎲 Новый вариант", callback_data="review:regen")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="review:cancel")],
        ]
    )


def text_payment_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💳 Сгенерировать текст за 19 ₽", callback_data="textpay:confirm")],
            [InlineKeyboardButton(text="⏳ Подождать до завтра", callback_data="textpay:wait")],
        ]
    )


def title_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🎲 Автоназвание", callback_data="title:auto")]]
    )


def balance_keyboard() -> InlineKeyboardMarkup:
    options = [99, 199, 499, 999]
    buttons = [
        [InlineKeyboardButton(text=f"Пополнить {amount} ₽", callback_data=f"topup:{amount}")]
        for amount in options
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def second_variant_keyboard(track_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🎧 Второй вариант", callback_data=f"track:second:{track_id}")]
        ]
    )
