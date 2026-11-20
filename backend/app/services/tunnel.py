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


async def fetch_quicktunnel_url(metrics_url: str | None = None) -> str | None:
    base = metrics_url or get_settings().tunnel_metrics_url
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as http:
        async with http.get(f"{base.rstrip('/')}/quicktunnel") as response:
            response.raise_for_status()
            # cloudflared отдаёт JSON с Content-Type text/plain.
            hostname = (await response.json(content_type=None)).get("hostname")
    return f"https://{hostname}" if hostname else None


async def is_alive(url: str) -> bool:
    """Адрес действительно ведёт на наш API: GET <url>/health отвечает {"status": "ok"}."""
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as http:
            async with http.get(f"{url.rstrip('/')}/health") as response:
                if response.status != 200:
                    return False
                return (await response.json(content_type=None)).get("status") == "ok"
    except Exception:
        return False


class PublicUrlError(Exception):
    pass


async def resolve_public_url(
    fetch_local: Fetcher | None = None,
    alive: Callable[[str], Awaitable[bool]] = is_alive,
) -> str:
    """Живой публичный адрес для команд с компьютера (connect-bot, refresh-webhooks).

    Сначала спрашиваем запущенный туннель рабочего режима (метрики cloudflared на
    127.0.0.1:2000), иначе берём PUBLIC_BASE_URL из .env. Адрес принимается, только
    если через него отвечает наш /health — иначе команда переключила бы ботов
    на мёртвый адрес старого туннеля.
    """
    settings = get_settings()
    if fetch_local is None:
        async def fetch_local() -> str | None:
            return await fetch_quicktunnel_url(settings.tunnel_local_metrics_url)

    candidates = []
    try:
        if url := await fetch_local():
            candidates.append(url)
    except Exception:
        pass  # рабочий туннель не запущен — это нормально в режиме разработки
    candidates.append(settings.base_url)

    for url in dict.fromkeys(candidates):
        if url.startswith("https://") and await alive(url):
            return url
    raise PublicUrlError(
        f"Адрес {settings.base_url} из PUBLIC_BASE_URL не отвечает, а рабочий туннель не найден. "
        "Запустите платформу (docker compose ... up -d) или туннель разработки "
        "и укажите его адрес в PUBLIC_BASE_URL. Боты не изменены."
    )


async def sync_public_url(
    db: AsyncSession, fetch: Fetcher = fetch_quicktunnel_url, force: bool = False
) -> bool:
    """Обновляет PUBLIC_BASE_URL и ботов, если адрес туннеля сменился. True — было изменение.

    force — перенастроить ботов даже при том же адресе (первый опрос после старта API).
    """
    url = await fetch()
    settings = get_settings()
    if not url or (url == settings.base_url and not force):
        return False

    logger.info("Новый адрес туннеля: %s — перенастраиваю ботов", url)
    settings.public_base_url = url
    bots = (await db.scalars(select(ClubBot).where(ClubBot.status == BotStatus.ACTIVE))).all()
    for club_bot in bots:
        try:
            await refresh_bot(db, club_bot)
        except Exception:
            logger.exception("Не удалось перенастроить бота %s", club_bot.bot_id)
    try:
        from app.platform_bot.setup import configure_platform_bot

        await configure_platform_bot()
    except Exception:
        logger.exception("Не удалось перенастроить бота платформы")
    return True


async def watch_tunnel(session_factory: Callable[[], AsyncSession]) -> None:
    known = False
    while True:
        try:
            async with session_factory() as db:
                await sync_public_url(db, force=not known)
            known = True
        except Exception as e:
            # Туннель ещё поднимается или временно недоступен — попробуем позже.
            logger.warning("Адрес туннеля пока недоступен: %s", type(e).__name__)
        # Пока адрес не получен ни разу (старт), проверяем чаще.
        await asyncio.sleep(CHECK_SECONDS if known else STARTUP_CHECK_SECONDS)
