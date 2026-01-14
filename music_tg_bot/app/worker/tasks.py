from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import httpx
from aiogram import Bot
from aiogram.enums import ChatAction
from aiogram.types import FSInputFile
from redis import Redis
from rq import Queue

from app.core.config import settings
from app.core.db import SessionLocal
from app.core.repo import capture_audio, release_audio, create_track
from app.core.utils import sanitize_filename
from app.integrations.genapi import call_suno, GenApiError

logger = logging.getLogger("worker.tasks")


async def _send_or_edit_status(
    bot: Bot,
    chat_id: int,
    message_id: int | None,
    text: str,
) -> int:
    if message_id:
        try:
            await bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=text)
            return message_id
        except Exception as exc:
            logger.warning("Не удалось обновить статусное сообщение: %s", exc)
    sent = await bot.send_message(chat_id=chat_id, text=text)
    return sent.message_id


def _get_queue() -> Queue:
    redis_conn = Redis.from_url(settings.redis_url)
    return Queue("default", connection=redis_conn)


def enqueue_audio_generation(
    user_id: int,
    chat_id: int,
    preset_id: str,
    preset_title: str,
    title: str,
    lyrics: str,
    tags: str,
    transaction_id: int,
    status_message_id: int | None,
) -> str:
    queue = _get_queue()
    job = queue.enqueue(
        generate_audio_task,
        user_id,
        chat_id,
        preset_id,
        preset_title,
        title,
        lyrics,
        tags,
        transaction_id,
        status_message_id,
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
    preset_title: str,
    title: str,
    lyrics: str,
    tags: str,
    transaction_id: int,
    status_message_id: int | None,
) -> None:
    logger.info("Старт генерации аудио", extra={"request_id": str(transaction_id)})
    status_text_prefix = f"🎛 Пресет: {preset_title}"
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
        async def _notify_failure() -> None:
            bot = Bot(token=settings.bot_token)
            try:
                await _send_or_edit_status(
                    bot,
                    chat_id,
                    status_message_id,
                    f"{status_text_prefix}\n{exc}",
                )
            finally:
                await bot.session.close()

        asyncio.run(_notify_failure())
        return

    tmp_dir = Path(settings.storage_dir) / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    filename = sanitize_filename(title)
    file_path = tmp_dir / f"{filename}.mp3"

    async def _set_status_downloading() -> None:
        bot = Bot(token=settings.bot_token)
        try:
            nonlocal status_message_id
            status_message_id = await _send_or_edit_status(
                bot,
                chat_id,
                status_message_id,
                f"{status_text_prefix}\n⏳ Скачиваю файл и загружаю в Telegram…",
            )
        finally:
            await bot.session.close()

    asyncio.run(_set_status_downloading())

    try:
        _download_file(mp3_url_1, file_path)
    except Exception as exc:
        logger.error("Ошибка загрузки mp3: %s", exc)
        return

    async def _send() -> None:
        bot = Bot(token=settings.bot_token)
        await bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_DOCUMENT)
        await bot.send_document(
            chat_id=chat_id,
            document=FSInputFile(file_path),
            caption=f"{status_text_prefix}\n✅ Готово! Вот ваш трек: {title}",
            reply_markup=second_variant_keyboard(track.id),
        )
        await _send_or_edit_status(
            bot,
            chat_id,
            status_message_id,
            f"{status_text_prefix}\n✅ Готово! Вот ваш трек: {title}",
        )
        await bot.session.close()

    from app.bot.keyboards.inline import second_variant_keyboard

    asyncio.run(_send())
    try:
        file_path.unlink()
    except OSError as exc:
        logger.warning("Не удалось удалить временный файл %s: %s", file_path, exc)


async def deliver_second_variant(track_id: int, chat_id: int) -> None:
    from app.core.models import Track

    with SessionLocal() as session:
        track = session.get(Track, track_id)
        if not track:
            return
        mp3_url_2 = track.mp3_url_2
        title = track.title

    tmp_dir = Path(settings.storage_dir) / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    filename = sanitize_filename(f"{title} (2)")
    file_path = tmp_dir / f"{filename}.mp3"

    async with httpx.AsyncClient(timeout=120) as client:
        response = await client.get(mp3_url_2)
        response.raise_for_status()
        file_path.write_bytes(response.content)

    bot = Bot(token=settings.bot_token)
    await bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_DOCUMENT)
    await bot.send_document(chat_id=chat_id, document=FSInputFile(file_path))
    await bot.session.close()
    try:
        file_path.unlink()
    except OSError as exc:
        logger.warning("Не удалось удалить временный файл %s: %s", file_path, exc)
