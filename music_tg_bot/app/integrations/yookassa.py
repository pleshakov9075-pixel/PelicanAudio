from __future__ import annotations

import base64
import logging
import uuid
from typing import Any

import httpx

from app.core.config import settings

logger = logging.getLogger("yookassa")


class YooKassaError(RuntimeError):
    pass


def _auth_header() -> dict[str, str]:
    if not settings.yookassa_shop_id or not settings.yookassa_secret_key:
        raise YooKassaError("Оплата временно недоступна: не настроены ключи")
    token = f"{settings.yookassa_shop_id}:{settings.yookassa_secret_key}".encode()
    encoded = base64.b64encode(token).decode()
    return {"Authorization": f"Basic {encoded}"}


def create_payment(amount_rub: int, description: str, user_id: int) -> str:
    idempotence_key = str(uuid.uuid4())
    payload = {
        "amount": {"value": f"{amount_rub}.00", "currency": "RUB"},
        "confirmation": {
            "type": "redirect",
            "return_url": settings.yookassa_return_url or settings.base_url,
        },
        "capture": True,
        "description": description,
        "metadata": {"user_id": str(user_id), "amount_rub": str(amount_rub)},
    }
    headers = _auth_header()
    headers.update({"Idempotence-Key": idempotence_key})
    url = "https://api.yookassa.ru/v3/payments"
    try:
        response = httpx.post(url, headers=headers, json=payload, timeout=20)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise YooKassaError(f"Ошибка YooKassa: {exc}") from exc
    data = response.json()
    confirmation = data.get("confirmation", {})
    return confirmation.get("confirmation_url", "")


def parse_webhook(payload: dict[str, Any]) -> tuple[str, int, str]:
    event = payload.get("event")
    if event != "payment.succeeded":
        raise YooKassaError("Платеж не подтвержден")
    obj = payload.get("object", {})
    payment_id = obj.get("id")
    metadata = obj.get("metadata", {})
    try:
        user_id = int(metadata.get("user_id"))
        amount = int(float(obj.get("amount", {}).get("value", "0")))
    except (TypeError, ValueError) as exc:
        raise YooKassaError("Некорректные данные платежа") from exc
    if not payment_id:
        raise YooKassaError("Отсутствует идентификатор платежа")
    return payment_id, user_id, amount
