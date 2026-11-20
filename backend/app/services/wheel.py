"""Колесо фортуны. Результат определяет только сервер, криптостойким генератором.

Спин атомарен: строка гостя блокируется (SELECT ... FOR UPDATE), поэтому два
одновременных нажатия выполняются по очереди, и второе упирается в лимит.
"""

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Club,
    ClubStatus,
    GameConfig,
    GameType,
    Guest,
    Play,
    PrizeCode,
    PrizeOption,
    Subscription,
)
from app.services import branding, subscription
from app.services.codes import new_unique_code
from app.services.guests import is_registered


class SpinError(Exception):
    def __init__(self, code: str, message: str, next_spin_at: datetime | None = None):
        super().__init__(message)
        self.code, self.message, self.next_spin_at = code, message, next_spin_at


@dataclass
class SpinResult:
    option: PrizeOption
    code: PrizeCode | None
    next_spin_at: datetime


def pick_option(options: list[PrizeOption]) -> PrizeOption:
    """Взвешенный случайный выбор через secrets (не random)."""
    candidates = [o for o in options if o.weight > 0]
    total = sum(o.weight for o in candidates)
    if total == 0:
        raise SpinError("no_prizes", "Призы ещё не настроены")
    point = secrets.randbelow(total)
    for option in candidates:
        if point < option.weight:
            return option
        point -= option.weight
    raise AssertionError("unreachable")


async def wheel_options(db: AsyncSession, club_id: int) -> list[PrizeOption]:
    return list(
        await db.scalars(
            select(PrizeOption)
            .where(
                PrizeOption.club_id == club_id,
                PrizeOption.game_type == GameType.WHEEL,
                PrizeOption.active.is_(True),
            )
            .order_by(PrizeOption.position, PrizeOption.id)
        )
    )


async def wheel_config(db: AsyncSession, club_id: int) -> GameConfig | None:
    return await db.get(GameConfig, (club_id, GameType.WHEEL))


async def last_play_at(db: AsyncSession, guest: Guest) -> datetime | None:
    return await db.scalar(
        select(Play.played_at)
        .where(
            Play.club_id == guest.club_id,
            Play.guest_id == guest.id,
            Play.game_type == GameType.WHEEL,
        )
        .order_by(Play.played_at.desc())
        .limit(1)
    )


async def next_spin_at(db: AsyncSession, guest: Guest, config: GameConfig) -> datetime | None:
    """Когда можно крутить снова; None — можно сейчас."""
    last = await last_play_at(db, guest)
    if last is None:
        return None
    available = last + timedelta(seconds=config.cooldown_seconds)
    return available if available > datetime.now(UTC) else None


async def is_available(db: AsyncSession, club: Club) -> bool:
    """Акция работает: клуб активен и подписка позволяет играть."""
    sub = await db.get(Subscription, club.id)
    return club.status == ClubStatus.ACTIVE and subscription.allows_play(sub, datetime.now(UTC))


async def spin(db: AsyncSession, club: Club, guest_id: int) -> SpinResult:
    """Проверки, выбор сектора, запись игры и промокода — в одной транзакции вызывающего кода."""
    now = datetime.now(UTC)
    guest = await db.scalar(
        select(Guest)
        .where(Guest.club_id == club.id, Guest.id == guest_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if guest is None or not is_registered(guest):
        raise SpinError("registration_required", "Сначала зарегистрируйтесь в боте")
    if not await is_available(db, club):
        raise SpinError("unavailable", "Акция временно недоступна")

    config = await wheel_config(db, club.id)
    if config is None or not config.enabled:
        raise SpinError("game_disabled", "Колесо сейчас выключено")

    wait_until = await next_spin_at(db, guest, config)
    if wait_until is not None:
        raise SpinError("cooldown", "Следующий спин ещё недоступен", next_spin_at=wait_until)

    option = pick_option(await wheel_options(db, club.id))

    play = Play(
        club_id=club.id,
        guest_id=guest.id,
        game_type=GameType.WHEEL,
        prize_option_id=option.id,
        played_at=now,
    )
    db.add(play)
    await db.flush()

    code = None
    if not option.is_empty:
        code = PrizeCode(
            code=await new_unique_code(db, branding.code_prefix(club)),
            club_id=club.id,
            guest_id=guest.id,
            prize_option_id=option.id,
            play_id=play.id,
            title=option.title,
            created_at=now,
            expires_at=now + timedelta(days=option.ttl_days),
        )
        db.add(code)
        await db.flush()

    return SpinResult(
        option=option,
        code=code,
        next_spin_at=now + timedelta(seconds=config.cooldown_seconds),
    )
