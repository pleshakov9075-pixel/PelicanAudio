from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import Message

from app.bot.keyboards.reply import main_menu
from app.core.db import SessionLocal
from app.core.repo import apply_welcome_bonus, get_or_create_user

router = Router()


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    welcome_bonus_rub = 79
    with SessionLocal() as session:
        user = get_or_create_user(session, message.from_user.id)
        bonus_message = ""
        granted = apply_welcome_bonus(session, user, welcome_bonus_rub)
        if granted:
            bonus_message = f"\n\n🎁 Стартовый бонус: {welcome_bonus_rub} ₽ — хватит на 1 трек."
    await message.answer(
        f"Привет! Я помогу создать трек. Выбери действие в меню ниже.{bonus_message}",
        reply_markup=main_menu(),
    )
