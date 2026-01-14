from __future__ import annotations

import datetime as dt

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.models import User, Transaction, Track, Task


FREE_QUOTA_PER_DAY = 3
TEXT_PRICE_RUB = 19


def get_or_create_user(session: Session, tg_id: int) -> User:
    user = session.scalar(select(User).where(User.tg_id == tg_id))
    if user:
        return user
    user = User(tg_id=tg_id)
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def reset_quota_if_needed(user: User) -> None:
    today = dt.date.today()
    if user.free_quota_date != today:
        user.free_quota_date = today
        user.free_quota_used = 0


def consume_free_quota(session: Session, user: User) -> bool:
    reset_quota_if_needed(user)
    if user.free_quota_used >= FREE_QUOTA_PER_DAY:
        return False
    user.free_quota_used += 1
    session.add(user)
    session.commit()
    return True


def charge_text(session: Session, user: User) -> bool:
    if user.balance_rub < TEXT_PRICE_RUB:
        return False
    user.balance_rub -= TEXT_PRICE_RUB
    session.add(user)
    session.add(
        Transaction(
            user_id=user.id,
            amount_rub=TEXT_PRICE_RUB,
            type="text",
            status="capture",
        )
    )
    session.commit()
    return True


def hold_audio(session: Session, user: User, amount_rub: int) -> Transaction | None:
    if user.balance_rub < amount_rub:
        return None
    user.balance_rub -= amount_rub
    transaction = Transaction(
        user_id=user.id,
        amount_rub=amount_rub,
        type="audio",
        status="hold",
    )
    session.add_all([user, transaction])
    session.commit()
    session.refresh(transaction)
    return transaction


def capture_audio(session: Session, transaction_id: int) -> None:
    transaction = session.get(Transaction, transaction_id)
    if not transaction:
        return
    transaction.status = "capture"
    session.add(transaction)
    session.commit()


def release_audio(session: Session, transaction_id: int) -> None:
    transaction = session.get(Transaction, transaction_id)
    if not transaction:
        return
    user = session.get(User, transaction.user_id)
    if user:
        user.balance_rub += transaction.amount_rub
        session.add(user)
    transaction.status = "release"
    session.add(transaction)
    session.commit()


def add_topup(session: Session, user: User, amount_rub: int, external_id: str) -> None:
    user.balance_rub += amount_rub
    transaction = Transaction(
        user_id=user.id,
        amount_rub=amount_rub,
        type="topup",
        status="capture",
        external_id=external_id,
    )
    session.add_all([user, transaction])
    session.commit()


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
    progress_chat_id: int | None = None,
    progress_message_id: int | None = None,
) -> Task:
    task = Task(
        user_id=user_id,
        preset_id=preset_id,
        status=status,
        brief=brief,
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
