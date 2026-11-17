"""Подключение бота к клубу и настройка бота в Telegram."""

import secrets

from aiogram import Bot
from aiogram.types import BotCommand, BotCommandScopeChat, MenuButtonWebApp, WebAppInfo
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bots.registry import registry
from app.config import get_settings
from app.crypto import encrypt
from app.models import BotStatus, Club, ClubBot

ALLOWED_UPDATES = ["message", "callback_query"]

GUEST_COMMANDS = [
    BotCommand(command="start", description="Начать"),
    BotCommand(command="rules", description="Правила акции"),
]
STAFF_COMMANDS = [*GUEST_COMMANDS, BotCommand(command="admin", description="Админ-панель")]


class BotSetupError(Exception):
    """Понятная ошибка для суперадмина (без токена в тексте)."""


def webhook_url(bot_id: int) -> str:
    return f"{get_settings().base_url}/tg/{bot_id}"


def _require_https() -> None:
    if not get_settings().base_url.startswith("https://"):
        raise BotSetupError(
            "PUBLIC_BASE_URL должен начинаться с https:// — Telegram не шлёт вебхуки на http. "
            "При разработке укажите адрес туннеля cloudflared."
        )


async def configure_bot(bot: Bot, club_bot: ClubBot, club: Club) -> None:
    """Вебхук с секретом, кнопка Mini App, команды и описание бота."""
    settings = get_settings()
    await bot.set_webhook(
        url=webhook_url(club_bot.bot_id),
        secret_token=club_bot.webhook_secret,
        allowed_updates=ALLOWED_UPDATES,
    )
    await bot.set_chat_menu_button(
        menu_button=MenuButtonWebApp(
            text="Играть", web_app=WebAppInfo(url=settings.webapp_url_for(club_bot.bot_id))
        )
    )
    await bot.set_my_commands(GUEST_COMMANDS)
    # Владельцам платформы /admin виден в меню каждого бота (если они уже писали боту).
    for owner_id in settings.owner_ids:
        await set_staff_commands(bot, owner_id)
    await bot.set_my_description(
        description=(
            f"Бот клуба «{club.name}».\n\n"
            "Раз в неделю крутите колесо фортуны и выигрывайте бонусы клуба: "
            "часы игры, скидки и напитки. Это бесплатно."
        )
    )
    await bot.set_my_short_description(
        short_description=f"Колесо фортуны клуба «{club.name}»: бесплатные бонусы раз в неделю."
    )


async def connect_bot(db: AsyncSession, club: Club, token: str) -> ClubBot:
    """Проверяет токен через getMe, сохраняет его зашифрованным и настраивает бота.

    Вызывающий код делает commit; если Telegram вернул ошибку, изменения откатываются.
    """
    _require_https()
    bot = registry.bot_for_token(token)
    me = await bot.get_me()

    existing = await db.get(ClubBot, me.id)
    if existing is not None and existing.club_id != club.id:
        raise BotSetupError(f"Бот @{me.username} уже подключён к другому клубу")

    current = await db.scalar(select(ClubBot).where(ClubBot.club_id == club.id))
    if current is not None and current.bot_id != me.id:
        raise BotSetupError(f"У клуба уже подключён другой бот: @{current.username}")

    club_bot = existing or ClubBot(bot_id=me.id, club_id=club.id)
    club_bot.username = me.username or ""
    club_bot.token_encrypted = encrypt(token)
    club_bot.webhook_secret = secrets.token_urlsafe(32)
    club_bot.status = BotStatus.ACTIVE
    db.add(club_bot)
    await db.flush()

    await configure_bot(bot, club_bot, club)
    registry.forget(me.id)
    return club_bot


async def refresh_bot(db: AsyncSession, club_bot: ClubBot) -> None:
    """Повторно настраивает бота: нужно после смены PUBLIC_BASE_URL (например, нового туннеля)."""
    _require_https()
    club = await db.get(Club, club_bot.club_id)
    await configure_bot(registry.get(club_bot), club_bot, club)


async def set_staff_commands(bot: Bot, telegram_id: int) -> bool:
    """Показывает /admin в меню команд только этому сотруднику.

    Telegram вернёт ошибку, если человек ещё ни разу не писал боту, — тогда False.
    """
    try:
        await bot.set_my_commands(STAFF_COMMANDS, scope=BotCommandScopeChat(chat_id=telegram_id))
        return True
    except Exception:
        return False


async def reset_commands(bot: Bot, telegram_id: int) -> None:
    """Убирает /admin из меню у бывшего сотрудника (возвращает общее меню гостя)."""
    try:
        await bot.delete_my_commands(scope=BotCommandScopeChat(chat_id=telegram_id))
    except Exception:
        pass
