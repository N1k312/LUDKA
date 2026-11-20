"""API для Mini App: /api/...

Каждый запрос несёт заголовки:
  Authorization: tma <initData>   — данные запуска Mini App от Telegram
  X-Bot-Id: <id бота>             — через какого бота открыт Mini App (из ?bot= в адресе)

Клуб определяется по боту, а подлинность initData проверяется токеном именно
этого бота и свежестью auth_date. Никакой club_id от клиента не принимается.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated

from aiogram import Bot
from aiogram.utils.web_app import WebAppUser, safe_parse_webapp_init_data
from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bots.notify import send_prize_code
from app.bots.registry import registry
from app.config import get_settings
from app.crypto import decrypt
from app.db import get_session
from app.models import MAX_BIGINT, BotStatus, Club, ClubBot, Guest, PrizeCode, PrizeOption
from app.services.codes import code_status
from app.services.guests import is_registered
from app.services.wheel import (
    SpinError,
    is_available,
    next_spin_at,
    spin,
    wheel_config,
    wheel_options,
)

router = APIRouter(prefix="/api", tags=["miniapp"])


def api_error(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


UNAUTHORIZED = ("unauthorized", "Откройте игру из бота клуба")


@dataclass
class MiniAppContext:
    club: Club
    bot: Bot
    user: WebAppUser
    guest: Guest | None


async def miniapp_context(
    db: Annotated[AsyncSession, Depends(get_session)],
    authorization: Annotated[str | None, Header()] = None,
    x_bot_id: Annotated[int | None, Header()] = None,
) -> MiniAppContext:
    if (not authorization or not authorization.startswith("tma ")
            or x_bot_id is None or not 0 < x_bot_id <= MAX_BIGINT):
        raise api_error(401, *UNAUTHORIZED)

    club_bot = await db.get(ClubBot, x_bot_id)
    if club_bot is None or club_bot.status != BotStatus.ACTIVE:
        raise api_error(401, *UNAUTHORIZED)

    try:
        data = safe_parse_webapp_init_data(
            decrypt(club_bot.token_encrypted), authorization.removeprefix("tma ")
        )
    except ValueError:
        raise api_error(401, *UNAUTHORIZED) from None

    age = (datetime.now(UTC) - data.auth_date).total_seconds()
    if data.user is None or age > get_settings().init_data_max_age_seconds:
        raise api_error(401, "session_expired", "Сессия устарела — откройте игру из бота заново")

    club = await db.get(Club, club_bot.club_id)
    guest = await db.scalar(
        select(Guest).where(Guest.club_id == club.id, Guest.telegram_id == data.user.id)
    )
    return MiniAppContext(club=club, bot=registry.get(club_bot), user=data.user, guest=guest)


Context = Annotated[MiniAppContext, Depends(miniapp_context)]
Db = Annotated[AsyncSession, Depends(get_session)]


def option_json(o: PrizeOption) -> dict:
    return {"id": o.id, "title": o.title, "emoji": o.emoji, "is_empty": o.is_empty}


@router.get("/me")
async def me(ctx: Context, db: Db) -> dict:
    registered = ctx.guest is not None and is_registered(ctx.guest)
    config = await wheel_config(db, ctx.club.id)
    wait_until = (
        await next_spin_at(db, ctx.guest, config) if registered and config is not None else None
    )
    return {
        "club": {"name": ctx.club.name, "timezone": ctx.club.timezone},
        "guest": {"first_name": ctx.user.first_name, "registered": registered},
        "available": await is_available(db, ctx.club),
        "wheel": {
            "enabled": config is not None and config.enabled,
            "cooldown_seconds": config.cooldown_seconds if config else None,
            "next_spin_at": wait_until,
        },
    }


@router.get("/wheel")
async def wheel(ctx: Context, db: Db) -> dict:
    return {"sectors": [option_json(o) for o in await wheel_options(db, ctx.club.id)]}


@router.post("/spin")
async def do_spin(ctx: Context, db: Db):
    if ctx.guest is None:
        raise api_error(403, "registration_required", "Сначала зарегистрируйтесь в боте")
    try:
        result = await spin(db, ctx.club, ctx.guest.id)
    except SpinError as e:
        # До отказа ничего не записано; блокировка гостя снимется при закрытии сессии.
        status = {"cooldown": 429, "registration_required": 403}.get(e.code, 409)
        return JSONResponse(
            status_code=status,
            content={
                "detail": {
                    "code": e.code,
                    "message": e.message,
                    "next_spin_at": e.next_spin_at.isoformat() if e.next_spin_at else None,
                }
            },
        )
    await db.commit()

    if result.code is not None:
        await send_prize_code(ctx.bot, ctx.user.id, ctx.club, result.option, result.code)

    return {
        "sector": option_json(result.option),
        "code": (
            {"code": result.code.code, "expires_at": result.code.expires_at}
            if result.code
            else None
        ),
        "next_spin_at": result.next_spin_at,
    }


@router.get("/prizes")
async def prizes(ctx: Context, db: Db) -> dict:
    if ctx.guest is None:
        return {"prizes": []}
    now = datetime.now(UTC)
    rows = await db.execute(
        select(PrizeCode, PrizeOption.emoji)
        .join(
            PrizeOption,
            (PrizeOption.club_id == PrizeCode.club_id) & (PrizeOption.id == PrizeCode.prize_option_id),
        )
        .where(PrizeCode.club_id == ctx.club.id, PrizeCode.guest_id == ctx.guest.id)
        .order_by(PrizeCode.created_at.desc())
    )
    return {
        "prizes": [
            {
                "code": c.code,
                "title": c.title,
                "emoji": emoji,
                "status": code_status(c, now),
                "created_at": c.created_at,
                "expires_at": c.expires_at,
                "redeemed_at": c.redeemed_at,
            }
            for c, emoji in rows
        ]
    }


@router.get("/rules")
async def rules(ctx: Context) -> dict:
    return {"club": ctx.club.name, "rules": ctx.club.rules_text}
