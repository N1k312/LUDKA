"""Команды суперадмина (в MVP вместо суперадминки).

Запуск из папки backend:  .venv\\Scripts\\python -m app.cli <команда> --help

  list-clubs                         клубы, боты, подписки
  create-club --name "..."           новый клуб: пробный период и колесо без призов
  add-prize --club-id N --title ... --weight N [--emoji 🎮] [--empty] [--ttl-days 14]
  list-prizes --club-id N            призы клуба с шансами
  connect-bot --club-id N            подключить бота; токен берётся из TEST_BOT_TOKEN в .env
                                     или запрашивается скрытым вводом
  add-staff --club-id N --telegram-id N [--role admin|owner]
  make-qr --club-id N [--out файл.png|.svg] [--startapp]   QR для печати у кассы
  set-cooldown --club-id N --seconds N   лимит спинов (604800 = 7 дней)
  refresh-webhooks                   перенастроить всех ботов после смены PUBLIC_BASE_URL
"""

import argparse
import asyncio
import getpass
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from aiogram.exceptions import TelegramAPIError, TelegramUnauthorizedError
from aiogram.utils.token import TokenValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bots.registry import registry
from app.bots.setup import BotSetupError, connect_bot, refresh_bot, set_staff_commands
from app.config import get_settings
from app.db import SessionLocal, engine
from app.services.qr import save_qr, start_link
from app.models import (
    BotStatus,
    Club,
    ClubBot,
    ClubStaff,
    GameConfig,
    GameType,
    PrizeOption,
    StaffRole,
    Subscription,
    SubscriptionStatus,
)

TRIAL_DAYS = 14
WHEEL_COOLDOWN_SECONDS = 7 * 24 * 3600


class CliError(Exception):
    pass


async def get_club(db: AsyncSession, club_id: int) -> Club:
    club = await db.get(Club, club_id)
    if club is None:
        raise CliError(f"Клуб id={club_id} не найден")
    return club


async def cmd_list_clubs(db: AsyncSession, args: argparse.Namespace) -> None:
    rows = await db.execute(
        select(Club, ClubBot, Subscription)
        .outerjoin(ClubBot, ClubBot.club_id == Club.id)
        .outerjoin(Subscription, Subscription.club_id == Club.id)
        .order_by(Club.id)
    )
    for club, bot, sub in rows:
        bot_text = f"@{bot.username} ({bot.status})" if bot else "бот не подключён"
        sub_text = sub.status if sub else "нет подписки"
        print(f"{club.id:>4}  {club.name}  |  {bot_text}  |  подписка: {sub_text}")


async def cmd_create_club(db: AsyncSession, args: argparse.Namespace) -> None:
    club = Club(name=args.name, timezone=args.timezone)
    db.add(club)
    await db.flush()
    db.add(
        Subscription(
            club_id=club.id,
            status=SubscriptionStatus.TRIAL,
            trial_ends_at=datetime.now(UTC) + timedelta(days=TRIAL_DAYS),
        )
    )
    db.add(
        GameConfig(
            club_id=club.id, game_type=GameType.WHEEL, cooldown_seconds=WHEEL_COOLDOWN_SECONDS
        )
    )
    print(f"Создан клуб id={club.id} «{club.name}», пробный период {TRIAL_DAYS} дней")


async def cmd_add_prize(db: AsyncSession, args: argparse.Namespace) -> None:
    club = await get_club(db, args.club_id)
    position = await db.scalar(
        select(func.coalesce(func.max(PrizeOption.position) + 1, 0)).where(
            PrizeOption.club_id == club.id
        )
    )
    db.add(
        PrizeOption(
            club_id=club.id,
            game_type=GameType.WHEEL,
            title=args.title,
            emoji=args.emoji,
            weight=args.weight,
            is_empty=args.empty,
            ttl_days=args.ttl_days,
            position=position,
        )
    )
    print(f"Приз «{args.title}» (вес {args.weight}) добавлен в клуб «{club.name}»")


async def cmd_list_prizes(db: AsyncSession, args: argparse.Namespace) -> None:
    club = await get_club(db, args.club_id)
    prizes = (
        await db.scalars(
            select(PrizeOption)
            .where(PrizeOption.club_id == club.id, PrizeOption.active.is_(True))
            .order_by(PrizeOption.position)
        )
    ).all()
    total = sum(p.weight for p in prizes) or 1
    for p in prizes:
        mark = " (пустой)" if p.is_empty else ""
        print(f"{p.id:>4}  {p.emoji or ' '} {p.title}{mark}  вес {p.weight}  ≈{p.weight / total:.0%}")


async def cmd_connect_bot(db: AsyncSession, args: argparse.Namespace) -> None:
    club = await get_club(db, args.club_id)
    secret = get_settings().test_bot_token
    token = secret.get_secret_value() if secret else ""
    if not token:
        token = getpass.getpass("Токен бота (ввод скрыт): ").strip()
    try:
        club_bot = await connect_bot(db, club, token)
    except TokenValidationError:
        raise CliError("Токен имеет неверный формат") from None
    except TelegramUnauthorizedError:
        raise CliError("Telegram отклонил токен: проверьте его в @BotFather") from None
    print(f"Бот @{club_bot.username} подключён к клубу «{club.name}», вебхук установлен")


async def cmd_add_staff(db: AsyncSession, args: argparse.Namespace) -> None:
    club = await get_club(db, args.club_id)
    staff = await db.scalar(
        select(ClubStaff).where(
            ClubStaff.club_id == club.id, ClubStaff.telegram_id == args.telegram_id
        )
    )
    if staff is None:
        staff = ClubStaff(club_id=club.id, telegram_id=args.telegram_id)
        db.add(staff)
    staff.role = StaffRole(args.role)
    print(f"Telegram id {args.telegram_id} — {args.role} в клубе «{club.name}»")
    club_bot = await db.scalar(select(ClubBot).where(ClubBot.club_id == club.id))
    if club_bot is not None:
        if await set_staff_commands(registry.get(club_bot), args.telegram_id):
            print("Команда /admin добавлена в меню бота у этого сотрудника")
        else:
            print("Меню команд не обновлено: сотрудник ещё не писал боту. /admin всё равно работает")


async def cmd_set_cooldown(db: AsyncSession, args: argparse.Namespace) -> None:
    club = await get_club(db, args.club_id)
    config = await db.get(GameConfig, (club.id, GameType.WHEEL))
    if config is None:
        config = GameConfig(club_id=club.id, game_type=GameType.WHEEL)
        db.add(config)
    config.cooldown_seconds = args.seconds
    print(f"Лимит колеса в клубе «{club.name}»: один спин в {args.seconds} с")


async def cmd_make_qr(db: AsyncSession, args: argparse.Namespace) -> None:
    club = await get_club(db, args.club_id)
    club_bot = await db.scalar(select(ClubBot).where(ClubBot.club_id == club.id))
    if club_bot is None:
        raise CliError("У клуба нет подключённого бота: сначала connect-bot")
    link = start_link(club_bot.username, startapp=args.startapp)
    out = Path(args.out or f"qr/club-{club.id}.png").resolve()
    save_qr(link, out)
    print(f"Ссылка: {link}")
    print(f"QR-код: {out}")


async def cmd_refresh_webhooks(db: AsyncSession, args: argparse.Namespace) -> None:
    bots = (
        await db.scalars(select(ClubBot).where(ClubBot.status == BotStatus.ACTIVE))
    ).all()
    for club_bot in bots:
        await refresh_bot(db, club_bot)
        print(f"@{club_bot.username}: вебхук → {get_settings().base_url}/tg/{club_bot.bot_id}")
    if not bots:
        print("Нет подключённых ботов")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="app.cli", description="Команды суперадмина")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list-clubs", help="клубы, боты, подписки").set_defaults(func=cmd_list_clubs)

    p = sub.add_parser("create-club", help="создать клуб")
    p.add_argument("--name", required=True)
    p.add_argument("--timezone", default="Asia/Almaty")
    p.set_defaults(func=cmd_create_club)

    p = sub.add_parser("add-prize", help="добавить приз на колесо")
    p.add_argument("--club-id", type=int, required=True)
    p.add_argument("--title", required=True)
    p.add_argument("--weight", type=int, required=True)
    p.add_argument("--emoji")
    p.add_argument("--empty", action="store_true", help="пустой сектор")
    p.add_argument("--ttl-days", type=int, default=14)
    p.set_defaults(func=cmd_add_prize)

    p = sub.add_parser("list-prizes", help="призы клуба")
    p.add_argument("--club-id", type=int, required=True)
    p.set_defaults(func=cmd_list_prizes)

    p = sub.add_parser("connect-bot", help="подключить бота к клубу")
    p.add_argument("--club-id", type=int, required=True)
    p.set_defaults(func=cmd_connect_bot)

    p = sub.add_parser("add-staff", help="назначить сотрудника клуба")
    p.add_argument("--club-id", type=int, required=True)
    p.add_argument("--telegram-id", type=int, required=True)
    p.add_argument("--role", choices=[r.value for r in StaffRole], default=StaffRole.ADMIN.value)
    p.set_defaults(func=cmd_add_staff)

    p = sub.add_parser("set-cooldown", help="как часто можно крутить колесо (в секундах)")
    p.add_argument("--club-id", type=int, required=True)
    p.add_argument("--seconds", type=int, required=True, help="604800 = 7 дней")
    p.set_defaults(func=cmd_set_cooldown)

    p = sub.add_parser("make-qr", help="QR-код со ссылкой на бота для печати у кассы")
    p.add_argument("--club-id", type=int, required=True)
    p.add_argument("--out", help="файл .png или .svg (по умолчанию qr/club-N.png)")
    p.add_argument("--startapp", action="store_true",
                   help="ссылка сразу открывает Mini App (нужен Main Mini App в @BotFather)")
    p.set_defaults(func=cmd_make_qr)

    sub.add_parser(
        "refresh-webhooks", help="перенастроить ботов после смены PUBLIC_BASE_URL"
    ).set_defaults(func=cmd_refresh_webhooks)
    return parser


async def run(args: argparse.Namespace) -> int:
    try:
        async with SessionLocal() as db:
            await args.func(db, args)
            await db.commit()
        return 0
    except (CliError, BotSetupError) as e:
        print(f"Ошибка: {e}", file=sys.stderr)
        return 1
    except TelegramAPIError as e:
        print(f"Ошибка Telegram: {e.message}", file=sys.stderr)
        return 1
    finally:
        await registry.close()
        await engine.dispose()


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    args = build_parser().parse_args()
    sys.exit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
