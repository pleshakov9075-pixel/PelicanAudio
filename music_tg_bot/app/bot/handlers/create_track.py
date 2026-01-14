from __future__ import annotations

import logging
from pathlib import Path

from aiogram import Router, F
from aiogram.enums import ChatAction
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext

from app.bot.keyboards.inline import (
    presets_keyboard,
    review_keyboard,
    text_payment_keyboard,
    title_keyboard,
    second_variant_keyboard,
)
from app.bot.keyboards.reply import main_menu
from app.bot.fsm.states import TrackStates
from app.core.db import SessionLocal
from app.core.repo import (
    get_or_create_user,
    consume_free_quota,
    charge_text,
    hold_audio,
)
from app.core.utils import build_auto_title, is_valid_title, sanitize_title
from app.integrations.genapi import call_grok, GenApiError
from app.presets.loader import load_presets, get_preset

router = Router()
logger = logging.getLogger("bot.create_track")

PROMPTS_DIR = Path(__file__).resolve().parents[2] / "presets" / "prompts"


def _load_prompt(name: str) -> str:
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")


def _build_grok_messages(system_text: str, user_text: str) -> list[dict]:
    return [
        {"role": "system", "content": [{"type": "text", "text": system_text}]},
        {"role": "user", "content": [{"type": "text", "text": user_text}]},
    ]


def _render_template(template: str, **kwargs: str) -> str:
    return template.format(**kwargs)


def _preset_line(preset: dict) -> str:
    return f"🎛 Пресет: {preset['title']}"


def _with_preset(preset: dict, text: str) -> str:
    return f"{_preset_line(preset)}\n{text}"


async def _send_or_edit_status(message: Message, state: FSMContext, text: str) -> int:
    data = await state.get_data()
    status_message_id = data.get("status_message_id")
    if status_message_id:
        try:
            await message.bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=status_message_id,
                text=text,
            )
            return status_message_id
        except Exception as exc:
            logger.warning("Не удалось обновить статусное сообщение: %s", exc)
    new_message = await message.answer(text)
    await state.update_data(status_message_id=new_message.message_id)
    return new_message.message_id


async def _generate_lyrics(preset: dict, brief: str) -> str:
    system_text = _load_prompt("grok_lyrics_system_ru.txt")
    user_template = _load_prompt("grok_lyrics_user_template.txt")
    user_text = _render_template(
        user_template,
        preset_title=preset["title"],
        preset_description=preset["description"],
        mood=preset["hints"]["mood"],
        vibe=preset["hints"]["vibe"],
        genre=preset["hints"]["genre"],
        short_form=str(preset["short_form"]).lower(),
        recommendations=preset["recommendations"],
        brief=brief,
    )
    return call_grok(_build_grok_messages(system_text, user_text))


async def _generate_tags(preset: dict, lyrics: str) -> str:
    system_text = _load_prompt("grok_tags_system_ru.txt")
    user_template = _load_prompt("grok_tags_user_template.txt")
    user_text = _render_template(
        user_template,
        preset_title=preset["title"],
        preset_description=preset["description"],
        mood=preset["hints"]["mood"],
        vibe=preset["hints"]["vibe"],
        genre=preset["hints"]["genre"],
        short_form=str(preset["short_form"]).lower(),
        lyrics=lyrics,
    )
    return call_grok(_build_grok_messages(system_text, user_text))


async def _generate_edit(lyrics: str, edit_request: str) -> str:
    system_text = _load_prompt("grok_edit_system_ru.txt")
    user_template = _load_prompt("grok_edit_user_template.txt")
    user_text = _render_template(user_template, lyrics=lyrics, edit_request=edit_request)
    return call_grok(_build_grok_messages(system_text, user_text))


def _consume_text_quota(user_id: int, paid_allowed: bool = False) -> tuple[bool, str]:
    with SessionLocal() as session:
        user = get_or_create_user(session, user_id)
        if consume_free_quota(session, user):
            return True, "free"
        if paid_allowed and charge_text(session, user):
            return True, "paid"
    return False, "denied"


@router.message(lambda message: message.text == "🎵 Создать трек")
async def start_create(message: Message, state: FSMContext) -> None:
    presets = load_presets()
    await state.clear()
    await message.answer("Выберите пресет:", reply_markup=presets_keyboard(presets))


@router.callback_query(lambda call: call.data.startswith("preset:"))
async def preset_selected(call: CallbackQuery, state: FSMContext) -> None:
    preset_id = call.data.split(":")[1]
    preset = get_preset(preset_id)
    if not preset:
        await call.message.answer("Пресет не найден.")
        await call.answer()
        return
    await state.update_data(preset_id=preset_id, used_new_variant=False)
    await state.set_state(TrackStates.waiting_for_brief)
    await call.message.answer(
        f"{_preset_line(preset)}\n\nОтправьте одним сообщением вводные для песни (brief).",
        reply_markup=main_menu(),
    )
    await call.answer()


@router.message(TrackStates.waiting_for_brief)
async def handle_brief(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    preset = get_preset(data["preset_id"])
    if not preset:
        await message.answer("Пресет не найден. Начните заново.")
        await state.clear()
        return

    allowed, mode = _consume_text_quota(message.from_user.id, paid_allowed=False)
    if not allowed:
        await message.answer(
            _with_preset(
                preset,
                "Лимит бесплатных генераций исчерпан. Хотите сгенерировать текст за 19 ₽?",
            ),
            reply_markup=text_payment_keyboard(),
        )
        await state.update_data(brief=message.text)
        return

    await state.update_data(brief=message.text)
    await _generate_and_review(message, state, preset, message.text)


@router.callback_query(lambda call: call.data == "textpay:confirm")
async def paid_text_confirm(call: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    brief = data.get("brief")
    preset = get_preset(data.get("preset_id", ""))
    if not brief or not preset:
        await call.message.answer("Данные не найдены. Начните заново.")
        await call.answer()
        return
    allowed, mode = _consume_text_quota(call.from_user.id, paid_allowed=True)
    if not allowed:
        await call.message.answer(_with_preset(preset, "Недостаточно средств на балансе."))
        await call.answer()
        return
    await _generate_and_review(call.message, state, preset, brief)
    await call.answer()


@router.callback_query(lambda call: call.data == "textpay:wait")
async def paid_text_wait(call: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    preset = get_preset(data.get("preset_id", "")) if data else None
    text = "Хорошо, возвращайтесь завтра за бесплатными генерациями."
    await call.message.answer(_with_preset(preset, text) if preset else text)
    await state.clear()
    await call.answer()


async def _generate_and_review(message: Message, state: FSMContext, preset: dict, brief: str) -> None:
    await _send_or_edit_status(
        message,
        state,
        _with_preset(preset, "⏳ Генерирую текст (≈15 сек)…"),
    )
    await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)
    try:
        lyrics = await _generate_lyrics(preset, brief)
        tags = await _generate_tags(preset, lyrics)
    except GenApiError as exc:
        logger.error("Ошибка генерации текста: %s", exc)
        await message.answer(_with_preset(preset, str(exc)))
        return
    await _send_or_edit_status(
        message,
        state,
        _with_preset(preset, "✅ Текст готов. Можно править или утвердить."),
    )
    await state.update_data(lyrics=lyrics, tags=tags)
    await state.set_state(TrackStates.waiting_for_review)
    await message.answer(
        f"{_preset_line(preset)}\n\nТекст песни:\n\n{lyrics}\n\nТеги: {tags}",
        reply_markup=review_keyboard(),
    )


@router.callback_query(lambda call: call.data.startswith("review:"))
async def review_actions(call: CallbackQuery, state: FSMContext) -> None:
    action = call.data.split(":")[1]
    data = await state.get_data()
    preset = get_preset(data.get("preset_id", ""))
    if not preset:
        await call.message.answer("Пресет не найден. Начните заново.")
        await state.clear()
        await call.answer()
        return

    if action == "approve":
        await state.set_state(TrackStates.waiting_for_title)
        await call.message.answer(
            f"{_preset_line(preset)}\n\n🎼 Введите название трека или нажмите 🎲 Автоназвание",
            reply_markup=title_keyboard(),
        )
    elif action == "edit":
        await state.set_state(TrackStates.waiting_for_edit)
        await call.message.answer(f"{_preset_line(preset)}\n\nНапишите, что поправить в тексте.")
    elif action == "regen":
        if data.get("used_new_variant"):
            await call.message.answer(_with_preset(preset, "Новый вариант уже был использован."))
            await call.answer()
            return
        allowed, mode = _consume_text_quota(call.from_user.id, paid_allowed=True)
        if not allowed:
            await call.message.answer(_with_preset(preset, "Недостаточно средств для нового варианта."))
            await call.answer()
            return
        brief = data.get("brief", "")
        await state.update_data(used_new_variant=True)
        await _generate_and_review(call.message, state, preset, brief)
    elif action == "cancel":
        await state.clear()
        await call.message.answer("Отмена. Выберите действие в меню.", reply_markup=main_menu())
    await call.answer()


@router.message(TrackStates.waiting_for_edit)
async def handle_edit(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    preset = get_preset(data.get("preset_id", ""))
    lyrics = data.get("lyrics")
    if not preset or not lyrics:
        await message.answer("Данные не найдены. Начните заново.")
        await state.clear()
        return
    await _send_or_edit_status(message, state, _with_preset(preset, "⏳ Вношу правки (≈15 сек)…"))
    await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)
    try:
        new_lyrics = await _generate_edit(lyrics, message.text)
        tags = await _generate_tags(preset, new_lyrics)
    except GenApiError as exc:
        logger.error("Ошибка правок текста: %s", exc)
        await message.answer(_with_preset(preset, str(exc)))
        return
    await _send_or_edit_status(
        message,
        state,
        _with_preset(preset, "✅ Текст готов. Можно править или утвердить."),
    )
    await state.update_data(lyrics=new_lyrics, tags=tags)
    await state.set_state(TrackStates.waiting_for_review)
    await message.answer(
        f"{_preset_line(preset)}\n\nОбновлённый текст:\n\n{new_lyrics}\n\nТеги: {tags}",
        reply_markup=review_keyboard(),
    )


@router.callback_query(lambda call: call.data == "title:auto")
async def handle_auto_title(call: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    preset = get_preset(data.get("preset_id", ""))
    if not preset:
        await call.message.answer("Пресет не найден.")
        await call.answer()
        return
    brief = data.get("brief", "")
    title = build_auto_title(preset["title"], brief)
    await state.update_data(title=title)
    await _finalize_track(call.message, state, preset, title)
    await call.answer()


@router.message(TrackStates.waiting_for_title)
async def handle_title(message: Message, state: FSMContext) -> None:
    if not is_valid_title(message.text):
        await message.answer("Некорректное название. Убедитесь, что нет запрещённых символов.")
        return
    data = await state.get_data()
    preset = get_preset(data.get("preset_id", ""))
    if not preset:
        await message.answer("Пресет не найден.")
        await state.clear()
        return
    title = sanitize_title(message.text)
    await state.update_data(title=title)
    await _finalize_track(message, state, preset, title)


async def _finalize_track(message: Message, state: FSMContext, preset: dict, title: str) -> None:
    data = await state.get_data()
    lyrics = data.get("lyrics")
    tags = data.get("tags")
    if not lyrics or not tags:
        await message.answer("Нет данных для генерации. Начните заново.")
        await state.clear()
        return
    amount = preset["price_audio_rub"]
    with SessionLocal() as session:
        user = get_or_create_user(session, message.from_user.id)
        transaction = hold_audio(session, user, amount)
    if not transaction:
        await message.answer(_with_preset(preset, "Недостаточно средств для аудио. Пополните баланс."))
        await state.clear()
        return
    status_message = await message.answer(
        _with_preset(preset, "⏳ Генерирую аудио (≈3 минуты)…"),
        reply_markup=main_menu(),
    )
    await state.clear()
    from app.worker.tasks import enqueue_audio_generation

    job_id = enqueue_audio_generation(
        user_id=user.id,
        chat_id=message.chat.id,
        preset_id=preset["id"],
        preset_title=preset["title"],
        title=title,
        lyrics=lyrics,
        tags=tags,
        transaction_id=transaction.id,
        status_message_id=status_message.message_id,
    )
    logger.info("Трек поставлен в очередь: %s", job_id)


@router.callback_query(lambda call: call.data.startswith("track:second:"))
async def send_second_variant(call: CallbackQuery) -> None:
    from app.worker.tasks import deliver_second_variant

    track_id = int(call.data.split(":")[2])
    await deliver_second_variant(track_id, call.message.chat.id)
    await call.answer()
