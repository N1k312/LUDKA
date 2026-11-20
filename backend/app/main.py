import asyncio
import logging
from contextlib import asynccontextmanager, suppress

from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.bots.registry import registry
from app.db import SessionLocal, engine, get_session
from app.logging_setup import install_redaction, setup_logging
from app.platform_bot.setup import configure_platform_bot
from app.routes import miniapp, platform, telegram
from app.config import get_settings
from app.services.subscription import refresh_statuses
from app.services.tunnel import watch_tunnel

setup_logging()
logger = logging.getLogger(__name__)


SUBSCRIPTION_CHECK_SECONDS = 600


async def watch_subscriptions() -> None:
    """Раз в 10 минут переводит статусы подписок по датам (trial → past_due → canceled)."""
    while True:
        try:
            async with SessionLocal() as db:
                await refresh_statuses(db)
                await db.commit()
        except Exception:
            logger.exception("Не удалось обновить статусы подписок")
        await asyncio.sleep(SUBSCRIPTION_CHECK_SECONDS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    install_redaction()  # на случай, если uvicorn пересоздал обработчики
    tasks = [asyncio.create_task(watch_subscriptions())]
    if get_settings().tunnel_metrics_url:
        # Адрес туннеля неизвестен до первого опроса: боты (и бот платформы) настроятся в нём.
        tasks.append(asyncio.create_task(watch_tunnel(SessionLocal)))
    else:
        try:
            await configure_platform_bot()
        except Exception:
            logger.exception("Не удалось настроить бота платформы")
    yield
    for task in tasks:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
    await registry.close()
    await engine.dispose()


app = FastAPI(title="Club Loyalty Games", lifespan=lifespan)
app.include_router(platform.router)  # /tg/platform — раньше /tg/{bot_id}
app.include_router(telegram.router)
app.include_router(miniapp.router)


@app.get("/health")
async def health(session: AsyncSession = Depends(get_session)):
    try:
        await session.execute(text("SELECT 1"))
    except Exception:
        logger.exception("Health check: database unavailable")
        return JSONResponse(status_code=503, content={"status": "error", "db": "unavailable"})
    return {"status": "ok", "db": "ok"}
