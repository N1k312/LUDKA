"""Общие помощники аудиторских тестов (баги / функционал / безопасность).

Сценарии идут через настоящие точки входа: вебхуки ботов (/tg/...), API Mini App
(/api/...) и бот платформы (/tg/platform). Telegram заменён фейковой сессией.
"""

import re
from collections.abc import AsyncIterator
from datetime import timedelta
from html import unescape

import pytest
from aiogram import methods
from aiogram.exceptions import TelegramBadRequest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from app.bots.handlers import CONSENT_CALLBACK
from app.bots.registry import registry
from app.bots.setup import connect_bot
from app.config import get_settings
from app.db import get_session
from app.main import app
from app.models import Club, ClubBot
from app.platform_bot.dispatcher import platform_dp
from app.platform_bot.handlers import Nav
from app.platform_bot.setup import webhook_secret
from app.services import clubs
from tests.botkit import BotClient, callback_update, contact_update, message_update
from tests.telegram_fake import FakeTelegram
from tests.test_miniapp import headers

TOKEN_A = "123456789:AAFakeTokenForTestsOnly_abcdefghijk"
TOKEN_B = "987654321:AAAnotherFakeTokenForTests_abcdefghij"
PLATFORM_TOKEN = "555000111:AAPlatformFakeTokenForTests_abcdefgh"
BOT_A, BOT_B, PLATFORM_BOT = 123456789, 987654321, 555000111
OWNER = 9000
WEEK = timedelta(days=7)


# --- Фейковый Telegram, который ведёт себя строже ------------------------------------


class StrictTelegram(FakeTelegram):
    """Как настоящий Telegram: ошибки на заданных методах и «message is not modified»."""

    def __init__(self, fail_on: tuple = ()) -> None:
        super().__init__()
        self.fail_on = fail_on
        self._shown: dict[tuple, tuple] = {}

    async def make_request(self, bot, method, timeout=None):
        if self.fail_on and isinstance(method, self.fail_on):
            self.calls.append(method)
            raise TelegramBadRequest(method=method, message="Bad Request: query is too old")
        if isinstance(method, methods.EditMessageText):
            key = (method.chat_id, method.message_id)
            shown = (method.text, repr(method.reply_markup))
            if self._shown.get(key) == shown:
                self.calls.append(method)
                raise TelegramBadRequest(method=method, message="Bad Request: message is not modified")
            self._shown[key] = shown
        return await super().make_request(bot, method, timeout)


def visible_len(html_text: str) -> int:
    """Длина текста так, как её считает Telegram: без HTML-тегов и с раскрытыми сущностями."""
    return len(unescape(re.sub(r"<[^>]+>", "", html_text)))


# --- Клуб, гость, бот платформы ----------------------------------------------------------


async def club_with_bot(
    db: AsyncSession, name: str, token: str,
    prizes=(("1 час игры", 1, False),), rules: str | None = "Правила клуба",
) -> tuple[Club, ClubBot]:
    club = await clubs.create_club(db, name)
    club.rules_text = rules
    for title, weight, empty in prizes:
        await clubs.add_prize(db, club, title, weight, "🎁", empty)
    club_bot = await connect_bot(db, club, token)
    return club, club_bot


async def register(bot: BotClient, user_id: int, phone: str) -> None:
    await bot.send(message_update("/start desk", user_id=user_id))
    await bot.send(contact_update(phone, contact_user_id=user_id, user_id=user_id))
    await bot.send(callback_update(CONSENT_CALLBACK, user_id=user_id))


def api(token: str, bot_id: int, user_id: int) -> dict:
    return headers(token, bot_id, user_id=user_id)


class PlatformClient:
    def __init__(self, client: AsyncClient, telegram: FakeTelegram):
        self.client, self.telegram = client, telegram

    async def send(self, update: dict) -> int:
        self.telegram.calls.clear()
        r = await self.client.post("/tg/platform", json=update,
                                   headers={"X-Telegram-Bot-Api-Secret-Token": webhook_secret()})
        return r.status_code

    def texts(self) -> list[str]:
        return [c.text for c in self.telegram.calls
                if isinstance(c, (methods.SendMessage, methods.EditMessageText))]

    def answers(self) -> list:
        return self.telegram.of(methods.AnswerCallbackQuery)


def nav(action: str, club_id: int = 0, arg: int = 0, user_id: int = OWNER) -> dict:
    return callback_update(Nav(action=action, club_id=club_id, arg=arg).pack(), user_id=user_id)


def say(text: str, user_id: int = OWNER) -> dict:
    return message_update(text, user_id=user_id)


@pytest.fixture
def platform_on(monkeypatch, telegram):
    settings = get_settings()
    monkeypatch.setattr(settings, "platform_bot_token", SecretStr(PLATFORM_TOKEN))
    monkeypatch.setattr(settings, "platform_owner_ids", str(OWNER))
    platform_dp.fsm.storage.storage.clear()
    yield
    platform_dp.fsm.storage.storage.clear()


@pytest.fixture
def pbot(client, telegram, platform_on) -> PlatformClient:
    return PlatformClient(client, telegram)


@pytest.fixture
def strict(monkeypatch) -> StrictTelegram:
    """Строгий Telegram вместо обычного фейка (после фикстуры telegram)."""
    fake = StrictTelegram()
    registry.use_session(fake)
    monkeypatch.setattr(get_settings(), "public_base_url", "https://example.test")
    yield fake
    registry.use_session(None)


@pytest.fixture
async def lenient_client(session: AsyncSession) -> AsyncIterator[AsyncClient]:
    """Клиент, который получает 500 вместо исключения из приложения."""

    async def override():
        yield session

    app.dependency_overrides[get_session] = override
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()
