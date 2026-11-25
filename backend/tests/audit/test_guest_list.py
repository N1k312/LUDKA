"""Админ-панель → «🙋 Гости»: сводка, список, поиск, карточка гостя, изоляция и права."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import update

from app.bots.admin import GUEST_CARD, GUEST_FIND, GUESTS_BUTTON, GUESTS_PAGE, REDEEM_PREFIX
from app.models import ClubStaff, Guest, Play, StaffRole
from app.services import guest_list
from tests.audit.conftest import BOT_A, TOKEN_A, TOKEN_B, api, club_with_bot
from tests.botkit import BotClient, callback_update, message_update

ADMIN, GUEST = 1001, 555


async def setup(session, telegram, client, extra_guests: int = 0):
    club, club_bot = await club_with_bot(session, "Arena", TOKEN_A)
    now = datetime.now(UTC)
    session.add_all([
        ClubStaff(club_id=club.id, telegram_id=ADMIN, role=StaffRole.ADMIN),
        Guest(club_id=club.id, telegram_id=GUEST, first_name="Аня", username="anya",
              phone="+77010001285", consent_at=now),
        Guest(club_id=club.id, telegram_id=556, first_name="Боря", phone="+77019990000"),  # без согласия
        Guest(club_id=club.id, telegram_id=557, first_name="ANDREW"),                       # без номера
    ])
    session.add_all(Guest(club_id=club.id, telegram_id=10_000 + i, first_name=f"Гость {i}") for i in range(extra_guests))
    await session.flush()
    code = (await client.post("/api/spin", headers=api(TOKEN_A, BOT_A, GUEST))).json()["code"]["code"]
    return club, BotClient(client, telegram, club_bot), code


def buttons(message) -> list:
    return [b for row in message.reply_markup.inline_keyboard for b in row]


async def test_list_shows_summary_statuses_and_counts(session, telegram, client):
    _, bot, _ = await setup(session, telegram, client)

    [reply] = await bot.send(message_update(GUESTS_BUTTON, user_id=ADMIN))

    assert "Всего: 3 · регистрация завершена: 1 · с активными призами: 1" in reply.text
    labels = [b.text for b in buttons(reply)]
    assert labels[0] == "✅ Аня · 🎮1 · 🎁1"  # недавно игравший — первым
    assert "📜 Боря · 🎮0" in labels and "📱 ANDREW · 🎮0" in labels
    assert labels[-1] == "🔎 Найти гостя"


async def test_guest_card_details_and_masked_phone(session, telegram, client):
    _, bot, code = await setup(session, telegram, client)
    [reply] = await bot.send(message_update(GUESTS_BUTTON, user_id=ADMIN))
    anya = buttons(reply)[0].callback_data

    await bot.send(callback_update(anya, user_id=ADMIN))
    [card] = bot.edited()

    for fragment in ("<b>Аня (@anya)</b>", "✅ регистрация завершена", "Телефон: …1285", f"<code>{GUEST}</code>",
                     "Игр: 1", "Призы: активных 1 · получено 0 · истекло 0", code):
        assert fragment in card.text, fragment
    assert "77010001285" not in card.text and "7010001" not in card.text
    assert buttons(card)[0].callback_data == f"{GUESTS_PAGE}0"


async def test_card_after_redemption_and_next_spin(session, telegram, client):
    club, bot, code = await setup(session, telegram, client)
    await bot.send(callback_update(REDEEM_PREFIX + code, user_id=ADMIN))
    guest_id = (await guest_list.search(session, club, "Аня"))[0][0].guest.id

    await bot.send(callback_update(f"{GUEST_CARD}{guest_id}:0", user_id=ADMIN))
    blocked = bot.edited()[-1].text
    await session.execute(update(Play).values(played_at=Play.played_at - timedelta(days=8)))
    await bot.send(callback_update(f"{GUEST_CARD}{guest_id}:0", user_id=ADMIN))
    available = bot.edited()[-1].text

    assert "получено 1" in blocked and "Можно играть: Колесо — сейчас" not in blocked
    assert "Можно играть: Колесо — сейчас" in available


async def test_pagination(session, telegram, client):
    _, bot, _ = await setup(session, telegram, client, extra_guests=10)  # 13 гостей → 2 страницы

    [first] = await bot.send(message_update(GUESTS_BUTTON, user_id=ADMIN))
    await bot.send(callback_update(f"{GUESTS_PAGE}1", user_id=ADMIN))
    [second] = bot.edited()

    nav_first = [b.text for b in buttons(first) if b.text in ("◀️", "▶️", "1/2", "2/2")]
    guests_on_second = [b for b in buttons(second) if b.callback_data.startswith(GUEST_CARD)]
    assert nav_first == ["1/2", "▶️"]
    assert len(guests_on_second) == 13 - guest_list.PAGE_SIZE


@pytest.mark.parametrize("query,expected", [
    ("аня", ["Аня"]), ("@ANYA", ["Аня"]), ("1285", ["Аня"]), ("+7 701 999 0000", ["Боря"]),
    ("andrew", ["ANDREW"]), ("ANDREW", ["ANDREW"]), ("%", []), ("12", []), ("нет такого", []),
])
async def test_search(session, telegram, client, query, expected):
    _, bot, _ = await setup(session, telegram, client)
    await bot.send(callback_update(GUEST_FIND, user_id=ADMIN))

    [reply] = await bot.send(message_update(query, user_id=ADMIN))

    found = [b.text.split(" ", 1)[1].split(" · ")[0] for b in buttons(reply)] if reply.reply_markup else []
    assert found == expected
    assert "не найден" not in reply.text  # «ANDREW» не принят за промокод


async def test_card_of_other_club_guest_not_found(session, telegram, client):
    _, bot, _ = await setup(session, telegram, client)
    other, _ = await club_with_bot(session, "Другой", TOKEN_B)
    stranger = Guest(club_id=other.id, telegram_id=999, first_name="Чужой", phone="+77015550000")
    session.add(stranger)
    await session.flush()

    await bot.send(callback_update(f"{GUEST_CARD}{stranger.id}:0", user_id=ADMIN))

    assert bot.edited() == []
    [answer] = [c for c in bot.telegram.calls if type(c).__name__ == "AnswerCallbackQuery"]
    assert answer.show_alert and "не найден" in answer.text


async def test_guest_cannot_open_guest_list(session, telegram, client):
    club, bot, _ = await setup(session, telegram, client)
    guest_id = (await guest_list.search(session, club, "Боря"))[0][0].guest.id

    [reply] = await bot.send(message_update(GUESTS_BUTTON, user_id=GUEST))
    await bot.send(callback_update(f"{GUEST_CARD}{guest_id}:0", user_id=GUEST))

    assert "Всего:" not in reply.text
    assert bot.edited() == []
