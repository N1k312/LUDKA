"""Бот платформы: токен в PLATFORM_BOT_TOKEN, вебхук POST /tg/platform.

Секрет вебхука выводится из ENCRYPTION_KEY, поэтому его не нужно хранить отдельно.
"""

import hashlib
import hmac
import logging

from aiogram import Bot
from aiogram.types import BotCommand

from app.bots.registry import registry
from app.config import get_settings

logger = logging.getLogger(__name__)

WEBHOOK_PATH = "/tg/platform"


def platform_token() -> str | None:
    token = get_settings().platform_bot_token
    return token.get_secret_value() if token and token.get_secret_value() else None


def platform_bot() -> Bot | None:
    token = platform_token()
    return registry.for_token(token) if token else None


def webhook_secret() -> str:
    key = get_settings().encryption_key.get_secret_value().encode()
    return hmac.new(key, b"platform-bot-webhook", hashlib.sha256).hexdigest()


async def configure_platform_bot() -> bool:
    """Ставит вебхук и меню бота платформы на текущий публичный адрес. False — бот не задан."""
    bot = platform_bot()
    settings = get_settings()
    if bot is None or not settings.base_url.startswith("https://"):
        return False
    await bot.set_webhook(
        url=f"{settings.base_url}{WEBHOOK_PATH}",
        secret_token=webhook_secret(),
        allowed_updates=["message", "callback_query"],
    )
    await bot.set_my_commands([
        BotCommand(command="start", description="Главное меню"),
        BotCommand(command="cancel", description="Отменить текущее действие"),
    ])
    await bot.set_my_description(
        description="Служебный бот платформы мини-игр: подключение клубов, призы, роли и подписки. "
                    "Доступен только владельцу платформы."
    )
    return True
