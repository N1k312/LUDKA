"""Сообщения гостю от бота клуба вне диалога (например, промокод после спина)."""

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import Bot, html

from app.models import Club, PrizeCode, PrizeOption

logger = logging.getLogger(__name__)


def local_date(value: datetime, club: Club) -> str:
    """Время хранится в UTC, гостю показывается в часовом поясе клуба."""
    return value.astimezone(ZoneInfo(club.timezone)).strftime("%d.%m.%Y")


def prize_message(club: Club, option: PrizeOption, code: PrizeCode) -> str:
    prize = f"{option.emoji} {option.title}" if option.emoji else option.title
    return (
        f"🎉 Вы выиграли: {html.bold(html.quote(prize))}\n\n"
        f"Промокод: {html.code(code.code)}\n"
        f"Действует до {local_date(code.expires_at, club)} включительно.\n\n"
        f"Покажите промокод администратору клуба {html.quote(club.name)}, чтобы получить приз."
    )


async def send_prize_code(
    bot: Bot, chat_id: int, club: Club, option: PrizeOption, code: PrizeCode
) -> None:
    """Дублирует промокод сообщением. Ошибка отправки не отменяет выигрыш."""
    try:
        await bot.send_message(chat_id, prize_message(club, option, code))
    except Exception:
        logger.exception("Не удалось отправить промокод гостю клуба %s", club.id)
