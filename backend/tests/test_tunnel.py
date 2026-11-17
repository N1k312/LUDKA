"""Смена адреса quick-туннеля: API перенастраивает вебхуки и кнопку Mini App у всех ботов."""

from aiogram import methods

from app.bots.setup import connect_bot
from app.config import get_settings
from app.models import Club
from app.services.tunnel import sync_public_url

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
