"""Тестовые данные для разработки: клуб-пилот с колесом и призами.

Запуск (из папки backend):  .venv\\Scripts\\python -m app.seed
Повторный запуск ничего не дублирует.
"""

import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Club,
    GameConfig,
    GameType,
    PrizeOption,
    Subscription,
    SubscriptionStatus,
)

PILOT_CLUB_NAME = "Тестовый клуб"
TRIAL_DAYS = 14
WHEEL_COOLDOWN = timedelta(days=7)

PILOT_RULES = (
    "1. Крутить колесо можно один раз в 7 дней.\n"
    "2. Приз выдаётся по промокоду у администратора клуба.\n"
    "3. Промокод действует 14 дней с момента выигрыша.\n"
    "4. Призы не обмениваются на деньги."
)

# (эмодзи, название, вес, пустой сектор). Веса в сумме дают 100 — это проценты.
PILOT_PRIZES = [
    ("🎮", "1 час игры", 20, False),
    ("🥤", "Энергетик", 15, False),
    ("😔", "Повезёт в следующий раз", 30, True),
    ("💸", "Скидка 10%", 20, False),
    ("🎮", "2 часа игры", 10, False),
    ("🏆", "3 часа игры", 5, False),
]


async def seed_pilot_club(session: AsyncSession) -> Club:
    """Создаёт клуб-пилот, если его ещё нет, и возвращает его."""
    club = await session.scalar(select(Club).where(Club.name == PILOT_CLUB_NAME))
    if club is not None:
        return club

    club = Club(name=PILOT_CLUB_NAME, timezone="Asia/Almaty", rules_text=PILOT_RULES)
    session.add(club)
    await session.flush()

    session.add(
        Subscription(
            club_id=club.id,
            status=SubscriptionStatus.TRIAL,
            trial_ends_at=datetime.now(UTC) + timedelta(days=TRIAL_DAYS),
        )
    )
    session.add(
        GameConfig(
            club_id=club.id,
            game_type=GameType.WHEEL,
            cooldown_seconds=int(WHEEL_COOLDOWN.total_seconds()),
            enabled=True,
        )
    )
    session.add_all(
        PrizeOption(
            club_id=club.id,
            game_type=GameType.WHEEL,
            emoji=emoji,
            title=title,
            weight=weight,
            is_empty=is_empty,
            ttl_days=14,
            position=position,
        )
        for position, (emoji, title, weight, is_empty) in enumerate(PILOT_PRIZES)
    )
    await session.flush()
    return club


async def main() -> None:
    from app.db import SessionLocal, engine

    async with SessionLocal() as session:
        club = await seed_pilot_club(session)
        await session.commit()
        print(f"Клуб-пилот: id={club.id}, «{club.name}»")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
