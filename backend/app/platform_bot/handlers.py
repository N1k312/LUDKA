"""Бот платформы: подключение и настройка клубов без консоли и кода.

Доступен только владельцам платформы (PLATFORM_OWNER_IDS). Всё управление — кнопками:
клубы, подключение бота клуба по токену, призы, суперадмин, подписка, правила, QR, лимит.
Многошаговые действия (ввод названия, токена, суммы) — через состояния FSM.
"""

import io
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

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
from app.bots.notify import notify_club_owner
from app.bots.registry import registry
from app.bots.setup import BotSetupError, connect_bot
from app.models import (
    Club,
    ClubBot,
    ClubStatus,
    GameType,
    Payment,
    PrizeOption,
    Subscription,
    SubscriptionStatus,
)
from app.services.wheel import GAME_ICONS, GAME_TITLES, GAMES
from app.services import branding, clubs, subscription
from app.services import prizes as prize_service
from app.services.prizes import PrizeError, PrizeNotFound
from app.services.qr import start_link
from app.services.staff import Person, Role, StaffError, assign_superadmin, is_platform_owner

PICK_SUPERADMIN = 10
PICK_RESET_GUEST = 11
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
    prize_edit = State()
    rules_text = State()
    pay_amount = State()
    super_pick = State()
    brand_hub = State()
    brand_prefix = State()
    brand_text = State()
    brand_image = State()
    reset_guest_pick = State()
    wipe_confirm = State()


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
    "Здесь подключаются и настраиваются клубы: бот клуба, игры и призы, суперадмин, "
    "подписка, правила и QR для кассы. /cancel — отменить ввод."
)
MENU_KB = kb([btn("🏢 Клубы", "clubs"), btn("💳 К оплате", "billing")], [btn("➕ Новый клуб", "new")])


def human_seconds(seconds: int) -> str:
    if seconds % 86400 == 0:
        return f"{seconds // 86400} дн."
    if seconds % 3600 == 0:
        return f"{seconds // 3600} ч."
    if seconds % 60 == 0:
        return f"{seconds // 60} мин."
    return f"{seconds} с"


def sub_line(sub: Subscription | None, club: Club) -> str:
    """Подписка словами. Неоплата игры не останавливает — доступ отключает владелец вручную."""
    if sub is None:
        return "нет"
    now = datetime.now(UTC)
    until = subscription.paid_until(sub)
    status = subscription.compute_status(sub, now)
    if status == SubscriptionStatus.CANCELED:
        return "отменена — играть нельзя, продлите, чтобы включить"
    if status == SubscriptionStatus.TRIAL:
        return f"пробный период до {clubs.local(until, club)}"
    if status == SubscriptionStatus.ACTIVE:
        return f"оплачено до {clubs.local(until, club)}"
    since = f"с {clubs.local(until, club)} " if until else ""
    return f"⚠️ не оплачено {since}({subscription.unpaid_days(sub, now)} дн.), игры работают"


def access_mark(club: Club, sub: Subscription | None) -> str:
    """Значок клуба в списках: ⏸ доступ отключён, ⚠️ не оплачено, ⏳ скоро к оплате."""
    if club.status != ClubStatus.ACTIVE:
        return "⏸"
    if sub is None:
        return "⚠️"
    now = datetime.now(UTC)
    if subscription.compute_status(sub, now) in (SubscriptionStatus.PAST_DUE, SubscriptionStatus.CANCELED):
        return "⚠️"
    until = subscription.paid_until(sub)
    return "⏳" if until and until - now < subscription.DUE_SOON else "🟢"


async def card(db: AsyncSession, club: Club) -> tuple[str, InlineKeyboardMarkup]:
    s = await clubs.club_summary(db, club)
    owner = s["owner"]
    warnings = []
    disabled = club.status != ClubStatus.ACTIVE
    if disabled:
        warnings.append("⏸ Доступ отключён: гости не могут играть. Данные и бот сохранены.")
    elif s["subscription"] and subscription.compute_status(
            s["subscription"], datetime.now(UTC)) == SubscriptionStatus.PAST_DUE:
        warnings.append("⚠️ Не оплачено. Игры работают, пока вы не отключите доступ.")
    if s["bot"] is None:
        warnings.append("⚠️ Бот не подключён")
    game_lines = []
    for game, config in (await clubs.game_configs(db, club)).items():
        prizes = await clubs.active_prizes(db, club, game)
        total = sum(p.weight for p in prizes)
        name = f"{GAME_ICONS[game]} {GAME_TITLES[game]}"
        if not config.enabled:
            game_lines.append(f"{name}: выключена, призов {len(prizes)}")
            continue
        game_lines.append(f"{name}: раз в {human_seconds(config.cooldown_seconds)}, "
                          f"призов {len(prizes)} (сумма весов {total})")
        if total == 0:
            warnings.append(f"⚠️ Нет призов в игре «{GAME_TITLES[game]}» — она не работает")
        elif not any(p.is_empty for p in prizes):
            warnings.append(f"ℹ️ Нет пустого сектора в игре «{GAME_TITLES[game]}»")
    lines = [
        f"🏢 <b>{html.quote(club.name)}</b> · id {club.id}",
        "",
        f"🔌 Доступ: {'⏸ отключён' if disabled else '✅ включён'}",
        f"🤖 Бот: {'@' + s['bot'].username if s['bot'] else 'не подключён'}",
        f"💳 Подписка: {sub_line(s['subscription'], club)}",
        *game_lines,
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
        [btn("🎮 Игры", "games", c), btn("🎨 Оформление", "brand", c)],
        [btn("▶️ Вернуть доступ", "access_on", c) if disabled else btn("⏸ Отключить доступ", "access_off", c)],
        [btn("🧹 Сброс данных", "data", c), btn("⬅️ Клубы", "clubs")],
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
        select(Club, ClubBot, Subscription)
        .outerjoin(ClubBot, ClubBot.club_id == Club.id)
        .outerjoin(Subscription, Subscription.club_id == Club.id)
        .order_by(Club.id)
    )).all()
    buttons = [[btn(f"{access_mark(c, s)} {c.name} · {'@' + b.username if b else 'без бота'}", "club", c.id)]
               for c, b, s in rows]
    buttons.append([btn("➕ Новый клуб", "new"), btn("⬅️ Меню", "menu")])
    text = ("🏢 <b>Клубы</b>\n\n🟢 оплачено · ⏳ скоро к оплате · ⚠️ не оплачено · ⏸ доступ отключён"
            if rows else "Клубов пока нет — создайте первый.")
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


def game_index(game: GameType) -> int:
    return GAMES.index(GameType(game))


def game_by_index(index: int) -> GameType | None:
    return GAMES[index] if 0 <= index < len(GAMES) else None


async def prizes_view(db: AsyncSession, club: Club, game: GameType = GameType.WHEEL) -> tuple[str, InlineKeyboardMarkup]:
    prizes = await clubs.active_prizes(db, club, game)
    total = sum(p.weight for p in prizes) or 1
    lines = [f"🎁 <b>Призы · {GAME_ICONS[game]} {GAME_TITLES[game]}</b> · {html.quote(club.name)}", ""]
    lines += [f"{prize_service.RARITY_MARKS[prize_service.rarity(p, prizes)]} {p.emoji or '🎁'} "
              f"{html.quote(p.title)}{' (пустой)' if p.is_empty else ''} — "
              f"вес {p.weight}, ≈{p.weight / total:.0%}" for p in prizes] or ["Призов пока нет."]
    lines += ["", "Шанс приза = его вес / сумма весов. Удобно, когда сумма = 100.",
              "Гости видят только цвет: 🔴 до 5% · 🔵 до 20% · 🟢 чаще."]
    i = game_index(game)
    rows = [[btn(f"{'• ' if g == game else ''}{GAME_ICONS[g]} {GAME_TITLES[g]}", "prizes", club.id, k)
             for k, g in enumerate(GAMES)]]
    rows += [[btn(f"✏️ {prize_service.label(p)}"[:50], "prize_card", club.id, p.id),
              btn("🗑", "prize_del", club.id, p.id)] for p in prizes]
    rows.append([btn("➕ Добавить приз", "prize_add", club.id, i), btn("⬅️ К клубу", "club", club.id)])
    return "\n".join(lines), kb(*rows)


def prize_card_view(club: Club, prize: PrizeOption, prizes: list[PrizeOption]) -> tuple[str, InlineKeyboardMarkup]:
    lines = [f"🎁 <b>{html.quote(prize_service.label(prize))}</b> · {html.quote(club.name)}", "",
             f"Тип: {'пустой сектор' if prize.is_empty else 'приз с промокодом'}",
             f"Шанс: {prize.weight} (сейчас {prize_service.chance(prize, prizes)})"]
    rows = [[btn("✏️ Название", "prize_t", club.id, prize.id), btn("🎯 Шанс", "prize_w", club.id, prize.id)]]
    if not prize.is_empty:
        lines.append(f"Срок промокода: {prize.ttl_days} дн.")
        rows.append([btn("⏳ Срок промокода", "prize_d", club.id, prize.id)])
    rows.append([btn("🗑 Выключить", "prize_del", club.id, prize.id),
                 btn("⬅️ К призам", "prizes", club.id, game_index(prize.game_type))])
    return "\n".join(lines), kb(*rows)


PRIZE_FIELDS = {
    "prize_t": ("rename", "Отправьте новое название, можно с эмодзи: <code>🎮 2 часа игры</code>."),
    "prize_w": ("set_weight", "Отправьте новый шанс числом, например <code>20</code>."),
    "prize_d": ("set_ttl", "Сколько дней действует промокод? Например, <code>14</code>. Выданные коды не изменятся."),
}


@router.callback_query(Nav.filter(F.action == "prize_card"))
async def prize_card(callback: CallbackQuery, callback_data: Nav, db: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    club = await get_club(db, callback_data.club_id)
    try:
        prize = await prize_service.get_active(db, club, callback_data.arg)
    except PrizeError as e:
        await callback.answer(str(e), show_alert=True)
        return
    text, markup = prize_card_view(club, prize, await clubs.active_prizes(db, club, prize.game_type))
    await show(callback, text, markup)


@router.callback_query(Nav.filter(F.action.in_(PRIZE_FIELDS)))
async def prize_edit_ask(callback: CallbackQuery, callback_data: Nav, db: AsyncSession, state: FSMContext) -> None:
    club = await get_club(db, callback_data.club_id)
    try:
        prize = await prize_service.get_active(db, club, callback_data.arg)
    except PrizeError as e:
        await callback.answer(str(e), show_alert=True)
        return
    operation, question = PRIZE_FIELDS[callback_data.action]
    await state.set_state(PF.prize_edit)
    await state.update_data(club_id=club.id, prize_id=prize.id, operation=operation)
    await show(callback, f"{html.quote(prize_service.label(prize))}\n\n{question}\n/cancel — отменить.",
               kb([btn("⬅️ К призу", "prize_card", club.id, prize.id)]))


@router.message(PF.prize_edit, F.text)
async def prize_edit_save(message: Message, db: AsyncSession, state: FSMContext) -> None:
    data = await state.get_data()
    club = await get_club(db, data["club_id"])
    try:
        prize, _ = await getattr(prize_service, data["operation"])(db, club, data["prize_id"], message.text)
    except PrizeNotFound as e:
        await state.clear()
        await message.answer(f"⛔ {e}")
        return
    except PrizeError as e:
        await message.answer(f"⛔ {e}")
        return
    await state.clear()
    await db.commit()
    text, markup = prize_card_view(club, prize, await clubs.active_prizes(db, club, prize.game_type))
    await message.answer(f"✅ Сохранено.\n\n{text}", reply_markup=markup)


@router.callback_query(Nav.filter(F.action == "prizes"))
async def prizes(callback: CallbackQuery, callback_data: Nav, db: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    game = game_by_index(callback_data.arg) or GameType.WHEEL
    text, markup = await prizes_view(db, await get_club(db, callback_data.club_id), game)
    await show(callback, text, markup)


@router.callback_query(Nav.filter(F.action == "prize_del"))
async def prize_del(callback: CallbackQuery, callback_data: Nav, db: AsyncSession) -> None:
    club = await get_club(db, callback_data.club_id)
    try:
        # Строка не удаляется: на приз ссылаются прошлые игры и промокоды.
        prize, _ = await prize_service.disable(db, club, callback_data.arg)
    except PrizeError as e:
        await callback.answer(str(e), show_alert=True)
        return
    await db.commit()
    text, markup = await prizes_view(db, club, GameType(prize.game_type))
    await show(callback, text, markup)


@router.callback_query(Nav.filter(F.action == "prize_add"))
async def prize_add(callback: CallbackQuery, callback_data: Nav, state: FSMContext) -> None:
    game = game_by_index(callback_data.arg) or GameType.WHEEL
    await state.set_state(PF.prize_title)
    await state.update_data(club_id=callback_data.club_id, game=game.value)
    await show(callback, f"{GAME_ICONS[game]} {GAME_TITLES[game]}: отправьте приз — эмодзи и название, "
               "например <code>🎮 1 час игры</code>.", back(callback_data.club_id))


@router.message(PF.prize_title, F.text)
async def prize_title(message: Message, db: AsyncSession, state: FSMContext) -> None:
    emoji, title = clubs.split_emoji(message.text)
    if not 1 <= len(title) <= 128:
        await message.answer("Название — до 128 символов. Попробуйте ещё раз.")
        return
    data = await state.get_data()
    game = GameType(data.get("game", GameType.WHEEL))
    total = sum(p.weight for p in await clubs.active_prizes(db, await get_club(db, data["club_id"]), game))
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
    await state.clear()
    game = GameType(data.get("game", GameType.WHEEL))
    try:
        await prize_service.create(db, club, data["emoji"], data["title"], data["weight"], bool(callback_data.arg),
                                   game=game)
    except PrizeError as e:
        await callback.answer(str(e), show_alert=True)
        return
    await db.commit()
    text, markup = await prizes_view(db, club, game)
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
    lines += ["", "Подписка продлевается сама: если клуб не заплатил, игры продолжают работать, "
              "пока вы не отключите доступ в карточке клуба."]
    await show(callback, "\n".join(lines), kb(
        [btn(f"+{d} дней", "sub_add", club.id, d) for d in EXTEND_DAYS],
        [btn("⬅️ К клубу", "club", club.id)],
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


# --- Доступ клуба: отключить (например, за неоплату) и вернуть ---------------------------------
# «sub_cancel», «sub_cancel_yes» — кнопки старых экранов «Отменить подписку»: теперь это отключение доступа.

ACCESS_OFF_TEXT = (
    "⏸ Доступ к играм клуба приостановлен владельцем платформы.\n\n"
    "Гости пока не могут играть. Уже выданные промокоды можно погасить как обычно. "
    "По вопросам оплаты свяжитесь с владельцем платформы."
)
ACCESS_ON_TEXT = "▶️ Доступ клуба восстановлен — игры снова работают."


@router.callback_query(Nav.filter(F.action.in_({"access_off", "sub_cancel"})))
async def access_off_ask(callback: CallbackQuery, callback_data: Nav, db: AsyncSession) -> None:
    club = await get_club(db, callback_data.club_id)
    c = club.id
    await show(callback,
               f"⏸ Отключить доступ клуба «{html.quote(club.name)}»?\n\n"
               "• Гости не смогут играть — в Mini App будет «Акция временно недоступна».\n"
               "• Уже выданные промокоды админ сможет погасить.\n"
               "• Бот, гости, призы, сотрудники и история сохранятся.\n"
               "• Суперадмин клуба получит сообщение.\n\n"
               "Вернуть доступ можно в любой момент кнопкой «▶️ Вернуть доступ».",
               kb([btn("⏸ Да, отключить", "access_off_yes", c), btn("Нет", "club", c)]))


@router.callback_query(Nav.filter(F.action.in_({"access_off_yes", "sub_cancel_yes"})))
async def access_off(callback: CallbackQuery, callback_data: Nav, db: AsyncSession) -> None:
    await change_access(callback, db, callback_data.club_id, enabled=False)


@router.callback_query(Nav.filter(F.action == "access_on"))
async def access_on(callback: CallbackQuery, callback_data: Nav, db: AsyncSession) -> None:
    await change_access(callback, db, callback_data.club_id, enabled=True)


async def change_access(callback: CallbackQuery, db: AsyncSession, club_id: int, enabled: bool) -> None:
    club = await get_club(db, club_id)
    changed = await clubs.set_access(db, club, enabled)
    await db.commit()  # сначала база, потом Telegram
    text, markup = await card(db, club)
    note = ("▶️ Доступ возвращён." if enabled else "⏸ Доступ отключён.") if changed else "Без изменений."
    await show(callback, f"{note}\n\n{text}", markup)
    if changed:
        await notify_club_owner(db, club, ACCESS_ON_TEXT if enabled else ACCESS_OFF_TEXT)


# --- К оплате ---------------------------------------------------------------------------------


@router.callback_query(Nav.filter(F.action == "billing"))
async def billing(callback: CallbackQuery, db: AsyncSession, state: FSMContext) -> None:
    """Кому выставить счёт: не оплачено, срок истекает в ближайшие 3 дня, доступ отключён."""
    await state.clear()
    now = datetime.now(UTC)
    rows = (await db.execute(
        select(Club, Subscription).outerjoin(Subscription, Subscription.club_id == Club.id).order_by(Club.id)
    )).all()
    lines, buttons = ["💳 <b>К оплате</b>", ""], []
    for club, sub in rows:
        mark = access_mark(club, sub)
        if mark == "🟢":
            continue
        status = "доступ отключён" if mark == "⏸" else sub_line(sub, club).removeprefix("⚠️ ")
        lines.append(f"{mark} {html.quote(club.name)} — {status}")
        buttons.append([btn(f"{mark} {club.name}"[:60], "club", club.id)])
    if not buttons:
        lines.append("Все клубы оплачены, ближайшие 3 дня платежей нет. 🎉")
    lines += ["", "⏳ — срок оплаты в ближайшие 3 дня, ⚠️ — не оплачено (игры работают), ⏸ — доступ отключён. "
              f"Обновлено {now.astimezone(ZoneInfo('Asia/Almaty')):%d.%m %H:%M}."]
    buttons.append([btn("⬅️ Меню", "menu")])
    await show(callback, "\n".join(lines), kb(*buttons))


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


# --- Игры: включение и лимит ----------------------------------------------------------------
# game_cd: arg = секунды * 10 + номер игры (в Nav только одно число).


async def games_view(db: AsyncSession, club: Club) -> tuple[str, InlineKeyboardMarkup]:
    configs = await clubs.game_configs(db, club)
    lines = [f"🎮 <b>Игры</b> · {html.quote(club.name)}", ""]
    rows = []
    for k, game in enumerate(GAMES):
        config = configs[game]
        count = len(await clubs.active_prizes(db, club, game))
        lines.append(f"{GAME_ICONS[game]} {GAME_TITLES[game]}: {'✅ включена' if config.enabled else '⛔ выключена'}, "
                     f"раз в {human_seconds(config.cooldown_seconds)}, призов {count}")
        rows.append([btn(f"{GAME_ICONS[game]} {'Выключить' if config.enabled else 'Включить'}", "game_on", club.id, k),
                     btn("🎁 Призы", "prizes", club.id, k)])
        rows.append([btn(f"{'✓ ' if config.cooldown_seconds == s else ''}{label}", "game_cd", club.id, s * 10 + k)
                     for s, label in COOLDOWNS])
    rows.append([btn("⬅️ К клубу", "club", club.id)])
    return "\n".join(lines), kb(*rows)


# «limit», «limit_set» — кнопки старых карточек «⏱ Лимит спинов»: ведут на «🎮 Игры».
@router.callback_query(Nav.filter(F.action.in_({"games", "limit", "limit_set"})))
async def games_menu(callback: CallbackQuery, callback_data: Nav, db: AsyncSession) -> None:
    club = await get_club(db, callback_data.club_id)
    text, markup = await games_view(db, club)
    await db.commit()
    await show(callback, text, markup)


@router.callback_query(Nav.filter(F.action == "game_on"))
async def game_toggle(callback: CallbackQuery, callback_data: Nav, db: AsyncSession) -> None:
    game = game_by_index(callback_data.arg)
    if game is None:
        await callback.answer("Недопустимое значение", show_alert=True)
        return
    club = await get_club(db, callback_data.club_id)
    config = (await clubs.game_configs(db, club))[game]
    try:
        await clubs.set_game_enabled(db, club, game, not config.enabled)
    except clubs.GameSettingError as e:
        await callback.answer(str(e), show_alert=True)
        return
    await db.commit()
    text, markup = await games_view(db, club)
    await show(callback, text, markup)


@router.callback_query(Nav.filter(F.action == "game_cd"))
async def game_cooldown(callback: CallbackQuery, callback_data: Nav, db: AsyncSession) -> None:
    seconds, index = divmod(callback_data.arg, 10)
    game = game_by_index(index)
    if game is None or seconds not in {s for s, _ in COOLDOWNS}:
        await callback.answer("Недопустимое значение", show_alert=True)
        return
    club = await get_club(db, callback_data.club_id)
    await clubs.set_cooldown(db, club, seconds, game)
    await db.commit()
    text, markup = await games_view(db, club)
    await show(callback, text, markup)


# --- Оформление клуба ---------------------------------------------------------------------

PALETTE_KEYS = list(branding.PALETTES)
TEXT_KEYS = list(branding.TEXT_KINDS)
IMAGE_TITLES = {"logo": "Логотип", "banner": "Баннер приветствия"}


def brand_view(club: Club) -> tuple[str, InlineKeyboardMarkup]:
    b = branding.resolved(club)
    texts = branding.data(club).get("texts") or {}
    lines = [
        f"🎨 <b>Оформление</b> · {html.quote(club.name)}",
        "",
        f"Палитра: {branding.PALETTES[b['palette']]['title']}",
        f"Надпись в центре колеса: {html.quote(b['hub_label'])}",
        f"Префикс промокодов: {b['code_prefix']} (новые коды: <code>{b['code_prefix']}-XXXXXX</code>)",
        f"Логотип: {'есть' if branding.image_path(club, 'logo') else 'нет'} · "
        f"баннер: {'есть' if branding.image_path(club, 'banner') else 'нет'}",
        "Тексты: " + ", ".join(f"{title.lower()} — {'свой' if texts.get(k) else 'стандартный'}"
                               for k, title in branding.TEXT_KINDS.items()),
        "",
        "Гости увидят изменения при следующем открытии Mini App.",
    ]
    c = club.id
    keyboard = kb(
        [btn("🎨 Палитра", "brand_palettes", c), btn("🔤 Надпись колеса", "brand_hub", c)],
        [btn("🏷 Префикс кодов", "brand_prefix", c), btn("✍️ Тексты", "brand_texts", c)],
        [btn("🖼 Логотип", "brand_image", c, 0), btn("🌅 Баннер", "brand_image", c, 1)],
        [btn("↩️ Сбросить", "brand_reset", c), btn("⬅️ К клубу", "club", c)],
    )
    return "\n".join(lines), keyboard


async def show_brand(target: Message | CallbackQuery, club: Club, note: str = "") -> None:
    text, markup = brand_view(club)
    await show(target, f"{note}\n\n{text}" if note else text, markup)


@router.callback_query(Nav.filter(F.action == "brand"))
async def brand_menu(callback: CallbackQuery, callback_data: Nav, db: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    await show_brand(callback, await get_club(db, callback_data.club_id))


@router.callback_query(Nav.filter(F.action == "brand_palettes"))
async def brand_palettes(callback: CallbackQuery, callback_data: Nav, db: AsyncSession) -> None:
    club = await get_club(db, callback_data.club_id)
    current = branding.resolved(club)["palette"]
    rows = [[btn(f"{'✅ ' if key == current else ''}{p['title']}", "brand_palette", club.id, i)]
            for i, (key, p) in enumerate(branding.PALETTES.items())]
    rows.append([btn("⬅️ Оформление", "brand", club.id)])
    await show(callback, "🎨 Выберите палитру колеса и кнопок Mini App:", kb(*rows))


@router.callback_query(Nav.filter(F.action == "brand_palette"))
async def brand_palette(callback: CallbackQuery, callback_data: Nav, db: AsyncSession) -> None:
    if not 0 <= callback_data.arg < len(PALETTE_KEYS):
        await callback.answer("Недопустимое значение", show_alert=True)
        return
    club = await get_club(db, callback_data.club_id)
    branding.set_palette(club, PALETTE_KEYS[callback_data.arg])
    await db.flush()
    await show_brand(callback, club, "✅ Палитра сохранена.")


@router.callback_query(Nav.filter(F.action == "brand_hub"))
async def brand_hub(callback: CallbackQuery, callback_data: Nav, state: FSMContext) -> None:
    await state.set_state(PF.brand_hub)
    await state.update_data(club_id=callback_data.club_id)
    await show(callback, f"🔤 Отправьте надпись для центра колеса — до {branding.HUB_LABEL_MAX} символов, "
                         "например <code>CA</code> или <code>⚡</code>.",
               kb([btn("⬅️ Оформление", "brand", callback_data.club_id)]))


@router.callback_query(Nav.filter(F.action == "brand_prefix"))
async def brand_prefix(callback: CallbackQuery, callback_data: Nav, state: FSMContext) -> None:
    await state.set_state(PF.brand_prefix)
    await state.update_data(club_id=callback_data.club_id)
    await show(callback, "🏷 Отправьте префикс промокодов — 2–4 латинские буквы, например <code>CA</code>.\n\n"
                         "Уже выданные коды не меняются и продолжают гаситься: при ручном вводе "
                         "старого кода админ вводит его целиком, с прежним префиксом.",
               kb([btn("⬅️ Оформление", "brand", callback_data.club_id)]))


@router.callback_query(Nav.filter(F.action == "brand_texts"))
async def brand_texts(callback: CallbackQuery, callback_data: Nav, db: AsyncSession) -> None:
    club = await get_club(db, callback_data.club_id)
    texts = branding.data(club).get("texts") or {}
    lines = ["✍️ <b>Тексты</b>", ""] + [
        f"<b>{title}:</b> {html.quote(texts[k]) if texts.get(k) else 'стандартный'}"
        for k, title in branding.TEXT_KINDS.items()]
    rows = [[btn(f"✍️ {title}", "brand_text", club.id, i)] for i, title in enumerate(branding.TEXT_KINDS.values())]
    rows.append([btn("⬅️ Оформление", "brand", club.id)])
    await show(callback, "\n".join(lines), kb(*rows))


@router.callback_query(Nav.filter(F.action == "brand_text"))
async def brand_text(callback: CallbackQuery, callback_data: Nav, state: FSMContext) -> None:
    if not 0 <= callback_data.arg < len(TEXT_KEYS):
        await callback.answer("Недопустимое значение", show_alert=True)
        return
    kind = TEXT_KEYS[callback_data.arg]
    await state.set_state(PF.brand_text)
    await state.update_data(club_id=callback_data.club_id, kind=kind)
    hints = {
        "welcome": "Его гость видит в боте после регистрации.",
        "win": "Его гость видит в Mini App, когда выигрывает приз.",
        "lose": "Его гость видит в Mini App, когда выпадает пустой сектор.",
    }
    await show(callback, f"✍️ {branding.TEXT_KINDS[kind]}: отправьте текст до {branding.TEXT_MAX} символов. "
                         f"{hints[kind]}",
               kb([btn("⬅️ Оформление", "brand", callback_data.club_id)]))


@router.callback_query(Nav.filter(F.action == "brand_image"))
async def brand_image(callback: CallbackQuery, callback_data: Nav, state: FSMContext) -> None:
    kind = branding.IMAGE_KINDS[1 if callback_data.arg else 0]
    await state.set_state(PF.brand_image)
    await state.update_data(club_id=callback_data.club_id, kind=kind)
    hint = ("Квадратный, лучше на прозрачном или однотонном фоне — он показывается в шапке Mini App."
            if kind == "logo" else "Горизонтальный, примерно 16:9 — он приходит гостю вместе с приветствием.")
    await show(callback, f"🖼 {IMAGE_TITLES[kind]}: отправьте картинку фото или файлом (JPG, PNG, до 5 МБ). {hint}",
               kb([btn("⬅️ Оформление", "brand", callback_data.club_id)]))


@router.callback_query(Nav.filter(F.action == "brand_reset"))
async def brand_reset(callback: CallbackQuery, callback_data: Nav) -> None:
    c = callback_data.club_id
    await show(callback, "Сбросить оформление (палитру, надпись, тексты, логотип и баннер) к стандартному? "
                         "Префикс промокодов останется.",
               kb([btn("Да, сбросить", "brand_reset_yes", c), btn("Нет", "brand", c)]))


@router.callback_query(Nav.filter(F.action == "brand_reset_yes"))
async def brand_reset_yes(callback: CallbackQuery, callback_data: Nav, db: AsyncSession) -> None:
    club = await get_club(db, callback_data.club_id)
    branding.reset(club)
    await db.flush()
    await show_brand(callback, club, "✅ Оформление сброшено.")


async def _apply(message: Message, db: AsyncSession, state: FSMContext, change) -> None:
    """Общий шаг для текстовых настроек оформления: применить, сохранить, показать меню."""
    club = await get_club(db, (await state.get_data())["club_id"])
    try:
        change(club)
    except branding.BrandingError as e:
        await message.answer(f"⛔ {html.quote(str(e))}. Отправьте ещё раз или /cancel.")
        return
    await db.flush()
    await state.clear()
    await show_brand(message, club, "✅ Сохранено.")


@router.message(PF.brand_hub, F.text)
async def brand_hub_text(message: Message, db: AsyncSession, state: FSMContext) -> None:
    await _apply(message, db, state, lambda club: branding.set_hub_label(club, message.text))


@router.message(PF.brand_prefix, F.text)
async def brand_prefix_text(message: Message, db: AsyncSession, state: FSMContext) -> None:
    await _apply(message, db, state, lambda club: branding.set_code_prefix(club, message.text))


@router.message(PF.brand_text, F.text)
async def brand_text_value(message: Message, db: AsyncSession, state: FSMContext) -> None:
    kind = (await state.get_data())["kind"]
    await _apply(message, db, state, lambda club: branding.set_text(club, kind, message.text))


@router.message(PF.brand_image, F.photo | F.document)
async def brand_image_file(message: Message, db: AsyncSession, state: FSMContext) -> None:
    data = await state.get_data()
    if message.photo:
        file_id = message.photo[-1].file_id  # самый крупный размер
    elif (message.document.mime_type or "").startswith("image/"):
        if (message.document.file_size or 0) > branding.IMAGE_MAX_BYTES:
            await message.answer("⛔ Картинка больше 5 МБ. Отправьте поменьше или /cancel.")
            return
        file_id = message.document.file_id
    else:
        await message.answer("⛔ Нужна картинка (JPG, PNG). Отправьте ещё раз или /cancel.")
        return
    content = await message.bot.download(file_id)
    club = await get_club(db, data["club_id"])
    try:
        branding.set_image(club, data["kind"], content.read())
    except branding.BrandingError as e:
        await message.answer(f"⛔ {html.quote(str(e))}. Отправьте ещё раз или /cancel.")
        return
    await db.flush()
    await state.clear()
    await show_brand(message, club, f"✅ {IMAGE_TITLES[data['kind']]} сохранён.")


# --- Сброс данных клуба -------------------------------------------------------------------


@router.callback_query(Nav.filter(F.action == "data"))
async def data_menu(callback: CallbackQuery, callback_data: Nav, db: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    club = await get_club(db, callback_data.club_id)
    n = await clubs.game_data_counts(db, club)
    text = (
        f"🧹 <b>Сброс данных</b> · {html.quote(club.name)}\n\n"
        f"Гостей: {n['guests']} · игр: {n['plays']} · промокодов: {n['codes']} "
        f"(активных: {n['active_codes']}) · записей журнала: {n['log']}\n\n"
        "👤 <b>Сбросить регистрацию гостя</b> — снимает номер и согласие одного человека, "
        "его игры и промокоды остаются. Удобно для проверки регистрации.\n\n"
        "🗑 <b>Очистить игровые данные</b> — удаляет всех гостей, игры, промокоды и журнал проверок "
        "этого клуба. Бот, призы, сотрудники, подписка, оформление и правила остаются."
    )
    await show(callback, text, kb(
        [btn("👤 Сбросить регистрацию гостя", "reset_guest", club.id)],
        [btn("🗑 Очистить игровые данные", "wipe", club.id)],
        [btn("⬅️ К клубу", "club", club.id)],
    ))


@router.callback_query(Nav.filter(F.action == "reset_guest"))
async def reset_guest_menu(callback: CallbackQuery, callback_data: Nav, db: AsyncSession, state: FSMContext) -> None:
    club = await get_club(db, callback_data.club_id)
    await state.set_state(PF.reset_guest_pick)
    await state.update_data(club_id=club.id)
    await callback.answer()
    await callback.message.answer(
        f"👤 Чью регистрацию в клубе «{html.quote(club.name)}» сбросить? "
        "Нажмите кнопку внизу и выберите человека — можно и себя.",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="👤 Выбрать гостя", request_users=KeyboardButtonRequestUsers(
                request_id=PICK_RESET_GUEST, user_is_bot=False, max_quantity=1, request_name=True))],
                [KeyboardButton(text="/cancel")]],
            resize_keyboard=True, one_time_keyboard=True,
        ),
    )


@router.message(PF.reset_guest_pick, F.users_shared)
async def reset_guest_picked(message: Message, db: AsyncSession, state: FSMContext) -> None:
    club = await get_club(db, (await state.get_data())["club_id"])
    user = message.users_shared.users[0]
    await state.clear()
    if await clubs.reset_guest_registration(db, club, user.user_id):
        text = (f"✅ Регистрация {html.quote(user.first_name or str(user.user_id))} сброшена: "
                "при следующем входе он снова поделится номером и примет правила. Игры и промокоды сохранены.")
    else:
        text = f"Этот человек ещё не гость клуба «{html.quote(club.name)}» — сбрасывать нечего."
    await message.answer(text, reply_markup=ReplyKeyboardRemove())
    await show_card(message, db, club)


@router.callback_query(Nav.filter(F.action == "wipe"))
async def wipe_menu(callback: CallbackQuery, callback_data: Nav, db: AsyncSession, state: FSMContext) -> None:
    club = await get_club(db, callback_data.club_id)
    n = await clubs.game_data_counts(db, club)
    await state.set_state(PF.wipe_confirm)
    await state.update_data(club_id=club.id)
    warning = (f"\n⚠️ Из них {n['active_codes']} активных промокодов — гости не смогут их получить."
               if n["active_codes"] else "")
    await show(
        callback,
        f"🗑 <b>Очистка игровых данных</b> · {html.quote(club.name)}\n\n"
        f"Будет удалено: гостей — {n['guests']}, игр — {n['plays']}, промокодов — {n['codes']}, "
        f"записей журнала — {n['log']}.{warning}\n\n"
        "Это <b>нельзя отменить</b> из бота — восстановить можно только из ежедневного бэкапа.\n\n"
        f"Чтобы подтвердить, отправьте название клуба: <code>{html.quote(club.name)}</code>",
        back(club.id),
    )


@router.message(PF.wipe_confirm, F.text)
async def wipe_confirm(message: Message, db: AsyncSession, state: FSMContext) -> None:
    club = await get_club(db, (await state.get_data())["club_id"])
    await state.clear()
    if message.text.strip().casefold() != club.name.strip().casefold():
        await message.answer("Название не совпало — ничего не удалено.")
        await show_card(message, db, club)
        return
    n = await clubs.wipe_game_data(db, club)
    await message.answer(
        f"✅ Игровые данные клуба «{html.quote(club.name)}» очищены: гостей — {n['guests']}, "
        f"игр — {n['plays']}, промокодов — {n['codes']}, записей журнала — {n['log']}."
    )
    await show_card(message, db, club)


# --- Ввод не того типа в состоянии ------------------------------------------------------


@router.message(StateFilter("*"))
async def unexpected(message: Message, state: FSMContext) -> None:
    if await state.get_state() is None:
        await message.answer(MENU_TEXT, reply_markup=MENU_KB)
    else:
        await message.answer("Жду другой ответ. Отправьте его или нажмите /cancel.")
