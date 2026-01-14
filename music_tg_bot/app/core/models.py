from __future__ import annotations

import datetime as dt

from sqlalchemy import BigInteger, String, Integer, Date, DateTime, ForeignKey, UniqueConstraint, Text, Boolean
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tg_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    balance_rub: Mapped[int] = mapped_column(Integer, default=0)
    free_quota_date: Mapped[dt.date] = mapped_column(Date, default=dt.date.today)
    free_quota_used: Mapped[int] = mapped_column(Integer, default=0)
    welcome_bonus_given: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)

    transactions: Mapped[list[Transaction]] = relationship("Transaction", back_populates="user")


class Transaction(Base):
    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    amount_rub: Mapped[int] = mapped_column(Integer)
    type: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32))
    external_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)

    user: Mapped[User] = relationship("User", back_populates="transactions")

    __table_args__ = (UniqueConstraint("external_id", name="uq_transactions_external_id"),)


class Track(Base):
    __tablename__ = "tracks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    preset_id: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(128))
    lyrics: Mapped[str] = mapped_column(Text)
    tags: Mapped[str] = mapped_column(Text)
    mp3_url_1: Mapped[str] = mapped_column(Text)
    mp3_url_2: Mapped[str] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime)


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    preset_id: Mapped[str] = mapped_column(String(64))
    brief: Mapped[str | None] = mapped_column(Text, nullable=True)
    user_lyrics_raw: Mapped[str | None] = mapped_column(Text, nullable=True)
    edit_request: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32))
    stage: Mapped[str | None] = mapped_column(String(64), nullable=True)
    progress: Mapped[int | None] = mapped_column(Integer, nullable=True)
    progress_chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    progress_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    genapi_request_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    suno_request_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    mp3_url_1: Mapped[str | None] = mapped_column(Text, nullable=True)
    mp3_url_2: Mapped[str | None] = mapped_column(Text, nullable=True)
    title_text: Mapped[str | None] = mapped_column(String(128), nullable=True)
    suggested_title: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lyrics_current: Mapped[str | None] = mapped_column(Text, nullable=True)
    tags_current: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime,
        default=dt.datetime.utcnow,
        onupdate=dt.datetime.utcnow,
    )
