"""Бот клуба → «🎁 Призы и шансы»: редактор для суперадмина клуба и владельца платформы.

Роутер подключён раньше админского: пока идёт ввод, текст вроде «ANDREW» — это название
приза, а не промокод. Кнопка меню или команда выводит из ввода.
Права проверяются на сервере при каждом действии; логика — в app/services/prizes.py.
"""

from contextlib import suppress

from aiogram import F, Router, html
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import BaseFilter, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    User,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.bots.admin import (
    ENTER_BUTTON,
    GAMES_BUTTON,
    GUESTS_BUTTON,
    PRIZES_BUTTON,
    STAFF_BUTTON,
    TODAY_BUTTON,
    IsStaff,
    edit_or_answer,
)
from app.config import get_settings
from app.models import Club, GameType, PrizeOption
from app.platform_bot.setup import platform_bot
from app.services import clubs
from app.services import prizes as prize_service
from app.services.prizes import PrizeError, PrizeNotFound
from app.services.staff import Person, Role, can_edit_prizes
from app.services.wheel import GAME_ICONS, GAME_TITLES, GAMES

PREFIX = "pz:"
LIST = "pz:list"  # колесо (старые кнопки)
LIST_GAME = "pz:l:"
ADD = "pz:add"  # колесо (старые кнопки)
ADD_GAME = "pz:a:"
GAMES_VIEW = "pz:g"
GAME_TOGGLE = "pz:on:"
GAME_LIMIT = "pz:cd:"
CLUB_COOLDOWNS = [(24 * 3600, "1 день"), (3 * 24 * 3600, "3 дня"), (7 * 24 * 3600, "7 дней")]
CARD = "pz:c:"
EDIT_TITLE = "pz:t:"
EDIT_WEIGHT = "pz:w:"
EDIT_TTL = "pz:d:"
OFF_ASK = "pz:o:"
OFF_YES = "pz:y:"
KIND = "pz:k:"

MENU_BUTTONS = {ENTER_BUTTON, TODAY_BUTTON, GUESTS_BUTTON, PRIZES_BUTTON, GAMES_BUTTON, STAFF_BUTTON}


def parse_game(raw: str) -> GameType:
    try:
        return GameType(raw)
    except ValueError:
        raise PrizeNotFound from None


def game_from(data: str) -> GameType:
    """Игра из кнопки списка или добавления; старые кнопки без игры — колесо."""
    if data.startswith(LIST_GAME):
        return parse_game(data.removeprefix(LIST_GAME))
    if data.startswith(ADD_GAME):
        return parse_game(data.removeprefix(ADD_GAME))
    return GameType.WHEEL


class PrizeEdit(StatesGroup):
    title = State()
    weight = State()
    ttl = State()
    new_title = State()
    new_weight = State()
    new_kind = State()
    new_ttl = State()


class CanEdit(BaseFilter):
    async def __call__(self, event, role: Role) -> bool:
        return can_edit_prizes(role)


class CannotEdit(BaseFilter):
    async def __call__(self, event, role: Role) -> bool:
        return not can_edit_prizes(role)


def is_input(text: str) -> bool:
    return text not in MENU_BUTTONS and not text.startswith("/")


INPUT = (F.text.func(is_input), CanEdit())

router = Router(name="prize-editor")
router.message.filter(F.chat.type == "private", IsStaff())
router.callback_query.filter(IsStaff())


def button(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


# --- Экраны ----------------------------------------------------------------------------------


async def list_view(db: AsyncSession, club: Club, game: GameType = GameType.WHEEL) -> tuple[str, InlineKeyboardMarkup]:
    game = GameType(game)
    prizes = await clubs.active_prizes(db, club, game)
    lines = [f"🎁 <b>Призы и шансы · {GAME_ICONS[game]} {GAME_TITLES[game]}</b>", html.quote(club.name), ""]
    for p in prizes:
        kind = " (пустой)" if p.is_empty else ""
        mark = prize_service.RARITY_MARKS[prize_service.rarity(p, prizes)]
        lines.append(f"{mark} {html.quote(prize_service.label(p))}{kind} — {prize_service.chance(p, prizes)}")
    if not prizes:
        lines.append("Призов пока нет.")
    if notes := prize_service.warnings(prizes):
        lines += ["", *notes]
    lines += ["", "Гости не видят шансы, только цвет приза: 🔴 очень редкий (до 5%), 🔵 редкий (до 20%), "
              "🟢 обычный.", "Нажмите на приз, чтобы изменить. Новые настройки действуют на следующие игры."]
    rows = [[button(f"{'• ' if g == game else ''}{GAME_ICONS[g]} {GAME_TITLES[g]}", f"{LIST_GAME}{g.value}")
             for g in GAMES]]
    rows += [[button(f"{prize_service.label(p)} · {prize_service.chance(p, prizes)}"[:60], f"{CARD}{p.id}")]
             for p in prizes]
    rows.append([button("➕ Добавить приз", f"{ADD_GAME}{game.value}")])
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


async def card_view(db: AsyncSession, club: Club, prize: PrizeOption) -> tuple[str, InlineKeyboardMarkup]:
    game = GameType(prize.game_type)
    prizes = await clubs.active_prizes(db, club, game)
    level = prize_service.rarity(prize, prizes)
    lines = [
        f"🎁 <b>{html.quote(prize_service.label(prize))}</b>",
        f"Игра: {GAME_ICONS[game]} {GAME_TITLES[game]}",
        "",
        f"Тип: {'пустой' if prize.is_empty else 'приз с промокодом'}",
        f"Шанс: {prize.weight} (сейчас {prize_service.chance(prize, prizes)})",
        f"Цвет для гостей: {prize_service.RARITY_MARKS[level]} {prize_service.RARITY_TITLES[level]}",
    ]
    if not prize.is_empty:
        lines.append(f"Срок промокода: {prize.ttl_days} дн.")
    rows = [[button("✏️ Название", f"{EDIT_TITLE}{prize.id}"), button("🎯 Шанс", f"{EDIT_WEIGHT}{prize.id}")]]
    if not prize.is_empty:
        rows.append([button("⏳ Срок промокода", f"{EDIT_TTL}{prize.id}")])
    rows.append([button("🚫 Выключить", f"{OFF_ASK}{prize.id}")])
    rows.append([button("⬅️ К списку", f"{LIST_GAME}{game.value}")])
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


async def notify_platform(club: Club, role: Role, user: User, change: str) -> None:
    """Сообщает владельцу платформы в пульт о правке клуба. Сбой не отменяет сохранённое."""
    bot = platform_bot()
    if bot is None or role == Role.PLATFORM_OWNER:
        return
    who = Person(user.id, user.first_name, user.username).title()
    text = (f"🎁 Клуб {html.bold(html.quote(club.name))} изменил призы\n"
            f"Кто: {html.quote(who)}\nЧто: {html.quote(change)}")
    for owner_id in get_settings().owner_ids:
        with suppress(Exception):  # владелец мог ещё не писать пульту
            await bot.send_message(owner_id, text)


def parse_id(data: str, prefix: str) -> int:
    raw = data.removeprefix(prefix)
    if not (raw.isascii() and raw.isdigit()) or len(raw) > 19:
        raise PrizeNotFound
    return int(raw)


# --- Выход из ввода и права (раньше остальных обработчиков) ------------------------------------


@router.message(StateFilter(PrizeEdit), F.text)
async def leave_input(message: Message, state: FSMContext, role: Role) -> None:
    if is_input(message.text) and can_edit_prizes(role):
        raise SkipHandler
    await state.clear()
    if (message.text.split() or [""])[0].split("@")[0] == "/cancel":
        await message.answer("Отменено. Призы не изменены.")
        return
    raise SkipHandler  # кнопка меню или команда — пусть выполнится как обычно


@router.callback_query(F.data.startswith(PREFIX), CannotEdit())
async def denied(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.answer("Призы может менять только суперадмин клуба", show_alert=True)


@router.message(F.text.in_({PRIZES_BUTTON, GAMES_BUTTON}), CannotEdit())
async def denied_menu(message: Message) -> None:
    await message.answer("Игры, призы и шансы доступны суперадмину клуба.")


# --- Просмотр --------------------------------------------------------------------------------


@router.message(F.text == PRIZES_BUTTON)
async def open_prizes(message: Message, club: Club, db: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    text, markup = await list_view(db, club)
    await message.answer(text, reply_markup=markup)


@router.callback_query(F.data.func(lambda d: d == LIST or d.startswith(LIST_GAME)))
async def show_list(callback: CallbackQuery, club: Club, db: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    try:
        game = game_from(callback.data)
    except PrizeError as e:
        await not_found(callback, club, db, e)
        return
    text, markup = await list_view(db, club, game)
    await edit_or_answer(callback, text, markup)


async def not_found(callback: CallbackQuery, club: Club, db: AsyncSession, error: PrizeError) -> None:
    with suppress(TelegramAPIError):
        await callback.answer(str(error), show_alert=True)
    if isinstance(error, PrizeNotFound):
        text, markup = await list_view(db, club)
        with suppress(TelegramAPIError):
            await callback.message.edit_text(text, reply_markup=markup)


@router.callback_query(F.data.startswith(CARD))
async def show_card(callback: CallbackQuery, club: Club, db: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    try:
        prize = await prize_service.get_active(db, club, parse_id(callback.data, CARD))
    except PrizeError as e:
        await not_found(callback, club, db, e)
        return
    text, markup = await card_view(db, club, prize)
    await edit_or_answer(callback, text, markup)


# --- Изменение ------------------------------------------------------------------------------


async def ask_value(
    callback: CallbackQuery, club: Club, db: AsyncSession, state: FSMContext,
    prefix: str, target: State, question: str,
) -> None:
    try:
        prize = await prize_service.get_active(db, club, parse_id(callback.data, prefix))
    except PrizeError as e:
        await not_found(callback, club, db, e)
        return
    await state.set_state(target)
    await state.update_data(prize_id=prize.id)
    with suppress(TelegramAPIError):
        await callback.answer()
    await callback.message.answer(
        f"{html.quote(prize_service.label(prize))}\n\n{question}\n/cancel — отменить.")


@router.callback_query(F.data.startswith(EDIT_TITLE))
async def ask_title(callback: CallbackQuery, club: Club, db: AsyncSession, state: FSMContext) -> None:
    await ask_value(callback, club, db, state, EDIT_TITLE, PrizeEdit.title,
                    "Отправьте новое название, можно с эмодзи: <code>🎮 2 часа игры</code>.")


@router.callback_query(F.data.startswith(EDIT_WEIGHT))
async def ask_weight(callback: CallbackQuery, club: Club, db: AsyncSession, state: FSMContext) -> None:
    await ask_value(callback, club, db, state, EDIT_WEIGHT, PrizeEdit.weight,
                    "Отправьте новый шанс числом, например <code>20</code>. "
                    "Удобно, когда сумма всех шансов равна 100.")


@router.callback_query(F.data.startswith(EDIT_TTL))
async def ask_ttl(callback: CallbackQuery, club: Club, db: AsyncSession, state: FSMContext) -> None:
    await ask_value(callback, club, db, state, EDIT_TTL, PrizeEdit.ttl,
                    "Сколько дней действует промокод? Например, <code>14</code>. Выданные коды не изменятся.")


async def apply_edit(message: Message, club: Club, db: AsyncSession, state: FSMContext, role: Role, action) -> None:
    data = await state.get_data()
    try:
        prize, change = await action(db, club, data["prize_id"], message.text)
    except PrizeNotFound as e:
        await state.clear()
        await message.answer(f"⛔ {e}")
        return
    except PrizeError as e:
        await message.answer(f"⛔ {e}")  # остаёмся в режиме ввода
        return
    await state.clear()
    await db.commit()  # сначала база, потом Telegram
    text, markup = await card_view(db, club, prize)
    await message.answer(f"✅ Сохранено.\n\n{text}", reply_markup=markup)
    await notify_platform(club, role, message.from_user, change)


@router.message(PrizeEdit.title, *INPUT)
async def save_title(message: Message, club: Club, db: AsyncSession, state: FSMContext, role: Role) -> None:
    await apply_edit(message, club, db, state, role, prize_service.rename)


@router.message(PrizeEdit.weight, *INPUT)
async def save_weight(message: Message, club: Club, db: AsyncSession, state: FSMContext, role: Role) -> None:
    await apply_edit(message, club, db, state, role, prize_service.set_weight)


@router.message(PrizeEdit.ttl, *INPUT)
async def save_ttl(message: Message, club: Club, db: AsyncSession, state: FSMContext, role: Role) -> None:
    await apply_edit(message, club, db, state, role, prize_service.set_ttl)


# --- Выключение ------------------------------------------------------------------------------


@router.callback_query(F.data.startswith(OFF_ASK))
async def ask_off(callback: CallbackQuery, club: Club, db: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    try:
        prize = await prize_service.get_active(db, club, parse_id(callback.data, OFF_ASK))
    except PrizeError as e:
        await not_found(callback, club, db, e)
        return
    markup = InlineKeyboardMarkup(inline_keyboard=[[
        button("🚫 Да, выключить", f"{OFF_YES}{prize.id}"), button("⬅️ Нет", f"{CARD}{prize.id}")]])
    await edit_or_answer(
        callback,
        f"Выключить «{html.quote(prize_service.label(prize))}»?\n\n"
        "Приз исчезнет из игры. Уже выданные промокоды останутся действительными.",
        markup,
    )


@router.callback_query(F.data.startswith(OFF_YES))
async def confirm_off(callback: CallbackQuery, club: Club, db: AsyncSession, role: Role) -> None:
    try:
        prize, change = await prize_service.disable(db, club, parse_id(callback.data, OFF_YES))
    except PrizeError as e:
        await not_found(callback, club, db, e)
        return
    await db.commit()
    text, markup = await list_view(db, club, prize.game_type)
    await edit_or_answer(callback, f"✅ Сектор выключен.\n\n{text}", markup)
    await notify_platform(club, role, callback.from_user, change)


# --- Новый приз ------------------------------------------------------------------------------


@router.callback_query(F.data.func(lambda d: d == ADD or d.startswith(ADD_GAME)))
async def add_start(callback: CallbackQuery, club: Club, db: AsyncSession, state: FSMContext) -> None:
    try:
        game = game_from(callback.data)
    except PrizeError as e:
        await not_found(callback, club, db, e)
        return
    if len(await clubs.active_prizes(db, club, game)) >= prize_service.MAX_SECTORS:
        await callback.answer(f"В игре максимум {prize_service.MAX_SECTORS} призов", show_alert=True)
        return
    await state.set_state(PrizeEdit.new_title)
    await state.update_data(game=game.value)
    with suppress(TelegramAPIError):
        await callback.answer()
    await callback.message.answer(f"{GAME_ICONS[game]} {GAME_TITLES[game]}: отправьте приз — эмодзи и название, "
                                  "например <code>🎮 1 час игры</code>.\n/cancel — отменить.")


@router.message(PrizeEdit.new_title, *INPUT)
async def add_title(message: Message, club: Club, db: AsyncSession, state: FSMContext) -> None:
    try:
        emoji, title = prize_service.parse_title(message.text)
    except PrizeError as e:
        await message.answer(f"⛔ {e}")
        return
    game = GameType((await state.get_data()).get("game", GameType.WHEEL))
    total = sum(p.weight for p in await clubs.active_prizes(db, club, game))
    await state.update_data(emoji=emoji, title=title)
    await state.set_state(PrizeEdit.new_weight)
    await message.answer(f"Шанс приза числом, например <code>20</code>. Сейчас сумма шансов: {total}.")


@router.message(PrizeEdit.new_weight, *INPUT)
async def add_weight(message: Message, state: FSMContext) -> None:
    try:
        weight = prize_service.parse_weight(message.text)
    except PrizeError as e:
        await message.answer(f"⛔ {e}")
        return
    await state.update_data(weight=weight)
    await state.set_state(PrizeEdit.new_kind)
    await message.answer(
        "Это приз или пустой сектор («повезёт в следующий раз»)?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            button("🎁 Приз с промокодом", f"{KIND}0"), button("😔 Пустой сектор", f"{KIND}1")]]),
    )


async def finish_add(
    target: Message, user: User, club: Club, db: AsyncSession, state: FSMContext, role: Role, ttl_days: int,
    is_empty: bool,
) -> None:
    data = await state.get_data()
    await state.clear()
    game = GameType(data.get("game", GameType.WHEEL))
    try:
        _, change = await prize_service.create(
            db, club, data["emoji"], data["title"], data["weight"], is_empty, ttl_days, game=game)
    except PrizeError as e:
        await target.answer(f"⛔ {e}")
        return
    await db.commit()
    text, markup = await list_view(db, club, game)
    await target.answer(f"✅ Приз добавлен.\n\n{text}", reply_markup=markup)
    await notify_platform(club, role, user, change)


@router.callback_query(PrizeEdit.new_kind, F.data.startswith(KIND))
async def add_kind(callback: CallbackQuery, club: Club, db: AsyncSession, state: FSMContext, role: Role) -> None:
    with suppress(TelegramAPIError):
        await callback.answer()
    if callback.data == f"{KIND}1":
        await finish_add(callback.message, callback.from_user, club, db, state, role,
                         prize_service.DEFAULT_TTL_DAYS, True)
        return
    await state.set_state(PrizeEdit.new_ttl)
    await callback.message.answer("Сколько дней действует промокод? Например, <code>14</code>.")


@router.message(PrizeEdit.new_ttl, *INPUT)
async def add_ttl(message: Message, club: Club, db: AsyncSession, state: FSMContext, role: Role) -> None:
    try:
        days = prize_service.parse_ttl(message.text)
    except PrizeError as e:
        await message.answer(f"⛔ {e}")
        return
    await finish_add(message, message.from_user, club, db, state, role, days, False)


# --- Игры: включение и лимит -----------------------------------------------------------------


async def games_view(db: AsyncSession, club: Club) -> tuple[str, InlineKeyboardMarkup]:
    configs = await clubs.game_configs(db, club)
    lines = [f"🎮 <b>Игры</b> · {html.quote(club.name)}", ""]
    rows = []
    for game in GAMES:
        config = configs[game]
        count = len(await clubs.active_prizes(db, club, game))
        days = config.cooldown_seconds / 86400
        period = f"{days:g} дн." if days >= 1 else f"{config.cooldown_seconds} с"
        lines.append(f"{GAME_ICONS[game]} {GAME_TITLES[game]}: {'✅ включена' if config.enabled else '⛔ выключена'}, "
                     f"раз в {period}, призов {count}")
        rows.append([button(f"{GAME_ICONS[game]} {'Выключить' if config.enabled else 'Включить'}",
                            f"{GAME_TOGGLE}{game.value}"),
                     button("🎁 Призы", f"{LIST_GAME}{game.value}")])
        rows.append([button(f"{'✓ ' if config.cooldown_seconds == s else ''}{label}", f"{GAME_LIMIT}{game.value}:{s}")
                     for s, label in CLUB_COOLDOWNS])
    lines += ["", "Новую игру можно включить, когда в ней есть хотя бы один приз с промокодом."]
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(F.text == GAMES_BUTTON)
async def open_games(message: Message, club: Club, db: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    text, markup = await games_view(db, club)
    await db.commit()  # могли дописаться недостающие настройки
    await message.answer(text, reply_markup=markup)


@router.callback_query(F.data == GAMES_VIEW)
async def show_games(callback: CallbackQuery, club: Club, db: AsyncSession) -> None:
    text, markup = await games_view(db, club)
    await db.commit()
    await edit_or_answer(callback, text, markup)


@router.callback_query(F.data.startswith(GAME_TOGGLE))
async def toggle_game(callback: CallbackQuery, club: Club, db: AsyncSession, role: Role) -> None:
    try:
        game = parse_game(callback.data.removeprefix(GAME_TOGGLE))
        config = (await clubs.game_configs(db, club))[game]
        await clubs.set_game_enabled(db, club, game, not config.enabled)
    except (PrizeError, clubs.GameSettingError) as e:
        with suppress(TelegramAPIError):
            await callback.answer(str(e), show_alert=True)
        return
    await db.commit()
    text, markup = await games_view(db, club)
    await edit_or_answer(callback, text, markup)
    state_word = "включена" if config.enabled else "выключена"
    await notify_platform(club, role, callback.from_user, f"игра «{GAME_TITLES[game]}» {state_word}")


@router.callback_query(F.data.startswith(GAME_LIMIT))
async def set_game_limit(callback: CallbackQuery, club: Club, db: AsyncSession, role: Role) -> None:
    raw_game, _, raw_seconds = callback.data.removeprefix(GAME_LIMIT).partition(":")
    allowed = {str(s): s for s, _ in CLUB_COOLDOWNS}
    try:
        game = parse_game(raw_game)
        seconds = allowed[raw_seconds]
    except (PrizeError, KeyError):
        await callback.answer("Кнопка устарела", show_alert=True)
        return
    await clubs.set_cooldown(db, club, seconds, game)
    await db.commit()
    text, markup = await games_view(db, club)
    await edit_or_answer(callback, text, markup)
    await notify_platform(club, role, callback.from_user,
                          f"игра «{GAME_TITLES[game]}»: раз в {seconds // 86400} дн.")


@router.callback_query(F.data.startswith(PREFIX))
async def stale_button(callback: CallbackQuery) -> None:
    await callback.answer("Кнопка устарела. Откройте «🎁 Призы и шансы» заново.", show_alert=True)
