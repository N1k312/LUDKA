"""Публичный адрес при запуске без домена: quick-туннель cloudflared.

Адрес вида https://xxx.trycloudflare.com меняется при каждом перезапуске туннеля.
API раз в минуту спрашивает его у cloudflared (GET <metrics>/quicktunnel) и, если
адрес сменился, запоминает его и заново настраивает всех ботов: вебхук и кнопку Mini App.
QR-коды у кассы ведут на t.me/<бот> и от адреса не зависят.
"""

import asyncio
import logging
import time
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
DNS_SETTLE_SECONDS = 90
MAX_RETRY_SECONDS = 300

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


class SyncIncomplete(Exception):
    """Адрес туннеля ещё не готов — повторить позже."""


class TelegramRefused(SyncIncomplete):
    """Telegram не принял вебхук хотя бы одного бота — повторять редко, с нарастающей паузой."""


def retry_delay(refusals: int) -> int:
    """Пауза после N-го отказа Telegram подряд: 60, 120, 240, затем 300 секунд."""
    return min(CHECK_SECONDS * 2 ** (refusals - 1), MAX_RETRY_SECONDS)


def settled_check(
    alive: Callable[[str], Awaitable[bool]] = is_alive,
    clock: Callable[[], float] = time.monotonic,
) -> Callable[[str], Awaitable[bool]]:
    """Проверка «адрес открывается уже DNS_SETTLE_SECONDS подряд».

    Наш сервер видит новый адрес раньше, чем DNS Telegram, а неудачный поиск Telegram
    запоминает на минуты. Поэтому первый запрос к Telegram делаем только после выдержки.
    """
    first_seen: dict[str, float] = {}

    async def ready(url: str) -> bool:
        if not await alive(url):
            first_seen.pop(url, None)
            return False
        return clock() - first_seen.setdefault(url, clock()) >= DNS_SETTLE_SECONDS

    return ready


async def sync_public_url(
    db: AsyncSession,
    fetch: Fetcher = fetch_quicktunnel_url,
    force: bool = False,
    alive: Callable[[str], Awaitable[bool]] = is_alive,
) -> bool:
    """Обновляет PUBLIC_BASE_URL и ботов, если адрес туннеля сменился. True — было изменение.

    force — перенастроить ботов даже при том же адресе (первый опрос после старта API
    или повтор после неудачи).

    Новый адрес quick-туннеля появляется в DNS не сразу. Если отдать его Telegram раньше,
    Telegram отвечает «Failed to resolve host» и запоминает это на минуты. Поэтому ботов
    трогаем, только когда наш /health уже открывается по новому адресу. Если хоть один бот
    не настроился — SyncIncomplete: watch_tunnel повторит всё целиком.
    """
    url = await fetch()
    settings = get_settings()
    if not url or (url == settings.base_url and not force):
        return False
    if not await alive(url):
        raise SyncIncomplete(f"адрес {url} ещё не готов")

    logger.info("Новый адрес туннеля: %s — перенастраиваю ботов", url)
    settings.public_base_url = url
    failed = 0
    bots = (await db.scalars(select(ClubBot).where(ClubBot.status == BotStatus.ACTIVE))).all()
    for club_bot in bots:
        try:
            await refresh_bot(db, club_bot)
        except Exception:
            failed += 1
            logger.exception("Не удалось перенастроить бота %s", club_bot.bot_id)
    try:
        from app.platform_bot.setup import configure_platform_bot

        await configure_platform_bot()
    except Exception:
        failed += 1
        logger.exception("Не удалось перенастроить бота платформы")
    if failed:
        raise TelegramRefused(f"не настроено ботов: {failed}")
    logger.info("Все боты перенастроены на %s", url)
    return True


async def watch_tunnel(session_factory: Callable[[], AsyncSession]) -> None:
    pending = True  # последняя настройка не завершена — повторять даже при том же адресе
    refusals = 0  # отказов Telegram подряд
    ready = settled_check()
    while True:
        refused = False
        try:
            async with session_factory() as db:
                await sync_public_url(db, force=pending, alive=ready)
            pending, refusals = False, 0
        except Exception as e:
            pending = True
            refused = isinstance(e, TelegramRefused)
            refusals = refusals + 1 if refused else refusals
            # Туннель ещё поднимается, адрес не виден в DNS или Telegram его не принял.
            detail = str(e) if isinstance(e, SyncIncomplete) else type(e).__name__
            logger.warning("Боты пока не перенастроены (%s) — повторю", detail)
        if refused:
            delay = retry_delay(refusals)
        elif pending:
            delay = STARTUP_CHECK_SECONDS  # ждём готовности адреса
        else:
            delay = CHECK_SECONDS
        await asyncio.sleep(delay)
