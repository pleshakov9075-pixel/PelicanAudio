from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton


def categories_keyboard(categories: list[dict], prefix: str = "category") -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text=category["title"], callback_data=f"{prefix}:{category['id']}")]
        for category in categories
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def presets_keyboard(presets: list[dict]) -> InlineKeyboardMarkup:
    buttons = [
        [
            InlineKeyboardButton(
                text=f"{preset['title']} — {preset['price_audio_rub']} ₽",
                callback_data=f"preset:{preset['id']}",
            )
        ]
        for preset in presets
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def presets_info_keyboard(preset_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Выбрать пресет", callback_data=f"preset:{preset_id}")]]
    )


def presets_info_list_keyboard(presets: list[dict]) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text=f"{preset['title']} — {preset['price_audio_rub']} ₽", callback_data=f"presetinfo:{preset['id']}")]
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


def text_payment_keyboard(price_rub: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"💳 Сгенерировать текст за {price_rub} ₽", callback_data="textpay:pay")],
            [InlineKeyboardButton(text="⏳ Подождать до завтра", callback_data="textpay:wait")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="textpay:cancel")],
        ]
    )


def text_payment_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Подтвердить", callback_data="textpay:confirm")],
            [InlineKeyboardButton(text="↩️ Назад", callback_data="textpay:back")],
        ]
    )


def audio_payment_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Подтвердить", callback_data="audiopay:confirm")],
            [InlineKeyboardButton(text="↩️ Назад", callback_data="audiopay:back")],
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
