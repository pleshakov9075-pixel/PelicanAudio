from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import Message

from app.bot.keyboards.reply import main_menu
from app.core.db import SessionLocal
from app.core.repo import apply_welcome_bonus, get_or_create_user
from app.presets.loader import get_starter_preset

router = Router()


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    with SessionLocal() as session:
        user = get_or_create_user(session, message.from_user.id)
        starter = get_starter_preset()
        bonus_message = ""
        if starter:
            granted = apply_welcome_bonus(session, user, starter["price_audio_rub"])
            if granted:
                bonus_message = f"\n\n🎁 Стартовый бонус: {starter['price_audio_rub']} ₽ — хватит на 1 трек."
    await message.answer(
        f"Привет! Я помогу создать трек. Выбери действие в меню ниже.{bonus_message}",
        reply_markup=main_menu(),
    )
