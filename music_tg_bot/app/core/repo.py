from __future__ import annotations

import datetime as dt

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.models import User, Transaction, Track, Task


FREE_QUOTA_PER_DAY = 5
TEXT_PRICE_RUB = 10


class InsufficientFunds(ValueError):
    pass


def get_or_create_user(session: Session, tg_id: int) -> User:
    user = session.scalar(select(User).where(User.tg_id == tg_id))
    if user:
        return user
    user = User(tg_id=tg_id)
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def get_or_create_user_by_tg_id(session: Session, tg_id: int) -> User:
    return get_or_create_user(session, tg_id)


def get_balance(session: Session, tg_id: int) -> int:
    user = get_or_create_user_by_tg_id(session, tg_id)
    return user.balance_rub


def adjust_balance(
    session: Session,
    tg_id: int,
    delta: int,
    tx_type: str,
    task_id: int | None = None,
    external_id: str | None = None,
) -> int:
    user = session.scalar(select(User).where(User.tg_id == tg_id).with_for_update())
    if not user:
        user = User(tg_id=tg_id)
        session.add(user)
        session.flush()
    if delta == 0:
        return user.balance_rub
    new_balance = user.balance_rub + delta
    if delta < 0 and new_balance < 0:
        session.rollback()
        raise InsufficientFunds("Недостаточно средств")
    user.balance_rub = new_balance
    transaction = Transaction(
        user_id=user.id,
        amount_rub=delta,
        type=tx_type,
        status="capture",
        external_id=external_id,
        task_id=task_id,
    )
    session.add_all([user, transaction])
    session.commit()
    session.refresh(user)
    return user.balance_rub


def reset_quota_if_needed(session: Session, user: User) -> None:
    today = dt.date.today()
    if user.free_quota_date != today:
        user.free_quota_date = today
        user.free_quota_used = 0
        session.add(user)
        session.commit()


def get_free_quota_remaining(session: Session, user: User) -> int:
    reset_quota_if_needed(session, user)
    return max(0, FREE_QUOTA_PER_DAY - user.free_quota_used)


def consume_free_quota(session: Session, user: User) -> bool:
    reset_quota_if_needed(session, user)
    if user.free_quota_used >= FREE_QUOTA_PER_DAY:
        return False
    user.free_quota_used += 1
    session.add(user)
    session.commit()
    return True


def charge_text(session: Session, user: User) -> bool:
    try:
        adjust_balance(session, user.tg_id, -TEXT_PRICE_RUB, "spend_text")
    except InsufficientFunds:
        return False
    return True


def add_topup(session: Session, user: User, amount_rub: int, external_id: str) -> None:
    adjust_balance(session, user.tg_id, amount_rub, "topup", external_id=external_id)


def apply_welcome_bonus(session: Session, user: User, amount_rub: int) -> bool:
    if user.welcome_bonus_given:
        return False
    adjust_balance(session, user.tg_id, amount_rub, "welcome_bonus")
    user.welcome_bonus_given = True
    session.add(user)
    session.commit()
    return True


def create_track(
    session: Session,
    user_id: int,
    preset_id: str,
    title: str,
    lyrics: str,
    tags: str,
    mp3_url_1: str,
    mp3_url_2: str,
    ttl_hours: int = 24,
) -> Track:
    expires_at = dt.datetime.utcnow() + dt.timedelta(hours=ttl_hours)
    track = Track(
        user_id=user_id,
        preset_id=preset_id,
        title=title,
        lyrics=lyrics,
        tags=tags,
        mp3_url_1=mp3_url_1,
        mp3_url_2=mp3_url_2,
        expires_at=expires_at,
    )
    session.add(track)
    session.commit()
    session.refresh(track)
    return track


def create_task(
    session: Session,
    user_id: int,
    preset_id: str,
    status: str,
    brief: str | None = None,
    user_lyrics_raw: str | None = None,
    progress_chat_id: int | None = None,
    progress_message_id: int | None = None,
) -> Task:
    task = Task(
        user_id=user_id,
        preset_id=preset_id,
        status=status,
        brief=brief,
        user_lyrics_raw=user_lyrics_raw,
        progress_chat_id=progress_chat_id,
        progress_message_id=progress_message_id,
    )
    session.add(task)
    session.commit()
    session.refresh(task)
    return task


def get_task(session: Session, task_id: int) -> Task | None:
    return session.get(Task, task_id)


def update_task(session: Session, task_id: int, **fields: object) -> Task | None:
    task = session.get(Task, task_id)
    if not task:
        return None
    for key, value in fields.items():
        setattr(task, key, value)
    session.add(task)
    session.commit()
    session.refresh(task)
    return task
