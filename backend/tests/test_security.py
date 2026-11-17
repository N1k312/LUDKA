"""Секреты не утекают: ни в логи, ни во фронтенд; club_id из запроса игнорируется."""

import io
import logging
import sys

import pytest
from sqlalchemy import select

from app.bots.admin import REDEEM_PREFIX
from app.logging_setup import RedactingFormatter, install_redaction, redact
from app.models import Club, ClubBot, ClubStaff, StaffRole, Subscription, SubscriptionStatus
from app.services import subscription
from tests.botkit import BotClient, callback_update
from tests.test_miniapp import BOT_A, BOT_B, TOKEN_A, TOKEN_B, headers, make_club, make_guest

TOKEN = "111222333:AAFakeTokenForLogRedactionTest0000000"
PHONE = "+77010001285"


# --- Логи ---------------------------------------------------------------------------------


def test_redact_token_and_phone():
    text = f"GET https://api.telegram.org/bot{TOKEN}/sendMessage phone={PHONE} raw=77010001285"
    assert redact(text) == (
        "GET https://api.telegram.org/bot<bot-token>/sendMessage phone=<phone> raw=<phone>"
    )


@pytest.mark.parametrize(
    "safe",
    ["2026-11-17 09:57:04,123", "bot 9106357797", "GG-MZUJPC", "POST /tg/9106357797 HTTP/1.1 200"],
)
def test_redact_keeps_ordinary_text(safe):
    assert redact(safe) == safe


def test_formatter_redacts_traceback():
    try:
        raise RuntimeError(f"connect failed: https://api.telegram.org/bot{TOKEN}/getMe")
    except RuntimeError:
        record = logging.LogRecord("x", logging.ERROR, __file__, 1, "Ошибка для %s", (PHONE,), None)
        record.exc_info = sys.exc_info()
    output = RedactingFormatter(logging.Formatter("%(message)s")).format(record)

    assert TOKEN not in output and PHONE not in output
    assert "<bot-token>" in output and "<phone>" in output


def test_install_redaction_wraps_root_handlers():
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(message)s"))
    root = logging.getLogger()
    root.addHandler(handler)
    try:
        install_redaction()
        install_redaction()  # повторный вызов не оборачивает дважды
        logging.getLogger("aiogram.test").warning("token %s", TOKEN)
    finally:
        root.removeHandler(handler)

    assert stream.getvalue().strip() == "token <bot-token>"
    assert not isinstance(handler.formatter.inner, RedactingFormatter)


# --- Фронтенд ---------------------------------------------------------------------------


async def test_api_responses_have_no_phone_or_token(session, telegram, client):
    club = await make_club(session, "Клуб А", TOKEN_A, [("1 час", 1, False)])
    guest = await make_guest(session, club)
    auth = headers(TOKEN_A, BOT_A)

    bodies = [(await client.post("/api/spin", headers=auth)).text]
    for path in ("/me", "/wheel", "/prizes", "/rules"):
        bodies.append((await client.get(f"/api{path}", headers=auth)).text)

    joined = "\n".join(bodies)
    assert guest.phone not in joined and guest.phone.lstrip("+") not in joined
    assert TOKEN_A not in joined and "token" not in joined.lower()


async def test_club_id_from_request_is_ignored(session, telegram, client):
    club_a = await make_club(session, "Клуб А", TOKEN_A, [("Приз А", 1, False)])
    club_b = await make_club(session, "Клуб Б", TOKEN_B, [("Приз Б", 1, False)])
    await make_guest(session, club_a)
    await make_guest(session, club_b)
    await client.post("/api/spin", headers=headers(TOKEN_B, BOT_B))

    auth_a = {**headers(TOKEN_A, BOT_A), "X-Club-Id": str(club_b.id)}
    me = (await client.get(f"/api/me?club_id={club_b.id}", headers=auth_a)).json()
    prizes = (await client.get(f"/api/prizes?club_id={club_b.id}", headers=auth_a)).json()

    assert me["club"]["name"] == "Клуб А"
    assert prizes["prizes"] == []


# --- Подписка в работе -------------------------------------------------------------------


async def test_canceled_subscription_blocks_spin_but_not_redeem(session, telegram, client):
    club = await make_club(session, "Клуб А", TOKEN_A, [("1 час", 1, False)])
    await make_guest(session, club)
    auth = headers(TOKEN_A, BOT_A)
    code = (await client.post("/api/spin", headers=auth)).json()["code"]["code"]
    session.add(ClubStaff(club_id=club.id, telegram_id=1001, role=StaffRole.ADMIN))
    await subscription.cancel(session, await session.get(Subscription, club.id))
    await session.flush()

    me = (await client.get("/api/me", headers=auth)).json()
    bot = BotClient(client, telegram, await session.get(ClubBot, BOT_A))
    await bot.send(callback_update(REDEEM_PREFIX + code, user_id=1001))

    assert me["available"] is False
    prizes = (await client.get("/api/prizes", headers=auth)).json()["prizes"]
    assert prizes[0]["status"] == "redeemed"
    sub = await session.scalar(select(Subscription).where(Subscription.club_id == club.id))
    assert sub.status == SubscriptionStatus.CANCELED
    assert (await session.get(Club, club.id)) is not None  # данные клуба сохраняются
