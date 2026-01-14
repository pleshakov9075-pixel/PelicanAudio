from aiogram import Router
from aiogram.filters import Command
from aiogram.filters.command import CommandObject
from aiogram.types import Message

from app.core.config import settings
from app.core.db import SessionLocal
from app.core.repo import add_balance, get_balance, set_balance

router = Router()


def _is_admin(message: Message) -> bool:
    if not message.from_user:
        return False
    return message.from_user.id in settings.admin_ids


def _parse_single_int_arg(command: CommandObject) -> int | None:
    if not command.args:
        return None
    parts = command.args.split()
    if len(parts) != 1:
        return None
    try:
        return int(parts[0])
    except ValueError:
        return None


def _parse_two_int_args(command: CommandObject) -> tuple[int, int] | None:
    if not command.args:
        return None
    parts = command.args.split()
    if len(parts) != 2:
        return None
    try:
        first = int(parts[0])
        second = int(parts[1])
    except ValueError:
        return None
    return first, second


@router.message(Command("dev_balance"))
async def dev_balance(message: Message) -> None:
    if not _is_admin(message):
        await message.answer("⛔ Команда недоступна")
        return
    with SessionLocal() as session:
        balance = get_balance(session, message.from_user.id)
    await message.answer(f"💳 Баланс: {balance} ₽")


@router.message(Command("dev_add_balance"))
async def dev_add_balance(message: Message, command: CommandObject) -> None:
    if not _is_admin(message):
        await message.answer("⛔ Команда недоступна")
        return
    amount = _parse_single_int_arg(command)
    if amount is None:
        await message.answer("Использование: /dev_add_balance 1000")
        return
    if amount < 0:
        await message.answer("Сумма должна быть положительной")
        return
    with SessionLocal() as session:
        balance = add_balance(session, message.from_user.id, amount)
    await message.answer(f"💳 Баланс пополнен на {amount} ₽. Текущий баланс: {balance} ₽")


@router.message(Command("dev_set_balance"))
async def dev_set_balance(message: Message, command: CommandObject) -> None:
    if not _is_admin(message):
        await message.answer("⛔ Команда недоступна")
        return
    amount = _parse_single_int_arg(command)
    if amount is None:
        await message.answer("Использование: /dev_set_balance 500")
        return
    if amount < 0:
        await message.answer("Сумма должна быть положительной")
        return
    with SessionLocal() as session:
        balance = set_balance(session, message.from_user.id, amount)
    await message.answer(f"💳 Баланс установлен: {balance} ₽")


@router.message(Command("dev_give_balance"))
async def dev_give_balance(message: Message, command: CommandObject) -> None:
    if not _is_admin(message):
        await message.answer("⛔ Команда недоступна")
        return
    parsed = _parse_two_int_args(command)
    if parsed is None:
        await message.answer("Использование: /dev_give_balance 123456789 200")
        return
    tg_id, amount = parsed
    if amount < 0:
        await message.answer("Сумма должна быть положительной")
        return
    with SessionLocal() as session:
        balance = add_balance(session, tg_id, amount)
    await message.answer(
        f"✅ Начислено {amount} ₽ пользователю {tg_id}. Баланс теперь: {balance} ₽"
    )
