from aiogram import Router
from aiogram.types import Message, CallbackQuery

from app.bot.keyboards.inline import (
    categories_keyboard,
    presets_info_keyboard,
    presets_info_list_keyboard,
)
from app.presets.loader import load_categories, get_presets_by_category, get_preset

router = Router()


@router.message(lambda message: message.text == "⭐ Пресеты")
async def show_presets(message: Message) -> None:
    categories = load_categories()
    await message.answer(
        "Выберите категорию:",
        reply_markup=categories_keyboard(categories, prefix="preset_category"),
    )


@router.callback_query(lambda call: call.data.startswith("preset_category:"))
async def show_presets_by_category(call: CallbackQuery) -> None:
    category_id = call.data.split(":")[1]
    presets = get_presets_by_category(category_id)
    if not presets:
        await call.message.answer("В этой категории пока нет пресетов.")
        await call.answer()
        return
    await call.message.answer("Выберите пресет:", reply_markup=presets_info_list_keyboard(presets))
    await call.answer()


@router.callback_query(lambda call: call.data.startswith("presetinfo:"))
async def show_preset_info(call: CallbackQuery) -> None:
    preset_id = call.data.split(":")[1]
    preset = get_preset(preset_id)
    if not preset:
        await call.message.answer("Пресет не найден.")
        await call.answer()
        return
    description = preset.get("description", "")
    price = preset.get("price_audio_rub", 0)
    await call.message.answer(
        f"🎛 Пресет: {preset['title']}\n{description}\nЦена аудио: {price} ₽",
        reply_markup=presets_info_keyboard(preset_id),
    )
    await call.answer()
