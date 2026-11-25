"""Бот платформы: подключение клуба целиком кнопками, без консоли."""

from decimal import Decimal

import pytest
from aiogram import methods
from pydantic import SecretStr
from sqlalchemy import select

from app.config import get_settings
from app.models import (
    Club,
    ClubBot,
    ClubStaff,
    GameConfig,
    Payment,
    PrizeOption,
    StaffRole,
    Subscription,
    SubscriptionStatus,
)
from app.platform_bot.dispatcher import platform_dp
from app.platform_bot.handlers import PICK_SUPERADMIN, Nav
from app.platform_bot.setup import configure_platform_bot, webhook_secret
from app.services.clubs import split_emoji
from tests.botkit import callback_update, message_update, users_shared_update

PLATFORM_TOKEN = "555000111:AAPlatformFakeTokenForTests_abcdefgh"
CLUB_TOKEN = "123456789:AAFakeTokenForTestsOnly_abcdefghijk"
OWNER = 9000
STRANGER = 4242


@pytest.fixture(autouse=True)
def platform(monkeypatch, telegram):
    settings = get_settings()
    monkeypatch.setattr(settings, "platform_bot_token", SecretStr(PLATFORM_TOKEN))
    monkeypatch.setattr(settings, "platform_owner_ids", str(OWNER))
    platform_dp.fsm.storage.storage.clear()  # состояния не переходят между тестами
    yield
    platform_dp.fsm.storage.storage.clear()


class PlatformClient:
    def __init__(self, client, telegram):
        self.client, self.telegram = client, telegram

    async def send(self, update: dict, secret: str | None = None) -> int:
        self.telegram.calls.clear()
        response = await self.client.post(
            "/tg/platform", json=update,
            headers={"X-Telegram-Bot-Api-Secret-Token": secret or webhook_secret()},
        )
        return response.status_code

    def texts(self) -> list[str]:
        return [c.text for c in self.telegram.calls
                if isinstance(c, (methods.SendMessage, methods.EditMessageText))]


@pytest.fixture
def pbot(client, telegram) -> PlatformClient:
    return PlatformClient(client, telegram)


def nav(action: str, club_id: int = 0, arg: int = 0, user_id: int = OWNER) -> dict:
    return callback_update(Nav(action=action, club_id=club_id, arg=arg).pack(), user_id=user_id)


def say(text: str, user_id: int = OWNER) -> dict:
    return message_update(text, user_id=user_id)


# --- Доступ -------------------------------------------------------------------------------


async def test_wrong_secret_rejected(pbot):
    assert await pbot.send(say("/start"), secret="wrong") == 403


async def test_stranger_gets_refusal_and_cannot_act(session, pbot):
    await pbot.send(say("/start", user_id=STRANGER))
    assert "только владельцу платформы" in pbot.texts()[0]

    await pbot.send(nav("new", user_id=STRANGER))
    await pbot.send(say("Чужой клуб", user_id=STRANGER))

    assert await session.scalar(select(Club).where(Club.name == "Чужой клуб")) is None


async def test_owner_sees_menu(pbot):
    await pbot.send(say("/start"))
    assert "Бот платформы" in pbot.texts()[0]


# --- Полное подключение клуба ---------------------------------------------------------


async def test_full_club_onboarding_through_buttons(session, pbot):
    # 1. Новый клуб
    await pbot.send(nav("new"))
    await pbot.send(say("Cyber Arena"))
    club = await session.scalar(select(Club).where(Club.name == "Cyber Arena"))
    assert club is not None
    assert (await session.get(Subscription, club.id)).status == SubscriptionStatus.TRIAL
    assert await session.get(GameConfig, (club.id, "wheel")) is not None

    # 2. Бот клуба по токену; сообщение с токеном удаляется
    await pbot.send(nav("bot", club.id))
    await pbot.send(say(CLUB_TOKEN))
    club_bot = await session.scalar(select(ClubBot).where(ClubBot.club_id == club.id))
    assert club_bot is not None and club_bot.token_encrypted != CLUB_TOKEN
    assert pbot.telegram.of(methods.DeleteMessage)
    assert pbot.telegram.of(methods.SetWebhook)[0].url.endswith(f"/tg/{club_bot.bot_id}")
    assert not any(CLUB_TOKEN in t for t in pbot.texts())

    # 3. Призы: обычный и пустой
    for title, weight, empty in (("🎮 1 час игры", "70", 0), ("😔 Повезёт в следующий раз", "30", 1)):
        await pbot.send(nav("prize_add", club.id))
        await pbot.send(say(title))
        await pbot.send(say(weight))
        await pbot.send(nav("prize_kind", club.id, empty))
    prizes = list(await session.scalars(select(PrizeOption).where(PrizeOption.club_id == club.id)))
    assert [(p.emoji, p.title, p.weight, p.is_empty) for p in prizes] == [
        ("🎮", "1 час игры", 70, False), ("😔", "Повезёт в следующий раз", 30, True)]

    # 4. Суперадмин через выбор пользователя
    await pbot.send(nav("super", club.id))
    await pbot.send(users_shared_update(PICK_SUPERADMIN, 777, user_id=OWNER))
    staff = await session.scalar(select(ClubStaff).where(ClubStaff.club_id == club.id))
    assert staff.telegram_id == 777 and staff.role == StaffRole.OWNER

    # 5. Подписка +30 дней с оплатой
    await pbot.send(nav("sub_add", club.id, 30))
    await pbot.send(say("20 000"))
    payment = await session.scalar(select(Payment).where(Payment.club_id == club.id))
    assert payment.amount == Decimal("20000")
    assert (await session.get(Subscription, club.id)).status == SubscriptionStatus.ACTIVE

    # 6. Правила и лимит
    await pbot.send(nav("rules", club.id))
    await pbot.send(say("Крутить можно раз в неделю."))
    await pbot.send(nav("game_cd", club.id, 60 * 10))
    await session.refresh(club)
    assert club.rules_text == "Крутить можно раз в неделю."
    assert (await session.get(GameConfig, (club.id, "wheel"))).cooldown_seconds == 60

    # 7. QR для кассы — файлом
    await pbot.send(nav("qr", club.id))
    [doc] = pbot.telegram.of(methods.SendDocument)
    assert f"t.me/{club_bot.username}?start=desk" in doc.caption

    # Карточка клуба без предупреждений
    await pbot.send(nav("club", club.id))
    card = pbot.texts()[-1]
    assert "⚠️" not in card and "@test_club_bot" in card


async def test_bad_token_is_explained_and_deleted(session, pbot):
    await pbot.send(nav("new"))
    await pbot.send(say("Клуб"))
    club = await session.scalar(select(Club).where(Club.name == "Клуб"))

    await pbot.send(nav("bot", club.id))
    await pbot.send(say("не токен"))

    assert pbot.telegram.of(methods.DeleteMessage)
    assert "не похоже на токен" in pbot.texts()[-1]
    assert await session.scalar(select(ClubBot).where(ClubBot.club_id == club.id)) is None


async def test_removed_prize_is_deactivated_not_deleted(session, pbot):
    await pbot.send(nav("new"))
    await pbot.send(say("Клуб"))
    club = await session.scalar(select(Club).where(Club.name == "Клуб"))
    await pbot.send(nav("prize_add", club.id))
    await pbot.send(say("Энергетик"))
    await pbot.send(say("10"))
    await pbot.send(nav("prize_kind", club.id, 0))
    prize = await session.scalar(select(PrizeOption).where(PrizeOption.club_id == club.id))
    session.add(PrizeOption(club_id=club.id, game_type="wheel", title="Второй приз", weight=10))  # последний нельзя
    await session.flush()

    await pbot.send(nav("prize_del", club.id, prize.id))

    await session.refresh(prize)
    assert prize.active is False


async def test_invalid_weight_asks_again(session, pbot):
    await pbot.send(nav("new"))
    await pbot.send(say("Клуб"))
    club = await session.scalar(select(Club).where(Club.name == "Клуб"))
    await pbot.send(nav("prize_add", club.id))
    await pbot.send(say("Энергетик"))

    await pbot.send(say("много"))

    assert "целое число" in pbot.texts()[-1]


# --- Настройка бота платформы и мелочи -----------------------------------------------


async def test_configure_platform_bot_sets_webhook_with_secret(telegram):
    assert await configure_platform_bot() is True
    [hook] = telegram.of(methods.SetWebhook)
    assert hook.url == "https://example.test/tg/platform"
    assert hook.secret_token == webhook_secret()


@pytest.mark.parametrize("text,expected", [
    ("🎮 1 час игры", ("🎮", "1 час игры")),
    ("Энергетик", (None, "Энергетик")),
    ("2 часа игры", (None, "2 часа игры")),
])
def test_split_emoji(text, expected):
    assert split_emoji(text) == expected
