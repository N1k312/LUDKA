"""Админ-панель в боте клуба: промокоды и сотрудники.

Видна владельцам платформы (во всех ботах) и сотрудникам этого клуба.
Роль одного клуба в другом не действует: фильтр проверяет роль в текущем клубе.
Кнопки зависят от роли, а права заново проверяются на сервере при каждом действии
(см. app/services/staff.py).

Промокод можно ввести текстом или отсканировать: кнопка «📷 Сканировать QR»
открывает Mini App в режиме сканера, он отправляет код боту через web_app_data.
"""

from contextlib import suppress

from aiogram import Bot, F, Router, html
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import BaseFilter, Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    KeyboardButtonRequestUsers,
    Message,
    ReplyKeyboardMarkup,
    TelegramObject,
    WebAppInfo,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.bots.notify import local_datetime
from app.bots.setup import reset_commands, set_staff_commands
from app.config import get_settings
from app.models import Club, ClubStaff, RedemptionResult, StaffRole
from app.services import stats
from app.services.redemption import FAILED_WINDOW, Outcome, check_code, normalize_code, redeem_code
from app.services.staff import (
    ROLE_TITLES,
    Person,
    Role,
    StaffError,
    add_admin,
    assign_superadmin,
    can_assign_superadmin,
    can_manage_admins,
    display_name,
    list_staff,
    remove_staff,
    role_in_club,
)

REDEEM_PREFIX = "redeem:"
SCAN_BUTTON = "📷 Сканировать QR"
ENTER_BUTTON = "⌨️ Ввести код"
STAFF_BUTTON = "👥 Сотрудники"
TODAY_BUTTON = "📋 Сегодня"
NEXT_CALLBACK = "admin:next"
ADD_ADMIN_BUTTON = "➕ Добавить админа"
SUPERADMIN_BUTTON = "👑 Назначить суперадмина"
REMOVE_PREFIX = "staff:rm:"

# request_id для встроенного выбора пользователя Telegram.
PICK_ADMIN = 1
PICK_SUPERADMIN = 2


class IsStaff(BaseFilter):
    """Пропускает владельцев платформы и сотрудников текущего клуба; передаёт role в обработчик."""

    async def __call__(self, event: TelegramObject, club: Club, db: AsyncSession) -> bool | dict:
        user = getattr(event, "from_user", None)
        if user is None:
            return False
        role = await role_in_club(db, club, user.id)
        return {"role": role} if role else False


router = Router(name="admin")
router.message.filter(F.chat.type == "private", IsStaff())
router.callback_query.filter(IsStaff())


def _pick_user(text: str, request_id: int) -> KeyboardButton:
    return KeyboardButton(
        text=text,
        request_users=KeyboardButtonRequestUsers(
            request_id=request_id, user_is_bot=False, max_quantity=1,
            request_name=True, request_username=True,
        ),
    )


def admin_keyboard(bot_id: int, role: Role) -> ReplyKeyboardMarkup:
    scan_url = f"{get_settings().webapp_url_for(bot_id)}&scan=1"
    rows = [[KeyboardButton(text=SCAN_BUTTON, web_app=WebAppInfo(url=scan_url)),
             KeyboardButton(text=ENTER_BUTTON)],
            [KeyboardButton(text=TODAY_BUTTON)]]
    if can_manage_admins(role):
        rows.append([_pick_user(ADD_ADMIN_BUTTON, PICK_ADMIN), KeyboardButton(text=STAFF_BUTTON)])
    if can_assign_superadmin(role):
        rows.append([_pick_user(SUPERADMIN_BUTTON, PICK_SUPERADMIN)])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True, is_persistent=True)


def mask_phone(phone: str | None) -> str:
    return f"…{phone[-4:]}" if phone else "не указан"


def describe(
    outcome: Outcome, club: Club, admin_id: int, redeemer: str | None = None
) -> tuple[str, InlineKeyboardMarkup | None]:
    """Ответ на проверку или погашение. redeemer — имя погасившего (для «уже погашен»)."""
    code = html.code(html.quote(outcome.code))
    if outcome.result == RedemptionResult.RATE_LIMITED:
        minutes = int(FAILED_WINDOW.total_seconds() // 60)
        return (f"⛔ Слишком много неудачных проверок. Подождите до {minutes} минут.", None)
    if outcome.result == RedemptionResult.NOT_FOUND:
        return (f"❌ Промокод {code} не найден", None)

    prize, option, guest = outcome.prize, outcome.option, outcome.guest
    title = f"{option.emoji} {prize.title}" if option.emoji else prize.title
    who = html.quote(guest.first_name or "Гость")
    if guest.username:
        who += f" (@{html.quote(guest.username)})"
    details = (
        f"Приз: {html.bold(html.quote(title))}\n"
        f"Гость: {who}, телефон {mask_phone(guest.phone)}\n"
        f"Выдан: {local_datetime(prize.created_at, club)}\n"
        f"Действует до: {local_datetime(prize.expires_at, club)}"
    )

    if outcome.result == RedemptionResult.VALID:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✅ Погасить", callback_data=REDEEM_PREFIX + outcome.code)
        ]])
        return (f"🟢 Промокод {code} действителен\n\n{details}", keyboard)
    if outcome.result == RedemptionResult.REDEEMED:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="➡️ Следующий промокод", callback_data=NEXT_CALLBACK)
        ]])
        return (f"✅ Промокод {code} погашен. Выдайте приз гостю.\n\n{details}", keyboard)
    if outcome.result == RedemptionResult.ALREADY_REDEEMED:
        by = "вами" if prize.redeemed_by == admin_id else html.quote(redeemer or "другим сотрудником")
        return (
            f"⚠️ Промокод {code} уже погашен {local_datetime(prize.redeemed_at, club)} {by}\n\n{details}",
            None,
        )
    return (f"⌛ Промокод {code} истёк\n\n{details}", None)


async def notify_guest_redeemed(bot: Bot, outcome: Outcome, club: Club) -> None:
    try:
        await bot.send_message(
            outcome.guest.telegram_id,
            f"🎁 Приз получен: {html.bold(html.quote(outcome.prize.title))}\n\n"
            f"Спасибо, что играете в {html.quote(club.name)}! Следующий спин — через неделю после прошлого.",
        )
    except Exception:  # гость мог заблокировать бота — погашение от этого не отменяется
        pass


# --- Обработчики ----------------------------------------------------------------------


@router.message(Command("admin"))
async def admin_menu(message: Message, club: Club, role: Role) -> None:
    lines = [
        f"🛠 Админ-панель клуба {html.bold(html.quote(club.name))}",
        f"Ваша роль: {ROLE_TITLES[role]}",
        "",
        "Отсканируйте QR гостя или отправьте промокод сообщением (например, GG-7K3M9Q).",
    ]
    if can_manage_admins(role):
        lines.append("«➕ Добавить админа» — выбрать человека из контактов, «👥 Сотрудники» — список и удаление.")
    if can_assign_superadmin(role):
        lines.append("«👑 Назначить суперадмина» — один на клуб, прежний станет админом.")
    await message.answer("\n".join(lines), reply_markup=admin_keyboard(message.bot.id, role))


@router.message(F.text == ENTER_BUTTON)
async def ask_code(message: Message) -> None:
    await message.answer("Отправьте промокод сообщением, например GG-7K3M9Q.")


async def redeemer_name(db: AsyncSession, club: Club, outcome: Outcome) -> str | None:
    if outcome.result != RedemptionResult.ALREADY_REDEEMED:
        return None
    return await display_name(db, club, outcome.prize.redeemed_by)


async def _check_and_reply(message: Message, club: Club, db: AsyncSession, raw: str) -> None:
    outcome = await check_code(db, club, message.from_user.id, raw)
    text, keyboard = describe(outcome, club, message.from_user.id, await redeemer_name(db, club, outcome))
    await message.answer(text, reply_markup=keyboard)


@router.message(F.web_app_data)
async def scanned(message: Message, club: Club, db: AsyncSession) -> None:
    await _check_and_reply(message, club, db, message.web_app_data.data)


@router.message(F.text.func(lambda t: normalize_code(t) is not None))
async def typed_code(message: Message, club: Club, db: AsyncSession) -> None:
    await _check_and_reply(message, club, db, message.text)


@router.callback_query(F.data.startswith(REDEEM_PREFIX))
async def redeem(callback: CallbackQuery, club: Club, db: AsyncSession) -> None:
    raw = callback.data.removeprefix(REDEEM_PREFIX)
    outcome = await redeem_code(db, club, callback.from_user.id, raw)
    # Сначала фиксируем погашение и журнал, потом говорим с Telegram: если ответ
    # не дойдёт (например, «query is too old»), погашение всё равно останется в базе.
    await db.commit()
    text, keyboard = describe(outcome, club, callback.from_user.id, await redeemer_name(db, club, outcome))
    redeemed = outcome.result == RedemptionResult.REDEEMED
    with suppress(TelegramAPIError):
        await callback.message.edit_text(text, reply_markup=keyboard)
    with suppress(TelegramAPIError):
        await callback.answer("Погашено" if redeemed else "Не удалось погасить")
    if redeemed:
        await notify_guest_redeemed(callback.bot, outcome, club)


@router.callback_query(F.data == NEXT_CALLBACK)
async def next_code(callback: CallbackQuery, role: Role) -> None:
    await callback.answer()
    await callback.message.answer(
        "Отправьте следующий промокод сообщением или нажмите «📷 Сканировать QR».",
        reply_markup=admin_keyboard(callback.bot.id, role),
    )


@router.message(F.text == TODAY_BUTTON)
async def today_summary(message: Message, club: Club, db: AsyncSession) -> None:
    s = await stats.today(db, club)
    lines = [
        f"📋 <b>Сегодня</b> в {html.quote(club.name)}",
        "",
        f"🎡 Спинов: {s.spins}",
        f"🎁 Выдано промокодов: {s.codes_issued}",
        f"✅ Погашено: {s.redeemed}",
        f"🙋 Новых гостей: {s.new_guests}",
    ]
    if s.redeemed_by:
        lines += ["", "Кто гасил:"]
        for telegram_id, n in s.redeemed_by.most_common():
            lines.append(f"• {html.quote(await display_name(db, club, telegram_id))} — {n}")
    if s.failed_checks:
        lines += ["", f"❌ Проверок с неверным кодом: {s.failed_checks}"]
    await message.answer("\n".join(lines))


# --- Сотрудники -------------------------------------------------------------------------


def staff_text_and_keyboard(club: Club, staff: list[ClubStaff], role: Role, actor_id: int):
    if not staff:
        return f"В клубе {html.quote(club.name)} пока нет сотрудников.", None
    lines, buttons = [f"👥 Сотрудники клуба {html.bold(html.quote(club.name))}", ""], []
    for s in staff:
        person = Person(s.telegram_id, s.first_name, s.username)
        is_owner = s.role == StaffRole.OWNER
        lines.append(f"{'👑' if is_owner else '🔑'} {html.quote(person.title())} — "
                     f"{'суперадмин' if is_owner else 'админ'}")
        removable = (can_assign_superadmin(role) if is_owner else can_manage_admins(role)) and (
            s.telegram_id != actor_id or role == Role.PLATFORM_OWNER
        )
        if removable:
            buttons.append([InlineKeyboardButton(
                text=f"Убрать: {person.first_name or s.telegram_id}",
                callback_data=f"{REMOVE_PREFIX}{s.telegram_id}",
            )])
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons) if buttons else None
    return "\n".join(lines), keyboard


@router.message(F.text == STAFF_BUTTON)
async def show_staff(message: Message, club: Club, db: AsyncSession, role: Role) -> None:
    if not can_manage_admins(role):
        await message.answer("Список сотрудников доступен суперадмину клуба.")
        return
    text, keyboard = staff_text_and_keyboard(club, await list_staff(db, club), role, message.from_user.id)
    await message.answer(text, reply_markup=keyboard)


async def greet_new_staff(bot: Bot, club: Club, telegram_id: int, title: str) -> None:
    await set_staff_commands(bot, telegram_id)
    try:
        await bot.send_message(
            telegram_id,
            f"Вас назначили: {title} клуба {html.bold(html.quote(club.name))}.\n"
            "Откройте /admin, чтобы проверять и гасить промокоды.",
        )
    except Exception:  # человек ещё не писал боту — увидит /admin, когда напишет
        pass


@router.message(F.users_shared)
async def user_picked(message: Message, club: Club, db: AsyncSession, role: Role) -> None:
    shared = message.users_shared
    if not shared.users:
        return
    user = shared.users[0]
    person = Person(user.user_id, user.first_name, user.username)
    try:
        if shared.request_id == PICK_SUPERADMIN:
            _, previous = await assign_superadmin(db, club, role, person)
            text = f"👑 {html.quote(person.title())} — суперадмин клуба {html.quote(club.name)}."
            if previous is not None:
                text += f"\nПрежний суперадмин ({html.quote(Person(previous.telegram_id, previous.first_name, previous.username).title())}) теперь админ."
            await message.answer(text)
            await greet_new_staff(message.bot, club, person.telegram_id, "суперадмин")
        elif shared.request_id == PICK_ADMIN:
            await add_admin(db, club, role, person)
            await message.answer(f"🔑 {html.quote(person.title())} — админ клуба {html.quote(club.name)}.")
            await greet_new_staff(message.bot, club, person.telegram_id, "админ")
    except StaffError as e:
        await message.answer(f"⛔ {e}")


@router.callback_query(F.data.startswith(REMOVE_PREFIX))
async def remove(callback: CallbackQuery, club: Club, db: AsyncSession, role: Role) -> None:
    try:
        telegram_id = int(callback.data.removeprefix(REMOVE_PREFIX))
        removed = await remove_staff(db, club, role, callback.from_user.id, telegram_id)
    except (ValueError, StaffError) as e:
        await callback.answer(str(e) if isinstance(e, StaffError) else "Некорректный запрос", show_alert=True)
        return
    await reset_commands(callback.bot, telegram_id)
    await callback.answer("Убран")
    text, keyboard = staff_text_and_keyboard(club, await list_staff(db, club), role, callback.from_user.id)
    await callback.message.edit_text(
        f"{text}\n\n✅ Убран: {html.quote(Person(removed.telegram_id, removed.first_name, removed.username).title())}",
        reply_markup=keyboard,
    )
