"""Сводка клуба за сегодня (по часовому поясу клуба) — для кнопки «📋 Сегодня» в админ-панели."""

from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Club, Guest, Play, PrizeCode, RedemptionAction, RedemptionLog, RedemptionResult


@dataclass
class DaySummary:
    spins: int = 0  # игр всех видов
    by_game: Counter = field(default_factory=Counter)  # game_type → сколько игр
    codes_issued: int = 0
    redeemed: int = 0
    redeemed_by: Counter = field(default_factory=Counter)  # telegram_id → сколько погасил
    new_guests: int = 0
    failed_checks: int = 0


def day_start_utc(club: Club, now: datetime | None = None) -> datetime:
    now = now or datetime.now(UTC)
    local = now.astimezone(ZoneInfo(club.timezone))
    return datetime.combine(local.date(), time.min, tzinfo=local.tzinfo).astimezone(UTC)


async def today(db: AsyncSession, club: Club, now: datetime | None = None) -> DaySummary:
    since = day_start_utc(club, now)

    async def count(model, *conditions) -> int:
        return await db.scalar(select(func.count()).select_from(model).where(model.club_id == club.id, *conditions))

    rows = (await db.execute(
        select(PrizeCode.redeemed_by, func.count())
        .where(PrizeCode.club_id == club.id, PrizeCode.redeemed_at >= since)
        .group_by(PrizeCode.redeemed_by)
    )).all()
    by = Counter({telegram_id: n for telegram_id, n in rows})
    games = Counter({game: n for game, n in (await db.execute(
        select(Play.game_type, func.count())
        .where(Play.club_id == club.id, Play.played_at >= since)
        .group_by(Play.game_type)
    )).all()})
    return DaySummary(
        spins=sum(games.values()),
        by_game=games,
        codes_issued=await count(PrizeCode, PrizeCode.created_at >= since),
        redeemed=sum(by.values()),
        redeemed_by=by,
        new_guests=await count(Guest, Guest.consent_at >= since),
        failed_checks=await count(RedemptionLog, RedemptionLog.created_at >= since,
                                  RedemptionLog.action == RedemptionAction.CHECK,
                                  RedemptionLog.result == RedemptionResult.NOT_FOUND),
    )
