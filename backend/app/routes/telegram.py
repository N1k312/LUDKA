"""Вебхуки ботов клубов: POST /tg/{bot_id}.

Клуб определяется только по боту из адреса и секрету в заголовке;
никакой club_id из тела запроса не используется.
"""

import hmac
import logging

from aiogram.types import Update
from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.bots.dispatcher import dp
from app.bots.registry import registry
from app.db import get_session
from app.models import MAX_BIGINT, BotStatus, Club, ClubBot, ClubStatus

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/tg/{bot_id}", include_in_schema=False)
async def telegram_webhook(
    bot_id: int,
    request: Request,
    db: AsyncSession = Depends(get_session),
    secret: str | None = Header(default=None, alias="X-Telegram-Bot-Api-Secret-Token"),
) -> Response:
    # id вне диапазона BIGINT заведомо не наш бот (иначе база ответит ошибкой → 500).
    club_bot = await db.get(ClubBot, bot_id) if 0 < bot_id <= MAX_BIGINT else None
    # Один и тот же ответ для «нет такого бота» и «неверный секрет».
    if (
        club_bot is None
        or club_bot.status != BotStatus.ACTIVE
        or secret is None
        or not hmac.compare_digest(secret.encode(), club_bot.webhook_secret.encode())
    ):
        raise HTTPException(status_code=403)

    club = await db.get(Club, club_bot.club_id)
    if club is None or club.status != ClubStatus.ACTIVE:
        return Response(status_code=200)

    bot = registry.get(club_bot)
    update = Update.model_validate(await request.json(), context={"bot": bot})
    try:
        await dp.feed_update(bot, update, club=club, db=db)
        await db.commit()
    except Exception:
        # Ответ 200, чтобы Telegram не повторял обновление бесконечно.
        await db.rollback()
        logger.exception("Ошибка обработки обновления бота %s", bot_id)
    return Response(status_code=200)
