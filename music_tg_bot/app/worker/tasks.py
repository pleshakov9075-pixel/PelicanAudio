from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Optional

import httpx
from aiogram import Bot
from aiogram.types import FSInputFile
from redis import Redis
from rq import Queue

from app.core.config import settings
from app.core.db import SessionLocal
from app.core.repo import capture_audio, release_audio, create_track
from app.core.utils import sanitize_filename
from app.integrations.genapi import call_suno, GenApiError

logger = logging.getLogger("worker.tasks")


def _get_queue() -> Queue:
    redis_conn = Redis.from_url(settings.redis_url)
    return Queue("default", connection=redis_conn)


def enqueue_audio_generation(
    user_id: int,
    chat_id: int,
    preset_id: str,
    title: str,
    lyrics: str,
    tags: str,
    transaction_id: int,
) -> str:
    queue = _get_queue()
    job = queue.enqueue(
        generate_audio_task,
        user_id,
        chat_id,
        preset_id,
        title,
        lyrics,
        tags,
        transaction_id,
    )
    return job.id


def _download_file(url: str, target_path: Path) -> None:
    with httpx.stream("GET", url, timeout=120) as response:
        response.raise_for_status()
        with target_path.open("wb") as handle:
            for chunk in response.iter_bytes():
                handle.write(chunk)


def generate_audio_task(
    user_id: int,
    chat_id: int,
    preset_id: str,
    title: str,
    lyrics: str,
    tags: str,
    transaction_id: int,
) -> None:
    logger.info("Старт генерации аудио", extra={"request_id": str(transaction_id)})
    try:
        urls = call_suno(title=title, tags=tags, prompt=lyrics)
        mp3_url_1, mp3_url_2 = urls[0], urls[1]
        with SessionLocal() as session:
            track = create_track(
                session,
                user_id=user_id,
                preset_id=preset_id,
                title=title,
                lyrics=lyrics,
                tags=tags,
                mp3_url_1=mp3_url_1,
                mp3_url_2=mp3_url_2,
            )
            capture_audio(session, transaction_id)
    except GenApiError as exc:
        logger.error("Ошибка Suno: %s", exc)
        with SessionLocal() as session:
            release_audio(session, transaction_id)
        return

    filename = sanitize_filename(title)
    file_path = Path(settings.storage_dir) / f"{filename}.mp3"

    try:
        _download_file(mp3_url_1, file_path)
    except Exception as exc:
        logger.error("Ошибка загрузки mp3: %s", exc)
        return

    async def _send() -> None:
        bot = Bot(token=settings.bot_token)
        await bot.send_document(
            chat_id=chat_id,
            document=FSInputFile(file_path),
            caption="Ваш трек готов!",
            reply_markup=second_variant_keyboard(track.id),
        )
        await bot.session.close()

    from app.bot.keyboards.inline import second_variant_keyboard

    asyncio.run(_send())


async def deliver_second_variant(track_id: int, chat_id: int) -> None:
    from app.core.models import Track

    with SessionLocal() as session:
        track = session.get(Track, track_id)
        if not track:
            return
        mp3_url_2 = track.mp3_url_2
        title = track.title

    filename = sanitize_filename(f"{title} (2)")
    file_path = Path(settings.storage_dir) / f"{filename}.mp3"

    async with httpx.AsyncClient(timeout=120) as client:
        response = await client.get(mp3_url_2)
        response.raise_for_status()
        file_path.write_bytes(response.content)

    bot = Bot(token=settings.bot_token)
    await bot.send_document(chat_id=chat_id, document=FSInputFile(file_path))
    await bot.session.close()
