"""Пульт → «🧹 Сброс данных»: сброс регистрации гостя и очистка игровых данных клуба."""

from datetime import UTC, datetime

from sqlalchemy import func, select

from app.models import (
    ClubBot,
    ClubStaff,
    Guest,
    Play,
    PrizeCode,
    PrizeOption,
    RedemptionLog,
    StaffRole,
    Subscription,
)
from app.platform_bot.handlers import PICK_RESET_GUEST
from app.services import branding
from tests.audit.conftest import BOT_A, BOT_B, TOKEN_A, TOKEN_B, api, club_with_bot, nav, say
from tests.botkit import BotClient, message_update, users_shared_update

GUEST, ADMIN, STRANGER = 555, 1001, 4242


async def played_club(session, client, name, token, bot_id):
    """Клуб с гостем, одной игрой, промокодом и записью в журнале."""
    club, club_bot = await club_with_bot(session, name, token)
    branding.set_palette(club, "neon")
    session.add_all([Guest(club_id=club.id, telegram_id=GUEST, phone=f"+7701000{bot_id % 10000:04d}",
                           consent_at=datetime.now(UTC)),
                     ClubStaff(club_id=club.id, telegram_id=ADMIN, role=StaffRole.ADMIN)])
    await session.flush()
    await client.post("/api/spin", headers=api(token, bot_id, GUEST))
    session.add(RedemptionLog(club_id=club.id, code="GG-ZZZZZZ", admin_id=ADMIN, action="check", result="not_found"))
    await session.flush()
    return club, club_bot


async def count(session, model, club_id) -> int:
    return await session.scalar(select(func.count()).select_from(model).where(model.club_id == club_id))


async def test_wipe_requires_exact_club_name(session, telegram, client, pbot):
    club, _ = await played_club(session, client, "Arena", TOKEN_A, BOT_A)

    await pbot.send(nav("wipe", club.id))
    confirmation = pbot.texts()[-1]
    await pbot.send(say("Arena2"))
    refusal = pbot.texts()[0]
    await pbot.send(say("Arena"))  # подтверждение уже сброшено — это просто текст

    assert "активных промокодов" in confirmation and "нельзя отменить" in confirmation
    assert "ничего не удалено" in refusal
    assert await count(session, Guest, club.id) == 1 and await count(session, PrizeCode, club.id) == 1


async def test_wipe_deletes_only_game_data_of_this_club(session, telegram, client, pbot):
    club_a, bot_a = await played_club(session, client, "Arena", TOKEN_A, BOT_A)
    club_b, _ = await played_club(session, client, "Другой", TOKEN_B, BOT_B)

    await pbot.send(nav("wipe", club_a.id))
    await pbot.send(say("  arena "))  # регистр и пробелы не важны

    for model in (Guest, Play, PrizeCode, RedemptionLog):
        assert await count(session, model, club_a.id) == 0, model.__name__
        assert await count(session, model, club_b.id) == 1, f"задет другой клуб: {model.__name__}"
    assert await session.get(ClubBot, BOT_A) is not None
    assert await count(session, PrizeOption, club_a.id) == 1
    assert await count(session, ClubStaff, club_a.id) == 1
    assert await session.get(Subscription, club_a.id) is not None
    await session.refresh(club_a)
    assert branding.resolved(club_a)["palette"] == "neon"
    assert "очищены" in pbot.texts()[0]

    # Клуб продолжает работать: гость начинает с чистого листа.
    me = (await client.get("/api/me", headers=api(TOKEN_A, BOT_A, GUEST))).json()
    [hello] = await BotClient(client, telegram, bot_a).send(message_update("/start", user_id=GUEST))
    assert me["guest"]["registered"] is False and "Привет" in hello.text


async def test_reset_guest_registration_keeps_history(session, telegram, client, pbot):
    club, _ = await played_club(session, client, "Arena", TOKEN_A, BOT_A)

    await pbot.send(nav("reset_guest", club.id))
    await pbot.send(users_shared_update(PICK_RESET_GUEST, GUEST, user_id=9000))

    guest = await session.scalar(select(Guest).where(Guest.club_id == club.id).execution_options(populate_existing=True))
    me = (await client.get("/api/me", headers=api(TOKEN_A, BOT_A, GUEST))).json()
    assert guest.phone is None and guest.consent_at is None
    assert await count(session, Play, club.id) == 1 and await count(session, PrizeCode, club.id) == 1
    assert me["guest"]["registered"] is False
    assert "сброшена" in pbot.texts()[0]


async def test_reset_unknown_guest(session, telegram, client, pbot):
    club, _ = await club_with_bot(session, "Arena", TOKEN_A)
    await pbot.send(nav("reset_guest", club.id))
    await pbot.send(users_shared_update(PICK_RESET_GUEST, 777, user_id=9000))
    assert "ещё не гость" in pbot.texts()[0]


async def test_stranger_cannot_wipe_or_reset(session, telegram, client, pbot):
    club, _ = await played_club(session, client, "Arena", TOKEN_A, BOT_A)

    await pbot.send(nav("wipe", club.id, user_id=STRANGER))
    await pbot.send(say("Arena", user_id=STRANGER))
    await pbot.send(nav("reset_guest", club.id, user_id=STRANGER))
    await pbot.send(users_shared_update(PICK_RESET_GUEST, GUEST, user_id=STRANGER))

    guest = await session.scalar(select(Guest).where(Guest.club_id == club.id).execution_options(populate_existing=True))
    assert guest.phone is not None and await count(session, PrizeCode, club.id) == 1
    assert all(a.show_alert for a in pbot.answers())
