"""Промокоды: GG-XXXXXX из символов без похожих (нет 0/O, 1/I/L), уникальны на всей платформе."""

import secrets
from datetime import datetime
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import PrizeCode

PREFIX = "GG-"
ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"
LENGTH = 6
MAX_ATTEMPTS = 10


class CodeStatus(StrEnum):
    ACTIVE = "active"
    REDEEMED = "redeemed"
    EXPIRED = "expired"


def generate_code() -> str:
    return PREFIX + "".join(secrets.choice(ALPHABET) for _ in range(LENGTH))


async def new_unique_code(db: AsyncSession) -> str:
    """31^6 ≈ 887 млн вариантов: совпадение маловероятно, но проверяем.

    Первичный ключ code всё равно защищает от дубля при гонке.
    """
    for _ in range(MAX_ATTEMPTS):
        code = generate_code()
        if await db.scalar(select(PrizeCode.code).where(PrizeCode.code == code)) is None:
            return code
    raise RuntimeError("Не удалось сгенерировать уникальный промокод")


def code_status(code: PrizeCode, now: datetime) -> CodeStatus:
    if code.redeemed_at is not None:
        return CodeStatus.REDEEMED
    if now >= code.expires_at:
        return CodeStatus.EXPIRED
    return CodeStatus.ACTIVE
