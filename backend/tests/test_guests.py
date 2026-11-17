"""Регистрация гостя через бота: /start → свой номер → согласие → «Играть»."""

from itertools import count
from pathlib import Path

import pytest
from aiogram import methods
from aiogram.types import ReplyKeyboardMarkup, ReplyKeyboardRemove
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bots.handlers import CONSENT_CALLBACK
from app.bots.setup import connect_bot
from app.models import Club, ClubBot, Guest
from app.services.guests import normalize_phone
from app.services.qr import save_qr, start_link
from tests.telegram_fake import FakeTelegram

TOKEN_A = "123456789:AAFakeTokenForTestsOnly_abcdefghijk"
TOKEN_B = "987654321:AAAnotherFakeTokenForTests_abcdefghij"
GUEST_ID = 555
_update_ids = count(1)


def message_update(text: str | None = None, *, user_id: int = GUEST_ID, **extra) -> dict:
    message = {
        "message_id": next(_update_ids),
        "date": 1_700_000_000,
        "chat": {"id": user_id, "type": "private"},
        "from": {"id": user_id, "is_bot": False, "first_name": "Гость", "username": "guest"},
        **extra,
    }
    if text is not None:
        message["text"] = text
        if text.startswith("/"):
            command = text.split()[0]
            message["entities"] = [{"type": "bot_command", "offset": 0, "length": len(command)}]
    return {"update_id": next(_update_ids), "message": message}


def contact_update(phone: str, contact_user_id: int | None, user_id: int = GUEST_ID) -> dict:
    contact = {"phone_number": phone, "first_name": "Гость"}
    if contact_user_id is not None:
        contact["user_id"] = contact_user_id
    return message_update(user_id=user_id, contact=contact)


def consent_update(user_id: int = GUEST_ID) -> dict:
    return {
        "update_id": next(_update_ids),
        "callback_query": {
            "id": str(next(_update_ids)),
            "from": {"id": user_id, "is_bot": False, "first_name": "Гость"},
            "chat_instance": "1",
            "data": CONSENT_CALLBACK,
            "message": {
                "message_id": 99,
                "date": 1_700_000_000,
                "chat": {"id": user_id, "type": "private"},
                "from": {"id": 123456789, "is_bot": True, "first_name": "Test Club"},
                "text": "Правила",
            },
        },
    }


class BotClient:
    """Отправляет обновления в вебхук бота клуба и возвращает ответы бота."""

    def __init__(self, client, telegram: FakeTelegram, club_bot: ClubBot):
        self.client, self.telegram, self.club_bot = client, telegram, club_bot

    async def send(self, update: dict) -> list:
        self.telegram.calls.clear()
        response = await self.client.post(
            f"/tg/{self.club_bot.bot_id}",
            json=update,
            headers={"X-Telegram-Bot-Api-Secret-Token": self.club_bot.webhook_secret},
        )
        assert response.status_code == 200
        return self.telegram.of(methods.SendMessage)


async def make_club(db: AsyncSession, name: str) -> Club:
    club = Club(name=name, rules_text="Крутить можно раз в неделю.")
    db.add(club)
    await db.flush()
    return club


@pytest.fixture
async def bot_a(session, telegram, client) -> BotClient:
    club_bot = await connect_bot(session, await make_club(session, "Клуб А"), TOKEN_A)
    return BotClient(client, telegram, club_bot)


@pytest.fixture
async def bot_b(session, telegram, client) -> BotClient:
    club_bot = await connect_bot(session, await make_club(session, "Клуб Б"), TOKEN_B)
    return BotClient(client, telegram, club_bot)


async def get_guest(db: AsyncSession, club_id: int, telegram_id: int = GUEST_ID) -> Guest | None:
    return await db.scalar(
        select(Guest)
        .where(Guest.club_id == club_id, Guest.telegram_id == telegram_id)
        .execution_options(populate_existing=True)
    )


async def register(bot: BotClient, phone: str = "+7 701 000 00 00", user_id: int = GUEST_ID):
    await bot.send(message_update("/start desk", user_id=user_id))
    await bot.send(contact_update(phone, contact_user_id=user_id, user_id=user_id))
    await bot.send(consent_update(user_id=user_id))


# --- Нормализация номера -------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [("77010000000", "+77010000000"), ("+7 (701) 000-00-00", "+77010000000")],
)
def test_normalize_phone(raw, expected):
    assert normalize_phone(raw) == expected


def test_normalize_phone_rejects_garbage():
    with pytest.raises(ValueError):
        normalize_phone("12")


# --- Сценарий регистрации ---------------------------------------------------------


async def test_start_creates_guest_and_asks_for_phone(session, bot_a):
    [reply] = await bot_a.send(message_update("/start desk"))

    guest = await get_guest(session, bot_a.club_bot.club_id)
    assert guest is not None and guest.username == "guest"
    assert guest.phone is None and guest.consent_at is None
    assert "Клуб А" in reply.text
    assert isinstance(reply.reply_markup, ReplyKeyboardMarkup)
    assert reply.reply_markup.keyboard[0][0].request_contact is True


async def test_repeated_start_does_not_duplicate_guest(session, bot_a):
    await bot_a.send(message_update("/start"))
    await bot_a.send(message_update("/start"))

    guests = (await session.scalars(select(Guest).where(Guest.club_id == bot_a.club_bot.club_id))).all()
    assert len(guests) == 1


async def test_own_contact_saves_phone_and_asks_consent(session, bot_a):
    await bot_a.send(message_update("/start"))

    saved, consent = await bot_a.send(contact_update("77010000000", contact_user_id=GUEST_ID))

    guest = await get_guest(session, bot_a.club_bot.club_id)
    assert guest.phone == "+77010000000"
    assert isinstance(saved.reply_markup, ReplyKeyboardRemove)
    assert "Принимаю" in consent.text and "Крутить можно раз в неделю." in consent.text
    assert consent.reply_markup.inline_keyboard[0][0].callback_data == CONSENT_CALLBACK


@pytest.mark.parametrize("contact_user_id", [777, None], ids=["чужой контакт", "контакт без Telegram"])
async def test_foreign_contact_rejected(session, bot_a, contact_user_id):
    await bot_a.send(message_update("/start"))

    [reply] = await bot_a.send(contact_update("77019999999", contact_user_id=contact_user_id))

    guest = await get_guest(session, bot_a.club_bot.club_id)
    assert guest.phone is None
    assert "собственный номер" in reply.text


async def test_phone_of_other_guest_in_same_club_rejected(session, bot_a):
    await register(bot_a, phone="77010000000", user_id=GUEST_ID)
    await bot_a.send(message_update("/start", user_id=888))

    [reply] = await bot_a.send(contact_update("77010000000", contact_user_id=888, user_id=888))

    other = await get_guest(session, bot_a.club_bot.club_id, telegram_id=888)
    assert other.phone is None
    assert "уже привязан" in reply.text


async def test_consent_completes_registration(session, bot_a):
    await bot_a.send(message_update("/start"))
    await bot_a.send(contact_update("77010000000", contact_user_id=GUEST_ID))

    [welcome] = await bot_a.send(consent_update())

    guest = await get_guest(session, bot_a.club_bot.club_id)
    assert guest.consent_at is not None
    assert welcome.reply_markup.inline_keyboard[0][0].web_app is not None
    assert bot_a.telegram.of(methods.AnswerCallbackQuery)


async def test_consent_before_phone_is_ignored(session, bot_a):
    await bot_a.send(message_update("/start"))

    [reply] = await bot_a.send(consent_update())

    guest = await get_guest(session, bot_a.club_bot.club_id)
    assert guest.consent_at is None
    assert reply.reply_markup.keyboard[0][0].request_contact is True


async def test_registered_guest_gets_play_button_on_start(session, bot_a):
    await register(bot_a)

    [reply] = await bot_a.send(message_update("/start"))

    assert reply.reply_markup.inline_keyboard[0][0].web_app is not None


async def test_rules_command(bot_a):
    [reply] = await bot_a.send(message_update("/rules"))

    assert "Крутить можно раз в неделю." in reply.text


async def test_same_person_registers_separately_in_two_clubs(session, bot_a, bot_b):
    """Регистрация в клубе А не делает гостя зарегистрированным в клубе Б."""
    await register(bot_a, phone="77010000000")

    [reply] = await bot_b.send(message_update("/start"))

    guest_a = await get_guest(session, bot_a.club_bot.club_id)
    guest_b = await get_guest(session, bot_b.club_bot.club_id)
    assert guest_a.id != guest_b.id
    assert guest_a.consent_at is not None
    assert guest_b.phone is None and guest_b.consent_at is None
    assert "Клуб Б" in reply.text and "Клуб А" not in reply.text
    assert reply.reply_markup.keyboard[0][0].request_contact is True


# --- QR-код ------------------------------------------------------------------------


def test_start_link():
    assert start_link("club_bot") == "https://t.me/club_bot?start=desk"
    assert start_link("club_bot", startapp=True) == "https://t.me/club_bot?startapp=desk"


def test_save_qr_png(tmp_path: Path):
    path = save_qr(start_link("club_bot"), tmp_path / "qr.png")

    assert path.read_bytes().startswith(b"\x89PNG")
