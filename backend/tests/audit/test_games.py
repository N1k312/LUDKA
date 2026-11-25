"""Три игры: колесо, стаканчики, кейс — API, редкость, лимиты, включение, бот клуба и пульт."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.bots.admin import GAMES_BUTTON
from app.bots.dispatcher import dp
from app.bots.prize_editor import ADD_GAME, GAME_LIMIT, GAME_TOGGLE, KIND, LIST_GAME
from app.models import ClubStaff, GameConfig, GameType, Guest, Play, PrizeCode, PrizeOption, StaffRole
from app.services import clubs, prizes
from app.services.wheel import CUPS_COUNT
from tests.audit.conftest import BOT_A, TOKEN_A, TOKEN_B, api, club_with_bot, nav
from tests.botkit import BotClient, callback_update, message_update

SUPER, ADMIN, GUEST = 3001, 3002, 555
WEEK = 7 * 24 * 3600


@pytest.fixture(autouse=True)
def clean_fsm():
    dp.fsm.storage.storage.clear()
    yield
    dp.fsm.storage.storage.clear()


async def setup(session, telegram, client, enable=(GameType.CUPS, GameType.CASE)):
    club, club_bot = await club_with_bot(session, "Arena", TOKEN_A)
    for game in (GameType.CUPS, GameType.CASE):
        await clubs.add_prize(session, club, f"Приз {game}", 10, "🎁", game=game)
        await clubs.add_prize(session, club, "Пусто", 10, None, is_empty=True, game=game)
    for game in enable:
        await clubs.set_game_enabled(session, club, game, True)
    session.add_all([
        Guest(club_id=club.id, telegram_id=GUEST, phone="+77010000000", consent_at=datetime.now(UTC)),
        ClubStaff(club_id=club.id, telegram_id=SUPER, role=StaffRole.OWNER),
        ClubStaff(club_id=club.id, telegram_id=ADMIN, role=StaffRole.ADMIN),
    ])
    await session.flush()
    return club, BotClient(client, telegram, club_bot)


def h():
    return api(TOKEN_A, BOT_A, GUEST)


# --- Настройки по умолчанию -------------------------------------------------------------------


async def test_new_club_has_three_games_only_wheel_on(session):
    club = await clubs.create_club(session, "Новый")

    configs = await clubs.game_configs(session, club)

    assert {g: c.enabled for g, c in configs.items()} == {
        GameType.WHEEL: True, GameType.CUPS: False, GameType.CASE: False}
    assert all(c.cooldown_seconds == WEEK for c in configs.values())


async def test_me_lists_games_with_timers(session, telegram, client):
    await setup(session, telegram, client, enable=(GameType.CUPS,))

    games = (await client.get("/api/me", headers=h())).json()["games"]

    assert [(g["type"], g["enabled"]) for g in games] == [("wheel", True), ("cups", True), ("case", False)]
    assert all(g["next_play_at"] is None for g in games)


# --- Редкость вместо шансов ------------------------------------------------------------------


@pytest.mark.parametrize("weight,expected", [(1, "legendary"), (5, "legendary"), (6, "rare"), (20, "rare"),
                                             (21, "common"), (60, "common")])
def test_rarity_thresholds(weight, expected):
    prize = PrizeOption(weight=weight, is_empty=False)
    rest = PrizeOption(weight=100 - weight, is_empty=True)

    assert prizes.rarity(prize, [prize, rest]) == expected


async def test_guest_sees_rarity_but_not_weights(session, telegram, client):
    await setup(session, telegram, client)

    response = await client.get("/api/games/cups", headers=h())

    items = response.json()["prizes"]
    assert {i["rarity"] for i in items} == {"common", "empty"}
    assert "weight" not in response.text and "chance" not in response.text


async def test_prize_that_cannot_drop_is_hidden_from_guest(session, telegram, client):
    """Баг аудита 25.11: приз с весом 0 показывался «очень редким», хотя выпасть не может."""
    club, _ = await setup(session, telegram, client)
    await clubs.add_prize(session, club, "Приманка", 0, "💎")

    titles = [p["title"] for p in (await client.get("/api/games/wheel", headers=h())).json()["prizes"]]
    sectors = [p["title"] for p in (await client.get("/api/wheel", headers=h())).json()["sectors"]]

    assert "Приманка" not in titles and "Приманка" not in sectors
    assert titles == ["1 час игры"]


async def test_parallel_requests_create_missing_game_settings_once(engine):
    """Баг аудита 25.11: два одновременных запроса к клубу без настроек игр — повторная вставка падала."""
    import asyncio

    from sqlalchemy import delete
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.models import Club

    async with AsyncSession(engine) as db:
        club_id = (await clubs.create_club(db, "Гонка настроек")).id
        await db.execute(delete(GameConfig).where(GameConfig.club_id == club_id))
        await db.commit()
    try:
        async def open_app():
            async with AsyncSession(engine) as db:
                configs = await clubs.game_configs(db, await db.get(Club, club_id))
                await db.commit()
                return len(configs)

        assert await asyncio.gather(*(open_app() for _ in range(5))) == [3] * 5
        async with AsyncSession(engine) as db:
            rows = list(await db.scalars(select(GameConfig).where(GameConfig.club_id == club_id)))
            assert len(rows) == 3 and not any(r.enabled for r in rows)
    finally:
        async with AsyncSession(engine) as db:
            await db.execute(delete(Club).where(Club.id == club_id))
            await db.commit()


async def test_unknown_game_is_rejected(session, telegram, client):
    await setup(session, telegram, client)

    assert (await client.get("/api/games/slots", headers=h())).status_code == 422
    assert (await client.post("/api/play/slots", headers=h())).status_code == 422


# --- Стаканчики ------------------------------------------------------------------------------


async def test_cups_reveal_all_six_and_give_the_chosen_one(session, telegram, client):
    club, _ = await setup(session, telegram, client)

    body = (await client.post("/api/play/cups", headers=h(), json={"choice": 4})).json()

    assert len(body["layout"]) == CUPS_COUNT
    assert body["prize"] == body["layout"][4]
    play = await session.scalar(select(Play).where(Play.club_id == club.id))
    assert play.game_type == GameType.CUPS and play.prize_option_id == body["prize"]["id"]
    code = await session.scalar(select(PrizeCode).where(PrizeCode.club_id == club.id))
    assert (code is None) == body["prize"]["is_empty"]


@pytest.mark.parametrize("payload", [None, {}, {"choice": -1}, {"choice": 6}, {"choice": "x"}])
async def test_cups_need_a_valid_choice(session, telegram, client, payload):
    club, _ = await setup(session, telegram, client)

    response = await client.post("/api/play/cups", headers=h(), json=payload)

    assert response.status_code == 422
    assert await session.scalar(select(Play).where(Play.club_id == club.id)) is None


async def test_choice_does_not_change_the_odds(session, telegram, client):
    """Выбор номера не подкручивает результат: все стаканчики разыгрываются одинаково."""
    club, _ = await setup(session, telegram, client)
    config = await session.get(GameConfig, (club.id, GameType.CUPS))
    config.cooldown_seconds = 1
    seen = set()
    for _ in range(40):
        await session.execute(PrizeCode.__table__.delete().where(PrizeCode.club_id == club.id))
        await session.execute(Play.__table__.delete().where(Play.club_id == club.id))
        body = (await client.post("/api/play/cups", headers=h(), json={"choice": 0})).json()
        seen.update(p["is_empty"] for p in body["layout"])
    assert seen == {True, False}


# --- Кейс, лимиты, выключенные игры ----------------------------------------------------------


async def test_case_gives_one_prize_without_layout(session, telegram, client):
    await setup(session, telegram, client)

    body = (await client.post("/api/play/case", headers=h())).json()

    assert body["layout"] is None and body["prize"]["title"] in {"Приз case", "Пусто"}


async def test_each_game_has_its_own_limit(session, telegram, client):
    await setup(session, telegram, client)

    first = [(await client.post(f"/api/play/{g}", headers=h(), json=c)).status_code
             for g, c in (("wheel", None), ("cups", {"choice": 0}), ("case", None))]
    again = await client.post("/api/play/case", headers=h())

    assert first == [200, 200, 200]
    assert again.status_code == 429 and again.json()["detail"]["code"] == "cooldown"


async def test_disabled_game_cannot_be_played(session, telegram, client):
    club, _ = await setup(session, telegram, client, enable=())

    response = await client.post("/api/play/case", headers=h())

    assert response.status_code == 409 and response.json()["detail"]["code"] == "game_disabled"
    assert await session.scalar(select(Play).where(Play.club_id == club.id)) is None


async def test_game_prizes_are_separate(session, telegram, client):
    club, _ = await setup(session, telegram, client)

    wheel = [p.title for p in await clubs.active_prizes(session, club, GameType.WHEEL)]
    cups = [p.title for p in await clubs.active_prizes(session, club, GameType.CUPS)]

    assert wheel == ["1 час игры"] and cups == ["Приз cups", "Пусто"]


# --- Бот клуба: «🎮 Игры» и призы по играм ---------------------------------------------------


def press(data, user_id=SUPER):
    return callback_update(data, user_id=user_id)


async def test_superadmin_sees_games_and_toggles_one(session, telegram, client):
    club, bot = await setup(session, telegram, client, enable=())

    [reply] = await bot.send(message_update(GAMES_BUTTON, user_id=SUPER))
    await bot.send(press(f"{GAME_TOGGLE}cups"))

    assert "Стаканчики: ⛔ выключена" in reply.text
    assert (await session.get(GameConfig, (club.id, GameType.CUPS))).enabled is True


async def test_game_without_prizes_cannot_be_enabled(session, telegram, client):
    club, bot = await setup(session, telegram, client, enable=())
    for p in await clubs.active_prizes(session, club, GameType.CASE):
        p.active = False
    await session.flush()

    await bot.send(press(f"{GAME_TOGGLE}case"))

    assert (await session.get(GameConfig, (club.id, GameType.CASE))).enabled is False
    assert "приз" in bot.telegram.calls[-1].text


async def test_last_enabled_game_cannot_be_disabled(session, telegram, client):
    club, bot = await setup(session, telegram, client, enable=())

    await bot.send(press(f"{GAME_TOGGLE}wheel"))

    assert (await session.get(GameConfig, (club.id, GameType.WHEEL))).enabled is True


@pytest.mark.parametrize("data,expected", [(f"{GAME_LIMIT}cups:86400", 86400), (f"{GAME_LIMIT}cups:60", WEEK),
                                           (f"{GAME_LIMIT}slots:86400", WEEK)])
async def test_limit_only_from_allowed_values(session, telegram, client, data, expected):
    club, bot = await setup(session, telegram, client)

    await bot.send(press(data))

    assert (await session.get(GameConfig, (club.id, GameType.CUPS))).cooldown_seconds == expected


@pytest.mark.parametrize("data", [f"{GAME_TOGGLE}cups", f"{GAME_LIMIT}cups:86400", f"{LIST_GAME}cups"])
async def test_admin_cannot_manage_games(session, telegram, client, data):
    club, bot = await setup(session, telegram, client, enable=())

    await bot.send(press(data, user_id=ADMIN))

    config = await session.get(GameConfig, (club.id, GameType.CUPS))
    assert config.enabled is False and config.cooldown_seconds == WEEK


async def test_prize_added_to_chosen_game(session, telegram, client):
    club, bot = await setup(session, telegram, client)

    await bot.send(press(f"{ADD_GAME}case"))
    await bot.send(message_update("👑 Ночь в VIP", user_id=SUPER))
    await bot.send(message_update("1", user_id=SUPER))
    await bot.send(press(f"{KIND}0"))
    await bot.send(message_update("7", user_id=SUPER))

    added = await session.scalar(select(PrizeOption).where(PrizeOption.title == "Ночь в VIP"))
    assert added.game_type == GameType.CASE and added.club_id == club.id


async def test_other_club_games_untouched(session, telegram, client):
    _, bot = await setup(session, telegram, client)
    other, _ = await club_with_bot(session, "Чужой", TOKEN_B)

    await bot.send(press(f"{GAME_LIMIT}cups:86400"))

    assert (await session.get(GameConfig, (other.id, GameType.CUPS))).cooldown_seconds == WEEK


# --- Пульт ----------------------------------------------------------------------------------


async def test_pult_toggles_game_and_sets_limit(session, pbot):
    club, _ = await club_with_bot(session, "А", TOKEN_A)
    await clubs.add_prize(session, club, "Кола", 5, "🥤", game=GameType.CUPS)

    await pbot.send(nav("game_on", club.id, 1))
    await pbot.send(nav("game_cd", club.id, 86400 * 10 + 1))
    await pbot.send(nav("game_cd", club.id, 12345 * 10 + 1))  # не из списка — отказ

    config = await session.get(GameConfig, (club.id, GameType.CUPS))
    await session.refresh(config)
    assert config.enabled is True and config.cooldown_seconds == 86400
    assert pbot.answers()[-1].show_alert


async def test_pult_prizes_per_game(session, pbot):
    club, _ = await club_with_bot(session, "А", TOKEN_A)
    await clubs.add_prize(session, club, "Кола", 5, "🥤", game=GameType.CUPS)

    await pbot.send(nav("prizes", club.id, 1))

    [text] = pbot.texts()
    assert "Стаканчики" in text and "Кола" in text and "1 час игры" not in text
