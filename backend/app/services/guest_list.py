"""Гости клуба для админ-панели: сводка, список со статусами, поиск и карточка гостя.

Всё строго в пределах одного клуба. Телефон наружу отдаётся только последними 4 цифрами.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Club, Guest, Play, PrizeCode
from app.services.codes import CodeStatus, code_status
from app.services.guests import is_registered
from app.services.wheel import next_spin_at, wheel_config

PAGE_SIZE = 8
SEARCH_LIMIT = 10


@dataclass
class GuestRow:
    guest: Guest
    spins: int = 0
    last_play: datetime | None = None
    active_codes: int = 0


@dataclass
class GuestCard:
    row: GuestRow
    redeemed: int = 0
    expired: int = 0
    next_spin: datetime | None = None
    recent_codes: list[tuple[PrizeCode, CodeStatus]] = field(default_factory=list)


def status(guest: Guest) -> tuple[str, str]:
    """(значок, подпись) статуса регистрации."""
    if is_registered(guest):
        return "✅", "регистрация завершена"
    if guest.phone is None:
        return "📱", "нет номера телефона"
    return "📜", "правила не приняты"


def masked_phone(guest: Guest) -> str:
    return f"…{guest.phone[-4:]}" if guest.phone else "—"


async def summary(db: AsyncSession, club: Club) -> dict[str, int]:
    total = await db.scalar(select(func.count()).select_from(Guest).where(Guest.club_id == club.id))
    registered = await db.scalar(select(func.count()).select_from(Guest).where(
        Guest.club_id == club.id, Guest.phone.is_not(None), Guest.consent_at.is_not(None)))
    with_prizes = await db.scalar(select(func.count(func.distinct(PrizeCode.guest_id))).where(
        PrizeCode.club_id == club.id, PrizeCode.redeemed_at.is_(None), PrizeCode.expires_at > func.now()))
    return {"total": total, "registered": registered, "with_prizes": with_prizes}


def _rows_query(club: Club):
    plays = (
        select(Play.guest_id, func.count().label("spins"), func.max(Play.played_at).label("last_play"))
        .where(Play.club_id == club.id)  # игры всех видов
        .group_by(Play.guest_id)
        .subquery()
    )
    codes = (
        select(PrizeCode.guest_id, func.count().label("active"))
        .where(PrizeCode.club_id == club.id, PrizeCode.redeemed_at.is_(None), PrizeCode.expires_at > func.now())
        .group_by(PrizeCode.guest_id)
        .subquery()
    )
    query = (
        select(Guest, plays.c.spins, plays.c.last_play, codes.c.active)
        .outerjoin(plays, plays.c.guest_id == Guest.id)
        .outerjoin(codes, codes.c.guest_id == Guest.id)
        .where(Guest.club_id == club.id)
        # Недавно активные сверху: последний спин, иначе — когда впервые написал боту.
        .order_by(func.coalesce(plays.c.last_play, Guest.created_at).desc(), Guest.id.desc())
    )
    return query


def _to_rows(result) -> list[GuestRow]:
    return [GuestRow(g, spins or 0, last, active or 0) for g, spins, last, active in result]


async def page(db: AsyncSession, club: Club, number: int) -> tuple[list[GuestRow], int]:
    """Страница списка (с нуля) и общее число страниц."""
    total = await db.scalar(select(func.count()).select_from(Guest).where(Guest.club_id == club.id))
    pages = max(1, -(-total // PAGE_SIZE))
    number = min(max(number, 0), pages - 1)
    rows = _to_rows(await db.execute(_rows_query(club).offset(number * PAGE_SIZE).limit(PAGE_SIZE)))
    return rows, pages


def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def search(db: AsyncSession, club: Club, text: str) -> tuple[list[GuestRow], bool]:
    """Поиск по имени, @username или последним цифрам телефона. (гости, есть ли ещё)."""
    text = text.strip()
    digits = "".join(ch for ch in text if ch.isdigit())
    if digits and len(digits) == len(text.replace("+", "").replace(" ", "").replace("-", "")):
        if len(digits) < 3:
            return [], False
        condition = Guest.phone.like(f"%{_escape_like(digits)}", escape="\\")
    else:
        needle = f"%{_escape_like(text.lstrip('@'))}%"
        condition = or_(Guest.first_name.ilike(needle, escape="\\"), Guest.username.ilike(needle, escape="\\"))
    result = _to_rows(await db.execute(_rows_query(club).where(condition).limit(SEARCH_LIMIT + 1)))
    return result[:SEARCH_LIMIT], len(result) > SEARCH_LIMIT


async def card(db: AsyncSession, club: Club, guest_id: int) -> GuestCard | None:
    """Карточка гостя ЭТОГО клуба; гость другого клуба — None."""
    found = _to_rows(await db.execute(_rows_query(club).where(Guest.id == guest_id)))
    if not found:
        return None
    row = found[0]
    now = datetime.now(UTC)
    codes = list(await db.scalars(
        select(PrizeCode).where(PrizeCode.club_id == club.id, PrizeCode.guest_id == guest_id)
        .order_by(PrizeCode.created_at.desc())
    ))
    statuses = [code_status(c, now) for c in codes]
    config = await wheel_config(db, club.id)
    return GuestCard(
        row=row,
        redeemed=statuses.count(CodeStatus.REDEEMED),
        expired=statuses.count(CodeStatus.EXPIRED),
        next_spin=await next_spin_at(db, row.guest, config) if config and is_registered(row.guest) else None,
        recent_codes=list(zip(codes, statuses))[:5],
    )
