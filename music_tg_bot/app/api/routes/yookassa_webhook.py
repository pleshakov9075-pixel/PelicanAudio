import logging

from fastapi import APIRouter, Request, HTTPException

from app.core.db import SessionLocal
from app.core.repo import get_or_create_user, add_topup
from app.integrations.yookassa import parse_webhook, YooKassaError

router = APIRouter()
logger = logging.getLogger("api.yookassa")


@router.post("/yookassa/webhook")
async def yookassa_webhook(request: Request) -> dict:
    payload = await request.json()
    try:
        payment_id, user_id, amount = parse_webhook(payload)
    except YooKassaError as exc:
        logger.warning("Webhook отклонён: %s", exc)
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    with SessionLocal() as session:
        user = get_or_create_user(session, user_id)
        add_topup(session, user, amount, payment_id)

    return {"status": "ok"}
