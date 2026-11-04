"""Точка входу: FastAPI (API + Mini App) і Telegram-бот в одному процесі.

Запуск:  uvicorn app.main:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from . import services as svc
from .api import router
from .config import Settings
from .models import make_session_factory

log = logging.getLogger("warehouse")
WEBAPP_DIR = Path(__file__).resolve().parent.parent / "webapp"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.media_dir.mkdir(parents=True, exist_ok=True)
    session_factory = make_session_factory(f"sqlite:///{settings.db_path}")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        with session_factory() as db:
            for admin_id in settings.admin_ids:
                svc.upsert_user(db, admin_id, role="admin")

        bot_task = None
        if settings.run_bot and settings.bot_token:
            from .bot import build_bot, build_dispatcher, setup_bot_ui

            bot = build_bot(settings)
            dp = build_dispatcher(settings, session_factory)
            try:
                me = await bot.get_me()
                if not settings.app_link:
                    app.state.app_link = f"https://t.me/{me.username}?startapp="
                await setup_bot_ui(bot, settings)
                log.info("Бот @%s запущено", me.username)
            except Exception:
                log.exception("Не вдалося підключитися до Telegram — перевірте BOT_TOKEN")
            bot_task = asyncio.create_task(dp.start_polling(bot, handle_signals=False))
        elif settings.run_bot:
            log.warning("BOT_TOKEN не задано — працює лише веб-частина")

        yield

        if bot_task:
            bot_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await bot_task
            await bot.session.close()

    app = FastAPI(title="Склад", lifespan=lifespan)
    app.state.settings = settings
    app.state.session_factory = session_factory
    app.state.app_link = settings.app_link or f"{settings.webapp_url}/?startapp="

    app.include_router(router)

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    app.mount("/media", StaticFiles(directory=settings.media_dir), name="media")
    app.mount("/", StaticFiles(directory=WEBAPP_DIR, html=True), name="webapp")
    return app


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def __getattr__(name: str):
    # Лінивий `app`, щоб імпорт модуля в тестах не читав .env і не створював data/.
    if name == "app":
        global app
        app = create_app()
        return app
    raise AttributeError(name)
