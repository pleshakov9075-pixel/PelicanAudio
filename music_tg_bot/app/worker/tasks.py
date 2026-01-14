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
from app.core.generation import build_edit_messages, build_lyrics_messages, build_tags_messages
from app.core.repo import capture_audio, release_audio, create_track, get_task, update_task
from app.core.task_status import (
    AUDIO_POLLING,
    AUDIO_RUNNING,
    DOWNLOADING_AUDIO,
    EDIT_POLLING,
    EDIT_RUNNING,
    FAILED,
    REVIEW_READY,
    SENDING_DOCUMENT,
    SUCCEEDED,
    TAGS_RUNNING,
    TEXT_POLLING,
    TEXT_RUNNING,
)
from app.core.utils import sanitize_filename
from app.integrations.genapi import call_grok, call_suno, GenApiError
from app.presets.loader import get_preset

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
            logger.info(
                "Статусное сообщение обновлено",
                extra={"chat_id": chat_id, "message_id": message_id},
            )
            return message_id
        except Exception as exc:
            logger.exception(
                "Не удалось обновить статусное сообщение",
                extra={"chat_id": chat_id, "message_id": message_id},
            )
    sent = await bot.send_message(chat_id=chat_id, text=text)
    logger.info(
        "Статусное сообщение отправлено",
        extra={"chat_id": chat_id, "message_id": sent.message_id},
    )
    return sent.message_id


def _get_queue() -> Queue:
    redis_conn = Redis.from_url(settings.redis_url)
    return Queue("default", connection=redis_conn)


def enqueue_text_generation(task_id: int) -> str:
    queue = _get_queue()
    job = queue.enqueue(generate_text_task, task_id)
    return job.id


def enqueue_edit_generation(task_id: int) -> str:
    queue = _get_queue()
    job = queue.enqueue(generate_edit_task, task_id)
    return job.id


def enqueue_audio_generation(
    task_id: int,
    chat_id: int,
    transaction_id: int,
    status_message_id: int | None,
) -> str:
    queue = _get_queue()
    job = queue.enqueue(
        generate_audio_task,
        task_id,
        chat_id,
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


def _update_progress_message(
    chat_id: int,
    message_id: int | None,
    text: str,
) -> int:
    async def _run() -> int:
        bot = Bot(token=settings.bot_token)
        try:
            logger.info(
                "Обновление прогресса",
                extra={"chat_id": chat_id, "message_id": message_id},
            )
            return await _send_or_edit_status(bot, chat_id, message_id, text)
        finally:
            await bot.session.close()

    return asyncio.run(_run())


def _store_message_id(task_id: int, message_id: int) -> None:
    with SessionLocal() as session:
        update_task(session, task_id, progress_message_id=message_id)


def _load_task_and_preset(task_id: int) -> tuple[object | None, dict | None]:
    with SessionLocal() as session:
        task = get_task(session, task_id)
        preset = get_preset(task.preset_id) if task else None
    return task, preset


def generate_text_task(task_id: int) -> None:
    task, preset = _load_task_and_preset(task_id)
    if not task or not preset:
        return
    logger.info(
        "Старт генерации текста",
        extra={
            "task_id": task_id,
            "chat_id": task.progress_chat_id,
            "message_id": task.progress_message_id,
            "user_id": task.user_id,
        },
    )
    status_prefix = f"🎛 Пресет: {preset['title']}"
    with SessionLocal() as session:
        update_task(session, task_id, status=TEXT_RUNNING)
    status_message_id = _update_progress_message(
        chat_id=task.progress_chat_id,
        message_id=task.progress_message_id,
        text=f"{status_prefix}\n⏳ Генерирую текст…",
    )
    _store_message_id(task_id, status_message_id)
    try:
        lyrics_result = call_grok(build_lyrics_messages(preset, task.brief or ""))
        if lyrics_result.request_id is not None:
            with SessionLocal() as session:
                update_task(
                    session,
                    task_id,
                    status=TEXT_POLLING,
                    genapi_request_id=lyrics_result.request_id,
                )
            status_message_id = _update_progress_message(
                chat_id=task.progress_chat_id,
                message_id=status_message_id,
                text=f"{status_prefix}\n⏳ Генерирую текст… (polling)",
            )
            _store_message_id(task_id, status_message_id)
        lyrics = lyrics_result.result
        with SessionLocal() as session:
            update_task(session, task_id, lyrics_current=lyrics, status=TAGS_RUNNING, genapi_request_id=None)
        status_message_id = _update_progress_message(
            chat_id=task.progress_chat_id,
            message_id=status_message_id,
            text=f"{status_prefix}\n✅ Текст готов. Генерирую теги…",
        )
        _store_message_id(task_id, status_message_id)
        tags_result = call_grok(build_tags_messages(preset, lyrics))
        if tags_result.request_id is not None:
            with SessionLocal() as session:
                update_task(
                    session,
                    task_id,
                    status=TAGS_RUNNING,
                    genapi_request_id=tags_result.request_id,
                )
            status_message_id = _update_progress_message(
                chat_id=task.progress_chat_id,
                message_id=status_message_id,
                text=f"{status_prefix}\n⏳ Генерирую теги… (polling)",
            )
            _store_message_id(task_id, status_message_id)
        tags = tags_result.result
        with SessionLocal() as session:
            update_task(
                session,
                task_id,
                status=REVIEW_READY,
                tags_current=tags,
                genapi_request_id=None,
            )
        status_message_id = _update_progress_message(
            chat_id=task.progress_chat_id,
            message_id=status_message_id,
            text=f"{status_prefix}\n✅ Готово. Проверь текст ниже:",
        )
        _store_message_id(task_id, status_message_id)
        async def _send_review() -> None:
            from app.bot.keyboards.inline import review_keyboard

            bot = Bot(token=settings.bot_token)
            try:
                await bot.send_message(
                    chat_id=task.progress_chat_id,
                    text=f"{status_prefix}\n\nТекст песни:\n\n{lyrics}\n\nТеги: {tags}",
                    reply_markup=review_keyboard(),
                )
                logger.info(
                    "Сообщение с ревью текста отправлено",
                    extra={
                        "task_id": task_id,
                        "chat_id": task.progress_chat_id,
                        "user_id": task.user_id,
                    },
                )
            finally:
                await bot.session.close()

        asyncio.run(_send_review())
    except GenApiError as exc:
        logger.error(
            "Ошибка генерации текста",
            extra={"task_id": task_id, "chat_id": task.progress_chat_id, "user_id": task.user_id},
        )
        with SessionLocal() as session:
            update_task(session, task_id, status=FAILED, error_message=str(exc))
        _update_progress_message(
            chat_id=task.progress_chat_id,
            message_id=status_message_id,
            text=f"{status_prefix}\n{exc}",
        )


def generate_edit_task(task_id: int) -> None:
    task, preset = _load_task_and_preset(task_id)
    if not task or not preset:
        return
    logger.info(
        "Старт правок текста",
        extra={
            "task_id": task_id,
            "chat_id": task.progress_chat_id,
            "message_id": task.progress_message_id,
            "user_id": task.user_id,
        },
    )
    status_prefix = f"🎛 Пресет: {preset['title']}"
    with SessionLocal() as session:
        update_task(session, task_id, status=EDIT_RUNNING)
    status_message_id = _update_progress_message(
        chat_id=task.progress_chat_id,
        message_id=task.progress_message_id,
        text=f"{status_prefix}\n⏳ Применяю правки…",
    )
    _store_message_id(task_id, status_message_id)
    try:
        edit_result = call_grok(build_edit_messages(task.lyrics_current or "", task.edit_request or ""))
        if edit_result.request_id is not None:
            with SessionLocal() as session:
                update_task(
                    session,
                    task_id,
                    status=EDIT_POLLING,
                    genapi_request_id=edit_result.request_id,
                )
            status_message_id = _update_progress_message(
                chat_id=task.progress_chat_id,
                message_id=status_message_id,
                text=f"{status_prefix}\n⏳ Применяю правки… (polling)",
            )
            _store_message_id(task_id, status_message_id)
        new_lyrics = edit_result.result
        with SessionLocal() as session:
            update_task(
                session,
                task_id,
                lyrics_current=new_lyrics,
                tags_current=None,
                status=TAGS_RUNNING,
                genapi_request_id=None,
            )
        status_message_id = _update_progress_message(
            chat_id=task.progress_chat_id,
            message_id=status_message_id,
            text=f"{status_prefix}\n✅ Текст обновлён. Генерирую теги…",
        )
        _store_message_id(task_id, status_message_id)
        tags_result = call_grok(build_tags_messages(preset, new_lyrics))
        if tags_result.request_id is not None:
            with SessionLocal() as session:
                update_task(
                    session,
                    task_id,
                    status=TAGS_RUNNING,
                    genapi_request_id=tags_result.request_id,
                )
            status_message_id = _update_progress_message(
                chat_id=task.progress_chat_id,
                message_id=status_message_id,
                text=f"{status_prefix}\n⏳ Генерирую теги… (polling)",
            )
            _store_message_id(task_id, status_message_id)
        tags = tags_result.result
        with SessionLocal() as session:
            update_task(
                session,
                task_id,
                status=REVIEW_READY,
                tags_current=tags,
                genapi_request_id=None,
            )
        status_message_id = _update_progress_message(
            chat_id=task.progress_chat_id,
            message_id=status_message_id,
            text=f"{status_prefix}\n✅ Готово. Проверь текст ниже:",
        )
        _store_message_id(task_id, status_message_id)
        async def _send_review() -> None:
            from app.bot.keyboards.inline import review_keyboard

            bot = Bot(token=settings.bot_token)
            try:
                await bot.send_message(
                    chat_id=task.progress_chat_id,
                    text=f"{status_prefix}\n\nОбновлённый текст:\n\n{new_lyrics}\n\nТеги: {tags}",
                    reply_markup=review_keyboard(),
                )
                logger.info(
                    "Сообщение с ревью правок отправлено",
                    extra={
                        "task_id": task_id,
                        "chat_id": task.progress_chat_id,
                        "user_id": task.user_id,
                    },
                )
            finally:
                await bot.session.close()

        asyncio.run(_send_review())
    except GenApiError as exc:
        logger.error(
            "Ошибка правок текста",
            extra={"task_id": task_id, "chat_id": task.progress_chat_id, "user_id": task.user_id},
        )
        with SessionLocal() as session:
            update_task(session, task_id, status=FAILED, error_message=str(exc))
        _update_progress_message(
            chat_id=task.progress_chat_id,
            message_id=status_message_id,
            text=f"{status_prefix}\n{exc}",
        )


def generate_audio_task(
    task_id: int,
    chat_id: int,
    transaction_id: int,
    status_message_id: int | None,
) -> None:
    task, preset = _load_task_and_preset(task_id)
    if not task or not preset:
        return
    logger.info(
        "Старт генерации аудио",
        extra={
            "task_id": task_id,
            "chat_id": chat_id,
            "message_id": status_message_id,
            "user_id": task.user_id,
            "transaction_id": transaction_id,
        },
    )
    status_text_prefix = f"🎛 Пресет: {preset['title']}"
    try:
        with SessionLocal() as session:
            update_task(session, task_id, status=AUDIO_RUNNING)
        status_message_id = _update_progress_message(
            chat_id=chat_id,
            message_id=status_message_id,
            text=f"{status_text_prefix}\n⏳ Генерирую аудио…",
        )
        _store_message_id(task_id, status_message_id)
        suno_result = call_suno(title=task.title_text or "", tags=task.tags_current or "", prompt=task.lyrics_current or "")
        if suno_result.request_id is not None:
            with SessionLocal() as session:
                update_task(
                    session,
                    task_id,
                    status=AUDIO_POLLING,
                    suno_request_id=suno_result.request_id,
                )
            status_message_id = _update_progress_message(
                chat_id=chat_id,
                message_id=status_message_id,
                text=f"{status_text_prefix}\n⏳ Генерирую аудио… (polling)",
            )
            _store_message_id(task_id, status_message_id)
        urls = suno_result.result
        mp3_url_1, mp3_url_2 = urls[0], urls[1]
        with SessionLocal() as session:
            track = create_track(
                session,
                user_id=task.user_id,
                preset_id=task.preset_id,
                title=task.title_text or "",
                lyrics=task.lyrics_current or "",
                tags=task.tags_current or "",
                mp3_url_1=mp3_url_1,
                mp3_url_2=mp3_url_2,
            )
            update_task(
                session,
                task_id,
                mp3_url_1=mp3_url_1,
                mp3_url_2=mp3_url_2,
                suno_request_id=None,
            )
            capture_audio(session, transaction_id)
    except GenApiError as exc:
        logger.error(
            "Ошибка Suno",
            extra={"task_id": task_id, "chat_id": chat_id, "user_id": task.user_id},
        )
        with SessionLocal() as session:
            release_audio(session, transaction_id)
            update_task(session, task_id, status=FAILED, error_message=str(exc))
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
    filename = sanitize_filename(task.title_text or "Трек")
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
    with SessionLocal() as session:
        update_task(session, task_id, status=DOWNLOADING_AUDIO, progress_message_id=status_message_id)

    try:
        _download_file(mp3_url_1, file_path)
    except Exception as exc:
        logger.exception(
            "Ошибка загрузки mp3",
            extra={"task_id": task_id, "chat_id": chat_id, "user_id": task.user_id},
        )
        with SessionLocal() as session:
            update_task(session, task_id, status=FAILED, error_message=str(exc))
        async def _notify_download_error() -> None:
            bot = Bot(token=settings.bot_token)
            try:
                await _send_or_edit_status(
                    bot,
                    chat_id,
                    status_message_id,
                    f"{status_text_prefix}\n⚠️ Не удалось скачать аудио, попробуйте позже.",
                )
            finally:
                await bot.session.close()

        asyncio.run(_notify_download_error())
        return

    async def _send() -> None:
        bot = Bot(token=settings.bot_token)
        try:
            with SessionLocal() as session:
                update_task(session, task_id, status=SENDING_DOCUMENT)
            await bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_DOCUMENT)
            await bot.send_document(
                chat_id=chat_id,
                document=FSInputFile(file_path),
                caption=f"{status_text_prefix}\n✅ Готово! Вот ваш трек: {task.title_text}",
                reply_markup=second_variant_keyboard(track.id),
            )
            logger.info(
                "Аудио отправлено в Telegram",
                extra={"task_id": task_id, "chat_id": chat_id, "user_id": task.user_id},
            )
            await _send_or_edit_status(
                bot,
                chat_id,
                status_message_id,
                f"{status_text_prefix}\n✅ Готово! Вот ваш трек: {task.title_text}",
            )
            with SessionLocal() as session:
                update_task(session, task_id, status=SUCCEEDED)
        except Exception as exc:
            logger.exception(
                "Ошибка отправки аудио в Telegram",
                extra={"task_id": task_id, "chat_id": chat_id, "user_id": task.user_id},
            )
            with SessionLocal() as session:
                update_task(session, task_id, status=FAILED, error_message=str(exc))
            await _send_or_edit_status(
                bot,
                chat_id,
                status_message_id,
                f"{status_text_prefix}\n⚠️ Не удалось отправить аудио в Telegram.",
            )
        finally:
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
