"""Бот клуба → «🎁 Призы и шансы»: редактор, права, изоляция клубов, защита колеса, пульт."""

from datetime import UTC, datetime

import pytest
from aiogram import methods
from sqlalchemy import select

from app.bots.admin import ADD_ADMIN_BUTTON, PRIZES_BUTTON, TODAY_BUTTON
from app.bots.dispatcher import dp
from app.bots.prize_editor import ADD, CARD, EDIT_TITLE, EDIT_TTL, EDIT_WEIGHT, KIND, OFF_ASK, OFF_YES
from app.models import ClubStaff, Guest, PrizeCode, PrizeOption, StaffRole
from app.services import clubs, prizes
from tests.audit.conftest import BOT_A, OWNER, TOKEN_A, TOKEN_B, api, club_with_bot, nav, say
from tests.botkit import BotClient, callback_update, message_update

SUPER, ADMIN, GUEST = 2001, 2002, 555
PRIZES = (("Час игры", 30, False), ("Энергетик", 20, False), ("Мимо", 50, True))


@pytest.fixture(autouse=True)
def clean_fsm():
    dp.fsm.storage.storage.clear()
    yield
    dp.fsm.storage.storage.clear()


async def setup(session, telegram, client, prize_rows=PRIZES):
    club, club_bot = await club_with_bot(session, "Arena", TOKEN_A, prizes=prize_rows)
    session.add_all([ClubStaff(club_id=club.id, telegram_id=SUPER, role=StaffRole.OWNER),
                     ClubStaff(club_id=club.id, telegram_id=ADMIN, role=StaffRole.ADMIN)])
    await session.flush()
    return club, BotClient(client, telegram, club_bot), await clubs.active_prizes(session, club)


def press(data: str, user_id: int = SUPER) -> dict:
    return callback_update(data, user_id=user_id)


def text(data: str, user_id: int = SUPER) -> dict:
    return message_update(data, user_id=user_id)


async def test_list_shows_real_percentages(session, telegram, client):
    _, bot, _ = await setup(session, telegram, client)

    [reply] = await bot.send(text(PRIZES_BUTTON))

    assert "Час игры — 30%" in reply.text and "Мимо (пустой) — 50%" in reply.text


async def test_superadmin_changes_chance_title_and_ttl(session, telegram, client):
    _, bot, rows = await setup(session, telegram, client)
    prize = rows[0]

    await bot.send(press(f"{EDIT_WEIGHT}{prize.id}"))
    await bot.send(text("40"))
    await bot.send(press(f"{EDIT_TITLE}{prize.id}"))
    await bot.send(text("🎮 ANDREW"))  # похоже на промокод, но это название
    await bot.send(press(f"{EDIT_TTL}{prize.id}"))
    await bot.send(text("30"))

    await session.refresh(prize)
    assert (prize.weight, prize.title, prize.emoji, prize.ttl_days) == (40, "ANDREW", "🎮", 30)


@pytest.mark.parametrize("value", ["0", "-5", "много", "100001", "1.5", "٣"])
async def test_bad_chance_is_refused_and_input_continues(session, telegram, client, value):
    _, bot, rows = await setup(session, telegram, client)
    await bot.send(press(f"{EDIT_WEIGHT}{rows[0].id}"))

    [reply] = await bot.send(text(value))
    await bot.send(text("25"))

    assert "⛔" in reply.text
    await session.refresh(rows[0])
    assert rows[0].weight == 25


async def test_add_prize_with_ttl(session, telegram, client):
    club, bot, _ = await setup(session, telegram, client)

    await bot.send(press(ADD))
    await bot.send(text("🥤 Кола"))
    await bot.send(text("10"))
    await bot.send(press(f"{KIND}0"))
    await bot.send(text("7"))

    added = await session.scalar(select(PrizeOption).where(PrizeOption.club_id == club.id,
                                                           PrizeOption.title == "Кола"))
    assert (added.weight, added.ttl_days, added.is_empty, added.emoji) == (10, 7, False, "🥤")


async def test_sector_limit(session, telegram, client):
    club, bot, _ = await setup(session, telegram, client)
    for i in range(prizes.MAX_SECTORS - len(PRIZES)):
        await clubs.add_prize(session, club, f"П{i}", 1)

    await bot.send(press(ADD))

    [answer] = telegram.of(methods.AnswerCallbackQuery)
    assert answer.show_alert and "максимум" in answer.text


async def test_menu_button_leaves_input(session, telegram, client):
    _, bot, rows = await setup(session, telegram, client)
    await bot.send(press(f"{EDIT_TITLE}{rows[0].id}"))

    [reply] = await bot.send(text(TODAY_BUTTON))
    await bot.send(text("Не название"))

    assert "Сегодня" in reply.text
    await session.refresh(rows[0])
    assert rows[0].title == "Час игры"


async def test_cancel_leaves_input(session, telegram, client):
    _, bot, rows = await setup(session, telegram, client)
    await bot.send(press(f"{EDIT_WEIGHT}{rows[0].id}"))

    [reply] = await bot.send(text("/cancel"))
    await bot.send(text("77"))

    assert "Отменено" in reply.text
    await session.refresh(rows[0])
    assert rows[0].weight == 30


# --- Защита колеса --------------------------------------------------------------------------


async def test_last_sector_cannot_be_disabled(session, telegram, client):
    _, bot, [only] = await setup(session, telegram, client, (("Единственный", 1, False),))

    await bot.send(press(f"{OFF_YES}{only.id}"))

    await session.refresh(only)
    assert only.active is True
    assert "последний" in telegram.of(methods.AnswerCallbackQuery)[0].text


async def test_last_real_prize_cannot_be_disabled(session, telegram, client):
    _, bot, rows = await setup(session, telegram, client, (("Приз", 1, False), ("Мимо", 1, True)))

    await bot.send(press(f"{OFF_YES}{rows[0].id}"))

    await session.refresh(rows[0])
    assert rows[0].active is True


async def test_disable_keeps_issued_code_and_row(session, telegram, client):
    club, bot, rows = await setup(session, telegram, client, (("Приз", 1, False), ("Другой", 0, False),
                                                              ("Запас", 1, False)))
    session.add(Guest(club_id=club.id, telegram_id=GUEST, phone="+77010000000", consent_at=datetime.now(UTC)))
    await session.flush()
    spin = (await client.post("/api/spin", headers=api(TOKEN_A, BOT_A, GUEST))).json()
    code = await session.scalar(select(PrizeCode).where(PrizeCode.code == spin["code"]["code"]))
    won = await session.get(PrizeOption, code.prize_option_id)
    title = code.title

    await bot.send(press(f"{EDIT_TITLE}{won.id}"))
    await bot.send(text("Новое имя"))
    await bot.send(press(f"{OFF_YES}{won.id}"))

    await session.refresh(code)
    await session.refresh(won)
    assert code.title == title and won.active is False


# --- Права и изоляция ------------------------------------------------------------------------


@pytest.mark.parametrize("prefix", [CARD, EDIT_TITLE, EDIT_WEIGHT, EDIT_TTL, OFF_ASK, OFF_YES])
@pytest.mark.parametrize("user_id", [ADMIN, GUEST])
async def test_admin_and_guest_cannot_edit(session, telegram, client, prefix, user_id):
    _, bot, rows = await setup(session, telegram, client)

    await bot.send(press(f"{prefix}{rows[0].id}", user_id=user_id))
    await bot.send(text("99", user_id=user_id))

    await session.refresh(rows[0])
    assert rows[0].active and rows[0].weight == 30 and rows[0].title == "Час игры"


async def test_admin_keyboard_has_no_prizes_button(session, telegram, client):
    _, bot, _ = await setup(session, telegram, client)

    [admin_reply] = await bot.send(text("/admin", user_id=ADMIN))
    [super_reply] = await bot.send(text("/admin", user_id=SUPER))

    labels = lambda m: [b.text for row in m.reply_markup.keyboard for b in row]  # noqa: E731
    assert PRIZES_BUTTON not in labels(admin_reply)
    assert PRIZES_BUTTON in labels(super_reply) and ADD_ADMIN_BUTTON in labels(super_reply)


async def test_demoted_superadmin_cannot_finish_input(session, telegram, client):
    club, bot, rows = await setup(session, telegram, client)
    await bot.send(press(f"{EDIT_WEIGHT}{rows[0].id}"))
    staff = await session.scalar(select(ClubStaff).where(ClubStaff.club_id == club.id, ClubStaff.telegram_id == SUPER))
    staff.role = StaffRole.ADMIN
    await session.flush()

    await bot.send(text("99"))

    await session.refresh(rows[0])
    assert rows[0].weight == 30


@pytest.mark.parametrize("prefix", [CARD, EDIT_TITLE, EDIT_WEIGHT, EDIT_TTL, OFF_ASK, OFF_YES])
async def test_other_club_prize_is_not_found(session, telegram, client, prefix):
    _, bot, _ = await setup(session, telegram, client)
    _, _ = await club_with_bot(session, "Чужой", TOKEN_B, prizes=(("Чужой приз", 5, False), ("Ещё", 5, False)))
    foreign = await session.scalar(select(PrizeOption).where(PrizeOption.title == "Чужой приз"))

    await bot.send(press(f"{prefix}{foreign.id}"))
    await bot.send(text("99"))

    await session.refresh(foreign)
    assert foreign.active and foreign.weight == 5 and foreign.title == "Чужой приз"
    assert telegram.of(methods.AnswerCallbackQuery) or telegram.calls


@pytest.mark.parametrize("raw", ["abc", "-1", "99999999999999999999", ""])
async def test_garbage_callback_is_safe(session, telegram, client, raw):
    _, bot, _ = await setup(session, telegram, client)

    await bot.send(press(f"{CARD}{raw}"))  # BotClient проверяет ответ 200

    assert telegram.of(methods.AnswerCallbackQuery)[0].show_alert


async def test_title_is_html_escaped(session, telegram, client):
    _, bot, rows = await setup(session, telegram, client)
    await bot.send(press(f"{EDIT_TITLE}{rows[0].id}"))
    await bot.send(text("<b>x</b>"))

    [reply] = await bot.send(text(PRIZES_BUTTON))

    assert "&lt;b&gt;x&lt;/b&gt;" in reply.text


# --- Уведомление и пульт ---------------------------------------------------------------------


async def test_owner_is_notified_about_club_change(session, telegram, client, platform_on):
    _, bot, rows = await setup(session, telegram, client)
    await bot.send(press(f"{EDIT_WEIGHT}{rows[0].id}"))

    sent = await bot.send(text("90"))

    note = [m for m in sent if m.chat_id == OWNER]
    assert len(note) == 1 and "Arena" in note[0].text and "30 → 90" in note[0].text


async def test_platform_owner_edit_does_not_notify_himself(session, telegram, client, platform_on):
    _, bot, rows = await setup(session, telegram, client)
    await bot.send(press(f"{EDIT_WEIGHT}{rows[0].id}", user_id=OWNER))

    sent = await bot.send(text("90", user_id=OWNER))

    assert len(sent) == 1 and "Сохранено" in sent[0].text


async def test_pult_edits_chance_and_keeps_club_filter(session, pbot):
    club, _ = await club_with_bot(session, "А", TOKEN_A, prizes=PRIZES)
    other, _ = await club_with_bot(session, "Б", TOKEN_B, prizes=(("Чужой", 5, False),))
    prize = (await clubs.active_prizes(session, club))[0]
    foreign = (await clubs.active_prizes(session, other))[0]

    await pbot.send(nav("prize_w", club.id, prize.id))
    await pbot.send(say("45"))
    await pbot.send(nav("prize_w", club.id, foreign.id))

    await session.refresh(prize)
    await session.refresh(foreign)
    assert prize.weight == 45 and foreign.weight == 5
    assert pbot.answers()[0].text == "Приз не найден"


async def test_pult_cannot_disable_last_sector(session, pbot):
    club, _ = await club_with_bot(session, "А", TOKEN_A)
    only = (await clubs.active_prizes(session, club))[0]

    await pbot.send(nav("prize_del", club.id, only.id))

    await session.refresh(only)
    assert only.active is True
