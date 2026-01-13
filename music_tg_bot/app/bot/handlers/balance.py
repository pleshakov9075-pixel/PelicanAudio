import logging

from aiogram import Router
from aiogram.types import Message, CallbackQuery

from app.bot.keyboards.inline import balance_keyboard
from app.core.db import SessionLocal
from app.core.repo import get_or_create_user
from app.integrations.yookassa import create_payment, YooKassaError

router = Router()
logger = logging.getLogger("bot.balance")


@router.message(lambda message: message.text == "💳 Баланс")
async def show_balance(message: Message) -> None:
    with SessionLocal() as session:
        user = get_or_create_user(session, message.from_user.id)
        balance = user.balance_rub
    await message.answer(
        f"Ваш баланс: {balance} ₽\nВыберите сумму пополнения:",
        reply_markup=balance_keyboard(),
    )


@router.callback_query(lambda call: call.data.startswith("topup:"))
async def handle_topup(call: CallbackQuery) -> None:
    amount = int(call.data.split(":")[1])
    with SessionLocal() as session:
        user = get_or_create_user(session, call.from_user.id)
    try:
        payment_url = create_payment(amount, f"Пополнение баланса на {amount} ₽", user.id)
    except YooKassaError as exc:
        logger.error("Ошибка YooKassa: %s", exc)
        await call.message.answer(str(exc))
        await call.answer()
        return
    if not payment_url:
        await call.message.answer("Не удалось создать платеж. Попробуйте позже.")
        await call.answer()
        return
    await call.message.answer(f"Перейдите по ссылке для оплаты: {payment_url}")
    await call.answer()
