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
    GUESTS_BUTTON,
    PRIZES_BUTTON,
    STAFF_BUTTON,
    TODAY_BUTTON,
    IsStaff,
    edit_or_answer,
)
from app.config import get_settings
from app.models import Club, PrizeOption
from app.platform_bot.setup import platform_bot
from app.services import clubs
from app.services import prizes as prize_service
from app.services.prizes import PrizeError, PrizeNotFound
from app.services.staff import Person, Role, can_edit_prizes

PREFIX = "pz:"
LIST = "pz:list"
ADD = "pz:add"
CARD = "pz:c:"
EDIT_TITLE = "pz:t:"
EDIT_WEIGHT = "pz:w:"
EDIT_TTL = "pz:d:"
OFF_ASK = "pz:o:"
OFF_YES = "pz:y:"
KIND = "pz:k:"

MENU_BUTTONS = {ENTER_BUTTON, TODAY_BUTTON, GUESTS_BUTTON, PRIZES_BUTTON, STAFF_BUTTON}


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


async def list_view(db: AsyncSession, club: Club) -> tuple[str, InlineKeyboardMarkup]:
    prizes = await clubs.active_prizes(db, club)
    lines = [f"🎁 <b>Призы и шансы</b> · {html.quote(club.name)}", ""]
    for p in prizes:
        kind = " (пустой)" if p.is_empty else ""
        lines.append(f"{html.quote(prize_service.label(p))}{kind} — {prize_service.chance(p, prizes)}")
    if not prizes:
        lines.append("Призов пока нет.")
    if notes := prize_service.warnings(prizes):
        lines += ["", *notes]
    lines += ["", "Нажмите на приз, чтобы изменить. Новые настройки действуют на следующие спины."]
    rows = [[button(f"{prize_service.label(p)} · {prize_service.chance(p, prizes)}"[:60], f"{CARD}{p.id}")]
            for p in prizes]
    rows.append([button("➕ Добавить приз", ADD)])
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


async def card_view(db: AsyncSession, club: Club, prize: PrizeOption) -> tuple[str, InlineKeyboardMarkup]:
    prizes = await clubs.active_prizes(db, club)
    lines = [
        f"🎁 <b>{html.quote(prize_service.label(prize))}</b>",
        "",
        f"Тип: {'пустой сектор' if prize.is_empty else 'приз с промокодом'}",
        f"Шанс: {prize.weight} (сейчас {prize_service.chance(prize, prizes)})",
    ]
    if not prize.is_empty:
        lines.append(f"Срок промокода: {prize.ttl_days} дн.")
    rows = [[button("✏️ Название", f"{EDIT_TITLE}{prize.id}"), button("🎯 Шанс", f"{EDIT_WEIGHT}{prize.id}")]]
    if not prize.is_empty:
        rows.append([button("⏳ Срок промокода", f"{EDIT_TTL}{prize.id}")])
    rows.append([button("🚫 Выключить", f"{OFF_ASK}{prize.id}")])
    rows.append([button("⬅️ К списку", LIST)])
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


@router.message(F.text == PRIZES_BUTTON, CannotEdit())
async def denied_menu(message: Message) -> None:
    await message.answer("Призы и шансы доступны суперадмину клуба.")


# --- Просмотр --------------------------------------------------------------------------------


@router.message(F.text == PRIZES_BUTTON)
async def open_prizes(message: Message, club: Club, db: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    text, markup = await list_view(db, club)
    await message.answer(text, reply_markup=markup)


@router.callback_query(F.data == LIST)
async def show_list(callback: CallbackQuery, club: Club, db: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    text, markup = await list_view(db, club)
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
        "Сектор исчезнет с колеса. Уже выданные промокоды останутся действительными.",
        markup,
    )


@router.callback_query(F.data.startswith(OFF_YES))
async def confirm_off(callback: CallbackQuery, club: Club, db: AsyncSession, role: Role) -> None:
    try:
        _, change = await prize_service.disable(db, club, parse_id(callback.data, OFF_YES))
    except PrizeError as e:
        await not_found(callback, club, db, e)
        return
    await db.commit()
    text, markup = await list_view(db, club)
    await edit_or_answer(callback, f"✅ Сектор выключен.\n\n{text}", markup)
    await notify_platform(club, role, callback.from_user, change)


# --- Новый приз ------------------------------------------------------------------------------


@router.callback_query(F.data == ADD)
async def add_start(callback: CallbackQuery, club: Club, db: AsyncSession, state: FSMContext) -> None:
    if len(await clubs.active_prizes(db, club)) >= prize_service.MAX_SECTORS:
        await callback.answer(f"На колесе максимум {prize_service.MAX_SECTORS} секторов", show_alert=True)
        return
    await state.set_state(PrizeEdit.new_title)
    with suppress(TelegramAPIError):
        await callback.answer()
    await callback.message.answer("Отправьте приз: эмодзи и название, например "
                                  "<code>🎮 1 час игры</code>.\n/cancel — отменить.")


@router.message(PrizeEdit.new_title, *INPUT)
async def add_title(message: Message, club: Club, db: AsyncSession, state: FSMContext) -> None:
    try:
        emoji, title = prize_service.parse_title(message.text)
    except PrizeError as e:
        await message.answer(f"⛔ {e}")
        return
    total = sum(p.weight for p in await clubs.active_prizes(db, club))
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
    try:
        _, change = await prize_service.create(
            db, club, data["emoji"], data["title"], data["weight"], is_empty, ttl_days)
    except PrizeError as e:
        await target.answer(f"⛔ {e}")
        return
    await db.commit()
    text, markup = await list_view(db, club)
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


@router.callback_query(F.data.startswith(PREFIX))
async def stale_button(callback: CallbackQuery) -> None:
    await callback.answer("Кнопка устарела. Откройте «🎁 Призы и шансы» заново.", show_alert=True)
