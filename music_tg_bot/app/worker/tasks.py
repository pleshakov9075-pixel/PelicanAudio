from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import httpx
from aiogram import Bot
from aiogram.enums import ChatAction
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import FSInputFile
from redis import Redis
from rq import Queue

from app.core.config import settings
from app.core.db import SessionLocal
from app.core.generation import (
    build_edit_messages,
    build_instrumental_messages,
    build_lyrics_messages,
    build_tags_messages,
    build_user_lyrics_messages,
)
from app.core.models import User
from app.core.repo import (
    FREE_QUOTA_PER_DAY,
    adjust_balance,
    create_track,
    get_free_quota_remaining,
    get_task,
    update_task,
)
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
from app.core.utils import build_track_filename, sanitize_filename
from app.integrations.genapi import call_grok, call_suno, GenApiError
from app.presets.loader import get_preset

logger = logging.getLogger("worker.tasks")

LYRICS_MESSAGE_LIMIT = 3500


def _parse_instrumental_result(result: str) -> tuple[str | None, str]:
    title: str | None = None
    prompt_lines: list[str] = []
    for line in result.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        lower = stripped.lower()
        if lower.startswith("title:"):
            title = stripped.split(":", 1)[1].strip()
            continue
        if lower.startswith("prompt:"):
            prompt_lines.append(stripped.split(":", 1)[1].strip())
            continue
        prompt_lines.append(stripped)
    prompt = " ".join(prompt_lines).strip() or result.strip()
    required_phrase = "инструментальная композиция, без вокала, без слов"
    if required_phrase not in prompt.lower():
        prompt = f"{prompt}. {required_phrase}"
    return title, prompt


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
        except TelegramBadRequest as exc:
            if "message is not modified" in str(exc):
                logger.debug(
                    "Статусное сообщение без изменений",
                    extra={"chat_id": chat_id, "message_id": message_id},
                )
                return message_id
            logger.exception(
                "Не удалось обновить статусное сообщение",
                extra={"chat_id": chat_id, "message_id": message_id},
            )
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
    status_message_id: int | None,
) -> str:
    queue = _get_queue()
    job = queue.enqueue(
        generate_audio_task,
        task_id,
        chat_id,
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


def _get_user_balance_and_remaining(user_id: int) -> tuple[int, int]:
    with SessionLocal() as session:
        user = session.get(User, user_id)
        if not user:
            return 0, 0
        remaining = get_free_quota_remaining(session, user)
        return user.balance_rub, remaining


def _build_lyrics_filename(base: str | None) -> str:
    safe_base = sanitize_filename(base or "lyrics", max_length=60)
    return f"{safe_base}_lyrics.txt"


async def _send_review_payload(
    *,
    bot: Bot,
    chat_id: int,
    task_id: int,
    status_prefix: str,
    lyrics: str | None,
    tags: str | None,
    price: int,
    balance: int,
    remaining: int,
    mode: str,
    filename_hint: str | None,
    reply_markup,
) -> None:
    if not isinstance(lyrics, str) or not lyrics.strip():
        await bot.send_message(
            chat_id=chat_id,
            text="❌ Не удалось получить текст песни. Попробуй ещё раз.",
        )
        logger.warning(
            "Пустой текст песни при отправке ревью",
            extra={"task_id": task_id, "chat_id": chat_id},
        )
        return
    clean_lyrics = lyrics.strip()
    logger.info(
        "Отправка текста песни",
        extra={
            "task_id": task_id,
            "chat_id": chat_id,
            "lyrics_len": len(clean_lyrics),
            "lyrics_preview": clean_lyrics[:80],
        },
    )
    label = "Описание инструментала" if mode == "instrumental" else "Текст песни"
    body = f"{label}:\n\n{clean_lyrics}"
    tags_text = tags or ""
    review_suffix = (
        f"\n\nТеги: {tags_text}\n"
        f"📝 Бесплатных текстов сегодня: {max(0, remaining)}/{FREE_QUOTA_PER_DAY}\n"
        f"Цена аудио: {price} ₽ | Баланс: {balance} ₽"
    )
    if len(body) > LYRICS_MESSAGE_LIMIT or len(clean_lyrics) > LYRICS_MESSAGE_LIMIT:
        tmp_dir = Path(settings.storage_dir) / "tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        filename = _build_lyrics_filename(filename_hint or "lyrics")
        file_path = tmp_dir / filename
        file_path.write_text(clean_lyrics, encoding="utf-8")
        try:
            sent_doc = await bot.send_document(
                chat_id=chat_id,
                document=FSInputFile(file_path),
                caption=f"{status_prefix}\n{label} отправлен файлом.",
            )
            logger.info(
                "Текст песни отправлен файлом",
                extra={"task_id": task_id, "chat_id": chat_id, "message_id": sent_doc.message_id},
            )
        except Exception:
            logger.exception(
                "Ошибка отправки текста песни файлом",
                extra={"task_id": task_id, "chat_id": chat_id},
            )
            await bot.send_message(
                chat_id=chat_id,
                text="❌ Не удалось отправить текст песни. Попробуй ещё раз.",
            )
            return
        finally:
            try:
                file_path.unlink()
            except OSError as exc:
                logger.warning("Не удалось удалить файл %s: %s", file_path, exc)
        try:
            sent_message = await bot.send_message(
                chat_id=chat_id,
                text=(
                    f"{status_prefix}\n\nТеги: {tags_text}\n"
                    f"📝 Бесплатных текстов сегодня: {max(0, remaining)}/{FREE_QUOTA_PER_DAY}\n"
                    f"Цена аудио: {price} ₽ | Баланс: {balance} ₽"
                ),
                reply_markup=reply_markup,
            )
            logger.info(
                "Сообщение с тегами отправлено",
                extra={"task_id": task_id, "chat_id": chat_id, "message_id": sent_message.message_id},
            )
        except Exception:
            logger.exception(
                "Ошибка отправки сообщения с тегами",
                extra={"task_id": task_id, "chat_id": chat_id},
            )
        return
    try:
        sent_message = await bot.send_message(
            chat_id=chat_id,
            text=f"{status_prefix}\n\n{body}{review_suffix}",
            reply_markup=reply_markup,
        )
        logger.info(
            "Сообщение с ревью текста отправлено",
            extra={"task_id": task_id, "chat_id": chat_id, "message_id": sent_message.message_id},
        )
    except Exception:
        logger.exception(
            "Ошибка отправки сообщения с ревью",
            extra={"task_id": task_id, "chat_id": chat_id},
        )


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
    mode = preset.get("mode", "song")
    lyrics_for_review: str | None = None
    with SessionLocal() as session:
        update_task(session, task_id, status=TEXT_RUNNING)
    initial_status = "⏳ Генерирую текст…"
    if mode == "instrumental":
        initial_status = "⏳ Генерирую описание инструментала…"
    elif mode == "user_lyrics":
        initial_status = "⏳ Оформляю текст…"
    status_message_id = _update_progress_message(
        chat_id=task.progress_chat_id,
        message_id=task.progress_message_id,
        text=f"{status_prefix}\n{initial_status}",
    )
    _store_message_id(task_id, status_message_id)
    try:
        if mode == "instrumental":
            instrumental_result = call_grok(build_instrumental_messages(preset, task.brief or ""))
            if instrumental_result.request_id is not None:
                with SessionLocal() as session:
                    update_task(
                        session,
                        task_id,
                        status=TEXT_POLLING,
                        genapi_request_id=instrumental_result.request_id,
                    )
                status_message_id = _update_progress_message(
                    chat_id=task.progress_chat_id,
                    message_id=status_message_id,
                    text=f"{status_prefix}\n⏳ Генерирую описание инструментала… (polling)",
                )
                _store_message_id(task_id, status_message_id)
            suggested_title, prompt = _parse_instrumental_result(instrumental_result.result)
            lyrics_for_review = prompt
            with SessionLocal() as session:
                update_task(
                    session,
                    task_id,
                    lyrics_current=prompt,
                    suggested_title=suggested_title,
                    status=TAGS_RUNNING,
                    genapi_request_id=None,
                )
            status_message_id = _update_progress_message(
                chat_id=task.progress_chat_id,
                message_id=status_message_id,
                text=f"{status_prefix}\n✅ Описание готово. Генерирую теги…",
            )
            _store_message_id(task_id, status_message_id)
            tags_result = call_grok(build_tags_messages(preset, prompt, mode))
        elif mode == "user_lyrics":
            lyrics_result = call_grok(
                build_user_lyrics_messages(preset, task.brief or "", task.user_lyrics_raw or "")
            )
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
                    text=f"{status_prefix}\n⏳ Оформляю текст… (polling)",
                )
                _store_message_id(task_id, status_message_id)
            lyrics = lyrics_result.result
            lyrics_for_review = lyrics
            with SessionLocal() as session:
                update_task(session, task_id, lyrics_current=lyrics, status=TAGS_RUNNING, genapi_request_id=None)
            status_message_id = _update_progress_message(
                chat_id=task.progress_chat_id,
                message_id=status_message_id,
                text=f"{status_prefix}\n✅ Текст оформлен. Генерирую теги…",
            )
            _store_message_id(task_id, status_message_id)
            tags_result = call_grok(build_tags_messages(preset, lyrics, mode))
        else:
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
            lyrics_for_review = lyrics
            with SessionLocal() as session:
                update_task(session, task_id, lyrics_current=lyrics, status=TAGS_RUNNING, genapi_request_id=None)
            status_message_id = _update_progress_message(
                chat_id=task.progress_chat_id,
                message_id=status_message_id,
                text=f"{status_prefix}\n✅ Текст готов. Генерирую теги…",
            )
            _store_message_id(task_id, status_message_id)
            tags_result = call_grok(build_tags_messages(preset, lyrics, mode))
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
            text=f"{status_prefix}\n✅ Готово. Проверь результат ниже:",
        )
        _store_message_id(task_id, status_message_id)
        async def _send_review() -> None:
            from app.bot.keyboards.inline import review_keyboard

            bot = Bot(token=settings.bot_token)
            try:
                balance, remaining = _get_user_balance_and_remaining(task.user_id)
                price = preset.get("price_audio_rub", 0)
                await _send_review_payload(
                    bot=bot,
                    chat_id=task.progress_chat_id,
                    task_id=task_id,
                    status_prefix=status_prefix,
                    lyrics=lyrics_for_review,
                    tags=tags,
                    price=price,
                    balance=balance,
                    remaining=remaining,
                    mode=mode,
                    filename_hint=preset.get("title"),
                    reply_markup=review_keyboard(),
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
    mode = preset.get("mode", "song")
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
        if not isinstance(new_lyrics, str) or not new_lyrics.strip():
            with SessionLocal() as session:
                update_task(
                    session,
                    task_id,
                    status=FAILED,
                    error_message="Empty edit result",
                    genapi_request_id=None,
                )
            _update_progress_message(
                chat_id=task.progress_chat_id,
                message_id=status_message_id,
                text=f"{status_prefix}\n❌ Не удалось применить правки. Попробуй ещё раз.",
            )
            return
        new_lyrics = new_lyrics.strip()
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
        tags_result = call_grok(build_tags_messages(preset, new_lyrics, mode))
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
                lyrics_current=new_lyrics,
                tags_current=tags,
                genapi_request_id=None,
            )
        status_message_id = _update_progress_message(
            chat_id=task.progress_chat_id,
            message_id=status_message_id,
            text=f"{status_prefix}\n✅ Готово. Проверь результат ниже:",
        )
        _store_message_id(task_id, status_message_id)
        async def _send_review() -> None:
            from app.bot.keyboards.inline import review_keyboard

            bot = Bot(token=settings.bot_token)
            try:
                balance, remaining = _get_user_balance_and_remaining(task.user_id)
                price = preset.get("price_audio_rub", 0)
                await _send_review_payload(
                    bot=bot,
                    chat_id=task.progress_chat_id,
                    task_id=task_id,
                    status_prefix=status_prefix,
                    lyrics=new_lyrics,
                    tags=tags,
                    price=price,
                    balance=balance,
                    remaining=remaining,
                    mode=mode,
                    filename_hint=preset.get("title"),
                    reply_markup=review_keyboard(),
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
    status_message_id: int | None,
) -> None:
    task, preset = _load_task_and_preset(task_id)
    if not task or not preset:
        return
    title_text = (task.title_text or "").strip()
    title_line = f"🎼 Название: {title_text}" if title_text else "🎼 Название: —"
    logger.info(
        "Старт генерации аудио",
        extra={
            "task_id": task_id,
            "chat_id": chat_id,
            "message_id": status_message_id,
            "user_id": task.user_id,
        },
    )
    status_text_prefix = f"🎛 Пресет: {preset['title']}\n{title_line}"
    try:
        with SessionLocal() as session:
            update_task(session, task_id, status=AUDIO_RUNNING)
        status_message_id = _update_progress_message(
            chat_id=chat_id,
            message_id=status_message_id,
            text=f"{status_text_prefix}\n⏳ Генерирую аудио…",
        )
        _store_message_id(task_id, status_message_id)
        prompt = task.lyrics_current or ""
        if preset.get("mode") == "instrumental":
            required_phrase = "инструментальная композиция, без вокала, без слов"
            if required_phrase not in prompt.lower():
                prompt = f"{prompt}. {required_phrase}".strip()
        suno_result = call_suno(title=task.title_text or "", tags=task.tags_current or "", prompt=prompt)
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
        track_id = None
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
            track_id = track.id
            update_task(
                session,
                task_id,
                mp3_url_1=mp3_url_1,
                mp3_url_2=mp3_url_2,
                suno_request_id=None,
            )
    except GenApiError as exc:
        logger.error(
            "Ошибка Suno",
            extra={"task_id": task_id, "chat_id": chat_id, "user_id": task.user_id},
        )
        with SessionLocal() as session:
            price = preset.get("price_audio_rub", 0)
            if price:
                user = session.get(User, task.user_id)
                if user:
                    adjust_balance(session, user.tg_id, price, "refund", task_id=task_id)
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
    filename = build_track_filename(task.title_text or "Трек")
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
                caption=f"{status_text_prefix}\n✅ Готово! Вот ваш трек: {title_text}",
                reply_markup=second_variant_keyboard(track_id),
            )
            logger.info(
                "Аудио отправлено в Telegram",
                extra={"task_id": task_id, "chat_id": chat_id, "user_id": task.user_id},
            )
            await _send_or_edit_status(
                bot,
                chat_id,
                status_message_id,
                f"{status_text_prefix}\n✅ Готово! Вот ваш трек: {title_text}",
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
    filename = build_track_filename(f"{title} (2)")
    file_path = tmp_dir / f"{filename}.mp3"

    async with httpx.AsyncClient(timeout=120) as client:
        response = await client.get(mp3_url_2)
        response.raise_for_status()
        file_path.write_bytes(response.content)

    bot = Bot(token=settings.bot_token)
    await bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_DOCUMENT)
    title_line = f"🎼 Название: {title}" if title else "🎼 Название: —"
    await bot.send_document(
        chat_id=chat_id,
        document=FSInputFile(file_path),
        caption=f"{title_line}\n🎧 Второй вариант",
    )
    await bot.session.close()
    try:
        file_path.unlink()
    except OSError as exc:
        logger.warning("Не удалось удалить временный файл %s: %s", file_path, exc)
