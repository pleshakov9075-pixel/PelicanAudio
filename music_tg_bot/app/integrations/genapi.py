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
        raise GenApiError("Не задан ключ API сервиса генерации")
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
            raise GenApiError("⚠️ Проблема с сетью. Пробую ещё раз…") from exc
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
            raise GenApiError("⚠️ Проблема с сетью. Пробую ещё раз…") from exc
        except httpx.HTTPStatusError as exc:
            logger.error("HTTP ошибка GenAPI (%s): %s", operation, exc)
            raise GenApiError("⚠️ Сервис временно недоступен. Попробуй позже.") from exc
    raise GenApiError("⚠️ Проблема с сетью. Пробую ещё раз…") from last_exc


def _parse_grok_response(data: Any) -> str:
    payload, branch = _unwrap_payload(data)
    if branch:
        logger.info("GenAPI grok: извлечён вложенный payload из '%s'", branch)
    payload = unwrap_payload(payload)
    if isinstance(payload, str):
        logger.info("GenAPI grok: получен текст напрямую")
        return payload
    try:
        logger.info("GenAPI grok: парсинг текста")
        return extract_llm_text(payload)
    except ValueError as exc:
        logger.error("Неожиданный ответ GenAPI (grok): %s", _payload_brief(payload))
        raise GenApiError("❌ Не удалось получить ответ от сервиса генерации. Попробуй ещё раз.") from exc


def _parse_suno_response(data: Any) -> list[str]:
    payload, branch = _unwrap_payload(data)
    if branch:
        logger.info("GenAPI suno: извлечён вложенный payload из '%s'", branch)
    if isinstance(payload, list):
        urls = _extract_urls_from_list(payload)
        if len(urls) >= 2:
            logger.info("GenAPI suno: парсинг списка")
            return urls[:2]
    if isinstance(payload, dict):
        urls = _extract_urls_from_dict(payload)
        if len(urls) >= 2:
            logger.info("GenAPI suno: парсинг словаря")
            return urls[:2]
    logger.error("Неожиданный ответ Suno: %s", _payload_brief(payload))
    raise GenApiError("Неожиданный ответ Suno")


def _maybe_processing(data: Any) -> tuple[bool, int | None]:
    if isinstance(data, dict) and data.get("status") in {"processing", "queued", "pending", "running"}:
        return True, data.get("request_id")
    return False, None


def _raise_if_failed(data: Any) -> None:
    if isinstance(data, dict) and data.get("status") in {"failed", "error"}:
        message = data.get("error") or data.get("message")
        if isinstance(message, str) and "genapi" in message.lower():
            message = None
        raise GenApiError(message or "⚠️ Сервис временно недоступен. Попробуй позже.")


def _payload_brief(data: Any) -> str:
    if isinstance(data, dict):
        return f"keys={list(data.keys())}"
    if isinstance(data, list):
        return f"list(len={len(data)})"
    return f"type={type(data).__name__}"


def _unwrap_payload(data: Any) -> tuple[Any, str | None]:
    if isinstance(data, dict):
        for key in ("response", "result", "full_response", "data", "payload"):
            if key in data:
                return data[key], key
    return data, None


def unwrap_payload(payload: Any) -> Any:
    if isinstance(payload, list):
        if len(payload) == 1:
            return payload[0]
        for item in payload:
            if isinstance(item, dict) and ("choices" in item or "text" in item):
                return item
        return payload[0]
    if isinstance(payload, dict):
        return payload
    return payload


def extract_llm_text(payload: Any) -> str:
    if isinstance(payload, dict) and "choices" in payload:
        try:
            return payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            keys = list(payload.keys())
            raise ValueError(f"Unexpected choices payload keys={keys}") from exc
    if isinstance(payload, dict) and "text" in payload:
        return str(payload["text"])
    keys = list(payload.keys()) if isinstance(payload, dict) else None
    raise ValueError(f"Unexpected payload type={type(payload).__name__} keys={keys}")


def _extract_urls_from_list(data: list[Any]) -> list[str]:
    urls: list[str] = []
    for item in data:
        if isinstance(item, str):
            urls.append(item)
            continue
        if isinstance(item, dict):
            url = item.get("audio_url") or item.get("url") or item.get("mp3_url")
            if url:
                urls.append(str(url))
    return urls


def _extract_urls_from_dict(data: dict[str, Any]) -> list[str]:
    urls: list[str] = []
    for key in ("audio_url_1", "audio_url_2", "mp3_url_1", "mp3_url_2"):
        if data.get(key):
            urls.append(str(data[key]))
    if len(urls) >= 2:
        return urls
    for key in ("audio_url", "mp3_url"):
        if data.get(key):
            urls.append(str(data[key]))
    clips = data.get("clips")
    if isinstance(clips, list):
        urls.extend(_extract_urls_from_list(clips))
    return urls


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
            raise GenApiError("⏱️ Превышено время ожидания сервиса генерации.")
        response = _request_with_retries("GET", url, None, timeout, operation)
        data = response.json()
        if isinstance(data, dict):
            logger.info(
                "GenAPI polling %s: status=%s keys=%s request_id=%s",
                operation,
                data.get("status"),
                list(data.keys()),
                request_id,
            )
        if isinstance(data, dict):
            status = data.get("status")
            if status in {"processing", "queued", "pending", "running"}:
                attempt += 1
                sleep_seconds = min(1.0 + 0.5 * attempt, 2.0)
                time.sleep(sleep_seconds)
                continue
            if status in {"failed", "error"}:
                message = data.get("error") or data.get("message")
                if isinstance(message, str) and "genapi" in message.lower():
                    message = None
                raise GenApiError(message or "⚠️ Сервис временно недоступен. Попробуй позже.")
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
    if isinstance(data, dict):
        logger.info("GenAPI grok: initial keys=%s status=%s", list(data.keys()), data.get("status"))
    _raise_if_failed(data)
    is_processing, request_id = _maybe_processing(data)
    if is_processing and not request_id:
        raise GenApiError("❌ Не удалось получить ответ от сервиса генерации. Попробуй ещё раз.")
    if is_processing and request_id:
        logger.info("GenAPI grok: request_id=%s status=processing", request_id)
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
    if isinstance(data, dict):
        logger.info("GenAPI suno: initial keys=%s status=%s", list(data.keys()), data.get("status"))
    _raise_if_failed(data)
    is_processing, request_id = _maybe_processing(data)
    if is_processing and not request_id:
        raise GenApiError("❌ Не удалось получить ответ от сервиса генерации. Попробуй ещё раз.")
    if is_processing and request_id:
        logger.info("GenAPI suno: request_id=%s status=processing", request_id)
        data = _poll_request(request_id, 180, timeout, "suno")
    return GenApiResult(result=_parse_suno_response(data), request_id=request_id)
