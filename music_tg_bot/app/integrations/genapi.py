from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from app.core.config import settings

logger = logging.getLogger("genapi")


class GenApiError(RuntimeError):
    pass


def _headers() -> dict[str, str]:
    if not settings.genapi_api_key:
        raise GenApiError("Не задан ключ GENAPI_API_KEY")
    return {"Authorization": f"Bearer {settings.genapi_api_key}"}


def _timeout(connect: float, read: float) -> httpx.Timeout:
    return httpx.Timeout(connect=connect, read=read, write=connect, pool=connect)


def _post_with_retries(
    url: str,
    payload: dict[str, Any],
    timeout: httpx.Timeout,
    operation: str,
) -> httpx.Response:
    retries = max(settings.genapi_retries, 1)
    backoff = max(settings.genapi_retry_backoff, 0)
    last_exc: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            response = httpx.post(url, headers=_headers(), json=payload, timeout=timeout)
            response.raise_for_status()
            return response
        except httpx.RequestError as exc:
            last_exc = exc
            if attempt < retries:
                delay = backoff * (2 ** (attempt - 1))
                logger.warning(
                    "Сетевая ошибка GenAPI (%s), повтор через %s сек: %s",
                    operation,
                    delay,
                    exc,
                )
                if delay:
                    time.sleep(delay)
                continue
            logger.exception("Сетевая ошибка GenAPI (%s) после ретраев", operation)
            raise GenApiError("⚠️ Не удалось связаться с GenAPI, попробуйте ещё раз") from exc
        except httpx.HTTPStatusError as exc:
            logger.error("HTTP ошибка GenAPI (%s): %s", operation, exc)
            raise GenApiError(f"Ошибка GenAPI: {exc}") from exc
    raise GenApiError("⚠️ Не удалось связаться с GenAPI, попробуйте ещё раз") from last_exc


def call_grok(messages: list[dict[str, Any]]) -> str:
    payload = {
        "model": "grok-4-1-fast-reasoning",
        "n": 1,
        "temperature": 1,
        "top_p": 1,
        "presence_penalty": 0,
        "frequency_penalty": 0,
        "response_format": {"type": "text"},
        "messages": messages,
        "stream": False,
        "is_sync": False,
    }
    url = f"{settings.genapi_base_url.rstrip('/')}/v1/chat/completions"
    response = _post_with_retries(
        url,
        payload,
        _timeout(settings.genapi_timeout_connect, settings.genapi_timeout_read_grok),
        "grok",
    )
    data = response.json()
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as exc:
        logger.error("Неожиданный ответ GenAPI: %s", data)
        raise GenApiError("Неожиданный ответ GenAPI") from exc


def call_suno(title: str, tags: str, prompt: str) -> list[str]:
    payload = {
        "title": title,
        "tags": tags,
        "prompt": prompt,
        "translate_input": False,
        "model": "v5",
    }
    url = f"{settings.genapi_base_url.rstrip('/')}/v1/suno"
    response = _post_with_retries(
        url,
        payload,
        _timeout(settings.genapi_timeout_connect, settings.genapi_timeout_read_suno),
        "suno",
    )
    data = response.json()
    if not isinstance(data, list) or len(data) < 2:
        raise GenApiError("Неожиданный ответ Suno")
    return data
