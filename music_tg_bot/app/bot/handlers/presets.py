from aiogram import Router
from aiogram.types import Message

from app.bot.keyboards.inline import presets_keyboard
from app.presets.loader import load_presets

router = Router()


@router.message(lambda message: message.text == "⭐ Пресеты")
async def show_presets(message: Message) -> None:
    presets = load_presets()
    await message.answer("Выберите пресет:", reply_markup=presets_keyboard(presets))
