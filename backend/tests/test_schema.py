from datetime import UTC, datetime, timedelta

import pytest
from alembic import command
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Club,
    GameType,
    Guest,
    Play,
    PrizeCode,
    PrizeOption,
)
from app.seed import PILOT_PRIZES, seed_pilot_club
from tests.conftest import alembic_config


async def make_club(session: AsyncSession, name: str) -> Club:
    club = Club(name=name)
    session.add(club)
    await session.flush()
    return club


async def make_guest(session: AsyncSession, club: Club, telegram_id: int, phone: str | None = None) -> Guest:
    guest = Guest(club_id=club.id, telegram_id=telegram_id, phone=phone)
    session.add(guest)
    await session.flush()
    return guest


async def make_prize(session: AsyncSession, club: Club) -> PrizeOption:
    prize = PrizeOption(club_id=club.id, game_type=GameType.WHEEL, title="1 час", weight=10)
    session.add(prize)
    await session.flush()
    return prize


def test_migrations_downgrade_and_upgrade(test_db_url: str):
    cfg = alembic_config(test_db_url)
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")


async def test_seed_creates_pilot_once(session: AsyncSession):
    first = await seed_pilot_club(session)
    second = await seed_pilot_club(session)

    assert first.id == second.id
    prizes = (
        await session.scalars(select(PrizeOption).where(PrizeOption.club_id == first.id))
    ).all()
    assert len(prizes) == len(PILOT_PRIZES)
    assert sum(p.weight for p in prizes) == 100
    assert sum(p.is_empty for p in prizes) == 1


async def test_same_person_in_two_clubs_is_two_guests(session: AsyncSession):
    club_a = await make_club(session, "A")
    club_b = await make_club(session, "B")

    guest_a = await make_guest(session, club_a, telegram_id=111, phone="+77010000000")
    guest_b = await make_guest(session, club_b, telegram_id=111, phone="+77010000000")

    assert guest_a.id != guest_b.id


async def test_guest_unique_per_club_by_telegram_id(session: AsyncSession):
    club = await make_club(session, "A")
    await make_guest(session, club, telegram_id=111)

    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            await make_guest(session, club, telegram_id=111)


async def test_guest_unique_per_club_by_phone(session: AsyncSession):
    club = await make_club(session, "A")
    await make_guest(session, club, telegram_id=111, phone="+77010000000")
    # Гости без телефона не конфликтуют между собой.
    await make_guest(session, club, telegram_id=222)
    await make_guest(session, club, telegram_id=333)

    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            await make_guest(session, club, telegram_id=444, phone="+77010000000")


async def test_play_cannot_mix_guest_and_prize_of_different_clubs(session: AsyncSession):
    club_a = await make_club(session, "A")
    club_b = await make_club(session, "B")
    guest_a = await make_guest(session, club_a, telegram_id=111)
    prize_b = await make_prize(session, club_b)

    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            session.add(
                Play(
                    club_id=club_a.id,
                    guest_id=guest_a.id,
                    game_type=GameType.WHEEL,
                    prize_option_id=prize_b.id,
                )
            )
            await session.flush()


async def test_prize_code_unique_across_platform(session: AsyncSession):
    expires = datetime.now(UTC) + timedelta(days=14)
    clubs = [await make_club(session, "A"), await make_club(session, "B")]
    for club in clubs[:1]:
        guest = await make_guest(session, club, telegram_id=111)
        prize = await make_prize(session, club)
        session.add(
            PrizeCode(code="GG-7K3M9Q", club_id=club.id, guest_id=guest.id,
                      prize_option_id=prize.id, title=prize.title, expires_at=expires)
        )
        await session.flush()

    club_b = clubs[1]
    guest_b = await make_guest(session, club_b, telegram_id=111)
    prize_b = await make_prize(session, club_b)
    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            session.add(
                PrizeCode(code="GG-7K3M9Q", club_id=club_b.id, guest_id=guest_b.id,
                          prize_option_id=prize_b.id, title=prize_b.title, expires_at=expires)
            )
            await session.flush()


async def test_negative_weight_rejected(session: AsyncSession):
    club = await make_club(session, "A")

    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            session.add(PrizeOption(club_id=club.id, game_type=GameType.WHEEL, title="x", weight=-1))
            await session.flush()


async def test_deleting_club_removes_its_data(session: AsyncSession):
    club = await seed_pilot_club(session)
    guest = await make_guest(session, club, telegram_id=111)
    prize = (await session.scalars(select(PrizeOption).where(PrizeOption.club_id == club.id))).first()
    session.add(Play(club_id=club.id, guest_id=guest.id, game_type=GameType.WHEEL,
                     prize_option_id=prize.id))
    await session.flush()

    await session.execute(delete(Club).where(Club.id == club.id))
    session.expunge_all()

    for model in (Guest, Play, PrizeOption):
        count = await session.scalar(select(func.count()).select_from(model).where(model.club_id == club.id))
        assert count == 0
