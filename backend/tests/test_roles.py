"""Роли в боте клуба: владелец платформы → суперадмин клуба → админ."""

from datetime import UTC, datetime, timedelta

import pytest
from aiogram import methods
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.bots.admin import (
    ADD_ADMIN_BUTTON,
    PICK_ADMIN,
    PICK_SUPERADMIN,
    REDEEM_PREFIX,
    REMOVE_PREFIX,
    STAFF_BUTTON,
    SUPERADMIN_BUTTON,
)
from app.bots.setup import connect_bot
from app.config import get_settings
from app.models import (
    Club,
    ClubBot,
    ClubStaff,
    GameType,
    Guest,
    PrizeCode,
    PrizeOption,
    StaffRole,
)
from tests.botkit import BotClient, callback_update, message_update, users_shared_update

TOKEN_A = "123456789:AAFakeTokenForTestsOnly_abcdefghijk"
TOKEN_B = "987654321:AAAnotherFakeTokenForTests_abcdefghij"
OWNER = 9000          # владелец платформы
SUPER_A = 1001        # суперадмин клуба А
ADMIN_A = 1002
NEWBIE = 777


@pytest.fixture(autouse=True)
def platform_owner(monkeypatch):
    monkeypatch.setattr(get_settings(), "platform_owner_ids", str(OWNER))


async def make_club(db: AsyncSession, name: str, token: str, bot_id: int, client, telegram) -> BotClient:
    club = Club(name=name)
    db.add(club)
    await db.flush()
    await connect_bot(db, club, token)
    return BotClient(client, telegram, await db.get(ClubBot, bot_id))


@pytest.fixture
async def bot_a(session, telegram, client) -> BotClient:
    bot = await make_club(session, "Клуб А", TOKEN_A, 123456789, client, telegram)
    session.add_all([
        ClubStaff(club_id=bot.club_bot.club_id, telegram_id=SUPER_A, role=StaffRole.OWNER, first_name="Супер"),
        ClubStaff(club_id=bot.club_bot.club_id, telegram_id=ADMIN_A, role=StaffRole.ADMIN, first_name="Админ"),
    ])
    await session.flush()
    return bot


async def staff_roles(db: AsyncSession, club_id: int) -> dict[int, StaffRole]:
    rows = await db.execute(
        select(ClubStaff.telegram_id, ClubStaff.role)
        .where(ClubStaff.club_id == club_id)
        .execution_options(populate_existing=True)
    )
    return dict(rows.all())


def buttons(reply) -> list[str]:
    return [b.text for row in reply.reply_markup.keyboard for b in row]


# --- Кнопки по ролям ----------------------------------------------------------------------


async def test_platform_owner_has_full_panel_in_any_club_without_staff_record(session, bot_a):
    [reply] = await bot_a.send(message_update("/admin", user_id=OWNER))

    assert "владелец платформы" in reply.text
    assert {ADD_ADMIN_BUTTON, STAFF_BUTTON, SUPERADMIN_BUTTON} <= set(buttons(reply))
    assert OWNER not in await staff_roles(session, bot_a.club_bot.club_id)


async def test_superadmin_panel_can_add_admins_but_not_superadmin(bot_a):
    [reply] = await bot_a.send(message_update("/admin", user_id=SUPER_A))

    assert "суперадмин клуба" in reply.text
    assert ADD_ADMIN_BUTTON in buttons(reply) and STAFF_BUTTON in buttons(reply)
    assert SUPERADMIN_BUTTON not in buttons(reply)


async def test_admin_panel_has_only_codes(bot_a):
    [reply] = await bot_a.send(message_update("/admin", user_id=ADMIN_A))

    assert not {ADD_ADMIN_BUTTON, STAFF_BUTTON, SUPERADMIN_BUTTON} & set(buttons(reply))


# --- Назначения ------------------------------------------------------------------------------


async def test_owner_assigns_superadmin_previous_becomes_admin(session, bot_a):
    club_id = bot_a.club_bot.club_id

    [reply, greeting] = await bot_a.send(users_shared_update(PICK_SUPERADMIN, NEWBIE, user_id=OWNER))

    roles = await staff_roles(session, club_id)
    assert roles[NEWBIE] == StaffRole.OWNER and roles[SUPER_A] == StaffRole.ADMIN
    assert "суперадмин" in reply.text and "теперь админ" in reply.text
    assert greeting.chat_id == NEWBIE
    [menu] = [c for c in bot_a.telegram.of(methods.SetMyCommands) if c.scope]
    assert menu.scope.chat_id == NEWBIE and any(c.command == "admin" for c in menu.commands)


async def test_superadmin_cannot_assign_superadmin_even_with_forged_request(session, bot_a):
    [reply] = await bot_a.send(users_shared_update(PICK_SUPERADMIN, NEWBIE, user_id=SUPER_A))

    assert "только владелец платформы" in reply.text
    assert NEWBIE not in await staff_roles(session, bot_a.club_bot.club_id)


async def test_superadmin_adds_admin_with_name(session, bot_a):
    await bot_a.send(users_shared_update(PICK_ADMIN, NEWBIE, user_id=SUPER_A))

    staff = await session.scalar(select(ClubStaff).where(ClubStaff.telegram_id == NEWBIE))
    assert staff.role == StaffRole.ADMIN and staff.first_name == "Ваня" and staff.username == "vanya"


async def test_admin_cannot_add_admins(session, bot_a):
    [reply] = await bot_a.send(users_shared_update(PICK_ADMIN, NEWBIE, user_id=ADMIN_A))

    assert "⛔" in reply.text
    assert NEWBIE not in await staff_roles(session, bot_a.club_bot.club_id)


async def test_one_superadmin_per_club_in_database(session, bot_a):
    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            session.add(ClubStaff(club_id=bot_a.club_bot.club_id, telegram_id=NEWBIE, role=StaffRole.OWNER))
            await session.flush()


# --- Список и удаление ---------------------------------------------------------------------


async def test_staff_list_shows_remove_buttons_by_rights(bot_a):
    [as_super] = await bot_a.send(message_update(STAFF_BUTTON, user_id=SUPER_A))
    [as_owner] = await bot_a.send(message_update(STAFF_BUTTON, user_id=OWNER))

    super_targets = [b[0].callback_data for b in as_super.reply_markup.inline_keyboard]
    owner_targets = [b[0].callback_data for b in as_owner.reply_markup.inline_keyboard]
    assert super_targets == [f"{REMOVE_PREFIX}{ADMIN_A}"]       # себя и суперадмина — нельзя
    assert set(owner_targets) == {f"{REMOVE_PREFIX}{SUPER_A}", f"{REMOVE_PREFIX}{ADMIN_A}"}


async def test_superadmin_removes_admin(session, bot_a):
    await bot_a.send(callback_update(f"{REMOVE_PREFIX}{ADMIN_A}", user_id=SUPER_A))

    assert ADMIN_A not in await staff_roles(session, bot_a.club_bot.club_id)
    assert bot_a.telegram.of(methods.DeleteMyCommands)[0].scope.chat_id == ADMIN_A


async def test_superadmin_cannot_remove_himself_or_be_removed_by_admin(session, bot_a):
    await bot_a.send(callback_update(f"{REMOVE_PREFIX}{SUPER_A}", user_id=SUPER_A))
    await bot_a.send(callback_update(f"{REMOVE_PREFIX}{SUPER_A}", user_id=ADMIN_A))

    assert SUPER_A in await staff_roles(session, bot_a.club_bot.club_id)
    answers = bot_a.telegram.of(methods.AnswerCallbackQuery)
    assert answers and answers[-1].show_alert


async def test_owner_removes_superadmin(session, bot_a):
    await bot_a.send(callback_update(f"{REMOVE_PREFIX}{SUPER_A}", user_id=OWNER))

    assert SUPER_A not in await staff_roles(session, bot_a.club_bot.club_id)


# --- Изоляция клубов и права на промокоды ---------------------------------------------


async def test_superadmin_of_club_a_has_no_rights_in_club_b(session, telegram, client, bot_a):
    bot_b = await make_club(session, "Клуб Б", TOKEN_B, 987654321, client, telegram)

    [menu] = await bot_b.send(message_update("/admin", user_id=SUPER_A))
    await bot_b.send(users_shared_update(PICK_ADMIN, NEWBIE, user_id=SUPER_A))

    assert "Админ-панель" not in menu.text
    assert NEWBIE not in await staff_roles(session, bot_b.club_bot.club_id)


async def test_platform_owner_manages_every_club(session, telegram, client, bot_a):
    bot_b = await make_club(session, "Клуб Б", TOKEN_B, 987654321, client, telegram)

    await bot_b.send(users_shared_update(PICK_SUPERADMIN, NEWBIE, user_id=OWNER))

    assert (await staff_roles(session, bot_b.club_bot.club_id))[NEWBIE] == StaffRole.OWNER


async def test_platform_owner_can_redeem_codes(session, bot_a):
    club_id = bot_a.club_bot.club_id
    option = PrizeOption(club_id=club_id, game_type=GameType.WHEEL, title="Энергетик", weight=1)
    guest = Guest(club_id=club_id, telegram_id=555, phone="+77010001285", consent_at=datetime.now(UTC))
    session.add_all([option, guest])
    await session.flush()
    now = datetime.now(UTC)
    prize = PrizeCode(code="GG-7K3M9Q", club_id=club_id, guest_id=guest.id, prize_option_id=option.id,
                      title="Энергетик", created_at=now, expires_at=now + timedelta(days=14))
    session.add(prize)
    await session.flush()

    await bot_a.send(callback_update(REDEEM_PREFIX + "GG-7K3M9Q", user_id=OWNER))

    await session.refresh(prize)
    assert prize.redeemed_by == OWNER
