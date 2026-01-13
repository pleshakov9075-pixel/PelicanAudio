from fastapi import FastAPI

from app.api.routes.health import router as health_router
from app.api.routes.yookassa_webhook import router as yookassa_router
from app.core.logging import setup_logging


def create_app() -> FastAPI:
    setup_logging()
    app = FastAPI(title="Music TG Bot API")
    app.include_router(health_router)
    app.include_router(yookassa_router, prefix="/api/payments")
    return app


app = create_app()
