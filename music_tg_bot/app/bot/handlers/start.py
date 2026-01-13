from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import Message

from app.bot.keyboards.reply import main_menu
from app.core.db import SessionLocal
from app.core.repo import get_or_create_user

router = Router()


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    with SessionLocal() as session:
        get_or_create_user(session, message.from_user.id)
    await message.answer(
        "Привет! Я помогу создать трек. Выбери действие в меню ниже.",
        reply_markup=main_menu(),
    )
