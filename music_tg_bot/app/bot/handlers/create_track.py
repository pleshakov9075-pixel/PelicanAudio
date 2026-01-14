from __future__ import annotations

import logging

from aiogram import Router
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext

from app.bot.keyboards.inline import (
    categories_keyboard,
    presets_keyboard,
    text_payment_keyboard,
    title_keyboard,
)
from app.bot.keyboards.reply import main_menu
from app.bot.fsm.states import TrackStates
from app.core.db import SessionLocal
from app.core.repo import (
    adjust_balance,
    get_or_create_user,
    get_balance,
    consume_free_quota,
    charge_text,
    create_task,
    get_task,
    update_task,
)
from app.core.task_status import (
    AUDIO_QUEUED,
    CANCELED,
    EDIT_QUEUED,
    PAYMENT_WAITING,
    REVIEW_READY,
    TEXT_QUEUED,
    TITLE_WAITING,
    WAITING_EDIT_REQUEST,
)
from app.core.utils import build_auto_title, is_valid_title, sanitize_title
from app.presets.loader import load_categories, get_presets_by_category, get_preset

router = Router()
logger = logging.getLogger("bot.create_track")


_EDIT_CANCEL_KEYWORDS = {
    "нет",
    "не надо",
    "ничего",
    "отмена",
    "cancel",
    "no",
}


def _preset_line(preset: dict, balance: int | None = None) -> str:
    line = f"🎛 Пресет: {preset['title']}\nЦена аудио: {preset['price_audio_rub']} ₽"
    if balance is not None:
        line = f"{line}\nБаланс: {balance} ₽"
    return line


def _with_preset(preset: dict, text: str, balance: int | None = None) -> str:
    return f"{_preset_line(preset, balance=balance)}\n{text}"


def _get_user_balance(user_id: int) -> int:
    with SessionLocal() as session:
        user = get_or_create_user(session, user_id)
        return user.balance_rub


def _is_edit_cancel(text: str | None) -> bool:
    normalized = (text or "").strip().lower()
    if not normalized:
        return True
    if normalized in _EDIT_CANCEL_KEYWORDS:
        return True
    if normalized in {".", ".."}:
        return True
    return False


async def _send_or_edit_progress(message: Message, task_id: int, text: str) -> int:
    with SessionLocal() as session:
        task = get_task(session, task_id)
        status_message_id = task.progress_message_id if task else None
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
    with SessionLocal() as session:
        update_task(
            session,
            task_id,
            progress_chat_id=message.chat.id,
            progress_message_id=new_message.message_id,
        )
    return new_message.message_id


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
    await state.clear()
    categories = load_categories()
    await message.answer("Выберите категорию:", reply_markup=categories_keyboard(categories, prefix="create_category"))


@router.callback_query(lambda call: call.data.startswith("create_category:"))
async def create_category_selected(call: CallbackQuery) -> None:
    category_id = call.data.split(":")[1]
    presets = get_presets_by_category(category_id)
    if not presets:
        await call.message.answer("В этой категории пока нет пресетов.")
        await call.answer()
        return
    await call.message.answer("Выберите пресет:", reply_markup=presets_keyboard(presets))
    await call.answer()


@router.callback_query(lambda call: call.data.startswith("preset:"))
async def preset_selected(call: CallbackQuery, state: FSMContext) -> None:
    preset_id = call.data.split(":")[1]
    preset = get_preset(preset_id)
    if not preset:
        await call.message.answer("Пресет не найден.")
        await call.answer()
        return
    balance = _get_user_balance(call.from_user.id)
    await state.update_data(preset_id=preset_id, used_new_variant=False)
    mode = preset.get("mode", "song")
    if mode == "user_lyrics":
        await state.set_state(TrackStates.waiting_for_user_lyrics_brief)
        await call.message.answer(
            f"{_preset_line(preset, balance=balance)}\n\nОтправьте одним сообщением стиль, настроение и жанр.",
            reply_markup=main_menu(),
        )
    elif mode == "instrumental":
        await state.set_state(TrackStates.waiting_for_brief)
        await call.message.answer(
            f"{_preset_line(preset, balance=balance)}\n\nОпишите инструментал: настроение, темп, инструменты, где будет играть.",
            reply_markup=main_menu(),
        )
    else:
        await state.set_state(TrackStates.waiting_for_brief)
        await call.message.answer(
            f"{_preset_line(preset, balance=balance)}\n\nОтправьте одним сообщением вводные для песни (brief).",
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
    await _queue_text_generation(message, state, preset, message.text)


@router.message(TrackStates.waiting_for_user_lyrics_brief)
async def handle_user_lyrics_brief(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    preset = get_preset(data["preset_id"])
    if not preset:
        await message.answer("Пресет не найден. Начните заново.")
        await state.clear()
        return
    await state.update_data(brief=message.text)
    await state.set_state(TrackStates.waiting_for_user_lyrics_text)
    await message.answer(_with_preset(preset, "Теперь отправьте ваш текст песни одним сообщением."))


@router.message(TrackStates.waiting_for_user_lyrics_text)
async def handle_user_lyrics_text(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    preset = get_preset(data.get("preset_id", ""))
    if not preset:
        await message.answer("Пресет не найден. Начните заново.")
        await state.clear()
        return
    allowed, mode = _consume_text_quota(message.from_user.id, paid_allowed=False)
    if not allowed:
        await message.answer(
            _with_preset(
                preset,
                "Лимит бесплатных генераций исчерпан. Хотите оформить текст за 19 ₽?",
            ),
            reply_markup=text_payment_keyboard(),
        )
        await state.update_data(user_lyrics_raw=message.text)
        return
    await state.update_data(user_lyrics_raw=message.text)
    await _queue_text_generation(message, state, preset, data.get("brief", ""), user_lyrics_raw=message.text)


@router.callback_query(lambda call: call.data == "textpay:confirm")
async def paid_text_confirm(call: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    brief = data.get("brief")
    preset = get_preset(data.get("preset_id", ""))
    user_lyrics_raw = data.get("user_lyrics_raw")
    if not brief or not preset:
        await call.message.answer("Данные не найдены. Начните заново.")
        await call.answer()
        return
    allowed, mode = _consume_text_quota(call.from_user.id, paid_allowed=True)
    if not allowed:
        await call.message.answer(_with_preset(preset, "Недостаточно средств на балансе."))
        await call.answer()
        return
    await _queue_text_generation(call.message, state, preset, brief, user_lyrics_raw=user_lyrics_raw)
    await call.answer()


@router.callback_query(lambda call: call.data == "textpay:wait")
async def paid_text_wait(call: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    preset = get_preset(data.get("preset_id", "")) if data else None
    text = "Хорошо, возвращайтесь завтра за бесплатными генерациями."
    await call.message.answer(_with_preset(preset, text) if preset else text)
    await state.clear()
    await call.answer()


async def _queue_text_generation(
    message: Message,
    state: FSMContext,
    preset: dict,
    brief: str,
    user_lyrics_raw: str | None = None,
) -> None:
    mode = preset.get("mode", "song")
    if mode == "instrumental":
        status_text = "⏳ Генерирую описание инструментала…"
    elif mode == "user_lyrics":
        status_text = "⏳ Оформляю текст…"
    else:
        status_text = "⏳ Генерирую текст…"
    status_message = await message.answer(_with_preset(preset, status_text))
    with SessionLocal() as session:
        user = get_or_create_user(session, message.from_user.id)
        task = create_task(
            session,
            user_id=user.id,
            preset_id=preset["id"],
            status=TEXT_QUEUED,
            brief=brief,
            user_lyrics_raw=user_lyrics_raw,
            progress_chat_id=message.chat.id,
            progress_message_id=status_message.message_id,
        )
    await state.update_data(task_id=task.id, preset_id=preset["id"], brief=brief, user_lyrics_raw=user_lyrics_raw)
    await state.set_state(TrackStates.waiting_for_review)
    from app.worker.tasks import enqueue_text_generation

    job_id = enqueue_text_generation(task.id)
    logger.info("Текстовая генерация поставлена в очередь: %s", job_id)


async def _queue_regeneration(message: Message, state: FSMContext, preset: dict, brief: str) -> None:
    data = await state.get_data()
    task_id = data.get("task_id")
    user_lyrics_raw = data.get("user_lyrics_raw")
    if not task_id:
        await message.answer("Данные не найдены. Начните заново.")
        await state.clear()
        return
    await _send_or_edit_progress(message, task_id, _with_preset(preset, "⏳ Генерирую текст…"))
    with SessionLocal() as session:
        update_task(
            session,
            task_id,
            status=TEXT_QUEUED,
            brief=brief,
            lyrics_current=None,
            tags_current=None,
            error_message=None,
            genapi_request_id=None,
            user_lyrics_raw=user_lyrics_raw,
        )
    from app.worker.tasks import enqueue_text_generation

    job_id = enqueue_text_generation(task_id)
    logger.info("Повторная генерация поставлена в очередь: %s", job_id)


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
        task_id = data.get("task_id")
        if task_id:
            with SessionLocal() as session:
                update_task(session, task_id, status=TITLE_WAITING)
        await state.set_state(TrackStates.waiting_for_title)
        await call.message.answer(
            f"{_preset_line(preset)}\n\n🎼 Введите название трека или нажмите 🎲 Автоназвание",
            reply_markup=title_keyboard(),
        )
    elif action == "edit":
        task_id = data.get("task_id")
        if task_id:
            with SessionLocal() as session:
            update_task(session, task_id, status=WAITING_EDIT_REQUEST)
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
        await _queue_regeneration(call.message, state, preset, brief)
    elif action == "cancel":
        task_id = data.get("task_id")
        if task_id:
            with SessionLocal() as session:
                update_task(session, task_id, status=CANCELED)
        await state.clear()
        await call.message.answer("Отмена. Выберите действие в меню.", reply_markup=main_menu())
    await call.answer()


@router.message(TrackStates.waiting_for_edit)
async def handle_edit(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    preset = get_preset(data.get("preset_id", ""))
    task_id = data.get("task_id")
    if not preset or not task_id:
        await message.answer("Данные не найдены. Начните заново.")
        await state.clear()
        return
    if _is_edit_cancel(message.text):
        with SessionLocal() as session:
            task = get_task(session, task_id)
            lyrics = task.lyrics_current if task else None
            tags = task.tags_current if task else None
            update_task(session, task_id, status=REVIEW_READY, edit_request=None)
            balance = get_balance(session, message.from_user.id)
        if not lyrics or not tags:
            await message.answer("Нет данных для ревью. Начните заново.")
            await state.clear()
            return
        from app.bot.keyboards.inline import review_keyboard

        mode = preset.get("mode", "song")
        status_prefix = f"🎛 Пресет: {preset['title']}"
        if mode == "instrumental":
            body = f"Описание инструментала:\n\n{lyrics}"
        else:
            body = f"Текст песни:\n\n{lyrics}"
        price = preset.get("price_audio_rub", 0)
        await message.answer(
            text=(
                "Ок, оставляем как есть ✅\n\n"
                f"{status_prefix}\n\n{body}\n\nТеги: {tags}\n"
                f"Цена аудио: {price} ₽ | Баланс: {balance} ₽"
            ),
            reply_markup=review_keyboard(),
        )
        await state.set_state(TrackStates.waiting_for_review)
        return
    await _send_or_edit_progress(message, task_id, _with_preset(preset, "⏳ Применяю правки…"))
    with SessionLocal() as session:
        update_task(
            session,
            task_id,
            status=EDIT_QUEUED,
            edit_request=message.text,
        )
    await state.set_state(TrackStates.waiting_for_review)
    from app.worker.tasks import enqueue_edit_generation

    job_id = enqueue_edit_generation(task_id)
    logger.info("Правка поставлена в очередь: %s", job_id)


@router.callback_query(lambda call: call.data == "title:auto")
async def handle_auto_title(call: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    preset = get_preset(data.get("preset_id", ""))
    if not preset:
        await call.message.answer("Пресет не найден.")
        await call.answer()
        return
    brief = data.get("brief", "")
    task_id = data.get("task_id")
    suggested_title = None
    if task_id:
        with SessionLocal() as session:
            task = get_task(session, task_id)
            suggested_title = task.suggested_title if task else None
    title = sanitize_title(suggested_title) if suggested_title else build_auto_title(preset["title"], brief)
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
    task_id = data.get("task_id")
    if not task_id:
        await message.answer("Нет данных для генерации. Начните заново.")
        await state.clear()
        return
    with SessionLocal() as session:
        task = get_task(session, task_id)
        lyrics = task.lyrics_current if task else None
        tags = task.tags_current if task else None
        update_task(session, task_id, status=TITLE_WAITING, title_text=title)
    if not lyrics or not tags:
        await message.answer("Нет данных для генерации. Начните заново.")
        await state.clear()
        return
    amount = preset["price_audio_rub"]
    with SessionLocal() as session:
        balance = get_balance(session, message.from_user.id)
        try:
            adjust_balance(session, message.from_user.id, -amount, "spend_audio", task_id=task_id)
            charged = True
        except ValueError:
            charged = False
    if not charged:
        with SessionLocal() as session:
            update_task(session, task_id, status=PAYMENT_WAITING)
        await message.answer(
            _with_preset(
                preset,
                f"Недостаточно средств для аудио. Цена: {amount} ₽, баланс: {balance} ₽.",
            )
        )
        await state.clear()
        return
    status_message = await message.answer(
        _with_preset(preset, "⏳ Генерирую аудио…"),
        reply_markup=main_menu(),
    )
    await state.clear()
    from app.worker.tasks import enqueue_audio_generation

    job_id = enqueue_audio_generation(
        task_id=task_id,
        chat_id=message.chat.id,
        status_message_id=status_message.message_id,
    )
    with SessionLocal() as session:
        update_task(
            session,
            task_id,
            status=AUDIO_QUEUED,
            progress_chat_id=message.chat.id,
            progress_message_id=status_message.message_id,
        )
    logger.info("Трек поставлен в очередь: %s", job_id)


@router.callback_query(lambda call: call.data.startswith("track:second:"))
async def send_second_variant(call: CallbackQuery) -> None:
    from app.worker.tasks import deliver_second_variant

    track_id = int(call.data.split(":")[2])
    await deliver_second_variant(track_id, call.message.chat.id)
    await call.answer()
