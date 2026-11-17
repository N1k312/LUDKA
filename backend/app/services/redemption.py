"""Проверка и погашение промокодов админом клуба.

- Поиск всегда с фильтром по клубу: чужой промокод — «не найден», без упоминания другого клуба.
- Погашение атомарно: условный UPDATE ... WHERE redeemed_at IS NULL, поэтому
  два админа одновременно погасят код только один раз.
- Каждая проверка и погашение (включая неудачные) пишутся в redemption_log.
- Неудачные проверки ограничены — защита от перебора кодов.
"""

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Club,
    Guest,
    PrizeCode,
    PrizeOption,
    RedemptionAction,
    RedemptionLog,
    RedemptionResult,
)
from app.services.codes import ALPHABET, LENGTH, PREFIX

FAILED_LIMIT = 10
FAILED_WINDOW = timedelta(minutes=15)

# GG-7K3M9Q, gg 7k3m9q, 7K3M9Q — с дефисом, пробелом или без префикса.
CODE_PATTERN = re.compile(r"^\s*(?:GG[\s-]?)?([A-Z0-9]{%d})\s*$" % LENGTH, re.IGNORECASE)


def normalize_code(raw: str) -> str | None:
    """Приводит ввод админа к виду GG-XXXXXX; None — если это не похоже на промокод."""
    match = CODE_PATTERN.match(raw or "")
    if not match:
        return None
    body = match.group(1).upper()
    if set(body) - set(ALPHABET):
        return None
    return PREFIX + body


@dataclass
class Outcome:
    result: RedemptionResult
    code: str
    prize: PrizeCode | None = None
    option: PrizeOption | None = None
    guest: Guest | None = None


async def _log(db: AsyncSession, club: Club, admin_id: int, code: str,
               action: RedemptionAction, result: RedemptionResult) -> None:
    db.add(RedemptionLog(club_id=club.id, code=code[:32], admin_id=admin_id,
                         action=action, result=result))
    await db.flush()


async def _is_rate_limited(db: AsyncSession, club: Club, admin_id: int) -> bool:
    failed = await db.scalar(
        select(func.count())
        .select_from(RedemptionLog)
        .where(
            RedemptionLog.club_id == club.id,
            RedemptionLog.admin_id == admin_id,
            RedemptionLog.result == RedemptionResult.NOT_FOUND,
            RedemptionLog.created_at > datetime.now(UTC) - FAILED_WINDOW,
        )
    )
    return failed >= FAILED_LIMIT


async def _load(db: AsyncSession, club: Club, code: str) -> Outcome:
    """Текущее состояние промокода в этом клубе (без записи в журнал)."""
    row = (
        await db.execute(
            select(PrizeCode, PrizeOption, Guest)
            .join(PrizeOption, (PrizeOption.club_id == PrizeCode.club_id)
                  & (PrizeOption.id == PrizeCode.prize_option_id))
            .join(Guest, (Guest.club_id == PrizeCode.club_id) & (Guest.id == PrizeCode.guest_id))
            .where(PrizeCode.club_id == club.id, PrizeCode.code == code)
            .execution_options(populate_existing=True)
        )
    ).first()
    if row is None:
        return Outcome(RedemptionResult.NOT_FOUND, code)
    prize, option, guest = row
    if prize.redeemed_at is not None:
        result = RedemptionResult.ALREADY_REDEEMED
    elif datetime.now(UTC) >= prize.expires_at:
        result = RedemptionResult.EXPIRED
    else:
        result = RedemptionResult.VALID
    return Outcome(result, code, prize, option, guest)


async def check_code(db: AsyncSession, club: Club, admin_id: int, raw: str) -> Outcome:
    code = normalize_code(raw) or raw.strip().upper()[:32]
    if await _is_rate_limited(db, club, admin_id):
        await _log(db, club, admin_id, code, RedemptionAction.CHECK, RedemptionResult.RATE_LIMITED)
        return Outcome(RedemptionResult.RATE_LIMITED, code)
    outcome = await _load(db, club, code)
    await _log(db, club, admin_id, code, RedemptionAction.CHECK, outcome.result)
    return outcome


async def redeem_code(db: AsyncSession, club: Club, admin_id: int, raw: str) -> Outcome:
    code = normalize_code(raw) or raw.strip().upper()[:32]
    if await _is_rate_limited(db, club, admin_id):
        await _log(db, club, admin_id, code, RedemptionAction.REDEEM, RedemptionResult.RATE_LIMITED)
        return Outcome(RedemptionResult.RATE_LIMITED, code)

    now = datetime.now(UTC)
    redeemed = await db.scalar(
        update(PrizeCode)
        .where(
            PrizeCode.club_id == club.id,
            PrizeCode.code == code,
            PrizeCode.redeemed_at.is_(None),
            PrizeCode.expires_at > now,
        )
        .values(redeemed_at=now, redeemed_by=admin_id)
        .returning(PrizeCode.code)
    )
    outcome = await _load(db, club, code)
    if redeemed is not None:
        outcome.result = RedemptionResult.REDEEMED
    await _log(db, club, admin_id, code, RedemptionAction.REDEEM, outcome.result)
    return outcome
