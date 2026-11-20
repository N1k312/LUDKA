"""Аудит 2/3 — ФУНКЦИОНАЛ: сквозные сценарии по требованиям, через настоящие точки входа.

Вебхуки ботов, API Mini App и бот платформы вызываются так же, как их вызывает Telegram.
"""

from collections import Counter
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from aiogram import methods
from pydantic import SecretStr
from sqlalchemy import select, update

from app.bots.admin import PICK_ADMIN, REDEEM_PREFIX, REMOVE_PREFIX
from app.config import get_settings
from app.models import (
    Club,
    ClubBot,
    ClubStaff,
    Guest,
    Payment,
    Play,
    PrizeCode,
    PrizeOption,
    StaffRole,
    Subscription,
    SubscriptionStatus,
)
from app.platform_bot.handlers import PICK_SUPERADMIN
from app.services import subscription
from app.services.tunnel import sync_public_url
from app.services.wheel import pick_option
from tests.audit.conftest import (
    BOT_A,
    BOT_B,
    OWNER,
    PLATFORM_TOKEN,
    TOKEN_A,
    TOKEN_B,
    api,
    club_with_bot,
    nav,
    register,
    say,
)
from tests.botkit import BotClient, callback_update, message_update, users_shared_update

GUEST, ADMIN, SUPER = 555, 1001, 1002


def texts(calls) -> str:
    return "\n".join(c.text for c in calls)


# --- F1. Полный путь гостя: QR → регистрация → спин → промокод → погашение → следующий спин


async def test_f01_full_guest_journey(session, telegram, client):
    club, club_bot = await club_with_bot(session, "Arena", TOKEN_A)
    session.add(ClubStaff(club_id=club.id, telegram_id=ADMIN, role=StaffRole.ADMIN))
    bot = BotClient(client, telegram, club_bot)
    h = api(TOKEN_A, BOT_A, GUEST)

    await register(bot, GUEST, "77010000000")
    me = (await client.get("/api/me", headers=h)).json()
    assert me["guest"]["registered"] and me["available"] and me["wheel"]["next_spin_at"] is None

    telegram.calls.clear()
    spin = (await client.post("/api/spin", headers=h)).json()
    code = spin["code"]["code"]
    assert code in texts(telegram.of(methods.SendMessage))  # промокод продублирован в чат
    assert (await client.get("/api/prizes", headers=h)).json()["prizes"][0]["status"] == "active"

    [check] = await bot.send(message_update(code.lower(), user_id=ADMIN))
    assert "действителен" in check.text
    to_guest = await bot.send(callback_update(REDEEM_PREFIX + code, user_id=ADMIN))
    assert any(m.chat_id == GUEST and "Приз получен" in m.text for m in to_guest)
    assert (await client.get("/api/prizes", headers=h)).json()["prizes"][0]["status"] == "redeemed"

    assert (await client.post("/api/spin", headers=h)).status_code == 429
    await session.execute(update(Play).where(Play.club_id == club.id)
                          .values(played_at=Play.played_at - timedelta(days=7, seconds=1)))
    assert (await client.post("/api/spin", headers=h)).status_code == 200


# --- F2. Подключение клуба через бот платформы → роли → игра → погашение ------------


async def test_f02_platform_onboarding_to_first_redemption(session, telegram, client, pbot):
    await pbot.send(nav("new"))
    await pbot.send(say("Cyber Arena"))
    club = await session.scalar(select(Club).where(Club.name == "Cyber Arena"))
    await pbot.send(nav("bot", club.id))
    await pbot.send(say(TOKEN_A))
    for title, weight, empty in (("🎮 1 час игры", "1", 0),):
        await pbot.send(nav("prize_add", club.id))
        await pbot.send(say(title))
        await pbot.send(say(weight))
        await pbot.send(nav("prize_kind", club.id, empty))
    await pbot.send(nav("super", club.id))
    await pbot.send(users_shared_update(PICK_SUPERADMIN, SUPER, user_id=OWNER))

    club_bot = await session.scalar(select(ClubBot).where(ClubBot.club_id == club.id))
    bot = BotClient(client, telegram, club_bot)
    await bot.send(users_shared_update(PICK_ADMIN, ADMIN, user_id=SUPER))  # суперадмин назначает админа
    await register(bot, GUEST, "77010000000")
    code = (await client.post("/api/spin", headers=api(TOKEN_A, BOT_A, GUEST))).json()["code"]["code"]
    await bot.send(callback_update(REDEEM_PREFIX + code, user_id=ADMIN))

    roles = dict((await session.execute(select(ClubStaff.telegram_id, ClubStaff.role)
                                        .where(ClubStaff.club_id == club.id))).all())
    assert roles == {SUPER: StaffRole.OWNER, ADMIN: StaffRole.ADMIN}
    assert (await session.get(PrizeCode, code)).redeemed_by == ADMIN


# --- F3. Жизненный цикл подписки по датам --------------------------------------------


async def test_f03_subscription_lifecycle(session, telegram, client):
    club, _ = await club_with_bot(session, "Arena", TOKEN_A)
    session.add(Guest(club_id=club.id, telegram_id=GUEST, phone="+77010000000", consent_at=datetime.now(UTC)))
    sub = await session.get(Subscription, club.id)
    h = api(TOKEN_A, BOT_A, GUEST)

    async def available() -> bool:
        await session.flush()
        return (await client.get("/api/me", headers=h)).json()["available"]

    now = datetime.now(UTC)
    stages = []
    for trial_end in (now + timedelta(days=1), now - timedelta(days=1), now - timedelta(days=4)):
        sub.trial_ends_at = trial_end
        await subscription.refresh_statuses(session)
        stages.append((sub.status, await available()))
    await subscription.extend(session, sub, 30, Decimal("15000"), "счёт")
    stages.append((sub.status, await available()))

    assert stages == [(SubscriptionStatus.TRIAL, True), (SubscriptionStatus.PAST_DUE, True),
                      (SubscriptionStatus.CANCELED, False), (SubscriptionStatus.ACTIVE, True)]
    assert (await session.scalar(select(Payment).where(Payment.club_id == club.id))).amount == 15000


# --- F4. Лимит спинов настраивается из пульта ------------------------------------------


async def test_f04_cooldown_set_from_platform_applies(session, telegram, client, pbot):
    club, _ = await club_with_bot(session, "Arena", TOKEN_A)
    session.add(Guest(club_id=club.id, telegram_id=GUEST, phone="+77010000000", consent_at=datetime.now(UTC)))
    h = api(TOKEN_A, BOT_A, GUEST)

    await pbot.send(nav("limit_set", club.id, 60))
    first = (await client.post("/api/spin", headers=h)).json()
    blocked = await client.post("/api/spin", headers=h)
    await session.execute(update(Play).values(played_at=Play.played_at - timedelta(seconds=61)))
    again = await client.post("/api/spin", headers=h)

    wait = datetime.fromisoformat(first["next_spin_at"]) - datetime.now(UTC)
    assert timedelta(seconds=50) < wait <= timedelta(seconds=60)
    assert blocked.status_code == 429 and again.status_code == 200


# --- F5. Срок действия промокода ------------------------------------------------------------


async def test_f05_code_expiry_everywhere(session, telegram, client):
    club, club_bot = await club_with_bot(session, "Arena", TOKEN_A)
    session.add_all([Guest(club_id=club.id, telegram_id=GUEST, phone="+77010000000", consent_at=datetime.now(UTC)),
                     ClubStaff(club_id=club.id, telegram_id=ADMIN, role=StaffRole.ADMIN)])
    h = api(TOKEN_A, BOT_A, GUEST)
    code = (await client.post("/api/spin", headers=h)).json()["code"]["code"]
    await session.execute(update(PrizeCode).values(expires_at=datetime.now(UTC) - timedelta(seconds=1)))
    bot = BotClient(client, telegram, club_bot)

    [check] = await bot.send(message_update(code, user_id=ADMIN))
    await bot.send(callback_update(REDEEM_PREFIX + code, user_id=ADMIN))

    assert (await client.get("/api/prizes", headers=h)).json()["prizes"][0]["status"] == "expired"
    assert "истёк" in check.text and check.reply_markup is None
    assert (await session.get(PrizeCode, code)).redeemed_at is None


# --- F6. Шансы колеса соответствуют весам (настоящий secrets, 20 000 спинов) -----------


def test_f06_prize_distribution_matches_weights():
    options = [PrizeOption(id=1, weight=70), PrizeOption(id=2, weight=30), PrizeOption(id=3, weight=0)]
    counts = Counter(pick_option(options).id for _ in range(20_000))
    assert counts[3] == 0
    assert 0.68 < counts[1] / 20_000 < 0.72


# --- F7. Правила из пульта видны гостю везде ---------------------------------------------


async def test_f07_rules_from_platform_reach_guest(session, telegram, client, pbot):
    club, club_bot = await club_with_bot(session, "Arena", TOKEN_A)
    await pbot.send(nav("rules", club.id))
    await pbot.send(say("Новые правила: раз в неделю"))

    [reply] = await BotClient(client, telegram, club_bot).send(message_update("/rules", user_id=GUEST))
    rules = (await client.get("/api/rules", headers=api(TOKEN_A, BOT_A, GUEST))).json()

    assert "Новые правила" in reply.text and rules["rules"] == "Новые правила: раз в неделю"


# --- F8. Два клуба, один человек: всё раздельно ------------------------------------------


async def test_f08_two_clubs_same_person(session, telegram, client):
    _, bot_a = await club_with_bot(session, "Клуб А", TOKEN_A)
    _, bot_b = await club_with_bot(session, "Клуб Б", TOKEN_B)
    a, b = BotClient(client, telegram, bot_a), BotClient(client, telegram, bot_b)

    [hello_b] = await b.send(message_update("/start", user_id=GUEST))
    await register(a, GUEST, "77010000000")
    await register(b, GUEST, "77010000000")  # тот же номер в другом клубе — можно
    code_a = (await client.post("/api/spin", headers=api(TOKEN_A, BOT_A, GUEST))).json()["code"]["code"]
    spin_b = await client.post("/api/spin", headers=api(TOKEN_B, BOT_B, GUEST))

    assert "Клуб Б" in hello_b.text
    assert spin_b.status_code == 200  # лимит клуба А не мешает клубу Б
    prizes_b = (await client.get("/api/prizes", headers=api(TOKEN_B, BOT_B, GUEST))).json()["prizes"]
    assert code_a not in [p["code"] for p in prizes_b]


# --- F9. Жизненный цикл сотрудника ---------------------------------------------------------


async def test_f09_staff_lifecycle(session, telegram, client):
    club, club_bot = await club_with_bot(session, "Arena", TOKEN_A)
    session.add(ClubStaff(club_id=club.id, telegram_id=SUPER, role=StaffRole.OWNER))
    session.add(Guest(club_id=club.id, telegram_id=GUEST, phone="+77010000000", consent_at=datetime.now(UTC)))
    code = (await client.post("/api/spin", headers=api(TOKEN_A, BOT_A, GUEST))).json()["code"]["code"]
    bot = BotClient(client, telegram, club_bot)

    await bot.send(users_shared_update(PICK_ADMIN, ADMIN, user_id=SUPER))
    [as_admin] = await bot.send(message_update(code, user_id=ADMIN))
    await bot.send(callback_update(f"{REMOVE_PREFIX}{ADMIN}", user_id=SUPER))
    [as_ex_admin] = await bot.send(message_update(code, user_id=ADMIN))

    assert "действителен" in as_admin.text
    assert "действителен" not in as_ex_admin.text  # убранный админ больше не проверяет коды


async def test_f10_staff_can_also_play_as_guest(session, telegram, client):
    club, club_bot = await club_with_bot(session, "Arena", TOKEN_A)
    session.add(ClubStaff(club_id=club.id, telegram_id=ADMIN, role=StaffRole.ADMIN))
    await register(BotClient(client, telegram, club_bot), ADMIN, "77010000000")

    spin = await client.post("/api/spin", headers=api(TOKEN_A, BOT_A, ADMIN))

    assert spin.status_code == 200


# --- F11. Смена адреса туннеля перенастраивает всех ботов, включая пульт -------------


async def test_f11_tunnel_change_reconfigures_every_bot(session, telegram, monkeypatch):
    monkeypatch.setattr(get_settings(), "platform_bot_token", SecretStr(PLATFORM_TOKEN))
    await club_with_bot(session, "А", TOKEN_A)
    await club_with_bot(session, "Б", TOKEN_B)
    telegram.calls.clear()

    async def fetch():
        return "https://new.trycloudflare.com"

    await sync_public_url(session, fetch)

    urls = sorted(h.url for h in telegram.of(methods.SetWebhook))
    assert urls == ["https://new.trycloudflare.com/tg/123456789",
                    "https://new.trycloudflare.com/tg/987654321",
                    "https://new.trycloudflare.com/tg/platform"]


# --- F12. Карточка клуба в пульте честно показывает состояние ----------------------------


async def test_f12_platform_card_reflects_state(session, telegram, pbot):
    club, _ = await club_with_bot(session, "Arena", TOKEN_A, prizes=(("Приз", 70, False), ("Пусто", 30, True)))
    session.add(ClubStaff(club_id=club.id, telegram_id=SUPER, role=StaffRole.OWNER, first_name="Аня"))
    await session.flush()

    await pbot.send(nav("club", club.id))
    card = pbot.texts()[-1]

    for fragment in ("Arena", "@test_club_bot", "trial", "раз в 7 дн.", "призов 2", "сумма весов 100", "Аня"):
        assert fragment in card, fragment
    assert "⚠️" not in card


@pytest.mark.parametrize("prizes,warning", [((), "Нет призов"), ((("Приз", 1, False),), "Нет пустого сектора")])
async def test_f13_platform_card_warns(session, telegram, pbot, prizes, warning):
    club, _ = await club_with_bot(session, "Arena", TOKEN_A, prizes=prizes)
    await pbot.send(nav("club", club.id))
    assert warning in pbot.texts()[-1]
