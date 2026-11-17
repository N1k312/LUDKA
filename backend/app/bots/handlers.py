"""Обработчики сообщений гостя. Клуб (club) и сессия базы (db) передаются из вебхука.

Регистрация гостя перед первой игрой:
  /start → «Поделиться номером» (только свой контакт) → правила и согласие → «Играть».
"""

from aiogram import F, Router, html
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    WebAppInfo,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import Club, Guest
from app.services.guests import (
    PhoneTakenError,
    accept_consent,
    get_or_create_guest,
    is_registered,
    set_phone,
)

CONSENT_CALLBACK = "consent:accept"

router = Router(name="guest")
# Mini App и регистрация работают только в личном чате с ботом.
router.message.filter(F.chat.type == "private")


# --- Клавиатуры и тексты ----------------------------------------------------


def play_keyboard(bot_id: int) -> InlineKeyboardMarkup:
    url = get_settings().webapp_url_for(bot_id)
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🎡 Играть", web_app=WebAppInfo(url=url))]]
    )


def contact_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📱 Поделиться номером", request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def consent_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="✅ Принимаю", callback_data=CONSENT_CALLBACK)]]
    )


def club_title(club: Club) -> str:
    return html.bold(html.quote(club.name))


def rules_text(club: Club) -> str:
    rules = club.rules_text or "Правила акции уточняйте у администратора клуба."
    return f"📜 Правила акции клуба {club_title(club)}\n\n{html.quote(rules)}"


def consent_text(club: Club) -> str:
    return (
        f"{rules_text(club)}\n\n"
        "Нажимая «Принимаю», вы соглашаетесь с правилами акции и даёте согласие "
        f"клубу {club_title(club)} на обработку номера телефона и данных Telegram "
        "для проведения акции и выдачи призов."
    )


# --- Шаги регистрации ---------------------------------------------------------


async def continue_registration(message: Message, club: Club, guest: Guest) -> None:
    """Показывает гостю следующий шаг: номер → согласие → игра."""
    if guest.phone is None:
        await message.answer(
            f"Привет! Это бот клуба {club_title(club)}.\n\n"
            "Раз в неделю здесь можно бесплатно крутить колесо фортуны и выигрывать "
            "бонусы клуба: часы игры, скидки, напитки.\n\n"
            "Чтобы участвовать, поделитесь номером телефона — кнопка ниже. "
            "По нему администратор сможет найти ваш приз.",
            reply_markup=contact_keyboard(),
        )
    elif guest.consent_at is None:
        await message.answer(consent_text(club), reply_markup=consent_keyboard())
    else:
        await message.answer(
            f"Добро пожаловать в {club_title(club)}! 🎉\n\n"
            "Крутите колесо раз в неделю. Выигранный приз получите у администратора "
            "по промокоду — он появится в разделе «Мои призы».",
            reply_markup=play_keyboard(message.bot.id),
        )


@router.message(CommandStart())
async def start(message: Message, club: Club, db: AsyncSession) -> None:
    guest = await get_or_create_guest(db, club, message.from_user)
    await continue_registration(message, club, guest)


@router.message(F.contact)
async def contact(message: Message, club: Club, db: AsyncSession) -> None:
    guest = await get_or_create_guest(db, club, message.from_user)
    # Принимаем только собственный контакт гостя, отправленный кнопкой.
    if message.contact.user_id != message.from_user.id:
        await message.answer(
            "Нужен ваш собственный номер. Нажмите кнопку «📱 Поделиться номером» ниже.",
            reply_markup=contact_keyboard(),
        )
        return
    try:
        await set_phone(db, guest, message.contact.phone_number)
    except PhoneTakenError:
        await message.answer(
            "Этот номер уже привязан к другому аккаунту в клубе. "
            "Если это ваш номер, обратитесь к администратору.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return
    except ValueError:
        await message.answer("Не удалось распознать номер, попробуйте ещё раз.",
                             reply_markup=contact_keyboard())
        return

    await message.answer("Спасибо, номер сохранён ✅", reply_markup=ReplyKeyboardRemove())
    await continue_registration(message, club, guest)


@router.callback_query(F.data == CONSENT_CALLBACK)
async def consent(callback: CallbackQuery, club: Club, db: AsyncSession) -> None:
    guest = await get_or_create_guest(db, club, callback.from_user)
    if guest.phone is None:
        await callback.answer("Сначала поделитесь номером телефона")
        await continue_registration(callback.message, club, guest)
        return

    await accept_consent(db, guest)
    await callback.answer("Готово!")
    await callback.message.edit_reply_markup(reply_markup=None)
    await continue_registration(callback.message, club, guest)


@router.message(Command("rules"))
async def rules(message: Message, club: Club) -> None:
    await message.answer(rules_text(club))


@router.message()
async def fallback(message: Message, club: Club, db: AsyncSession) -> None:
    guest = await get_or_create_guest(db, club, message.from_user)
    if is_registered(guest):
        await message.answer("Нажмите «Играть», чтобы открыть колесо 🎡", reply_markup=play_keyboard(message.bot.id))
    else:
        await continue_registration(message, club, guest)
