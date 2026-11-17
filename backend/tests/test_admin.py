"""Админ-панель в боте: проверка и погашение промокодов, журнал, перебор, изоляция клубов."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from aiogram import methods
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.bots.admin import REDEEM_PREFIX, SCAN_BUTTON
from app.bots.setup import connect_bot
from app.db import get_session
from app.main import app
from app.models import (
    Club,
    ClubBot,
    ClubStaff,
    GameType,
    Guest,
    PrizeCode,
    PrizeOption,
    RedemptionAction,
    RedemptionLog,
    RedemptionResult,
    StaffRole,
)
from app.services.redemption import FAILED_LIMIT, normalize_code
from tests.botkit import GUEST_ID, BotClient, callback_update, message_update, web_app_data_update

TOKEN_A = "123456789:AAFakeTokenForTestsOnly_abcdefghijk"
TOKEN_B = "987654321:AAAnotherFakeTokenForTests_abcdefghij"
ADMIN_A, ADMIN_B = 1001, 2002
CODE = "GG-7K3M9Q"


async def make_club(db: AsyncSession, name: str, token: str, admin_id: int) -> tuple[Club, PrizeOption]:
    club = Club(name=name)
    db.add(club)
    await db.flush()
    option = PrizeOption(club_id=club.id, game_type=GameType.WHEEL, title="Энергетик", emoji="🥤", weight=1)
    db.add_all([option, ClubStaff(club_id=club.id, telegram_id=admin_id, role=StaffRole.ADMIN)])
    await db.flush()
    await connect_bot(db, club, token)
    return club, option


async def give_code(db: AsyncSession, club: Club, option: PrizeOption, code: str = CODE,
                    expires_in: timedelta = timedelta(days=14)) -> PrizeCode:
    guest = await db.scalar(select(Guest).where(Guest.club_id == club.id, Guest.telegram_id == GUEST_ID))
    if guest is None:
        guest = Guest(club_id=club.id, telegram_id=GUEST_ID, first_name="Аня", username="anya",
                      phone="+77010001285", consent_at=datetime.now(UTC))
        db.add(guest)
        await db.flush()
    now = datetime.now(UTC)
    prize = PrizeCode(code=code, club_id=club.id, guest_id=guest.id, prize_option_id=option.id,
                      title=option.title, created_at=now - timedelta(days=1), expires_at=now + expires_in)
    db.add(prize)
    await db.flush()
    return prize


@pytest.fixture
async def club_a(session, telegram) -> tuple[Club, PrizeOption]:
    return await make_club(session, "Клуб А", TOKEN_A, ADMIN_A)


@pytest.fixture
async def bot_a(session, telegram, client, club_a) -> BotClient:
    return BotClient(client, telegram, await session.get(ClubBot, 123456789))


async def log_rows(db: AsyncSession, club_id: int) -> list[tuple]:
    rows = await db.execute(
        select(RedemptionLog.admin_id, RedemptionLog.action, RedemptionLog.result)
        .where(RedemptionLog.club_id == club_id).order_by(RedemptionLog.id)
    )
    return [tuple(r) for r in rows]


# --- Нормализация ввода ---------------------------------------------------------------


@pytest.mark.parametrize("raw", ["GG-7K3M9Q", "gg-7k3m9q", "GG 7K3M9Q", "7K3M9Q", "  gg7k3m9q "])
def test_normalize_code_variants(raw):
    assert normalize_code(raw) == CODE


@pytest.mark.parametrize("raw", ["привет", "GG-7K3M9", "GG-0OIL11", "hello world", ""])
def test_normalize_code_rejects(raw):
    assert normalize_code(raw) is None


# --- Доступ к админ-панели ---------------------------------------------------------------


async def test_admin_menu_has_scanner_button(bot_a):
    [reply] = await bot_a.send(message_update("/admin", user_id=ADMIN_A))

    scan_button = reply.reply_markup.keyboard[0][0]
    assert scan_button.text == SCAN_BUTTON
    assert scan_button.web_app.url.endswith("?bot=123456789&scan=1")


async def test_guest_cannot_open_admin_or_check_codes(session, bot_a, club_a):
    club, option = club_a
    await give_code(session, club, option)

    [menu] = await bot_a.send(message_update("/admin", user_id=GUEST_ID))
    [reply] = await bot_a.send(message_update(CODE, user_id=GUEST_ID))

    assert "Админ-панель" not in menu.text
    assert "действителен" not in reply.text
    assert await log_rows(session, club.id) == []


# --- Проверка и погашение ------------------------------------------------------------


async def test_check_valid_code_shows_details_and_redeem_button(session, bot_a, club_a):
    club, option = club_a
    await give_code(session, club, option)

    [reply] = await bot_a.send(message_update("gg-7k3m9q", user_id=ADMIN_A))

    assert "действителен" in reply.text
    assert "Энергетик" in reply.text and "@anya" in reply.text
    assert "…1285" in reply.text and "+77010001285" not in reply.text
    button = reply.reply_markup.inline_keyboard[0][0]
    assert button.callback_data == REDEEM_PREFIX + CODE
    assert await log_rows(session, club.id) == [(ADMIN_A, RedemptionAction.CHECK, RedemptionResult.VALID)]


async def test_scanned_qr_is_checked(session, bot_a, club_a):
    club, option = club_a
    await give_code(session, club, option)

    [reply] = await bot_a.send(web_app_data_update(CODE, user_id=ADMIN_A))

    assert "действителен" in reply.text


async def test_redeem_marks_code_and_notifies_guest(session, bot_a, club_a):
    club, option = club_a
    prize = await give_code(session, club, option)

    messages = await bot_a.send(callback_update(REDEEM_PREFIX + CODE, user_id=ADMIN_A))

    await session.refresh(prize)
    assert prize.redeemed_at is not None and prize.redeemed_by == ADMIN_A
    [edited] = bot_a.edited()
    assert "погашен" in edited.text
    [to_guest] = messages
    assert to_guest.chat_id == GUEST_ID and "Приз получен" in to_guest.text
    assert (ADMIN_A, RedemptionAction.REDEEM, RedemptionResult.REDEEMED) in await log_rows(session, club.id)


async def test_second_redeem_reports_already_redeemed(session, bot_a, club_a):
    club, option = club_a
    await give_code(session, club, option)
    await bot_a.send(callback_update(REDEEM_PREFIX + CODE, user_id=ADMIN_A))

    messages = await bot_a.send(callback_update(REDEEM_PREFIX + CODE, user_id=ADMIN_A))
    [check] = await bot_a.send(message_update(CODE, user_id=ADMIN_A))

    assert messages == []  # гость второй раз не уведомляется
    assert "уже погашен" in check.text and "вами" in check.text
    assert (ADMIN_A, RedemptionAction.REDEEM, RedemptionResult.ALREADY_REDEEMED) in await log_rows(session, club.id)


async def test_expired_code_cannot_be_redeemed(session, bot_a, club_a):
    club, option = club_a
    prize = await give_code(session, club, option, expires_in=timedelta(seconds=-1))

    [check] = await bot_a.send(message_update(CODE, user_id=ADMIN_A))
    await bot_a.send(callback_update(REDEEM_PREFIX + CODE, user_id=ADMIN_A))

    await session.refresh(prize)
    assert "истёк" in check.text
    assert check.reply_markup is None
    assert prize.redeemed_at is None


async def test_unknown_code_not_found(session, bot_a, club_a):
    [reply] = await bot_a.send(message_update("GG-ZZZZZZ", user_id=ADMIN_A))

    assert "не найден" in reply.text
    assert await log_rows(session, club_a[0].id) == [
        (ADMIN_A, RedemptionAction.CHECK, RedemptionResult.NOT_FOUND)
    ]


# --- Изоляция клубов ------------------------------------------------------------------


async def test_admin_of_club_b_cannot_see_or_redeem_code_of_club_a(session, telegram, client, club_a):
    club, option = club_a
    prize = await give_code(session, club, option)
    club_b, _ = await make_club(session, "Клуб Б", TOKEN_B, ADMIN_B)
    bot_b = BotClient(client, telegram, await session.get(ClubBot, 987654321))

    [check] = await bot_b.send(message_update(CODE, user_id=ADMIN_B))
    await bot_b.send(callback_update(REDEEM_PREFIX + CODE, user_id=ADMIN_B))

    await session.refresh(prize)
    assert "не найден" in check.text and "Клуб А" not in check.text
    assert prize.redeemed_at is None
    assert [r[2] for r in await log_rows(session, club_b.id)] == [
        RedemptionResult.NOT_FOUND, RedemptionResult.NOT_FOUND
    ]
    assert await log_rows(session, club.id) == []


async def test_admin_of_club_a_is_guest_in_club_b(session, telegram, client, club_a):
    """Админ клуба А, написавший боту клуба Б, — обычный гость: админ-панели нет."""
    club, option = club_a
    await make_club(session, "Клуб Б", TOKEN_B, ADMIN_B)
    bot_b = BotClient(client, telegram, await session.get(ClubBot, 987654321))

    [reply] = await bot_b.send(message_update("/admin", user_id=ADMIN_A))

    assert "Админ-панель" not in reply.text


# --- Защита от перебора ---------------------------------------------------------------


async def test_failed_checks_are_rate_limited(session, bot_a, club_a):
    club, option = club_a
    await give_code(session, club, option)
    for i in range(FAILED_LIMIT):
        await bot_a.send(message_update(f"GG-ZZZZ{'23456789AB'[i]}Z", user_id=ADMIN_A))

    [reply] = await bot_a.send(message_update(CODE, user_id=ADMIN_A))

    assert "Слишком много" in reply.text
    assert (await log_rows(session, club.id))[-1][2] == RedemptionResult.RATE_LIMITED


# --- Два админа одновременно ------------------------------------------------------------


async def test_concurrent_redeem_by_two_admins_redeems_once(engine: AsyncEngine, telegram):
    async with AsyncSession(engine, expire_on_commit=False) as db:
        club, option = await make_club(db, "Гонка", TOKEN_A, ADMIN_A)
        db.add(ClubStaff(club_id=club.id, telegram_id=ADMIN_B, role=StaffRole.ADMIN))
        await give_code(db, club, option)
        await db.commit()
        club_bot = await db.get(ClubBot, 123456789)

    async def own_session():
        async with AsyncSession(engine, expire_on_commit=False) as s:
            yield s

    app.dependency_overrides[get_session] = own_session
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            telegram.calls.clear()
            await asyncio.gather(
                c.post(f"/tg/{club_bot.bot_id}", json=callback_update(REDEEM_PREFIX + CODE, user_id=ADMIN_A),
                       headers={"X-Telegram-Bot-Api-Secret-Token": club_bot.webhook_secret}),
                c.post(f"/tg/{club_bot.bot_id}", json=callback_update(REDEEM_PREFIX + CODE, user_id=ADMIN_B),
                       headers={"X-Telegram-Bot-Api-Secret-Token": club_bot.webhook_secret}),
            )
        guest_notices = [m for m in telegram.of(methods.SendMessage) if m.chat_id == GUEST_ID]
        assert len(guest_notices) == 1
        async with AsyncSession(engine) as db:
            results = (await db.scalars(
                select(RedemptionLog.result).where(RedemptionLog.club_id == club.id)
            )).all()
            assert sorted(results) == sorted([RedemptionResult.REDEEMED, RedemptionResult.ALREADY_REDEEMED])
            assert await db.scalar(select(func.count()).select_from(PrizeCode)
                                   .where(PrizeCode.club_id == club.id, PrizeCode.redeemed_at.is_not(None))) == 1
    finally:
        app.dependency_overrides.clear()
        async with AsyncSession(engine) as db:
            await db.execute(delete(Club).where(Club.id == club.id))
            await db.commit()
