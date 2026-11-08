import asyncio
import logging
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from app.bot import tg_bot
from app.config import settings
from app.db import db
from app.scheduler import (
    create_scheduler,
    poll_rss_feeds,
    run_gradual_ai_digest,
    run_weekly_ai_digest,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger("newz")


@asynccontextmanager
async def lifespan(app: FastAPI):
    is_first_run = db.init_db()
    logger.info("База данных инициализирована (первый запуск: %s).", is_first_run)

    # Привязываем колбэки для ручных команд из Telegram (/check, /gradual, /digest)
    tg_bot.on_force_check = lambda: poll_rss_feeds(silent_seed=False)
    tg_bot.on_force_gradual = run_gradual_ai_digest
    tg_bot.on_force_weekly = run_weekly_ai_digest

    scheduler = create_scheduler()
    scheduler.start()
    await tg_bot.start_polling()

    # Первичная индексация в фоне при старте контейнера
    asyncio.create_task(
        poll_rss_feeds(silent_seed=(is_first_run and settings.seed_silently_on_first_run))
    )

    yield

    await tg_bot.stop_polling()
    scheduler.shutdown(wait=False)
    logger.info("Сервис NEWZ остановлен.")


app = FastAPI(
    title="NEWZ Telegram Monitoring Service",
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/")
async def health_check():
    """Health check эндпоинт для платформы Koyeb."""
    return {"status": "healthy"}


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=settings.port)
