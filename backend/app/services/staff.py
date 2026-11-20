"""Роли в боте клуба и права на управление сотрудниками.

  Владелец платформы (PLATFORM_OWNER_IDS в .env) — максимальные права во всех ботах:
      назначает суперадмина клуба, добавляет и убирает любых сотрудников, гасит коды.
  Суперадмин клуба (club_staff.role = owner, один на клуб) — добавляет и убирает админов
      своего клуба, гасит коды.
  Админ (club_staff.role = admin) — проверяет и гасит промокоды.

Роли действуют только в своём клубе: суперадмин клуба А в клубе Б — обычный гость.
Все проверки прав — на сервере, кнопки в Telegram лишь удобство.
"""

from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import Club, ClubStaff, StaffRole


class Role(StrEnum):
    PLATFORM_OWNER = "platform_owner"
    SUPERADMIN = "superadmin"
    ADMIN = "admin"


ROLE_TITLES = {
    Role.PLATFORM_OWNER: "владелец платформы",
    Role.SUPERADMIN: "суперадмин клуба",
    Role.ADMIN: "админ",
}


class StaffError(Exception):
    """Понятная причина отказа для ответа в боте."""


@dataclass
class Person:
    telegram_id: int
    first_name: str | None = None
    username: str | None = None

    def title(self) -> str:
        name = self.first_name or f"id {self.telegram_id}"
        return f"{name} (@{self.username})" if self.username else name


def is_platform_owner(telegram_id: int) -> bool:
    return telegram_id in get_settings().owner_ids


async def display_name(db: AsyncSession, club: Club, telegram_id: int | None) -> str:
    """Как назвать сотрудника в ответах бота: имя (@username), «владелец платформы» или id."""
    if telegram_id is None:
        return "неизвестно кем"
    if is_platform_owner(telegram_id):
        return "владелец платформы"
    staff = await db.scalar(
        select(ClubStaff).where(ClubStaff.club_id == club.id, ClubStaff.telegram_id == telegram_id)
    )
    if staff is None:
        return f"бывший сотрудник (id {telegram_id})"
    return Person(staff.telegram_id, staff.first_name, staff.username).title()


async def role_in_club(db: AsyncSession, club: Club, telegram_id: int) -> Role | None:
    if is_platform_owner(telegram_id):
        return Role.PLATFORM_OWNER
    staff = await db.scalar(
        select(ClubStaff).where(ClubStaff.club_id == club.id, ClubStaff.telegram_id == telegram_id)
    )
    if staff is None:
        return None
    return Role.SUPERADMIN if staff.role == StaffRole.OWNER else Role.ADMIN


def can_manage_admins(role: Role | None) -> bool:
    return role in (Role.PLATFORM_OWNER, Role.SUPERADMIN)


def can_assign_superadmin(role: Role | None) -> bool:
    return role == Role.PLATFORM_OWNER


async def list_staff(db: AsyncSession, club: Club) -> list[ClubStaff]:
    return list(
        await db.scalars(
            select(ClubStaff)
            .where(ClubStaff.club_id == club.id)
            .order_by(ClubStaff.role.desc(), ClubStaff.created_at)  # owner перед admin
        )
    )


async def _get(db: AsyncSession, club: Club, telegram_id: int) -> ClubStaff | None:
    return await db.scalar(
        select(ClubStaff).where(ClubStaff.club_id == club.id, ClubStaff.telegram_id == telegram_id)
    )


def _fill(staff: ClubStaff, person: Person) -> None:
    if person.first_name:
        staff.first_name = person.first_name
    if person.username:
        staff.username = person.username


async def add_admin(db: AsyncSession, club: Club, actor_role: Role | None, person: Person) -> ClubStaff:
    if not can_manage_admins(actor_role):
        raise StaffError("Добавлять админов может суперадмин клуба или владелец платформы")
    if is_platform_owner(person.telegram_id):
        raise StaffError("Это владелец платформы — у него и так все права")
    staff = await _get(db, club, person.telegram_id)
    if staff is not None and staff.role == StaffRole.OWNER:
        raise StaffError("Это суперадмин клуба — он уже может всё, что админ")
    if staff is None:
        staff = ClubStaff(club_id=club.id, telegram_id=person.telegram_id, role=StaffRole.ADMIN)
        db.add(staff)
    _fill(staff, person)
    await db.flush()
    return staff


async def assign_superadmin(
    db: AsyncSession, club: Club, actor_role: Role | None, person: Person
) -> tuple[ClubStaff, ClubStaff | None]:
    """Назначает суперадмина клуба. Прежний суперадмин становится админом.

    Возвращает (новый, прежний или None).
    """
    if not can_assign_superadmin(actor_role):
        raise StaffError("Назначать суперадмина может только владелец платформы")
    if is_platform_owner(person.telegram_id):
        raise StaffError("Это владелец платформы — у него и так все права")

    previous = await db.scalar(
        select(ClubStaff)
        .where(ClubStaff.club_id == club.id, ClubStaff.role == StaffRole.OWNER)
        .with_for_update()
    )
    if previous is not None and previous.telegram_id == person.telegram_id:
        _fill(previous, person)
        await db.flush()
        return previous, None
    if previous is not None:
        previous.role = StaffRole.ADMIN
        await db.flush()  # сначала снять роль, иначе сработает «один суперадмин на клуб»

    staff = await _get(db, club, person.telegram_id)
    if staff is None:
        staff = ClubStaff(club_id=club.id, telegram_id=person.telegram_id)
        db.add(staff)
    staff.role = StaffRole.OWNER
    _fill(staff, person)
    try:
        async with db.begin_nested():
            await db.flush()
    except IntegrityError:
        raise StaffError("Суперадмина только что назначили одновременно — повторите") from None
    return staff, previous


async def remove_staff(
    db: AsyncSession, club: Club, actor_role: Role | None, actor_id: int, telegram_id: int
) -> ClubStaff:
    staff = await _get(db, club, telegram_id)
    if staff is None:
        raise StaffError("Этот человек уже не сотрудник клуба")
    if staff.role == StaffRole.OWNER:
        if not can_assign_superadmin(actor_role):
            raise StaffError("Суперадмина может снять только владелец платформы")
    elif not can_manage_admins(actor_role):
        raise StaffError("Убирать админов может суперадмин клуба или владелец платформы")
    if telegram_id == actor_id and actor_role != Role.PLATFORM_OWNER:
        raise StaffError("Нельзя убрать самого себя")
    await db.delete(staff)
    await db.flush()
    return staff
