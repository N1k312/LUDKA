"""Доступ клуба: подписка продлевается сама, владелец отключает доступ в пульте и возвращает его."""

from datetime import UTC, datetime, timedelta

from aiogram import methods
from sqlalchemy import select

from app.bots.admin import REDEEM_PREFIX
from app.models import Club, ClubStaff, ClubStatus, Guest, PrizeCode, StaffRole, Subscription, SubscriptionStatus
from app.services import subscription
from tests.audit.conftest import BOT_A, TOKEN_A, TOKEN_B, api, club_with_bot, nav
from tests.botkit import BotClient, callback_update, message_update

SUPER, ADMIN, GUEST, STRANGER = 4001, 4002, 555, 4999


async def setup(session, telegram, client):
    club, club_bot = await club_with_bot(session, "Arena", TOKEN_A)
    session.add_all([
        ClubStaff(club_id=club.id, telegram_id=SUPER, role=StaffRole.OWNER),
        ClubStaff(club_id=club.id, telegram_id=ADMIN, role=StaffRole.ADMIN),
        Guest(club_id=club.id, telegram_id=GUEST, phone="+77010000000", consent_at=datetime.now(UTC)),
    ])
    await session.flush()
    return club, BotClient(client, telegram, club_bot)


def to_super(telegram) -> list[str]:
    return [m.text for m in telegram.of(methods.SendMessage) if m.chat_id == SUPER]


async def test_disable_asks_then_stops_games_and_notifies_superadmin(session, telegram, client, pbot):
    club, _ = await setup(session, telegram, client)

    await pbot.send(nav("access_off", club.id))
    asked = pbot.texts()[-1]
    await session.refresh(club)
    assert club.status == ClubStatus.ACTIVE and "Вернуть доступ можно" in asked  # только вопрос

    await pbot.send(nav("access_off_yes", club.id))
    notes = to_super(pbot.telegram)
    card = next(t for t in pbot.texts() if "🔌 Доступ" in t)
    me =(await client.get("/api/me", headers=api(TOKEN_A, BOT_A, GUEST))).json()

    await session.refresh(club)
    assert club.status == ClubStatus.DISABLED and me["available"] is False
    assert "Доступ: ⏸ отключён" in card and any("приостановлен" in t for t in notes)


async def test_restore_brings_games_back(session, telegram, client, pbot):
    club, _ = await setup(session, telegram, client)
    await pbot.send(nav("access_off_yes", club.id))

    await pbot.send(nav("access_on", club.id))
    notes = to_super(pbot.telegram)
    play = await client.post("/api/play/wheel", headers=api(TOKEN_A, BOT_A, GUEST))

    await session.refresh(club)
    assert club.status == ClubStatus.ACTIVE and play.status_code == 200
    assert any("восстановлен" in t for t in notes)


async def test_disabled_club_bot_still_answers_and_redeems(session, telegram, client, pbot):
    club, bot = await setup(session, telegram, client)
    code = (await client.post("/api/play/wheel", headers=api(TOKEN_A, BOT_A, GUEST))).json()["code"]["code"]
    await pbot.send(nav("access_off_yes", club.id))

    [menu] = await bot.send(message_update("/admin", user_id=ADMIN))
    await bot.send(callback_update(REDEEM_PREFIX + code, user_id=ADMIN))
    welcome = await bot.send(message_update("/start", user_id=GUEST))

    redeemed = await session.scalar(select(PrizeCode).where(PrizeCode.code == code))
    await session.refresh(redeemed)
    assert "приостановлен" in menu.text
    assert redeemed.redeemed_at is not None  # выданный приз гостю отдают
    assert welcome  # бот не молчит


async def test_legacy_cancel_buttons_disable_access_instead(session, telegram, client, pbot):
    club, _ = await setup(session, telegram, client)

    await pbot.send(nav("sub_cancel_yes", club.id))

    await session.refresh(club)
    sub = await session.get(Subscription, club.id)
    assert club.status == ClubStatus.DISABLED and sub.status == SubscriptionStatus.TRIAL


async def test_restore_also_lifts_old_canceled_subscription(session, telegram, client, pbot):
    club, _ = await setup(session, telegram, client)
    sub = await session.get(Subscription, club.id)
    await subscription.cancel(session, sub)

    await pbot.send(nav("access_on", club.id))
    play = await client.post("/api/play/wheel", headers=api(TOKEN_A, BOT_A, GUEST))

    assert play.status_code == 200 and sub.status == SubscriptionStatus.TRIAL


async def test_billing_lists_unpaid_soon_and_disabled(session, telegram, client, pbot):
    paid, _ = await setup(session, telegram, client)
    await subscription.extend(session, await session.get(Subscription, paid.id), 30)
    unpaid, _ = await club_with_bot(session, "Должник", TOKEN_B)
    (await session.get(Subscription, unpaid.id)).trial_ends_at = datetime.now(UTC) - timedelta(days=5)
    disabled = await session.scalar(select(Club).where(Club.id == paid.id))
    await session.flush()

    await pbot.send(nav("billing"))
    text = pbot.texts()[-1]
    assert "Должник — не оплачено" in text and "(5 дн.)" in text and "Arena" not in text

    disabled.status = ClubStatus.DISABLED
    await session.flush()
    await pbot.send(nav("billing"))
    assert "⏸ Arena — доступ отключён" in pbot.texts()[-1]


async def test_stranger_cannot_change_access(session, telegram, client, pbot):
    club, _ = await setup(session, telegram, client)

    for action in ("access_off_yes", "sub_cancel_yes"):
        await pbot.send(nav(action, club.id, user_id=STRANGER))
    club.status = ClubStatus.DISABLED
    await session.flush()
    await pbot.send(nav("access_on", club.id, user_id=STRANGER))

    await session.refresh(club)
    assert club.status == ClubStatus.DISABLED  # чужой не отключил и не включил
    assert all(a.show_alert for a in pbot.answers())
