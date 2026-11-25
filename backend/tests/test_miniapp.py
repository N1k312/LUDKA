"""API Mini App: проверка initData, колесо, спин, промокоды, изоляция клубов."""

import asyncio
import hashlib
import hmac
import json
import time
from collections import Counter
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import pytest
from aiogram import methods
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.bots.setup import connect_bot
from app.db import get_session
from app.main import app
from app.models import (
    Club,
    ClubStatus,
    GameConfig,
    GameType,
    Guest,
    Play,
    PrizeCode,
    PrizeOption,
    Subscription,
    SubscriptionStatus,
)
from app.services import wheel
from app.services.codes import ALPHABET, PREFIX, generate_code

TOKEN_A = "123456789:AAFakeTokenForTestsOnly_abcdefghijk"
TOKEN_B = "987654321:AAAnotherFakeTokenForTests_abcdefghij"
BOT_A, BOT_B = 123456789, 987654321
USER_ID = 555


def init_data(token: str, user_id: int = USER_ID, auth_date: int | None = None) -> str:
    """initData, подписанный токеном бота, как это делает Telegram."""
    fields = {
        "auth_date": str(auth_date or int(time.time())),
        "query_id": "AAH-test",
        "user": json.dumps({"id": user_id, "first_name": "Гость"}, ensure_ascii=False),
    }
    check_string = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


def headers(token: str, bot_id: int, **kw) -> dict:
    return {"Authorization": f"tma {init_data(token, **kw)}", "X-Bot-Id": str(bot_id)}


async def make_club(
    db: AsyncSession,
    name: str,
    token: str,
    prizes: list[tuple[str, int, bool]],
    *,
    sub_status: SubscriptionStatus = SubscriptionStatus.TRIAL,
    trial_days: int = 14,
) -> Club:
    club = Club(name=name, rules_text=f"Правила {name}")
    db.add(club)
    await db.flush()
    db.add(
        Subscription(
            club_id=club.id,
            status=sub_status,
            trial_ends_at=datetime.now(UTC) + timedelta(days=trial_days),
        )
    )
    db.add(GameConfig(club_id=club.id, game_type=GameType.WHEEL, cooldown_seconds=7 * 24 * 3600))
    db.add_all(
        PrizeOption(club_id=club.id, game_type=GameType.WHEEL, title=title, weight=weight,
                    is_empty=empty, emoji="🎁", position=i)
        for i, (title, weight, empty) in enumerate(prizes)
    )
    await db.flush()
    await connect_bot(db, club, token)
    return club


async def make_guest(db: AsyncSession, club: Club, registered: bool = True) -> Guest:
    guest = Guest(
        club_id=club.id,
        telegram_id=USER_ID,
        phone=f"+7701{club.id:07d}" if registered else None,
        consent_at=datetime.now(UTC) if registered else None,
    )
    db.add(guest)
    await db.flush()
    return guest


@pytest.fixture
async def club_a(session, telegram) -> Club:
    return await make_club(session, "Клуб А", TOKEN_A, [("1 час игры", 1, False)])


@pytest.fixture
def auth_a() -> dict:
    return headers(TOKEN_A, BOT_A)


# --- Авторизация --------------------------------------------------------------------


async def test_no_auth_rejected(client, club_a):
    response = await client.get("/api/me")
    assert response.status_code == 401


async def test_tampered_init_data_rejected(client, club_a):
    data = init_data(TOKEN_A).replace("555", "556")  # подменили id пользователя
    response = await client.get(
        "/api/me", headers={"Authorization": f"tma {data}", "X-Bot-Id": str(BOT_A)}
    )
    assert response.status_code == 401


async def test_stale_init_data_rejected(client, club_a):
    old = int(time.time()) - 2 * 24 * 3600
    response = await client.get("/api/me", headers=headers(TOKEN_A, BOT_A, auth_date=old))
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "session_expired"


async def test_init_data_of_other_bot_rejected(session, client, club_a):
    """initData от бота клуба А не принимается от имени бота клуба Б."""
    await make_club(session, "Клуб Б", TOKEN_B, [("Скидка", 1, False)])

    response = await client.get("/api/me", headers=headers(TOKEN_A, BOT_B))

    assert response.status_code == 401


# --- /me, /wheel, /rules ---------------------------------------------------------------


async def test_me_unregistered(client, club_a, auth_a):
    response = await client.get("/api/me", headers=auth_a)

    assert response.status_code == 200
    body = response.json()
    assert body["club"]["name"] == "Клуб А"
    assert body["guest"]["registered"] is False
    assert body["available"] is True


async def test_wheel_returns_only_own_club_sectors(session, client, club_a, auth_a):
    await make_club(session, "Клуб Б", TOKEN_B, [("Чужой приз", 1, False)])

    sectors = (await client.get("/api/wheel", headers=auth_a)).json()["sectors"]

    assert [s["title"] for s in sectors] == ["1 час игры"]
    assert "weight" not in sectors[0]


async def test_rules(client, club_a, auth_a):
    body = (await client.get("/api/rules", headers=auth_a)).json()
    assert body["rules"] == "Правила Клуб А"


# --- Спин -----------------------------------------------------------------------------


async def test_spin_requires_registration(session, client, club_a, auth_a):
    await make_guest(session, club_a, registered=False)

    response = await client.post("/api/spin", headers=auth_a)

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "registration_required"


async def test_spin_creates_play_code_and_sends_message(session, client, telegram, club_a, auth_a):
    guest = await make_guest(session, club_a)
    telegram.calls.clear()

    response = await client.post("/api/spin", headers=auth_a)

    assert response.status_code == 200
    body = response.json()
    assert body["sector"]["title"] == "1 час игры"
    code = body["code"]["code"]
    assert code.startswith(PREFIX) and len(code) == len(PREFIX) + 6

    saved = await session.get(PrizeCode, code)
    assert saved.club_id == club_a.id and saved.guest_id == guest.id
    assert saved.expires_at - saved.created_at == timedelta(days=14)

    [message] = telegram.of(methods.SendMessage)
    assert message.chat_id == USER_ID and code in message.text


async def test_second_spin_hits_cooldown(session, client, club_a, auth_a):
    await make_guest(session, club_a)
    first = await client.post("/api/spin", headers=auth_a)

    second = await client.post("/api/spin", headers=auth_a)

    assert second.status_code == 429
    detail = second.json()["detail"]
    assert detail["code"] == "cooldown"
    assert detail["next_spin_at"] == first.json()["next_spin_at"].replace("Z", "+00:00")
    me = (await client.get("/api/me", headers=auth_a)).json()
    assert me["wheel"]["next_spin_at"] is not None


async def test_empty_sector_gives_no_code(session, client, telegram):
    club = await make_club(session, "Клуб А", TOKEN_A, [("Пусто", 1, True)])
    await make_guest(session, club)
    telegram.calls.clear()

    body = (await client.post("/api/spin", headers=headers(TOKEN_A, BOT_A))).json()

    assert body["sector"]["is_empty"] is True and body["code"] is None
    assert await session.scalar(select(func.count()).select_from(PrizeCode)) == 0
    assert await session.scalar(select(func.count()).select_from(Play)) == 1
    assert telegram.of(methods.SendMessage) == []


async def test_unpaid_club_still_plays(session, client, telegram):
    """Подписка продлевается сама: неоплата игры не останавливает."""
    club = await make_club(session, "Клуб А", TOKEN_A, [("1 час", 1, False)], trial_days=-40)
    await make_guest(session, club)

    response = await client.post("/api/spin", headers=headers(TOKEN_A, BOT_A))

    assert response.status_code == 200


async def test_spin_unavailable_when_access_disabled(session, client, telegram):
    club = await make_club(session, "Клуб А", TOKEN_A, [("1 час", 1, False)])
    await make_guest(session, club)
    club.status = ClubStatus.DISABLED
    await session.flush()

    response = await client.post("/api/spin", headers=headers(TOKEN_A, BOT_A))
    me = (await client.get("/api/me", headers=headers(TOKEN_A, BOT_A))).json()

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "unavailable"
    assert me["available"] is False


# --- Мои призы и изоляция ------------------------------------------------------------


async def test_prizes_are_isolated_between_clubs(session, client, club_a, auth_a):
    """Один человек в двух клубах: призы клуба А не видны в клубе Б."""
    await make_club(session, "Клуб Б", TOKEN_B, [("Скидка", 1, False)])
    club_b = await session.scalar(select(Club).where(Club.name == "Клуб Б"))
    await make_guest(session, club_a)
    await make_guest(session, club_b)
    auth_b = headers(TOKEN_B, BOT_B)

    code_a = (await client.post("/api/spin", headers=auth_a)).json()["code"]["code"]

    prizes_a = (await client.get("/api/prizes", headers=auth_a)).json()["prizes"]
    prizes_b = (await client.get("/api/prizes", headers=auth_b)).json()["prizes"]
    assert [p["code"] for p in prizes_a] == [code_a]
    assert prizes_a[0]["status"] == "active"
    assert prizes_b == []
    # Лимит в клубе Б не зависит от спина в клубе А.
    assert (await client.post("/api/spin", headers=auth_b)).status_code == 200


async def test_prize_statuses(session, client, club_a, auth_a):
    await make_guest(session, club_a)
    code = (await client.post("/api/spin", headers=auth_a)).json()["code"]["code"]
    saved = await session.get(PrizeCode, code)

    saved.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await session.flush()
    assert (await client.get("/api/prizes", headers=auth_a)).json()["prizes"][0]["status"] == "expired"

    saved.redeemed_at = datetime.now(UTC)
    await session.flush()
    assert (await client.get("/api/prizes", headers=auth_a)).json()["prizes"][0]["status"] == "redeemed"


# --- Генераторы -----------------------------------------------------------------------


def test_generate_code_format():
    codes = {generate_code() for _ in range(1000)}
    assert len(codes) == 1000
    for code in codes:
        assert code.startswith(PREFIX)
        assert set(code[len(PREFIX):]) <= set(ALPHABET)
    assert not set("01OIL") & set(ALPHABET)


def test_pick_option_respects_weights(monkeypatch):
    options = [PrizeOption(id=1, weight=3), PrizeOption(id=2, weight=0), PrizeOption(id=3, weight=1)]
    picks = Counter()
    for point in range(4):  # перебираем все значения randbelow(4)
        monkeypatch.setattr(wheel.secrets, "randbelow", lambda total, p=point: p)
        picks[wheel.pick_option(options).id] += 1
    assert picks == {1: 3, 3: 1}


def test_pick_option_without_weights():
    with pytest.raises(wheel.SpinError):
        wheel.pick_option([PrizeOption(id=1, weight=0)])


# --- Двойное нажатие: реальная конкуренция двух транзакций ------------------------------


async def test_concurrent_spins_give_one_play(engine: AsyncEngine, telegram):
    """Два одновременных запроса в разных соединениях: ровно один спин."""
    async with AsyncSession(engine, expire_on_commit=False) as db:
        club = await make_club(db, "Гонка", TOKEN_A, [("1 час", 1, False)])
        await make_guest(db, club)
        await db.commit()

    async def own_session():
        async with AsyncSession(engine, expire_on_commit=False) as s:
            yield s

    app.dependency_overrides[get_session] = own_session
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            auth = headers(TOKEN_A, BOT_A)
            responses = await asyncio.gather(
                c.post("/api/spin", headers=auth), c.post("/api/spin", headers=auth)
            )
        assert sorted(r.status_code for r in responses) == [200, 429]
        async with AsyncSession(engine) as db:
            plays = await db.scalar(select(func.count()).select_from(Play).where(Play.club_id == club.id))
            assert plays == 1
    finally:
        app.dependency_overrides.clear()
        async with AsyncSession(engine) as db:
            await db.execute(delete(Club).where(Club.id == club.id))
            await db.commit()
