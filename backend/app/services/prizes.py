"""Редактирование призов колеса — общее для бота клуба и пульта.

Все операции ищут приз только внутри переданного клуба. Изменения действуют на новые спины:
в выданном промокоде уже лежит копия названия и своя дата окончания.
Правки одного клуба выполняются по очереди (блокировка строки клуба), чтобы два сотрудника
одновременно не выключили последние сектора.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import MAX_BIGINT, Club, GameType, PrizeOption
from app.services import clubs

# Редкость приза для гостя — по его доле среди призов игры. Самих шансов гость не видит.
LEGENDARY_SHARE = 0.05  # до 5% — красный, «очень редкий»
RARE_SHARE = 0.20       # до 20% — синий, «редкий»; дальше зелёный, «обычный»
RARITY_TITLES = {"legendary": "очень редкий", "rare": "редкий", "common": "обычный", "empty": "пусто"}
RARITY_MARKS = {"legendary": "🔴", "rare": "🔵", "common": "🟢", "empty": "⚪"}


def rarity(prize: PrizeOption, prizes: list[PrizeOption]) -> str:
    if prize.is_empty:
        return "empty"
    total = sum(p.weight for p in prizes)
    share = prize.weight / total if total else 0
    if share <= LEGENDARY_SHARE:
        return "legendary"
    if share <= RARE_SHARE:
        return "rare"
    return "common"

MAX_SECTORS = 12
MAX_WEIGHT = 100_000
MAX_TTL_DAYS = 365
MAX_TITLE_LENGTH = 128
DEFAULT_TTL_DAYS = 14


class PrizeError(Exception):
    """Понятная причина отказа для ответа в боте."""


class PrizeNotFound(PrizeError):
    def __init__(self) -> None:
        super().__init__("Приз не найден")


# --- Проверка ввода -------------------------------------------------------------------------


def parse_title(text: str) -> tuple[str | None, str]:
    emoji, title = clubs.split_emoji(text)
    if not 1 <= len(title) <= MAX_TITLE_LENGTH:
        raise PrizeError(f"Название — от 1 до {MAX_TITLE_LENGTH} символов. Попробуйте ещё раз.")
    return emoji, title


def _parse_int(text: str, low: int, high: int, message: str) -> int:
    raw = text.strip().removesuffix("%").strip()
    if not (raw.isascii() and raw.isdigit()) or len(raw) > 7 or not low <= int(raw) <= high:
        raise PrizeError(message)
    return int(raw)


def parse_weight(text: str) -> int:
    return _parse_int(text, 1, MAX_WEIGHT, f"Шанс — целое число от 1 до {MAX_WEIGHT}, например 20.")


EMPTY_TITLE = "Повезёт в следующий раз"
EMPTY_EMOJI = "😔"


def parse_new_prize(text: str, empty: bool) -> tuple[str | None, str, int]:
    """«🎮 1 час игры 20» → (эмодзи, название, шанс). Для пустого сектора можно прислать только число."""
    text = text.strip()
    head, _, last = text.rpartition(" ")
    if empty and not head:
        head, last = "", text
    if not last.removesuffix("%").strip().isdigit():
        example = "😔 Повезёт в следующий раз 30" if empty else "🎮 1 час игры 20"
        raise PrizeError(f"Добавьте шанс числом в конце, например: {example}")
    weight = parse_weight(last)
    if not head.strip():
        if empty:
            return EMPTY_EMOJI, EMPTY_TITLE, weight
        raise PrizeError("Нужно название и шанс, например: 🎮 1 час игры 20")
    emoji, title = parse_title(head)
    return emoji, title, weight


def parse_ttl(text: str) -> int:
    return _parse_int(text, 1, MAX_TTL_DAYS, f"Срок — целое число дней от 1 до {MAX_TTL_DAYS}, например 14.")


# --- Как показать ----------------------------------------------------------------------------


def label(prize: PrizeOption) -> str:
    return f"{prize.emoji} {prize.title}" if prize.emoji else prize.title


def chance(prize: PrizeOption, prizes: list[PrizeOption]) -> str:
    """Настоящий шанс выпадения в процентах."""
    total = sum(p.weight for p in prizes)
    if not total or not prize.weight:
        return "0%"
    percent = prize.weight / total * 100
    if percent < 1:
        return "<1%"
    return f"{percent:.1f}".removesuffix(".0") + "%"


def warnings(prizes: list[PrizeOption]) -> list[str]:
    playable = [p for p in prizes if p.weight > 0]
    if not playable:
        return ["⛔ Нет ни одного приза: игра не работает."]
    notes = []
    if all(p.is_empty for p in playable):
        notes.append("⛔ Нет призов с промокодом: гости ничего не выиграют.")
    elif not any(p.is_empty for p in playable):
        notes.append("⚠️ Нет пустого сектора: каждая игра даёт приз.")
    total = sum(p.weight for p in prizes)
    if total != 100:
        notes.append(f"ℹ️ Сумма шансов {total}, а не 100: проценты выше пересчитаны автоматически.")
    return notes


# --- Операции --------------------------------------------------------------------------------


async def _lock_club(db: AsyncSession, club: Club) -> None:
    # FOR NO KEY UPDATE: правки идут по очереди, а спины (ссылка на клуб) не ждут.
    await db.execute(select(Club.id).where(Club.id == club.id).with_for_update(key_share=True))


async def get_active(db: AsyncSession, club: Club, prize_id: int) -> PrizeOption:
    if not 0 < prize_id <= MAX_BIGINT:
        raise PrizeNotFound
    prize = await db.scalar(
        select(PrizeOption)
        .where(PrizeOption.id == prize_id, PrizeOption.club_id == club.id, PrizeOption.active.is_(True))
        .execution_options(populate_existing=True)
    )
    if prize is None:
        raise PrizeNotFound
    return prize


async def create(
    db: AsyncSession, club: Club, emoji: str | None, title: str, weight: int,
    is_empty: bool, ttl_days: int = DEFAULT_TTL_DAYS, game: GameType = GameType.WHEEL,
) -> tuple[PrizeOption, str]:
    await _lock_club(db, club)
    if len(await clubs.active_prizes(db, club, game)) >= MAX_SECTORS:
        raise PrizeError(f"В игре максимум {MAX_SECTORS} призов. Сначала выключите лишний.")
    prize = await clubs.add_prize(db, club, title, weight, emoji, is_empty=is_empty, ttl_days=ttl_days, game=game)
    kind = "пустой сектор" if is_empty else f"приз, срок {ttl_days} дн."
    return prize, f"добавлен «{label(prize)}» ({kind}), шанс {weight}"


async def rename(db: AsyncSession, club: Club, prize_id: int, text: str) -> tuple[PrizeOption, str]:
    emoji, title = parse_title(text)
    await _lock_club(db, club)
    prize = await get_active(db, club, prize_id)
    before = label(prize)
    prize.title, prize.emoji = title, emoji or prize.emoji
    await db.flush()
    return prize, f"«{before}» переименован в «{label(prize)}»"


async def set_weight(db: AsyncSession, club: Club, prize_id: int, text: str) -> tuple[PrizeOption, str]:
    weight = parse_weight(text)
    await _lock_club(db, club)
    prize = await get_active(db, club, prize_id)
    before, prize.weight = prize.weight, weight
    await db.flush()
    return prize, f"«{label(prize)}»: шанс {before} → {weight} ({chance(prize, await clubs.active_prizes(db, club, prize.game_type))})"


async def set_ttl(db: AsyncSession, club: Club, prize_id: int, text: str) -> tuple[PrizeOption, str]:
    days = parse_ttl(text)
    await _lock_club(db, club)
    prize = await get_active(db, club, prize_id)
    if prize.is_empty:
        raise PrizeError("У пустого сектора нет промокода и срока.")
    before, prize.ttl_days = prize.ttl_days, days
    await db.flush()
    return prize, f"«{label(prize)}»: срок промокода {before} → {days} дн."


async def disable(db: AsyncSession, club: Club, prize_id: int) -> tuple[PrizeOption, str]:
    """Выключает сектор. Строка остаётся: на неё ссылаются прошлые игры и промокоды."""
    await _lock_club(db, club)
    prize = await get_active(db, club, prize_id)
    rest = [p for p in await clubs.active_prizes(db, club, prize.game_type) if p.id != prize.id and p.weight > 0]
    if prize.weight > 0:
        if not rest:
            raise PrizeError("Нельзя выключить последний приз игры: она останется пустой.")
        if not prize.is_empty and all(p.is_empty for p in rest):
            raise PrizeError("Нельзя выключить последний приз с промокодом: останутся только пустые.")
    prize.active = False
    await db.flush()
    return prize, f"выключен «{label(prize)}»"
