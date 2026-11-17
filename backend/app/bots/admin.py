"""Админ-панель в боте клуба: проверка и погашение промокодов.

Видна только сотрудникам этого клуба (club_staff). Админ одного клуба
в другом клубе — обычный гость: фильтр проверяет роль именно в текущем клубе.

Промокод можно ввести текстом или отсканировать: кнопка «📷 Сканировать QR»
открывает Mini App в режиме сканера, он отправляет код боту через web_app_data.
"""

from aiogram import Bot, F, Router, html
from aiogram.filters import BaseFilter, Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    TelegramObject,
    WebAppInfo,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bots.notify import local_datetime
from app.config import get_settings
from app.models import Club, ClubStaff, RedemptionResult
from app.services.redemption import FAILED_WINDOW, Outcome, check_code, normalize_code, redeem_code

REDEEM_PREFIX = "redeem:"
SCAN_BUTTON = "📷 Сканировать QR"
ENTER_BUTTON = "⌨️ Ввести код"


class IsStaff(BaseFilter):
    """Пропускает только сотрудников текущего клуба и передаёт запись в обработчик как staff."""

    async def __call__(self, event: TelegramObject, club: Club, db: AsyncSession) -> bool | dict:
        user = getattr(event, "from_user", None)
        if user is None:
            return False
        staff = await db.scalar(
            select(ClubStaff).where(ClubStaff.club_id == club.id, ClubStaff.telegram_id == user.id)
        )
        return {"staff": staff} if staff else False


router = Router(name="admin")
router.message.filter(F.chat.type == "private", IsStaff())
router.callback_query.filter(IsStaff())


def admin_keyboard(bot_id: int) -> ReplyKeyboardMarkup:
    scan_url = f"{get_settings().webapp_url_for(bot_id)}&scan=1"
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=SCAN_BUTTON, web_app=WebAppInfo(url=scan_url))],
            [KeyboardButton(text=ENTER_BUTTON)],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


def mask_phone(phone: str | None) -> str:
    return f"…{phone[-4:]}" if phone else "не указан"


def describe(outcome: Outcome, club: Club, admin_id: int) -> tuple[str, InlineKeyboardMarkup | None]:
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
        return (f"✅ Промокод {code} погашен. Выдайте приз гостю.\n\n{details}", None)
    if outcome.result == RedemptionResult.ALREADY_REDEEMED:
        by = "вами" if prize.redeemed_by == admin_id else "другим администратором"
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
async def admin_menu(message: Message, club: Club) -> None:
    await message.answer(
        f"🛠 Админ-панель клуба {html.bold(html.quote(club.name))}\n\n"
        "Отсканируйте QR гостя или отправьте промокод сообщением (например, GG-7K3M9Q).",
        reply_markup=admin_keyboard(message.bot.id),
    )


@router.message(F.text == ENTER_BUTTON)
async def ask_code(message: Message) -> None:
    await message.answer("Отправьте промокод сообщением, например GG-7K3M9Q.")


async def _check_and_reply(message: Message, club: Club, db: AsyncSession, raw: str) -> None:
    outcome = await check_code(db, club, message.from_user.id, raw)
    text, keyboard = describe(outcome, club, message.from_user.id)
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
    text, _ = describe(outcome, club, callback.from_user.id)
    await callback.message.edit_text(text, reply_markup=None)
    if outcome.result == RedemptionResult.REDEEMED:
        await callback.answer("Погашено")
        await notify_guest_redeemed(callback.bot, outcome, club)
    else:
        await callback.answer("Не удалось погасить", show_alert=False)
