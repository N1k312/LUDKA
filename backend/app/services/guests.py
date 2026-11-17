"""Гости клуба: создание, телефон, согласие.

Один человек в двух клубах — две независимые записи гостя.
Номер телефона не пишется в логи.
"""

import re
from datetime import UTC, datetime

from aiogram.types import User
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Club, Guest


class PhoneTakenError(Exception):
    """Номер уже привязан к другому гостю этого клуба."""


def normalize_phone(raw: str) -> str:
    """Telegram присылает номер то с «+», то без: храним как +цифры."""
    digits = re.sub(r"\D", "", raw)
    if not 7 <= len(digits) <= 15:
        raise ValueError("Некорректный номер телефона")
    return f"+{digits}"


def is_registered(guest: Guest) -> bool:
    return guest.phone is not None and guest.consent_at is not None


async def get_or_create_guest(db: AsyncSession, club: Club, user: User) -> Guest:
    """Находит гостя клуба по Telegram id или создаёт его; обновляет username и имя.

    INSERT ... ON CONFLICT защищает от дубля при двух одновременных /start.
    """
    stmt = (
        insert(Guest)
        .values(
            club_id=club.id,
            telegram_id=user.id,
            username=user.username,
            first_name=user.first_name,
        )
        .on_conflict_do_update(
            index_elements=[Guest.club_id, Guest.telegram_id],
            set_={"username": user.username, "first_name": user.first_name},
        )
        .returning(Guest)
    )
    return (await db.scalars(stmt, execution_options={"populate_existing": True})).one()


async def set_phone(db: AsyncSession, guest: Guest, raw_phone: str) -> None:
    phone = normalize_phone(raw_phone)
    if guest.phone == phone:
        return
    other = await db.scalar(
        select(Guest.id).where(
            Guest.club_id == guest.club_id, Guest.phone == phone, Guest.id != guest.id
        )
    )
    if other is not None:
        raise PhoneTakenError
    guest.phone = phone
    try:
        async with db.begin_nested():
            await db.flush()
    except IntegrityError:
        # Одновременная регистрация того же номера другим аккаунтом.
        raise PhoneTakenError from None


async def accept_consent(db: AsyncSession, guest: Guest) -> None:
    if guest.consent_at is None:
        guest.consent_at = datetime.now(UTC)
        await db.flush()
