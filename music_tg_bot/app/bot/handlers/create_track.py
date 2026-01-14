from __future__ import annotations

import logging

from aiogram import Router
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext

from app.bot.keyboards.inline import (
    categories_keyboard,
    presets_keyboard,
    text_payment_keyboard,
    text_payment_confirm_keyboard,
    title_keyboard,
    audio_payment_confirm_keyboard,
)
from app.bot.keyboards.reply import main_menu
from app.bot.fsm.states import TrackStates
from app.core.db import SessionLocal
from app.core.repo import (
    adjust_balance,
    get_balance,
    get_free_quota_remaining,
    get_or_create_user,
    consume_free_quota,
    InsufficientFunds,
    FREE_QUOTA_PER_DAY,
    TEXT_PRICE_RUB,
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


def _title_line(title: str | None) -> str:
    safe_title = (title or "").strip()
    return f"🎼 Название: {safe_title}" if safe_title else "🎼 Название: —"


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


async def _send_or_edit_progress(
    message: Message,
    task_id: int,
    text: str,
    reply_markup=None,
) -> int:
    with SessionLocal() as session:
        task = get_task(session, task_id)
        status_message_id = task.progress_message_id if task else None
    if status_message_id:
        try:
            await message.bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=status_message_id,
                text=text,
                reply_markup=reply_markup,
            )
            return status_message_id
        except Exception as exc:
            logger.warning("Не удалось обновить статусное сообщение: %s", exc)
    new_message = await message.answer(text, reply_markup=reply_markup)
    with SessionLocal() as session:
        update_task(
            session,
            task_id,
            progress_chat_id=message.chat.id,
            progress_message_id=new_message.message_id,
        )
    return new_message.message_id


def _free_text_remaining_line(remaining: int) -> str:
    safe_remaining = max(0, remaining)
    return f"📝 Бесплатных текстов сегодня: {safe_remaining}/{FREE_QUOTA_PER_DAY}"


def _paid_text_offer_message(balance: int) -> str:
    return (
        f"Лимит бесплатных текстов исчерпан ({FREE_QUOTA_PER_DAY}/{FREE_QUOTA_PER_DAY}).\n"
        f"Стоимость генерации текста: {TEXT_PRICE_RUB} ₽.\n"
        f"Баланс: {balance} ₽"
    )


def _paid_text_confirm_message(balance: int) -> str:
    return f"Цена: {TEXT_PRICE_RUB} ₽ | Баланс: {balance} ₽"


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

    with SessionLocal() as session:
        user = get_or_create_user(session, message.from_user.id)
        remaining = get_free_quota_remaining(session, user)
        balance = user.balance_rub
        if remaining <= 0:
            await message.answer(
                _with_preset(preset, _paid_text_offer_message(balance)),
                reply_markup=text_payment_keyboard(TEXT_PRICE_RUB),
            )
            await state.update_data(brief=message.text, pending_text_action="generate")
            return
        consume_free_quota(session, user)
    await message.answer(_with_preset(preset, _free_text_remaining_line(remaining)))

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
    with SessionLocal() as session:
        user = get_or_create_user(session, message.from_user.id)
        remaining = get_free_quota_remaining(session, user)
        balance = user.balance_rub
        if remaining <= 0:
            await message.answer(
                _with_preset(preset, _paid_text_offer_message(balance)),
                reply_markup=text_payment_keyboard(TEXT_PRICE_RUB),
            )
            await state.update_data(user_lyrics_raw=message.text, pending_text_action="user_lyrics")
            return
        consume_free_quota(session, user)
    await message.answer(_with_preset(preset, _free_text_remaining_line(remaining)))
    await state.update_data(user_lyrics_raw=message.text)
    await _queue_text_generation(message, state, preset, data.get("brief", ""), user_lyrics_raw=message.text)


@router.callback_query(lambda call: call.data == "textpay:pay")
async def paid_text_start(call: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    preset = get_preset(data.get("preset_id", ""))
    if not preset:
        await call.message.answer("Пресет не найден. Начните заново.")
        await call.answer()
        return
    with SessionLocal() as session:
        balance = get_balance(session, call.from_user.id)
    await call.message.answer(
        _with_preset(preset, _paid_text_confirm_message(balance)),
        reply_markup=text_payment_confirm_keyboard(),
    )
    await call.answer()


@router.callback_query(lambda call: call.data == "textpay:back")
async def paid_text_back(call: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    preset = get_preset(data.get("preset_id", ""))
    if not preset:
        await call.message.answer("Пресет не найден. Начните заново.")
        await call.answer()
        return
    with SessionLocal() as session:
        balance = get_balance(session, call.from_user.id)
    await call.message.answer(
        _with_preset(preset, _paid_text_offer_message(balance)),
        reply_markup=text_payment_keyboard(TEXT_PRICE_RUB),
    )
    await call.answer()


@router.callback_query(lambda call: call.data == "textpay:confirm")
async def paid_text_confirm(call: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    preset = get_preset(data.get("preset_id", ""))
    pending_action = data.get("pending_text_action")
    brief = data.get("brief")
    user_lyrics_raw = data.get("user_lyrics_raw")
    if not preset or not pending_action:
        await call.message.answer("Данные не найдены. Начните заново.")
        await call.answer()
        return
    with SessionLocal() as session:
        try:
            adjust_balance(session, call.from_user.id, -TEXT_PRICE_RUB, "spend_text")
        except InsufficientFunds:
            current_balance = get_balance(session, call.from_user.id)
            await call.message.answer(
                _with_preset(preset, f"Недостаточно средств. Баланс: {current_balance} ₽.")
            )
            await call.answer()
            return
    await state.update_data(pending_text_action=None)
    if pending_action == "regen":
        await state.update_data(used_new_variant=True)
        await _queue_regeneration(call.message, state, preset, data.get("brief", ""))
    else:
        if not brief:
            await call.message.answer("Данные не найдены. Начните заново.")
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


@router.callback_query(lambda call: call.data == "textpay:cancel")
async def paid_text_cancel(call: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    preset = get_preset(data.get("preset_id", "")) if data else None
    text = "Отмена. Выберите действие в меню."
    await call.message.answer(_with_preset(preset, text) if preset else text, reply_markup=main_menu())
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
        if task_id:
            await _send_or_edit_progress(
                call.message,
                task_id,
                f"{_preset_line(preset)}\n\n🎼 Введите название трека или нажмите 🎲 Автоназвание",
                reply_markup=title_keyboard(),
            )
        else:
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
        with SessionLocal() as session:
            user = get_or_create_user(session, call.from_user.id)
            remaining = get_free_quota_remaining(session, user)
            balance = user.balance_rub
            if remaining <= 0:
                await call.message.answer(
                    _with_preset(preset, _paid_text_offer_message(balance)),
                    reply_markup=text_payment_keyboard(TEXT_PRICE_RUB),
                )
                await state.update_data(pending_text_action="regen")
                await call.answer()
                return
            consume_free_quota(session, user)
        await call.message.answer(_with_preset(preset, _free_text_remaining_line(remaining)))
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
        with SessionLocal() as session:
            user = get_or_create_user(session, message.from_user.id)
            remaining = get_free_quota_remaining(session, user)
        await message.answer(
            text=(
                "Ок, оставляем как есть ✅\n\n"
                f"{status_prefix}\n\n{body}\n\nТеги: {tags}\n"
                f"{_free_text_remaining_line(remaining)}\n"
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
    await state.update_data(pending_audio_amount=amount)
    await state.set_state(TrackStates.waiting_for_audio_confirm)
    status_text = (
        f"{_preset_line(preset, balance=balance)}\n"
        f"{_title_line(title)}\n"
        f"Списать {amount} ₽ за аудио?"
    )
    await _send_or_edit_progress(
        message,
        task_id,
        status_text,
        reply_markup=audio_payment_confirm_keyboard(),
    )


@router.callback_query(lambda call: call.data == "audiopay:confirm")
async def audio_payment_confirm(call: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    preset = get_preset(data.get("preset_id", ""))
    task_id = data.get("task_id")
    amount = data.get("pending_audio_amount")
    if not preset or not task_id or amount is None:
        await call.message.answer("Данные не найдены. Начните заново.")
        await call.answer()
        return
    title_text = data.get("title")
    with SessionLocal() as session:
        task = get_task(session, task_id)
        title_text = title_text or (task.title_text if task else None)
        balance_before = get_balance(session, call.from_user.id)
        try:
            balance_after = adjust_balance(session, call.from_user.id, -amount, "spend_audio", task_id=task_id)
        except InsufficientFunds:
            current_balance = get_balance(session, call.from_user.id)
            update_task(session, task_id, status=PAYMENT_WAITING)
            await _send_or_edit_progress(
                call.message,
                task_id,
                (
                    f"{_preset_line(preset, balance=current_balance)}\n"
                    f"{_title_line(title_text)}\n"
                    f"Недостаточно средств для аудио. Цена: {amount} ₽."
                ),
            )
            await state.clear()
            await call.answer()
            return
    logger.info(
        "Списан баланс за аудио",
        extra={
            "task_id": task_id,
            "user_id": call.from_user.id,
            "balance_before": balance_before,
            "price": amount,
            "balance_after": balance_after,
        },
    )
    status_message_id = await _send_or_edit_progress(
        call.message,
        task_id,
        f"{_preset_line(preset)}\n{_title_line(title_text)}\n⏳ Генерирую аудио…",
        reply_markup=None,
    )
    await state.clear()
    from app.worker.tasks import enqueue_audio_generation

    job_id = enqueue_audio_generation(
        task_id=task_id,
        chat_id=call.message.chat.id,
        status_message_id=status_message_id,
    )
    with SessionLocal() as session:
        update_task(
            session,
            task_id,
            status=AUDIO_QUEUED,
            progress_chat_id=call.message.chat.id,
            progress_message_id=status_message_id,
        )
    logger.info("Трек поставлен в очередь: %s", job_id)
    await call.answer()


@router.callback_query(lambda call: call.data == "audiopay:back")
async def audio_payment_back(call: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    preset = get_preset(data.get("preset_id", ""))
    if not preset:
        await call.message.answer("Пресет не найден. Начните заново.")
        await call.answer()
        return
    await state.set_state(TrackStates.waiting_for_title)
    task_id = data.get("task_id")
    if task_id:
        await _send_or_edit_progress(
            call.message,
            task_id,
            f"{_preset_line(preset)}\n\n🎼 Введите название трека или нажмите 🎲 Автоназвание",
            reply_markup=title_keyboard(),
        )
    else:
        await call.message.answer(
            f"{_preset_line(preset)}\n\n🎼 Введите название трека или нажмите 🎲 Автоназвание",
            reply_markup=title_keyboard(),
        )
    await call.answer()


@router.callback_query(lambda call: call.data.startswith("track:second:"))
async def send_second_variant(call: CallbackQuery) -> None:
    from app.worker.tasks import deliver_second_variant

    track_id = int(call.data.split(":")[2])
    await deliver_second_variant(track_id, call.message.chat.id)
    await call.answer()
