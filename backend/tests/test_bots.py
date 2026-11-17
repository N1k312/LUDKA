"""Подключение ботов и вебхуки. Telegram заменён фейковой сессией aiogram."""

import pytest
from aiogram import methods
from sqlalchemy.ext.asyncio import AsyncSession

from app.bots.setup import BotSetupError, connect_bot
from app.config import get_settings
from app.crypto import decrypt, encrypt
from app.models import Club, ClubBot
from tests.telegram_fake import FakeTelegram

TOKEN = "123456789:AAFakeTokenForTestsOnly_abcdefghijk"
BOT_ID = 123456789


async def make_club(db: AsyncSession, name: str = "Клуб А") -> Club:
    club = Club(name=name)
    db.add(club)
    await db.flush()
    return club


def test_encrypt_roundtrip():
    encrypted = encrypt(TOKEN)
    assert TOKEN not in encrypted
    assert decrypt(encrypted) == TOKEN


async def test_connect_bot_stores_encrypted_token_and_sets_webhook(session, telegram):
    club = await make_club(session)

    club_bot = await connect_bot(session, club, TOKEN)

    assert club_bot.bot_id == BOT_ID
    assert club_bot.club_id == club.id
    assert club_bot.username == "test_club_bot"
    assert club_bot.token_encrypted != TOKEN
    assert decrypt(club_bot.token_encrypted) == TOKEN

    [webhook] = telegram.of(methods.SetWebhook)
    assert webhook.url == f"https://example.test/tg/{BOT_ID}"
    assert webhook.secret_token == club_bot.webhook_secret
    [menu] = telegram.of(methods.SetChatMenuButton)
    assert menu.menu_button.web_app.url == f"https://example.test/app/?bot={BOT_ID}"
    assert telegram.of(methods.SetMyCommands)
    assert "Клуб А" in telegram.of(methods.SetMyDescription)[0].description


async def test_connect_bot_requires_https(session, telegram, monkeypatch):
    monkeypatch.setattr(get_settings(), "public_base_url", "http://localhost:8000")
    club = await make_club(session)

    with pytest.raises(BotSetupError):
        await connect_bot(session, club, TOKEN)
    assert telegram.calls == []


async def test_bot_cannot_be_connected_to_second_club(session, telegram):
    club_a = await make_club(session, "А")
    club_b = await make_club(session, "Б")
    await connect_bot(session, club_a, TOKEN)

    with pytest.raises(BotSetupError, match="другому клубу"):
        await connect_bot(session, club_b, TOKEN)


async def test_reconnect_same_bot_rotates_webhook_secret(session, telegram):
    club = await make_club(session)
    first_secret = (await connect_bot(session, club, TOKEN)).webhook_secret

    second = await connect_bot(session, club, TOKEN)

    assert second.webhook_secret != first_secret


# --- Вебхук -----------------------------------------------------------------

START_UPDATE = {
    "update_id": 1,
    "message": {
        "message_id": 1,
        "date": 1_700_000_000,
        "chat": {"id": 555, "type": "private"},
        "from": {"id": 555, "is_bot": False, "first_name": "Гость"},
        "text": "/start desk",
        "entities": [{"type": "bot_command", "offset": 0, "length": 6}],
    },
}


async def connected_bot(session: AsyncSession, telegram: FakeTelegram) -> ClubBot:
    club_bot = await connect_bot(session, await make_club(session), TOKEN)
    telegram.calls.clear()
    return club_bot


async def test_webhook_start_replies_with_club_name(session, telegram, client):
    club_bot = await connected_bot(session, telegram)

    response = await client.post(
        f"/tg/{BOT_ID}",
        json=START_UPDATE,
        headers={"X-Telegram-Bot-Api-Secret-Token": club_bot.webhook_secret},
    )

    assert response.status_code == 200
    [reply] = telegram.of(methods.SendMessage)
    assert reply.chat_id == 555
    assert "Клуб А" in reply.text
    # Новый гость сначала делится номером (сценарий регистрации — в test_guests.py).
    assert reply.reply_markup.keyboard[0][0].request_contact is True


@pytest.mark.parametrize("secret", [None, "wrong-secret"])
async def test_webhook_rejects_bad_secret(session, telegram, client, secret):
    await connected_bot(session, telegram)
    headers = {"X-Telegram-Bot-Api-Secret-Token": secret} if secret else {}

    response = await client.post(f"/tg/{BOT_ID}", json=START_UPDATE, headers=headers)

    assert response.status_code == 403
    assert telegram.calls == []


async def test_webhook_unknown_bot_same_answer_as_bad_secret(session, telegram, client):
    response = await client.post(
        "/tg/999", json=START_UPDATE, headers={"X-Telegram-Bot-Api-Secret-Token": "x"}
    )

    assert response.status_code == 403


async def test_webhook_secret_of_other_bot_rejected(session, telegram, client):
    """Секрет бота клуба А не открывает вебхук бота клуба Б."""
    club_bot_a = await connected_bot(session, telegram)
    club_b = await make_club(session, "Клуб Б")
    other_token = "987654321:AAAnotherFakeTokenForTests_abcdefghij"
    await connect_bot(session, club_b, other_token)
    telegram.calls.clear()

    response = await client.post(
        "/tg/987654321",
        json=START_UPDATE,
        headers={"X-Telegram-Bot-Api-Secret-Token": club_bot_a.webhook_secret},
    )

    assert response.status_code == 403
    assert telegram.calls == []
