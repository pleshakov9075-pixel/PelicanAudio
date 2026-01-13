from aiogram import Router

from app.bot.handlers import start, presets, balance, help as help_handler, create_track


def setup_router() -> Router:
    router = Router()
    router.include_router(start.router)
    router.include_router(presets.router)
    router.include_router(balance.router)
    router.include_router(help_handler.router)
    router.include_router(create_track.router)
    return router
