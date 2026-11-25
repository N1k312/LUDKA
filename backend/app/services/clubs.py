"""Операции суперадмина над клубами — общие для бота платформы и консоли app.cli."""

from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Club,
    ClubBot,
    ClubStaff,
    GameConfig,
    GameType,
    Guest,
    Play,
    PrizeCode,
    PrizeOption,
    RedemptionLog,
    StaffRole,
    Subscription,
)
from app.services import subscription

WHEEL_COOLDOWN_SECONDS = 7 * 24 * 3600
DEFAULT_TIMEZONE = "Asia/Almaty"


async def create_club(db: AsyncSession, name: str, timezone: str = DEFAULT_TIMEZONE) -> Club:
    """Клуб с пробным периодом и тремя играми (раз в 7 дней), без призов.

    Колесо включено, стаканчики и кейс выключены: клуб включает их, когда добавит призы.
    """
    ZoneInfo(timezone)  # неверный часовой пояс — ошибка сразу, а не при показе дат
    club = Club(name=name.strip(), timezone=timezone)
    db.add(club)
    await db.flush()
    db.add(subscription.new_trial(club.id))
    for game in GameType:
        db.add(GameConfig(club_id=club.id, game_type=game, cooldown_seconds=WHEEL_COOLDOWN_SECONDS,
                          enabled=game == GameType.WHEEL))
    await db.flush()
    return club


class GameSettingError(Exception):
    """Понятная причина отказа для ответа в боте."""


async def game_configs(db: AsyncSession, club: Club) -> dict[GameType, GameConfig]:
    """Настройки всех игр клуба; недостающие строки создаются (выключенными).

    INSERT ... ON CONFLICT DO NOTHING: два одновременных запроса (например, гость открыл
    Mini App дважды) не падают на повторной вставке.
    """
    query = select(GameConfig).where(GameConfig.club_id == club.id)
    rows = {GameType(c.game_type): c for c in await db.scalars(query)}
    missing = [game for game in GameType if game not in rows]
    if missing:
        await db.execute(
            pg_insert(GameConfig)
            .values([{"club_id": club.id, "game_type": game, "cooldown_seconds": WHEEL_COOLDOWN_SECONDS,
                      "enabled": False} for game in missing])
            .on_conflict_do_nothing()
        )
        rows = {GameType(c.game_type): c for c in await db.scalars(query)}
    return {game: rows[game] for game in GameType}


async def set_game_enabled(db: AsyncSession, club: Club, game: GameType, enabled: bool) -> GameConfig:
    configs = await game_configs(db, club)
    if enabled:
        prizes = await active_prizes(db, club, game)
        if not any(p.weight > 0 and not p.is_empty for p in prizes):
            raise GameSettingError("Сначала добавьте в эту игру хотя бы один приз с промокодом.")
    elif not any(c.enabled for g, c in configs.items() if g != game):
        raise GameSettingError("Хотя бы одна игра должна оставаться включённой.")
    configs[game].enabled = enabled
    await db.flush()
    return configs[game]


async def add_prize(
    db: AsyncSession,
    club: Club,
    title: str,
    weight: int,
    emoji: str | None = None,
    is_empty: bool = False,
    ttl_days: int = 14,
    game: GameType = GameType.WHEEL,
) -> PrizeOption:
    position = await db.scalar(
        select(func.coalesce(func.max(PrizeOption.position) + 1, 0)).where(
            PrizeOption.club_id == club.id
        )
    )
    prize = PrizeOption(
        club_id=club.id, game_type=game, title=title.strip(), emoji=emoji,
        weight=weight, is_empty=is_empty, ttl_days=ttl_days, position=position,
    )
    db.add(prize)
    await db.flush()
    return prize


async def active_prizes(db: AsyncSession, club: Club, game: GameType = GameType.WHEEL) -> list[PrizeOption]:
    return list(await db.scalars(
        select(PrizeOption)
        .where(PrizeOption.club_id == club.id, PrizeOption.game_type == game, PrizeOption.active.is_(True))
        .order_by(PrizeOption.position, PrizeOption.id)
    ))


async def set_cooldown(db: AsyncSession, club: Club, seconds: int, game: GameType = GameType.WHEEL) -> GameConfig:
    config = await db.get(GameConfig, (club.id, game))
    if config is None:
        config = GameConfig(club_id=club.id, game_type=game, enabled=game == GameType.WHEEL)
        db.add(config)
    config.cooldown_seconds = seconds
    await db.flush()
    return config


def split_emoji(text: str) -> tuple[str | None, str]:
    """«🎮 1 час игры» → ("🎮", "1 час игры"); без эмодзи в начале → (None, текст)."""
    text = text.strip()
    first, _, rest = text.partition(" ")
    if rest and first and not any(ch.isalnum() for ch in first):
        return first[:16], rest.strip()
    return None, text


def local(value: datetime, club: Club) -> str:
    return value.astimezone(ZoneInfo(club.timezone)).strftime("%d.%m.%Y %H:%M")


async def club_summary(db: AsyncSession, club: Club) -> dict:
    """Всё, что показывается в карточке клуба."""
    club_bot = await db.scalar(select(ClubBot).where(ClubBot.club_id == club.id))
    sub = await db.get(Subscription, club.id)
    config = await db.get(GameConfig, (club.id, GameType.WHEEL))
    prizes = await active_prizes(db, club)
    staff = list(await db.scalars(select(ClubStaff).where(ClubStaff.club_id == club.id)))
    owner = next((s for s in staff if s.role == StaffRole.OWNER), None)
    return {
        "bot": club_bot,
        "subscription": sub,
        "config": config,
        "prizes": prizes,
        "owner": owner,
        "admins": [s for s in staff if s.role == StaffRole.ADMIN],
    }


# --- Сброс данных клуба ---------------------------------------------------------------------


async def game_data_counts(db: AsyncSession, club: Club) -> dict[str, int]:
    """Сколько игровых данных у клуба — показывается перед очисткой."""

    async def count(model, *conditions) -> int:
        return await db.scalar(select(func.count()).select_from(model).where(model.club_id == club.id, *conditions))

    return {
        "guests": await count(Guest),
        "plays": await count(Play),
        "codes": await count(PrizeCode),
        "active_codes": await count(PrizeCode, PrizeCode.redeemed_at.is_(None),
                                    PrizeCode.expires_at > func.now()),
        "log": await count(RedemptionLog),
    }


async def wipe_game_data(db: AsyncSession, club: Club) -> dict[str, int]:
    """Удаляет гостей, игры, промокоды и журнал проверок ТОЛЬКО этого клуба.

    Бот, призы, сотрудники, подписка, оформление и правила остаются.
    Порядок удаления — от зависимых таблиц к главным (промокоды → игры → гости).
    """
    counts = await game_data_counts(db, club)
    for model in (RedemptionLog, PrizeCode, Play, Guest):
        await db.execute(delete(model).where(model.club_id == club.id))
    await db.flush()
    return counts


async def reset_guest_registration(db: AsyncSession, club: Club, telegram_id: int) -> bool:
    """Снимает номер и согласие гостя клуба (игры и промокоды остаются). False — такого гостя нет."""
    result = await db.execute(
        update(Guest)
        .where(Guest.club_id == club.id, Guest.telegram_id == telegram_id)
        .values(phone=None, consent_at=None)
    )
    return result.rowcount > 0
