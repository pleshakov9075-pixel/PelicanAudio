from aiogram.types import ReplyKeyboardMarkup, KeyboardButton


def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🎵 Создать трек")],
            [KeyboardButton(text="⭐ Пресеты"), KeyboardButton(text="💳 Баланс")],
            [KeyboardButton(text="❓ Помощь")],
        ],
        resize_keyboard=True,
    )
