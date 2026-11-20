"""Аудит 1/3 — БАГИ: граничные значения, сбои Telegram, неожиданный ввод.

Каждый тест описывает ожидаемое правильное поведение. Упавший тест = найденный баг.
"""

from datetime import UTC, datetime, timedelta

import pytest
from aiogram import methods
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.bots.admin import REDEEM_PREFIX
from app.bots.registry import registry
from app.db import get_session
from app.main import app
from app.models import (
    Club,
    ClubBot,
    ClubStaff,
    GameConfig,
    Guest,
    PrizeCode,
    PrizeOption,
    StaffRole,
)
from app.platform_bot.handlers import Nav
from app.services import clubs
from app.services.codes import code_status
from app.services.redemption import normalize_code
from tests.audit.conftest import (
    BOT_A,
    PLATFORM_BOT,
    PLATFORM_TOKEN,
    TOKEN_A,
    StrictTelegram,
    api,
    club_with_bot,
    nav,
    register,
    say,
    visible_len,
)
from tests.botkit import BotClient, callback_update, contact_update, message_update

GUEST = 555
ADMIN = 1001


# --- B1. Токен бота платформы нельзя подключить как бота клуба ------------------------


async def test_b01_platform_token_cannot_become_club_bot(session, pbot):
    await pbot.send(say("/start"))
    await pbot.send(nav("new"))
    await pbot.send(say("Клуб"))
    club = await session.scalar(select(Club).where(Club.name == "Клуб"))

    await pbot.send(nav("bot", club.id))
    await pbot.send(say(PLATFORM_TOKEN))

    # Иначе вебхук бота платформы переставится на /tg/<id> и пульт перестанет работать.
    assert await session.get(ClubBot, PLATFORM_BOT) is None
    assert not any(h.url.endswith(f"/tg/{PLATFORM_BOT}") for h in pbot.telegram.of(methods.SetWebhook))


# --- B2. Длинные правила не ломают регистрацию (лимит Telegram — 4096 символов) -------


async def test_b02_long_rules_fit_into_telegram_message(session, telegram, client):
    club, club_bot = await club_with_bot(session, "Клуб", TOKEN_A, rules="а" * 4000)
    bot = BotClient(client, telegram, club_bot)
    await bot.send(message_update("/start", user_id=GUEST))

    replies = await bot.send(contact_update("77010000000", contact_user_id=GUEST, user_id=GUEST))

    too_long = [visible_len(m.text) for m in replies if visible_len(m.text) > 4096]
    assert not too_long, f"сообщения длиннее лимита Telegram: {too_long} символов"
    assert replies[-1].reply_markup.inline_keyboard[0][0].callback_data == "consent:accept"
    assert any("а" * 4000 in m.text for m in replies)  # правила показаны целиком


async def test_b16_platform_rejects_rules_over_limit(session, pbot):
    from app.platform_bot.handlers import RULES_MAX_LENGTH

    club = await clubs.create_club(session, "Клуб")
    await pbot.send(nav("rules", club.id))
    await pbot.send(say("б" * (RULES_MAX_LENGTH + 1)))
    await session.refresh(club)

    assert club.rules_text is None
    assert f"до {RULES_MAX_LENGTH} символов" in pbot.texts()[-1]


async def test_b03_platform_rules_menu_fits_with_long_rules(session, pbot):
    club = await clubs.create_club(session, "Клуб")
    club.rules_text = "а" * 4000
    await session.flush()

    await pbot.send(nav("rules", club.id))

    assert all(visible_len(t) <= 4096 for t in pbot.texts())


# --- B3. Сумма оплаты: странный ввод — понятный отказ, а не падение -------------------


@pytest.mark.parametrize("amount", ["nan", "Infinity", "1e20", "0.001"])
async def test_b04_weird_payment_amount_is_rejected_politely(session, pbot, amount):
    club = await clubs.create_club(session, "Клуб")
    await pbot.send(nav("sub_add", club.id, 30))

    await pbot.send(say(amount))

    assert pbot.texts(), f"бот ничего не ответил на сумму {amount!r} (обработчик упал)"
    assert "Сумма" in pbot.texts()[-1]


# --- B4. Несуществующий клуб в кнопке пульта — ответ, а не падение --------------------


@pytest.mark.parametrize("action,arg", [
    ("club", 0), ("bot", 0), ("prizes", 0), ("super", 0), ("sub", 0),
    ("rules", 0), ("qr", 0), ("limit_set", 60), ("sub_cancel_yes", 0),
])
async def test_b05_unknown_club_in_platform_button(pbot, action, arg):
    await pbot.send(nav(action, 999_999, arg))

    assert pbot.answers(), f"«{action}»: колбэк не получил ответа (обработчик упал)"


# --- B5. Сбой Telegram после погашения не должен «распогашать» код -------------------


async def test_b06_redeem_survives_telegram_error_after_db_update(engine: AsyncEngine, monkeypatch):
    """Админ видит «погашен» → в базе код обязан быть погашен, даже если ответ на колбэк упал.

    Так бывает, если сервер лежал и Telegram доставил нажатие позже: «query is too old».
    """
    from app.config import get_settings

    flaky = StrictTelegram(fail_on=(methods.AnswerCallbackQuery,))
    registry.use_session(flaky)
    monkeypatch.setattr(get_settings(), "public_base_url", "https://example.test")
    async with AsyncSession(engine, expire_on_commit=False) as db:
        club, club_bot = await club_with_bot(db, "Сбой", TOKEN_A)
        option = await db.scalar(select(PrizeOption).where(PrizeOption.club_id == club.id))
        guest = Guest(club_id=club.id, telegram_id=GUEST, phone="+77010000001", consent_at=datetime.now(UTC))
        db.add_all([guest, ClubStaff(club_id=club.id, telegram_id=ADMIN, role=StaffRole.ADMIN)])
        await db.flush()
        now = datetime.now(UTC)
        db.add(PrizeCode(code="GG-7K3M9Q", club_id=club.id, guest_id=guest.id, prize_option_id=option.id,
                         title="1 час игры", created_at=now, expires_at=now + timedelta(days=14)))
        await db.commit()

    async def own_session():
        async with AsyncSession(engine, expire_on_commit=False) as s:
            yield s

    app.dependency_overrides[get_session] = own_session
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            await c.post(f"/tg/{club_bot.bot_id}", json=callback_update(REDEEM_PREFIX + "GG-7K3M9Q", user_id=ADMIN),
                         headers={"X-Telegram-Bot-Api-Secret-Token": club_bot.webhook_secret})
        shown_redeemed = any("погашен" in m.text for m in flaky.of(methods.EditMessageText))
        async with AsyncSession(engine) as db:
            redeemed_at = await db.scalar(select(PrizeCode.redeemed_at).where(PrizeCode.code == "GG-7K3M9Q"))
        assert shown_redeemed
        assert redeemed_at is not None, "админу показано «погашен», а в базе код остался активным"
    finally:
        app.dependency_overrides.clear()
        registry.use_session(None)
        async with AsyncSession(engine) as db:
            await db.execute(delete(Club).where(Club.id == club.id))
            await db.commit()


# --- B6. Повторное нажатие той же кнопки пульта («message is not modified») -----------


async def test_b07_same_button_twice_still_answers(session, telegram, platform_on, client, strict):
    from tests.audit.conftest import PlatformClient

    pbot = PlatformClient(client, strict)
    club = await clubs.create_club(session, "Клуб")
    await pbot.send(nav("limit_set", club.id, 60))

    await pbot.send(nav("limit_set", club.id, 60))  # та же карточка — Telegram откажет в редактировании

    assert pbot.answers(), "второе нажатие осталось без ответа — у пользователя «висит» кнопка"


# --- B7. Огромные id в запросах — отказ доступа, а не 500 ------------------------------


async def test_b08_huge_bot_id_in_webhook_path(lenient_client):
    r = await lenient_client.post("/tg/99999999999999999999", json={},
                                  headers={"X-Telegram-Bot-Api-Secret-Token": "x"})
    assert r.status_code == 403, f"получено {r.status_code}"


async def test_b09_huge_bot_id_in_miniapp_header(lenient_client):
    r = await lenient_client.get("/api/me", headers={"Authorization": "tma x", "X-Bot-Id": "99999999999999999999"})
    assert r.status_code == 401, f"получено {r.status_code}"


# --- B8. Граница срока действия: Mini App и админ видят одно и то же ----------------


async def test_b10_expiry_boundary_is_consistent():
    now = datetime.now(UTC)
    code = PrizeCode(code="GG-AAAAAA", expires_at=now, redeemed_at=None)
    # Ровно в момент истечения: и статус для гостя, и проверка админа считают код истёкшим.
    assert code_status(code, now) == "expired"


# --- B9. Ввод промокода: регистр, пробелы, похожие символы -------------------------


@pytest.mark.parametrize("raw,expected", [
    (" gg-7k3m9q ", "GG-7K3M9Q"), ("GG 7K3M9Q", "GG-7K3M9Q"), ("7k3m9q", "GG-7K3M9Q"),
    ("GG-7K3M9O", None), ("GG-7K3M91", None), ("GG—7K3M9Q", None), ("", None),
])
def test_b11_code_input_normalization(raw, expected):
    assert normalize_code(raw) == expected


# --- B10. Гость меняет номер; повторное согласие идемпотентно ------------------------


async def test_b12_guest_can_change_number_and_consent_twice(session, telegram, client):
    club, club_bot = await club_with_bot(session, "Клуб", TOKEN_A)
    bot = BotClient(client, telegram, club_bot)
    await register(bot, GUEST, "77010000000")
    guest = await session.scalar(select(Guest).where(Guest.telegram_id == GUEST))
    first_consent = guest.consent_at

    await bot.send(contact_update("77019999999", contact_user_id=GUEST, user_id=GUEST))
    await bot.send(callback_update("consent:accept", user_id=GUEST))

    await session.refresh(guest)
    assert guest.phone == "+77019999999"
    assert guest.consent_at == first_consent


# --- B11. Колесо без призов и выключенное колесо — понятная ошибка -------------------


async def test_b13_wheel_without_prizes_and_disabled_wheel(session, telegram, client):
    club, club_bot = await club_with_bot(session, "Клуб", TOKEN_A, prizes=(("Ноль", 0, False),))
    session.add(Guest(club_id=club.id, telegram_id=GUEST, phone="+77010000000", consent_at=datetime.now(UTC)))
    await session.flush()
    h = api(TOKEN_A, BOT_A, GUEST)

    no_prizes = await client.post("/api/spin", headers=h)
    (await session.get(GameConfig, (club.id, "wheel"))).enabled = False
    await session.flush()
    disabled = await client.post("/api/spin", headers=h)

    assert no_prizes.status_code == 409 and no_prizes.json()["detail"]["code"] == "no_prizes"
    assert disabled.status_code == 409 and disabled.json()["detail"]["code"] == "game_disabled"


# --- B12. callback_data укладывается в лимит Telegram (64 байта) --------------------


def test_b14_callback_data_fits_telegram_limit():
    biggest = 9_223_372_036_854_775_807
    worst = [Nav(action=a, club_id=biggest, arg=biggest).pack()
             for a in ("sub_cancel_yes", "prize_kind", "prize_del", "limit_set", "sub_add")]
    worst += [REDEEM_PREFIX + "GG-ZZZZZZ", f"staff:rm:{biggest}"]
    too_long = [d for d in worst if len(d.encode()) > 64]
    assert not too_long, f"длиннее 64 байт: {too_long}"


# --- B13. Выключенный приз: старые промокоды показываются и гасятся ---------------


async def test_b15_deactivated_prize_keeps_old_codes_working(session, telegram, client):
    club, club_bot = await club_with_bot(session, "Клуб", TOKEN_A)
    session.add_all([Guest(club_id=club.id, telegram_id=GUEST, phone="+77010000000", consent_at=datetime.now(UTC)),
                     ClubStaff(club_id=club.id, telegram_id=ADMIN, role=StaffRole.ADMIN)])
    await session.flush()
    code = (await client.post("/api/spin", headers=api(TOKEN_A, BOT_A, GUEST))).json()["code"]["code"]
    for p in await clubs.active_prizes(session, club):
        p.active = False
    await session.flush()

    prizes = (await client.get("/api/prizes", headers=api(TOKEN_A, BOT_A, GUEST))).json()["prizes"]
    await BotClient(client, telegram, club_bot).send(callback_update(REDEEM_PREFIX + code, user_id=ADMIN))

    assert [p["code"] for p in prizes] == [code]
    assert (await session.get(PrizeCode, code)).redeemed_at is not None
