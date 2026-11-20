"""Вебхук бота платформы: POST /tg/platform (объявлен раньше /tg/{bot_id})."""

import hmac
import logging

from aiogram.types import Update
from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.platform_bot.dispatcher import platform_dp
from app.platform_bot.setup import WEBHOOK_PATH, platform_bot, webhook_secret

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post(WEBHOOK_PATH, include_in_schema=False)
async def platform_webhook(
    request: Request,
    db: AsyncSession = Depends(get_session),
    secret: str | None = Header(default=None, alias="X-Telegram-Bot-Api-Secret-Token"),
) -> Response:
    bot = platform_bot()
    if bot is None or secret is None or not hmac.compare_digest(secret.encode(), webhook_secret().encode()):
        raise HTTPException(status_code=403)
    update = Update.model_validate(await request.json(), context={"bot": bot})
    try:
        await platform_dp.feed_update(bot, update, db=db)
        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception("Ошибка обработки обновления бота платформы")
    return Response(status_code=200)
