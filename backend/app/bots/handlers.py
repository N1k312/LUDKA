"""Обработчики сообщений гостя. Клуб (club) и сессия базы (db) передаются из вебхука.

Регистрация гостя перед первой игрой:
  /start → «Поделиться номером» (только свой контакт) → правила и согласие → «Играть».
Регистрацию можно пройти и прямо в Mini App (согласие через API, номер — requestContact):
номер всё равно приходит сюда обычным контактом и проверяется здесь же.
Если у клуба есть баннер (оформление), приветствия приходят картинкой с подписью.
"""

import re
from html import unescape

from aiogram import F, Router, html
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
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
from app.services import branding
from app.services.guests import (
    PhoneTakenError,
    accept_consent,
    get_or_create_guest,
    is_registered,
    set_phone,
)

CONSENT_CALLBACK = "consent:accept"
RULES_CALLBACK = "guest:rules"

router = Router(name="guest")
# Mini App и регистрация работают только в личном чате с ботом.
router.message.filter(F.chat.type == "private")


# --- Клавиатуры и тексты ----------------------------------------------------


def play_keyboard(bot_id: int) -> InlineKeyboardMarkup:
    """«Крутить» — Mini App на колесе, «Мои призы» — сразу на вкладке призов, «Правила» — в чат."""
    url = get_settings().webapp_url_for(bot_id)
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎡 Крутить колесо", web_app=WebAppInfo(url=url))],
        [InlineKeyboardButton(text="🎁 Мои призы", web_app=WebAppInfo(url=f"{url}&tab=prizes")),
         InlineKeyboardButton(text="📜 Правила", callback_data=RULES_CALLBACK)],
    ])


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


TELEGRAM_TEXT_LIMIT = 4096


def consent_note(club: Club) -> str:
    return (
        "Нажимая «Принимаю», вы соглашаетесь с правилами акции и даёте согласие "
        f"клубу {club_title(club)} на обработку номера телефона и данных Telegram "
        "для проведения акции и выдачи призов."
    )


def consent_text(club: Club) -> str:
    return f"{rules_text(club)}\n\n{consent_note(club)}"


def fits(text: str) -> bool:
    """Длина так, как её считает Telegram: без HTML-тегов и с раскрытыми сущностями."""
    return len(unescape(re.sub(r"<[^>]+>", "", text))) <= TELEGRAM_TEXT_LIMIT


def welcome_text(club: Club) -> str:
    custom = branding.text(club, "welcome")
    body = html.quote(custom) if custom else (
        "Крутите колесо раз в неделю. Выигранный приз получите у администратора "
        "по промокоду — он появится в разделе «Мои призы»."
    )
    return f"Добро пожаловать в {club_title(club)}! 🎉\n\n{body}"


async def send(message: Message, club: Club, text: str, reply_markup=None) -> None:
    """Текст или, если у клуба есть баннер, картинка с подписью (подпись — до 1024 символов)."""
    banner = branding.image_path(club, "banner")
    if banner is not None and banner.exists() and fits(text) and len(text) <= 1000:
        await message.answer_photo(FSInputFile(banner), caption=text, reply_markup=reply_markup)
    else:
        await message.answer(text, reply_markup=reply_markup)


# --- Шаги регистрации ---------------------------------------------------------


async def continue_registration(message: Message, club: Club, guest: Guest) -> None:
    """Показывает гостю следующий шаг: номер → согласие → игра."""
    if guest.phone is None:
        await send(
            message, club,
            f"Привет! Это бот клуба {club_title(club)}.\n\n"
            "Раз в неделю здесь можно бесплатно крутить колесо фортуны и выигрывать "
            "бонусы клуба: часы игры, скидки, напитки.\n\n"
            "Чтобы участвовать, поделитесь номером телефона — кнопка ниже. "
            "По нему администратор сможет найти ваш приз.",
            reply_markup=contact_keyboard(),
        )
    elif guest.consent_at is None:
        text = consent_text(club)
        if fits(text):
            await message.answer(text, reply_markup=consent_keyboard())
        else:
            # Длинные правила — отдельным сообщением, иначе Telegram не примет текст.
            await message.answer(rules_text(club))
            await message.answer(consent_note(club), reply_markup=consent_keyboard())
    else:
        await send(message, club, welcome_text(club), reply_markup=play_keyboard(message.bot.id))


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
    await db.commit()  # согласие сохраняется, даже если Telegram не примет правку сообщения
    await callback.answer("Готово!")
    # Сообщение с согласием превращается в приветствие (одно сообщение вместо двух).
    # С баннером так нельзя — картинку в текстовое сообщение не вставить, поэтому шлём новое.
    has_banner = branding.image_path(club, "banner") is not None
    text = "✅ Правила приняты." if has_banner else welcome_text(club)
    markup = None if has_banner else play_keyboard(callback.bot.id)
    try:
        await callback.message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest:
        has_banner = True  # не смогли отредактировать — пришлём приветствие отдельно
    if has_banner:
        await send(callback.message, club, welcome_text(club), reply_markup=play_keyboard(callback.bot.id))


@router.message(Command("rules"))
async def rules(message: Message, club: Club) -> None:
    await message.answer(rules_text(club))


@router.callback_query(F.data == RULES_CALLBACK)
async def rules_button(callback: CallbackQuery, club: Club) -> None:
    await callback.answer()
    await callback.message.answer(rules_text(club))


@router.message()
async def fallback(message: Message, club: Club, db: AsyncSession) -> None:
    guest = await get_or_create_guest(db, club, message.from_user)
    if is_registered(guest):
        await message.answer("Нажмите «Крутить колесо», чтобы открыть игру 🎡",
                             reply_markup=play_keyboard(message.bot.id))
    else:
        await continue_registration(message, club, guest)
