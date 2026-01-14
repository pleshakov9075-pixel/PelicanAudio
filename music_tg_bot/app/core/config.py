from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import os


@dataclass(frozen=True)
class Settings:
    bot_token: str
    genapi_base_url: str
    genapi_api_key: str
    genapi_timeout_connect: float
    genapi_timeout_read_grok: float
    genapi_timeout_read_suno: float
    genapi_retries: int
    genapi_retry_backoff: float
    database_url: str
    redis_url: str
    yookassa_shop_id: Optional[str]
    yookassa_secret_key: Optional[str]
    yookassa_return_url: Optional[str]
    base_url: str
    storage_dir: Path
    environment: str


def load_settings() -> Settings:
    storage_dir = Path(os.getenv("STORAGE_DIR", "./storage")).resolve()
    storage_dir.mkdir(parents=True, exist_ok=True)
    return Settings(
        bot_token=os.environ.get("BOT_TOKEN", "").strip(),
        genapi_base_url=os.environ.get("GENAPI_BASE_URL", "https://api.gen-api.ru"),
        genapi_api_key=os.environ.get("GENAPI_API_KEY", "").strip(),
        genapi_timeout_connect=float(os.environ.get("GENAPI_TIMEOUT_CONNECT", "30")),
        genapi_timeout_read_grok=float(os.environ.get("GENAPI_TIMEOUT_READ_GROK", "60")),
        genapi_timeout_read_suno=float(os.environ.get("GENAPI_TIMEOUT_READ_SUNO", "240")),
        genapi_retries=int(os.environ.get("GENAPI_RETRIES", "3")),
        genapi_retry_backoff=float(os.environ.get("GENAPI_RETRY_BACKOFF", "1")),
        database_url=os.environ.get(
            "DATABASE_URL",
            "postgresql+psycopg://postgres:postgres@postgres:5432/music_bot",
        ),
        redis_url=os.environ.get("REDIS_URL", "redis://redis:6379/0"),
        yookassa_shop_id=os.environ.get("YOOKASSA_SHOP_ID", "").strip() or None,
        yookassa_secret_key=os.environ.get("YOOKASSA_SECRET_KEY", "").strip() or None,
        yookassa_return_url=os.environ.get("YOOKASSA_RETURN_URL", "").strip() or None,
        base_url=os.environ.get("BASE_URL", "http://localhost:8000").rstrip("/"),
        storage_dir=storage_dir,
        environment=os.environ.get("ENVIRONMENT", "production"),
    )


settings = load_settings()
