import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.redis import RedisStorage
import redis.asyncio as redis

from app.core.config import settings
from app.core.logging import setup_logging
from app.bot.router import setup_router


async def main() -> None:
    setup_logging()
    logger = logging.getLogger("bot")
    if not settings.bot_token:
        logger.error("Не задан BOT_TOKEN")
        raise RuntimeError("BOT_TOKEN отсутствует")

    redis_client = redis.from_url(settings.redis_url)
    storage = RedisStorage(redis_client)

    bot = Bot(token=settings.bot_token)
    dispatcher = Dispatcher(storage=storage)
    dispatcher.include_router(setup_router())

    logger.info("Бот запущен")
    await dispatcher.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
