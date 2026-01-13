from __future__ import annotations

import logging
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
    try:
        response = httpx.post(url, headers=_headers(), json=payload, timeout=60)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise GenApiError(f"Ошибка GenAPI: {exc}") from exc
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
    try:
        response = httpx.post(url, headers=_headers(), json=payload, timeout=120)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise GenApiError(f"Ошибка Suno: {exc}") from exc
    data = response.json()
    if not isinstance(data, list) or len(data) < 2:
        raise GenApiError("Неожиданный ответ Suno")
    return data
