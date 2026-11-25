"""Сообщения от бота клуба вне диалога: промокод гостю, уведомление суперадмину клуба."""

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import Bot, html
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bots.registry import registry
from app.models import Club, ClubBot, ClubStaff, PrizeCode, PrizeOption, StaffRole

logger = logging.getLogger(__name__)


async def notify_club_owner(db: AsyncSession, club: Club, text: str) -> bool:
    """Сообщение суперадмину клуба через бот клуба. False — бота или суперадмина нет, или Telegram отказал."""
    club_bot = await db.scalar(select(ClubBot).where(ClubBot.club_id == club.id))
    owner = await db.scalar(select(ClubStaff).where(ClubStaff.club_id == club.id,
                                                    ClubStaff.role == StaffRole.OWNER))
    if club_bot is None or owner is None:
        return False
    try:
        await registry.get(club_bot).send_message(owner.telegram_id, text)
        return True
    except Exception:  # суперадмин мог не писать боту или заблокировать его
        logger.warning("Не удалось уведомить суперадмина клуба %s", club.id)
        return False


def local_date(value: datetime, club: Club) -> str:
    """Время хранится в UTC, гостю показывается в часовом поясе клуба."""
    return value.astimezone(ZoneInfo(club.timezone)).strftime("%d.%m.%Y")


def local_datetime(value: datetime, club: Club) -> str:
    return value.astimezone(ZoneInfo(club.timezone)).strftime("%d.%m.%Y %H:%M")


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
