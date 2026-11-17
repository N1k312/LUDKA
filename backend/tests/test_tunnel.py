"""Смена адреса quick-туннеля: API перенастраивает вебхуки и кнопку Mini App у всех ботов."""

import pytest
from aiogram import methods

from app.bots.setup import connect_bot
from app.config import get_settings
from app.models import Club
from app.services.tunnel import PublicUrlError, resolve_public_url, sync_public_url

TOKEN = "123456789:AAFakeTokenForTestsOnly_abcdefghijk"


def fetcher(url: str | None):
    async def fetch():
        return url
    return fetch


async def make_bot(session) -> None:
    club = Club(name="Клуб А")
    session.add(club)
    await session.flush()
    await connect_bot(session, club, TOKEN)


async def test_new_tunnel_url_reconfigures_bots(session, telegram):
    await make_bot(session)
    telegram.calls.clear()

    changed = await sync_public_url(session, fetcher("https://new-name.trycloudflare.com"))

    assert changed is True
    assert get_settings().base_url == "https://new-name.trycloudflare.com"
    [webhook] = telegram.of(methods.SetWebhook)
    assert webhook.url == "https://new-name.trycloudflare.com/tg/123456789"
    [menu] = telegram.of(methods.SetChatMenuButton)
    assert menu.menu_button.web_app.url == "https://new-name.trycloudflare.com/app/?bot=123456789"


async def test_same_url_changes_nothing(session, telegram):
    await make_bot(session)
    telegram.calls.clear()

    assert await sync_public_url(session, fetcher("https://example.test")) is False
    assert await sync_public_url(session, fetcher(None)) is False
    assert telegram.calls == []


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
