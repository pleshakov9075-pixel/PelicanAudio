from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Iterable

import httpx

from app.core.config import settings

logger = logging.getLogger("genapi")


class GenApiError(RuntimeError):
    pass


@dataclass(frozen=True)
class GenApiResult:
    result: Any
    request_id: int | None


def _headers() -> dict[str, str]:
    if not settings.genapi_api_key:
        raise GenApiError("Не задан ключ GENAPI_API_KEY")
    return {"Authorization": f"Bearer {settings.genapi_api_key}"}


def _timeout(connect: float, read: float) -> httpx.Timeout:
    return httpx.Timeout(connect=connect, read=read, write=connect, pool=connect)


def _is_ssl_handshake_timeout(exc: httpx.RequestError) -> bool:
    text = str(exc)
    return "TLS handshake timeout" in text or "_ssl.c:993" in text


def _ssl_retry_delays() -> Iterable[int]:
    return (1, 2, 4)


def _request_with_retries(
    method: str,
    url: str,
    payload: dict[str, Any] | None,
    timeout: httpx.Timeout,
    operation: str,
) -> httpx.Response:
    retries = max(settings.genapi_retries, 1)
    backoff = max(settings.genapi_retry_backoff, 0)
    last_exc: Exception | None = None
    ssl_delays = list(_ssl_retry_delays())
    ssl_attempts = 0
    for attempt in range(1, retries + 1):
        try:
            response = httpx.request(
                method,
                url,
                headers=_headers(),
                json=payload,
                timeout=timeout,
            )
            response.raise_for_status()
            return response
        except (httpx.TimeoutException, TimeoutError) as exc:
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
        except httpx.RequestError as exc:
            last_exc = exc
            if _is_ssl_handshake_timeout(exc) and ssl_attempts < len(ssl_delays):
                delay = ssl_delays[ssl_attempts]
                ssl_attempts += 1
                logger.warning(
                    "SSL handshake timeout GenAPI (%s), повтор через %s сек: %s",
                    operation,
                    delay,
                    exc,
                )
                if delay:
                    time.sleep(delay)
                continue
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
            logger.exception("Сетевая ошибка GenAPI (%s) без ретраев", operation)
            raise GenApiError("⚠️ Не удалось связаться с GenAPI, попробуйте ещё раз") from exc
        except httpx.HTTPStatusError as exc:
            logger.error("HTTP ошибка GenAPI (%s): %s", operation, exc)
            raise GenApiError(f"Ошибка GenAPI: {exc}") from exc
    raise GenApiError("⚠️ Не удалось связаться с GenAPI, попробуйте ещё раз") from last_exc


def _parse_grok_response(data: Any) -> str:
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        logger.error("Неожиданный ответ GenAPI: %s", data)
        raise GenApiError("Неожиданный ответ GenAPI") from exc


def _parse_suno_response(data: Any) -> list[str]:
    if not isinstance(data, list) or len(data) < 2:
        raise GenApiError("Неожиданный ответ Suno")
    return data


def _maybe_processing(data: Any) -> tuple[bool, int | None]:
    if isinstance(data, dict) and data.get("status") == "processing":
        return True, data.get("request_id")
    return False, None


def _raise_if_failed(data: Any) -> None:
    if isinstance(data, dict) and data.get("status") in {"failed", "error"}:
        message = data.get("error") or data.get("message") or "Ошибка GenAPI"
        raise GenApiError(message)


def _poll_request(
    request_id: int,
    timeout_seconds: float,
    timeout: httpx.Timeout,
    operation: str,
) -> Any:
    url = f"{settings.genapi_base_url.rstrip('/')}/api/v1/request/get/{request_id}"
    start = time.monotonic()
    attempt = 0
    while True:
        if time.monotonic() - start > timeout_seconds:
            raise GenApiError("⏱️ Превышено время ожидания GenAPI")
        response = _request_with_retries("GET", url, None, timeout, operation)
        data = response.json()
        if isinstance(data, dict):
            status = data.get("status")
            if status == "processing":
                attempt += 1
                sleep_seconds = min(1.0 + 0.5 * attempt, 2.0)
                time.sleep(sleep_seconds)
                continue
            if status in {"failed", "error"}:
                message = data.get("error") or data.get("message") or "Ошибка GenAPI"
                raise GenApiError(message)
        return data


def call_grok(messages: list[dict[str, Any]]) -> GenApiResult:
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
    url = f"{settings.genapi_base_url.rstrip('/')}/api/v1/networks/grok-4-1"
    timeout = _timeout(settings.genapi_timeout_connect, settings.genapi_timeout_read_grok)
    response = _request_with_retries("POST", url, payload, timeout, "grok")
    data = response.json()
    _raise_if_failed(data)
    is_processing, request_id = _maybe_processing(data)
    if is_processing and not request_id:
        raise GenApiError("Неожиданный ответ GenAPI без request_id")
    if is_processing and request_id:
        data = _poll_request(request_id, 90, timeout, "grok")
    return GenApiResult(result=_parse_grok_response(data), request_id=request_id)


def call_suno(title: str, tags: str, prompt: str) -> GenApiResult:
    payload = {
        "title": title,
        "tags": tags,
        "prompt": prompt,
        "translate_input": False,
        "model": "v5",
        "is_sync": False,
    }
    url = f"{settings.genapi_base_url.rstrip('/')}/api/v1/networks/suno"
    timeout = _timeout(settings.genapi_timeout_connect, settings.genapi_timeout_read_suno)
    response = _request_with_retries("POST", url, payload, timeout, "suno")
    data = response.json()
    _raise_if_failed(data)
    is_processing, request_id = _maybe_processing(data)
    if is_processing and not request_id:
        raise GenApiError("Неожиданный ответ GenAPI без request_id")
    if is_processing and request_id:
        data = _poll_request(request_id, 180, timeout, "suno")
    return GenApiResult(result=_parse_suno_response(data), request_id=request_id)
