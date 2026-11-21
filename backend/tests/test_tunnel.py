"""Смена адреса quick-туннеля: API перенастраивает вебхуки и кнопку Mini App у всех ботов."""

import pytest
from aiogram import methods

from app.bots.setup import connect_bot
from app.config import get_settings
from app.models import Club
from app.services.tunnel import (
    DNS_SETTLE_SECONDS,
    PublicUrlError,
    SyncIncomplete,
    TelegramRefused,
    resolve_public_url,
    retry_delay,
    settled_check,
    sync_public_url,
)

TOKEN = "123456789:AAFakeTokenForTestsOnly_abcdefghijk"
NEW_URL = "https://new-name.trycloudflare.com"


def fetcher(url: str | None):
    async def fetch():
        return url
    return fetch


async def always_alive(url: str) -> bool:
    return True


async def never_alive(url: str) -> bool:
    return False


async def make_bot(session) -> None:
    club = Club(name="Клуб А")
    session.add(club)
    await session.flush()
    await connect_bot(session, club, TOKEN)


async def test_new_tunnel_url_reconfigures_bots(session, telegram):
    await make_bot(session)
    telegram.calls.clear()

    changed = await sync_public_url(session, fetcher(NEW_URL), alive=always_alive)

    assert changed is True
    assert get_settings().base_url == "https://new-name.trycloudflare.com"
    [webhook] = telegram.of(methods.SetWebhook)
    assert webhook.url == "https://new-name.trycloudflare.com/tg/123456789"
    [menu] = telegram.of(methods.SetChatMenuButton)
    assert menu.menu_button.web_app.url == "https://new-name.trycloudflare.com/app/?bot=123456789"


async def test_same_url_changes_nothing(session, telegram):
    await make_bot(session)
    telegram.calls.clear()

    assert await sync_public_url(session, fetcher("https://example.test"), alive=always_alive) is False
    assert await sync_public_url(session, fetcher(None), alive=always_alive) is False
    assert telegram.calls == []


# --- Сбой 20.11: Telegram не нашёл новый адрес, и API больше не пытался ------------------


async def test_address_not_yet_visible_leaves_bots_untouched(session, telegram):
    """Адрес ещё не открывается — Telegram его не показываем, чтобы он не запомнил «нет такого»."""
    await make_bot(session)
    telegram.calls.clear()

    with pytest.raises(SyncIncomplete):
        await sync_public_url(session, fetcher(NEW_URL), alive=never_alive)

    assert telegram.calls == []
    assert get_settings().base_url == "https://example.test"


async def test_telegram_refusal_is_retried_until_it_succeeds(session, telegram):
    await make_bot(session)
    telegram.calls.clear()
    telegram.fail[methods.SetWebhook] = "Bad Request: bad webhook: Failed to resolve host"

    with pytest.raises(SyncIncomplete):
        await sync_public_url(session, fetcher(NEW_URL), alive=always_alive)

    # Адрес тот же, но настройка не завершена — watch_tunnel повторяет с force=True.
    telegram.fail.clear()
    telegram.calls.clear()
    assert await sync_public_url(session, fetcher(NEW_URL), force=True, alive=always_alive) is True
    [webhook] = telegram.of(methods.SetWebhook)
    assert webhook.url == f"{NEW_URL}/tg/123456789"


async def test_watcher_keeps_forcing_until_success(monkeypatch):
    """Неудача → следующий опрос снова с force=True; после успеха — обычный опрос."""
    from app.services import tunnel

    results = [SyncIncomplete("не готов"), TelegramRefused("нет"), TelegramRefused("нет"), True, False]
    forces, delays = [], []

    async def fake_sync(db, force=False, alive=None):
        forces.append(force)
        outcome = results[len(forces) - 1]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    class Stop(Exception):
        pass

    async def fake_sleep(seconds):
        delays.append(seconds)
        if len(forces) == len(results):
            raise Stop

    class FakeSession:
        async def __aenter__(self):
            return None

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(tunnel, "sync_public_url", fake_sync)
    monkeypatch.setattr(tunnel.asyncio, "sleep", fake_sleep)
    with pytest.raises(Stop):
        await tunnel.watch_tunnel(FakeSession)

    assert forces == [True, True, True, True, False]
    assert delays == [10, 60, 120, 60, 60]


def test_retry_delay_is_capped():
    assert [retry_delay(n) for n in (1, 2, 3, 4, 9)] == [60, 120, 240, 300, 300]


async def test_address_must_stay_alive_before_telegram_sees_it():
    now, up = [0.0], [True]

    async def alive(url):
        return up[0]

    ready = settled_check(alive, lambda: now[0])
    assert await ready(NEW_URL) is False
    now[0] = DNS_SETTLE_SECONDS - 1
    assert await ready(NEW_URL) is False
    now[0] = DNS_SETTLE_SECONDS
    assert await ready(NEW_URL) is True

    up[0] = False  # пропал — выдержка начинается заново
    assert await ready(NEW_URL) is False
    up[0] = True
    assert await ready(NEW_URL) is False


# --- Живой адрес для команд app.cli ---------------------------------------------------

ENV_URL = "https://old-dev.trycloudflare.com"
LIVE_URL = "https://live-server.trycloudflare.com"


def alive_only(*urls):
    async def alive(url):
        return url in urls
    return alive


def tunnel(url):
    async def fetch():
        if url is None:
            raise ConnectionError("туннель не запущен")
        return url
    return fetch


@pytest.fixture
def env_url(monkeypatch):
    monkeypatch.setattr(get_settings(), "public_base_url", ENV_URL)


async def test_running_tunnel_wins_over_stale_env(env_url):
    assert await resolve_public_url(tunnel(LIVE_URL), alive_only(LIVE_URL)) == LIVE_URL


async def test_env_url_used_when_no_server_tunnel_and_it_is_alive(env_url):
    assert await resolve_public_url(tunnel(None), alive_only(ENV_URL)) == ENV_URL


async def test_dead_addresses_are_refused(env_url):
    with pytest.raises(PublicUrlError, match="Боты не изменены"):
        await resolve_public_url(tunnel(None), alive_only())


async def test_http_address_is_never_used(monkeypatch):
    monkeypatch.setattr(get_settings(), "public_base_url", "http://localhost:8000")
    with pytest.raises(PublicUrlError):
        await resolve_public_url(tunnel(None), alive_only("http://localhost:8000"))
