"""Публичный адрес при запуске без домена: quick-туннель cloudflared.

Адрес вида https://xxx.trycloudflare.com меняется при каждом перезапуске туннеля.
API раз в минуту спрашивает его у cloudflared (GET <metrics>/quicktunnel) и, если
адрес сменился, запоминает его и заново настраивает всех ботов: вебхук и кнопку Mini App.
QR-коды у кассы ведут на t.me/<бот> и от адреса не зависят.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable

import aiohttp
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bots.setup import refresh_bot
from app.config import get_settings
from app.models import BotStatus, ClubBot

logger = logging.getLogger(__name__)

CHECK_SECONDS = 60
STARTUP_CHECK_SECONDS = 10

Fetcher = Callable[[], Awaitable[str | None]]


async def fetch_quicktunnel_url() -> str | None:
    url = f"{get_settings().tunnel_metrics_url.rstrip('/')}/quicktunnel"
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as http:
        async with http.get(url) as response:
            response.raise_for_status()
            # cloudflared отдаёт JSON с Content-Type text/plain.
            hostname = (await response.json(content_type=None)).get("hostname")
    return f"https://{hostname}" if hostname else None


async def sync_public_url(db: AsyncSession, fetch: Fetcher = fetch_quicktunnel_url) -> bool:
    """Обновляет PUBLIC_BASE_URL и ботов, если адрес туннеля сменился. True — было изменение."""
    url = await fetch()
    settings = get_settings()
    if not url or url == settings.base_url:
        return False

    logger.info("Новый адрес туннеля: %s — перенастраиваю ботов", url)
    settings.public_base_url = url
    bots = (await db.scalars(select(ClubBot).where(ClubBot.status == BotStatus.ACTIVE))).all()
    for club_bot in bots:
        try:
            await refresh_bot(db, club_bot)
        except Exception:
            logger.exception("Не удалось перенастроить бота %s", club_bot.bot_id)
    return True


async def watch_tunnel(session_factory: Callable[[], AsyncSession]) -> None:
    known = False
    while True:
        try:
            async with session_factory() as db:
                await sync_public_url(db)
            known = True
        except Exception as e:
            # Туннель ещё поднимается или временно недоступен — попробуем позже.
            logger.warning("Адрес туннеля пока недоступен: %s", type(e).__name__)
        # Пока адрес не получен ни разу (старт), проверяем чаще.
        await asyncio.sleep(CHECK_SECONDS if known else STARTUP_CHECK_SECONDS)
