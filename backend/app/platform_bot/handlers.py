"""Бот платформы: подключение и настройка клубов без консоли и кода.

Доступен только владельцам платформы (PLATFORM_OWNER_IDS). Всё управление — кнопками:
клубы, подключение бота клуба по токену, призы, суперадмин, подписка, правила, QR, лимит.
Многошаговые действия (ввод названия, токена, суммы) — через состояния FSM.
"""

import io

from aiogram import F, Router, html
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramUnauthorizedError
from aiogram.filters import BaseFilter, Command, CommandStart, StateFilter
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    KeyboardButtonRequestUsers,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    TelegramObject,
)
from aiogram.utils.token import TokenValidationError
import segno
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bots.admin import greet_new_staff
from app.bots.registry import registry
from app.bots.setup import BotSetupError, connect_bot
from app.models import Club, ClubBot, Payment, PrizeOption, Subscription
from app.services import clubs, subscription
from app.services.qr import start_link
from app.services.staff import Person, Role, StaffError, assign_superadmin, is_platform_owner

PICK_SUPERADMIN = 10
COOLDOWNS = [(60, "1 минута (тест)"), (24 * 3600, "1 день"), (7 * 24 * 3600, "7 дней")]
EXTEND_DAYS = [30, 90, 365]
# Правила и текст согласия должны поместиться в одно сообщение Telegram (4096 символов).
RULES_MAX_LENGTH = 3500


class Nav(CallbackData, prefix="p"):
    action: str
    club_id: int = 0
    arg: int = 0


class PF(StatesGroup):
    new_club_name = State()
    bot_token = State()
    prize_title = State()
    prize_weight = State()
    prize_kind = State()
    rules_text = State()
    pay_amount = State()
    super_pick = State()


class OwnerOnly(BaseFilter):
    async def __call__(self, event: TelegramObject) -> bool:
        user = getattr(event, "from_user", None)
        return user is not None and is_platform_owner(user.id)


router = Router(name="platform")
router.message.filter(F.chat.type == "private", OwnerOnly())
router.callback_query.filter(OwnerOnly())

strangers = Router(name="platform-strangers")


@strangers.message()
async def not_owner(message: Message) -> None:
    await message.answer("Это служебный бот платформы. Он доступен только владельцу платформы.")


@strangers.callback_query()
async def not_owner_callback(callback: CallbackQuery) -> None:
    await callback.answer("Нет доступа", show_alert=True)


# --- Клавиатуры и тексты ----------------------------------------------------------------


def btn(text: str, action: str, club_id: int = 0, arg: int = 0) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=Nav(action=action, club_id=club_id, arg=arg).pack())


def kb(*rows: list[InlineKeyboardButton]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[list(r) for r in rows])


def back(club_id: int) -> InlineKeyboardMarkup:
    return kb([btn("⬅️ К клубу", "club", club_id)])


MENU_TEXT = (
    "🛠 <b>Бот платформы</b>\n\n"
    "Здесь подключаются и настраиваются клубы: бот клуба, призы колеса, суперадмин, "
    "подписка, правила и QR для кассы. /cancel — отменить ввод."
)
MENU_KB = kb([btn("🏢 Клубы", "clubs"), btn("➕ Новый клуб", "new")])


def human_seconds(seconds: int) -> str:
    if seconds % 86400 == 0:
        return f"{seconds // 86400} дн."
    if seconds % 3600 == 0:
        return f"{seconds // 3600} ч."
    if seconds % 60 == 0:
        return f"{seconds // 60} мин."
    return f"{seconds} с"


def sub_line(sub: Subscription | None, club: Club) -> str:
    if sub is None:
        return "нет"
    until = subscription.paid_until(sub)
    return f"{sub.status}, оплачено до {clubs.local(until, club)}" if until else str(sub.status)


async def card(db: AsyncSession, club: Club) -> tuple[str, InlineKeyboardMarkup]:
    s = await clubs.club_summary(db, club)
    prizes: list[PrizeOption] = s["prizes"]
    total = sum(p.weight for p in prizes)
    owner = s["owner"]
    warnings = []
    if s["bot"] is None:
        warnings.append("⚠️ Бот не подключён")
    if total == 0:
        warnings.append("⚠️ Нет призов — колесо не крутится")
    elif not any(p.is_empty for p in prizes):
        warnings.append("ℹ️ Нет пустого сектора")
    lines = [
        f"🏢 <b>{html.quote(club.name)}</b> · id {club.id}",
        "",
        f"🤖 Бот: {'@' + s['bot'].username if s['bot'] else 'не подключён'}",
        f"💳 Подписка: {sub_line(s['subscription'], club)}",
        f"🎡 Колесо: раз в {human_seconds(s['config'].cooldown_seconds) if s['config'] else '—'}, "
        f"призов {len(prizes)} (сумма весов {total})",
        f"👑 Суперадмин: {html.quote(Person(owner.telegram_id, owner.first_name, owner.username).title()) if owner else 'не назначен'}"
        f" · админов: {len(s['admins'])}",
        f"📜 Правила: {'заданы' if club.rules_text else 'не заданы'}",
    ]
    if warnings:
        lines += [""] + warnings
    c = club.id
    keyboard = kb(
        [btn("🤖 Бот", "bot", c), btn("🎁 Призы", "prizes", c)],
        [btn("👑 Суперадмин", "super", c), btn("💳 Подписка", "sub", c)],
        [btn("📜 Правила", "rules", c), btn("🔲 QR для кассы", "qr", c)],
        [btn("⏱ Лимит спинов", "limit", c), btn("⬅️ Клубы", "clubs")],
    )
    return "\n".join(lines), keyboard


async def get_club(db: AsyncSession, club_id: int) -> Club | None:
    return await db.get(Club, club_id)


async def show(target: Message | CallbackQuery, text: str, markup=None) -> None:
    """Колбэк — редактируем сообщение с кнопками; обычное сообщение — отвечаем новым."""
    if isinstance(target, CallbackQuery):
        try:
            await target.message.edit_text(text, reply_markup=markup)
        except TelegramBadRequest as e:
            # Повторное нажатие той же кнопки: на экране уже то, что нужно.
            if "message is not modified" not in str(e):
                raise
        await target.answer()
    else:
        await target.answer(text, reply_markup=markup)


async def show_card(target: Message | CallbackQuery, db: AsyncSession, club: Club) -> None:
    text, markup = await card(db, club)
    await show(target, text, markup)


class ClubMissing(BaseFilter):
    """Кнопка ссылается на клуб, которого уже нет (например, старая карточка в чате)."""

    async def __call__(self, callback: CallbackQuery, db: AsyncSession) -> bool:
        try:
            data = Nav.unpack(callback.data)
        except (TypeError, ValueError):
            return False
        return data.club_id > 0 and await db.get(Club, data.club_id) is None


# Объявлен раньше всех остальных обработчиков кнопок, поэтому срабатывает первым.
@router.callback_query(ClubMissing())
async def club_missing(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.answer("Клуб не найден — откройте список клубов заново", show_alert=True)


# --- Меню и список клубов ---------------------------------------------------------------


@router.message(CommandStart())
async def start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(MENU_TEXT, reply_markup=MENU_KB)


@router.message(Command("cancel"))
async def cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Отменено.", reply_markup=ReplyKeyboardRemove())
    await message.answer(MENU_TEXT, reply_markup=MENU_KB)


@router.callback_query(Nav.filter(F.action == "menu"))
async def menu(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await show(callback, MENU_TEXT, MENU_KB)


@router.callback_query(Nav.filter(F.action == "clubs"))
async def club_list(callback: CallbackQuery, db: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    rows = (await db.execute(
        select(Club, ClubBot).outerjoin(ClubBot, ClubBot.club_id == Club.id).order_by(Club.id)
    )).all()
    buttons = [[btn(f"{c.name} · {'@' + b.username if b else 'без бота'}", "club", c.id)] for c, b in rows]
    buttons.append([btn("➕ Новый клуб", "new"), btn("⬅️ Меню", "menu")])
    text = "🏢 <b>Клубы</b>" if rows else "Клубов пока нет — создайте первый."
    await show(callback, text, kb(*buttons))


@router.callback_query(Nav.filter(F.action == "club"))
async def open_club(callback: CallbackQuery, callback_data: Nav, db: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    club = await get_club(db, callback_data.club_id)
    if club is None:
        await callback.answer("Клуб не найден", show_alert=True)
        return
    await show_card(callback, db, club)


# --- Новый клуб -----------------------------------------------------------------------------


@router.callback_query(Nav.filter(F.action == "new"))
async def new_club(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(PF.new_club_name)
    await show(callback, "Как называется клуб? Отправьте название сообщением.", kb([btn("⬅️ Меню", "menu")]))


@router.message(PF.new_club_name, F.text)
async def new_club_name(message: Message, db: AsyncSession, state: FSMContext) -> None:
    name = message.text.strip()
    if not 2 <= len(name) <= 128:
        await message.answer("Название — от 2 до 128 символов. Попробуйте ещё раз.")
        return
    club = await clubs.create_club(db, name)
    await state.clear()
    await message.answer(f"✅ Клуб «{html.quote(club.name)}» создан, пробный период 14 дней.\n"
                         "Дальше: подключите бота, добавьте призы и назначьте суперадмина.")
    await show_card(message, db, club)


# --- Бот клуба ------------------------------------------------------------------------------


@router.callback_query(Nav.filter(F.action == "bot"))
async def bot_menu(callback: CallbackQuery, callback_data: Nav, db: AsyncSession, state: FSMContext) -> None:
    club = await get_club(db, callback_data.club_id)
    club_bot = await db.scalar(select(ClubBot).where(ClubBot.club_id == club.id))
    await state.set_state(PF.bot_token)
    await state.update_data(club_id=club.id)
    if club_bot:
        text = (f"🤖 Подключён @{club_bot.username}.\n\n"
                "Чтобы обновить токен этого же бота (например, после «Revoke» в @BotFather), "
                "отправьте новый токен. Сообщение с токеном я сразу удалю.")
    else:
        text = ("🤖 <b>Подключение бота клуба</b>\n\n"
                "1. Владелец клуба открывает @BotFather → /newbot, имя — название клуба.\n"
                "2. Пришлите сюда токен вида <code>123456789:AA…</code>.\n\n"
                "Сообщение с токеном я сразу удалю, токен хранится только в зашифрованном виде.")
    await show(callback, text, back(club.id))


@router.message(PF.bot_token, F.text)
async def bot_token(message: Message, db: AsyncSession, state: FSMContext) -> None:
    club = await get_club(db, (await state.get_data())["club_id"])
    token = message.text.strip()
    try:
        await message.delete()  # токен не должен оставаться в истории чата
    except TelegramAPIError:
        pass
    try:
        club_bot = await connect_bot(db, club, token)
    except TokenValidationError:
        await message.answer("Это не похоже на токен бота. Пришлите токен из @BotFather целиком.")
        return
    except TelegramUnauthorizedError:
        await message.answer("Telegram отклонил токен. Проверьте его в @BotFather и пришлите снова.")
        return
    except BotSetupError as e:
        await message.answer(f"⛔ {html.quote(str(e))}")
        return
    await state.clear()
    await message.answer(f"✅ Бот @{club_bot.username} подключён: вебхук, кнопка «Играть», "
                         "описание и команды настроены. Токен сохранён в зашифрованном виде.")
    await show_card(message, db, club)


# --- Призы ----------------------------------------------------------------------------------


async def prizes_view(db: AsyncSession, club: Club) -> tuple[str, InlineKeyboardMarkup]:
    prizes = await clubs.active_prizes(db, club)
    total = sum(p.weight for p in prizes) or 1
    lines = [f"🎁 <b>Призы колеса</b> · {html.quote(club.name)}", ""]
    lines += [f"{p.emoji or '🎁'} {html.quote(p.title)}{' (пустой)' if p.is_empty else ''} — "
              f"вес {p.weight}, ≈{p.weight / total:.0%}" for p in prizes] or ["Призов пока нет."]
    lines += ["", "Шанс приза = его вес / сумма весов. Удобно, когда сумма = 100."]
    rows = [[btn(f"🗑 {p.emoji or ''} {p.title}".strip(), "prize_del", club.id, p.id)] for p in prizes]
    rows.append([btn("➕ Добавить приз", "prize_add", club.id), btn("⬅️ К клубу", "club", club.id)])
    return "\n".join(lines), kb(*rows)


@router.callback_query(Nav.filter(F.action == "prizes"))
async def prizes(callback: CallbackQuery, callback_data: Nav, db: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    text, markup = await prizes_view(db, await get_club(db, callback_data.club_id))
    await show(callback, text, markup)


@router.callback_query(Nav.filter(F.action == "prize_del"))
async def prize_del(callback: CallbackQuery, callback_data: Nav, db: AsyncSession) -> None:
    prize = await db.get(PrizeOption, callback_data.arg)
    if prize is None or prize.club_id != callback_data.club_id:
        await callback.answer("Приз не найден", show_alert=True)
        return
    # Не удаляем строку: на приз ссылаются прошлые игры и промокоды. Выключенный не выпадает.
    prize.active = False
    await db.flush()
    text, markup = await prizes_view(db, await get_club(db, callback_data.club_id))
    await show(callback, text, markup)


@router.callback_query(Nav.filter(F.action == "prize_add"))
async def prize_add(callback: CallbackQuery, callback_data: Nav, state: FSMContext) -> None:
    await state.set_state(PF.prize_title)
    await state.update_data(club_id=callback_data.club_id)
    await show(callback, "Отправьте приз: эмодзи и название, например <code>🎮 1 час игры</code>.",
               back(callback_data.club_id))


@router.message(PF.prize_title, F.text)
async def prize_title(message: Message, db: AsyncSession, state: FSMContext) -> None:
    emoji, title = clubs.split_emoji(message.text)
    if not 1 <= len(title) <= 128:
        await message.answer("Название — до 128 символов. Попробуйте ещё раз.")
        return
    data = await state.get_data()
    total = sum(p.weight for p in await clubs.active_prizes(db, await get_club(db, data["club_id"])))
    await state.update_data(emoji=emoji, title=title)
    await state.set_state(PF.prize_weight)
    await message.answer(f"Вес приза (целое число). Сейчас сумма весов: {total}. Например, 20.")


@router.message(PF.prize_weight, F.text)
async def prize_weight(message: Message, state: FSMContext) -> None:
    try:
        weight = int(message.text.strip())
    except ValueError:
        weight = 0
    if not 1 <= weight <= 100_000:
        await message.answer("Нужно целое число больше нуля, например 20.")
        return
    data = await state.update_data(weight=weight)
    await state.set_state(PF.prize_kind)
    await message.answer(
        "Это приз или пустой сектор («повезёт в следующий раз»)?",
        reply_markup=kb([btn("🎁 Приз с промокодом", "prize_kind", data["club_id"], 0),
                         btn("😔 Пустой сектор", "prize_kind", data["club_id"], 1)]),
    )


@router.callback_query(PF.prize_kind, Nav.filter(F.action == "prize_kind"))
async def prize_kind(callback: CallbackQuery, callback_data: Nav, db: AsyncSession, state: FSMContext) -> None:
    data = await state.get_data()
    club = await get_club(db, data["club_id"])
    await clubs.add_prize(db, club, data["title"], data["weight"], data["emoji"], is_empty=bool(callback_data.arg))
    await state.clear()
    text, markup = await prizes_view(db, club)
    await show(callback, "✅ Приз добавлен.\n\n" + text, markup)


# --- Суперадмин -----------------------------------------------------------------------------


@router.callback_query(Nav.filter(F.action == "super"))
async def super_menu(callback: CallbackQuery, callback_data: Nav, db: AsyncSession, state: FSMContext) -> None:
    club = await get_club(db, callback_data.club_id)
    owner = (await clubs.club_summary(db, club))["owner"]
    current = Person(owner.telegram_id, owner.first_name, owner.username).title() if owner else "не назначен"
    await state.set_state(PF.super_pick)
    await state.update_data(club_id=club.id)
    await callback.answer()
    await callback.message.answer(
        f"👑 Суперадмин клуба «{html.quote(club.name)}»: {html.quote(current)}.\n\n"
        "Нажмите кнопку внизу и выберите человека из контактов. Прежний суперадмин станет админом. "
        "Суперадмин сам добавляет админов в боте клуба через /admin.",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="👑 Выбрать суперадмина", request_users=KeyboardButtonRequestUsers(
                request_id=PICK_SUPERADMIN, user_is_bot=False, max_quantity=1,
                request_name=True, request_username=True))],
                [KeyboardButton(text="/cancel")]],
            resize_keyboard=True, one_time_keyboard=True,
        ),
    )


@router.message(PF.super_pick, F.users_shared)
async def super_picked(message: Message, db: AsyncSession, state: FSMContext) -> None:
    club = await get_club(db, (await state.get_data())["club_id"])
    user = message.users_shared.users[0]
    person = Person(user.user_id, user.first_name, user.username)
    try:
        _, previous = await assign_superadmin(db, club, Role.PLATFORM_OWNER, person)
    except StaffError as e:
        await message.answer(f"⛔ {html.quote(str(e))}", reply_markup=ReplyKeyboardRemove())
        return
    await state.clear()
    club_bot = await db.scalar(select(ClubBot).where(ClubBot.club_id == club.id))
    note = ""
    if club_bot:
        await greet_new_staff(registry.get(club_bot), club, person.telegram_id, "суперадмин")
        note = f"\nЕсли он уже писал @{club_bot.username}, ему пришло уведомление и /admin в меню."
    else:
        note = "\nБот клуба ещё не подключён — уведомление не отправлено. /admin заработает после подключения бота."
    if previous is not None:
        note += "\nПрежний суперадмин теперь админ."
    await message.answer(f"✅ {html.quote(person.title())} — суперадмин клуба.{note}",
                         reply_markup=ReplyKeyboardRemove())
    await show_card(message, db, club)


# --- Подписка -------------------------------------------------------------------------------


@router.callback_query(Nav.filter(F.action == "sub"))
async def sub_menu(callback: CallbackQuery, callback_data: Nav, db: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    club = await get_club(db, callback_data.club_id)
    sub = await db.get(Subscription, club.id)
    payments = list(await db.scalars(
        select(Payment).where(Payment.club_id == club.id).order_by(Payment.paid_at.desc()).limit(3)
    ))
    lines = [f"💳 <b>Подписка</b> · {html.quote(club.name)}", "", sub_line(sub, club)]
    if payments:
        lines += ["", "Последние оплаты:"] + [
            f"{clubs.local(p.paid_at, club)} — {p.amount} {p.currency} {html.quote(p.comment or '')}"
            for p in payments]
    await show(callback, "\n".join(lines), kb(
        [btn(f"+{d} дней", "sub_add", club.id, d) for d in EXTEND_DAYS],
        [btn("⛔ Отменить подписку", "sub_cancel", club.id), btn("⬅️ К клубу", "club", club.id)],
    ))


@router.callback_query(Nav.filter(F.action == "sub_add"))
async def sub_add(callback: CallbackQuery, callback_data: Nav, state: FSMContext) -> None:
    await state.set_state(PF.pay_amount)
    await state.update_data(club_id=callback_data.club_id, days=callback_data.arg)
    await show(callback, f"Продление на {callback_data.arg} дней.\n\n"
               "Отправьте сумму оплаты в тенге (например, <code>20000</code>) — она запишется в оплаты. "
               "Или «-», если продлеваете без оплаты.", back(callback_data.club_id))


@router.message(PF.pay_amount, F.text)
async def pay_amount(message: Message, db: AsyncSession, state: FSMContext) -> None:
    data = await state.get_data()
    raw = message.text.strip()
    amount = None
    if raw != "-":
        try:
            amount = subscription.parse_amount(raw)
        except ValueError as e:
            await message.answer(f"{e}. Отправьте сумму ещё раз или «-» без оплаты.")
            return
    club = await get_club(db, data["club_id"])
    sub = await db.get(Subscription, club.id)
    if sub is None:
        sub = subscription.new_trial(club.id)
        db.add(sub)
        await db.flush()
    await subscription.extend(db, sub, data["days"], amount, "через бот платформы" if amount else None)
    await state.clear()
    await message.answer(f"✅ Подписка продлена: {sub_line(sub, club)}.")
    await show_card(message, db, club)


@router.callback_query(Nav.filter(F.action == "sub_cancel"))
async def sub_cancel(callback: CallbackQuery, callback_data: Nav) -> None:
    c = callback_data.club_id
    await show(callback, "Отменить подписку? Колесо в клубе перестанет крутиться, "
               "выданные промокоды по-прежнему можно будет погасить.",
               kb([btn("Да, отменить", "sub_cancel_yes", c), btn("Нет", "sub", c)]))


@router.callback_query(Nav.filter(F.action == "sub_cancel_yes"))
async def sub_cancel_yes(callback: CallbackQuery, callback_data: Nav, db: AsyncSession) -> None:
    club = await get_club(db, callback_data.club_id)
    sub = await db.get(Subscription, club.id)
    if sub is not None:
        await subscription.cancel(db, sub)
    await show_card(callback, db, club)


# --- Правила, QR, лимит ------------------------------------------------------------------


@router.callback_query(Nav.filter(F.action == "rules"))
async def rules_menu(callback: CallbackQuery, callback_data: Nav, db: AsyncSession, state: FSMContext) -> None:
    club = await get_club(db, callback_data.club_id)
    await state.set_state(PF.rules_text)
    await state.update_data(club_id=club.id)
    current = html.quote(club.rules_text) if club.rules_text else "не заданы"
    await show(callback, f"📜 Сейчас правила:\n\n{current}\n\nОтправьте новый текст правил одним сообщением.",
               back(club.id))


@router.message(PF.rules_text, F.text)
async def rules_text(message: Message, db: AsyncSession, state: FSMContext) -> None:
    text = message.text.strip()
    if len(text) > RULES_MAX_LENGTH:
        await message.answer(f"Правила — до {RULES_MAX_LENGTH} символов, сейчас {len(text)}. "
                             "Сократите текст и отправьте ещё раз.")
        return
    club = await get_club(db, (await state.get_data())["club_id"])
    club.rules_text = text
    await db.flush()
    await state.clear()
    await message.answer("✅ Правила сохранены — гости видят их в Mini App и по /rules.")
    await show_card(message, db, club)


@router.callback_query(Nav.filter(F.action == "qr"))
async def qr(callback: CallbackQuery, callback_data: Nav, db: AsyncSession) -> None:
    club = await get_club(db, callback_data.club_id)
    club_bot = await db.scalar(select(ClubBot).where(ClubBot.club_id == club.id))
    if club_bot is None:
        await callback.answer("Сначала подключите бота клуба", show_alert=True)
        return
    link = start_link(club_bot.username)
    buffer = io.BytesIO()
    segno.make(link, error="m").save(buffer, kind="png", scale=20, border=4)
    await callback.answer()
    await callback.message.answer_document(
        BufferedInputFile(buffer.getvalue(), filename=f"qr-{club_bot.username}.png"),
        caption=f"🔲 QR для кассы клуба «{html.quote(club.name)}»\n{link}\n\n"
                "Файлом, чтобы не потерять качество при печати.",
    )


@router.callback_query(Nav.filter(F.action == "limit"))
async def limit_menu(callback: CallbackQuery, callback_data: Nav) -> None:
    c = callback_data.club_id
    await show(callback, "⏱ Как часто гость может крутить колесо?",
               kb(*[[btn(label, "limit_set", c, seconds)] for seconds, label in COOLDOWNS],
                  [btn("⬅️ К клубу", "club", c)]))


@router.callback_query(Nav.filter(F.action == "limit_set"))
async def limit_set(callback: CallbackQuery, callback_data: Nav, db: AsyncSession) -> None:
    if callback_data.arg not in {s for s, _ in COOLDOWNS}:
        await callback.answer("Недопустимое значение", show_alert=True)
        return
    club = await get_club(db, callback_data.club_id)
    await clubs.set_cooldown(db, club, callback_data.arg)
    await show_card(callback, db, club)


# --- Ввод не того типа в состоянии ------------------------------------------------------


@router.message(StateFilter("*"))
async def unexpected(message: Message, state: FSMContext) -> None:
    if await state.get_state() is None:
        await message.answer(MENU_TEXT, reply_markup=MENU_KB)
    else:
        await message.answer("Жду другой ответ. Отправьте его или нажмите /cancel.")
