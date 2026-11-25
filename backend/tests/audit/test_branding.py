"""Этап 1.5: оформление клуба, префикс промокодов, регистрация из Mini App, приветствие, админ-панель."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from aiogram import methods
from sqlalchemy import select

from app.bots.admin import NEXT_CALLBACK, REDEEM_PREFIX, TODAY_BUTTON
from app.bots.handlers import RULES_CALLBACK
from app.config import get_settings
from app.main import app
from app.models import Club, ClubStaff, Guest, PrizeCode, StaffRole
from app.services import branding, clubs
from app.services.redemption import normalize_code
from tests.audit.conftest import BOT_A, TOKEN_A, api, club_with_bot, nav, say
from tests.botkit import BotClient, callback_update, contact_update, message_update

GUEST, ADMIN, OTHER_ADMIN = 555, 1001, 1002
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


@pytest.fixture
def media(tmp_path: Path, monkeypatch) -> Path:
    """Картинки клубов — во временную папку, и раздача /media оттуда же."""
    monkeypatch.setattr(get_settings(), "media_dir", str(tmp_path))
    static = next(r for r in app.routes if getattr(r, "name", "") == "media").app
    monkeypatch.setattr(static, "directory", str(tmp_path))
    monkeypatch.setattr(static, "all_directories", [str(tmp_path)])
    return tmp_path


def photo_update(file_id: str, user_id: int) -> dict:
    sizes = [{"file_id": f"{file_id}-small", "file_unique_id": "s", "width": 90, "height": 90},
             {"file_id": file_id, "file_unique_id": "b", "width": 800, "height": 800}]
    return message_update(user_id=user_id, photo=sizes)


# --- Сервис оформления ---------------------------------------------------------------------


def test_defaults_without_branding():
    b = branding.resolved(Club(id=1, name="А"))
    assert b["palette"] == "classic" and b["hub_label"] == "GG" and b["code_prefix"] == "GG"
    assert b["logo_url"] is None and b["customized"] is False


@pytest.mark.parametrize("setter,value", [
    (branding.set_palette, "rainbow"), (branding.set_hub_label, "ABCDE"), (branding.set_hub_label, "  "),
    (branding.set_code_prefix, "C"), (branding.set_code_prefix, "C4"), (branding.set_code_prefix, "КЛ"),
    (branding.set_code_prefix, "ABCDE"),
])
def test_invalid_values_rejected(setter, value):
    with pytest.raises(branding.BrandingError):
        setter(Club(id=1, name="А"), value)


def test_text_and_image_validation(media):
    club = Club(id=1, name="А")
    with pytest.raises(branding.BrandingError):
        branding.set_text(club, "win", "x" * (branding.TEXT_MAX + 1))
    with pytest.raises(branding.BrandingError):
        branding.set_image(club, "logo", b"GIF89a not allowed")
    with pytest.raises(branding.BrandingError):
        branding.set_image(club, "logo", PNG + b"\x00" * branding.IMAGE_MAX_BYTES)


def test_prefix_is_normalized_and_reset_keeps_it(media):
    club = Club(id=7, name="А")
    branding.set_code_prefix(club, " ca ")
    branding.set_palette(club, "neon")
    path = branding.set_image(club, "logo", PNG)

    branding.reset(club)

    assert branding.code_prefix(club) == "CA"
    assert branding.resolved(club)["palette"] == "classic"
    assert not path.exists()


def test_new_image_replaces_old_file(media):
    club = Club(id=8, name="А")
    first = branding.set_image(club, "banner", PNG)
    second = branding.set_image(club, "banner", PNG + b"\x01")
    assert not first.exists() and second.exists()


# --- Префикс промокодов --------------------------------------------------------------------


@pytest.mark.parametrize("raw,prefix,expected", [
    ("7K3M9Q", "CA", "CA-7K3M9Q"), ("ca-7k3m9q", "GG", "CA-7K3M9Q"), ("GG 7K3M9Q", "CA", "GG-7K3M9Q"),
    ("CAX7K3M9Q", "GG", "CAX-7K3M9Q"), ("7K3M9O", "CA", None),
])
def test_normalize_with_club_prefix(raw, prefix, expected):
    assert normalize_code(raw, prefix) == expected


async def test_club_prefix_in_new_codes_and_bare_input(session, telegram, client):
    club, club_bot = await club_with_bot(session, "Arena", TOKEN_A)
    branding.set_code_prefix(club, "CA")
    session.add_all([Guest(club_id=club.id, telegram_id=GUEST, phone="+77010000000", consent_at=datetime.now(UTC)),
                     ClubStaff(club_id=club.id, telegram_id=ADMIN, role=StaffRole.ADMIN)])
    await session.flush()

    code = (await client.post("/api/spin", headers=api(TOKEN_A, BOT_A, GUEST))).json()["code"]["code"]
    [reply] = await BotClient(client, telegram, club_bot).send(message_update(code[3:].lower(), user_id=ADMIN))

    assert code.startswith("CA-")
    assert "действителен" in reply.text


# --- Пульт: «🎨 Оформление» ----------------------------------------------------------------


async def test_platform_branding_flow(session, pbot, media, client):
    club = await clubs.create_club(session, "Arena")
    await pbot.send(nav("club", club.id))
    assert any(b.text == "🎨 Оформление" for row in pbot.telegram.of(methods.EditMessageText)[-1]
               .reply_markup.inline_keyboard for b in row)

    await pbot.send(nav("brand_palette", club.id, list(branding.PALETTES).index("neon")))
    await pbot.send(nav("brand_hub", club.id))
    await pbot.send(say("⚡"))
    await pbot.send(nav("brand_prefix", club.id))
    await pbot.send(say("ca"))
    await pbot.send(nav("brand_text", club.id, list(branding.TEXT_KINDS).index("win")))
    await pbot.send(say("Красава!"))
    pbot.telegram.files["logo-file"] = PNG
    await pbot.send(nav("brand_image", club.id, 0))
    await pbot.send(photo_update("logo-file", user_id=9000))

    await session.refresh(club)
    b = branding.resolved(club)
    assert (b["palette"], b["hub_label"], b["code_prefix"], b["texts"]["win"]) == ("neon", "⚡", "CA", "Красава!")
    assert b["logo_url"] and (await client.get(b["logo_url"])).content == PNG
    assert "Сохранено" in pbot.texts()[-1] or "сохранён" in pbot.texts()[-1]


async def test_platform_rejects_bad_prefix_and_non_image(session, pbot, media):
    club = await clubs.create_club(session, "Arena")
    await pbot.send(nav("brand_prefix", club.id))
    await pbot.send(say("1A"))
    assert "латинских" in pbot.texts()[-1]

    await pbot.send(nav("brand_image", club.id, 1))
    await pbot.send(message_update(user_id=9000, document={"file_id": "d", "file_unique_id": "d",
                                                            "mime_type": "application/pdf", "file_size": 10}))
    assert "Нужна картинка" in pbot.texts()[-1]
    await session.refresh(club)
    assert branding.code_prefix(club) == "GG" and branding.image_path(club, "banner") is None


# --- API Mini App: оформление и регистрация ------------------------------------------------


async def test_me_returns_branding(session, telegram, client):
    club, _ = await club_with_bot(session, "Arena", TOKEN_A)
    branding.set_palette(club, "gold")
    await session.flush()

    me = (await client.get("/api/me", headers=api(TOKEN_A, BOT_A, GUEST))).json()

    assert me["branding"]["palette"] == "gold" and me["branding"]["customized"] is True
    assert me["guest"] == {"first_name": "Гость", "registered": False, "has_phone": False, "has_consent": False}


async def test_registration_from_miniapp(session, telegram, client):
    """Согласие — через API (гость создаётся сам), номер — контактом в бот; бот сразу приветствует."""
    club, club_bot = await club_with_bot(session, "Arena", TOKEN_A)
    h = api(TOKEN_A, BOT_A, GUEST)

    r = await client.post("/api/consent", headers=h)
    replies = await BotClient(client, telegram, club_bot).send(
        contact_update("77010000000", contact_user_id=GUEST, user_id=GUEST))
    me = (await client.get("/api/me", headers=h)).json()

    assert r.status_code == 200 and r.json()["registered"] is False
    assert me["guest"]["registered"] is True
    assert "Добро пожаловать" in replies[-1].text  # без повторной просьбы принять правила
    assert (await client.post("/api/spin", headers=h)).status_code == 200


async def test_consent_requires_auth(session, telegram, client):
    await club_with_bot(session, "Arena", TOKEN_A)
    assert (await client.post("/api/consent")).status_code == 401
    assert await session.scalar(select(Guest)) is None


# --- Бот клуба: приветствие, кнопки, баннер ----------------------------------------------


async def test_welcome_buttons_and_rules_callback(session, telegram, client):
    club, club_bot = await club_with_bot(session, "Arena", TOKEN_A, rules="Раз в неделю")
    branding.set_text(club, "welcome", "Ждём на турнире в пятницу!")
    session.add(Guest(club_id=club.id, telegram_id=GUEST, phone="+77010000000", consent_at=datetime.now(UTC)))
    await session.flush()
    bot = BotClient(client, telegram, club_bot)

    [welcome] = await bot.send(message_update("/start", user_id=GUEST))
    [rules] = await bot.send(callback_update(RULES_CALLBACK, user_id=GUEST))

    labels = [b.text for row in welcome.reply_markup.inline_keyboard for b in row]
    assert labels == ["Играть", "Мои призы", "Правила"]
    assert "Ждём на турнире в пятницу!" in welcome.text
    assert "Раз в неделю" in rules.text


async def test_banner_sent_as_photo(session, telegram, client, media):
    club, club_bot = await club_with_bot(session, "Arena", TOKEN_A)
    branding.set_image(club, "banner", PNG)
    await session.flush()

    await BotClient(client, telegram, club_bot).send(message_update("/start", user_id=GUEST))

    [photo] = telegram.of(methods.SendPhoto)
    assert "Привет" in photo.caption and photo.reply_markup.keyboard[0][0].request_contact


# --- Админ-панель: кто погасил, «Сегодня», «Следующий» ------------------------------------


async def admin_setup(session, telegram, client):
    club, club_bot = await club_with_bot(session, "Arena", TOKEN_A)
    session.add_all([
        Guest(club_id=club.id, telegram_id=GUEST, phone="+77010000000", consent_at=datetime.now(UTC)),
        ClubStaff(club_id=club.id, telegram_id=ADMIN, role=StaffRole.ADMIN, first_name="Аня", username="anya"),
        ClubStaff(club_id=club.id, telegram_id=OTHER_ADMIN, role=StaffRole.ADMIN, first_name="Боря"),
    ])
    await session.flush()
    code = (await client.post("/api/spin", headers=api(TOKEN_A, BOT_A, GUEST))).json()["code"]["code"]
    return club, BotClient(client, telegram, club_bot), code


async def test_already_redeemed_names_who(session, telegram, client):
    _, bot, code = await admin_setup(session, telegram, client)
    await bot.send(callback_update(REDEEM_PREFIX + code, user_id=ADMIN))
    [edited] = bot.edited()

    [reply] = await bot.send(message_update(code, user_id=OTHER_ADMIN))

    assert "Аня (@anya)" in reply.text
    assert edited.reply_markup is None  # без «Следующий промокод»: сканер и так внизу экрана


async def test_old_next_code_button_still_answers(session, telegram, client):
    """Кнопка «➡️ Следующий промокод» в старых сообщениях — подсказка и клавиатура со сканером."""
    _, bot, _ = await admin_setup(session, telegram, client)
    [reply] = await bot.send(callback_update(NEXT_CALLBACK, user_id=ADMIN))
    assert "следующий промокод" in reply.text.lower()
    assert reply.reply_markup.keyboard[0][0].web_app is not None


async def test_today_summary(session, telegram, client):
    club, bot, code = await admin_setup(session, telegram, client)
    await bot.send(callback_update(REDEEM_PREFIX + code, user_id=ADMIN))
    await bot.send(message_update("GG-ZZZZZZ", user_id=ADMIN))
    # Вчерашний погашенный код в сводку не попадает.
    old = await session.get(PrizeCode, code)
    session.add(PrizeCode(code="GG-OLDOLD".replace("O", "2"), club_id=club.id, guest_id=old.guest_id,
                          prize_option_id=old.prize_option_id, title="старый",
                          created_at=datetime.now(UTC) - timedelta(days=2),
                          expires_at=datetime.now(UTC) + timedelta(days=5),
                          redeemed_at=datetime.now(UTC) - timedelta(days=2), redeemed_by=OTHER_ADMIN))
    await session.flush()

    [summary] = await bot.send(message_update(TODAY_BUTTON, user_id=ADMIN))

    for fragment in ("Игр: 1", "Выдано промокодов: 1", "Погашено: 1", "Аня (@anya) — 1",
                     "Проверок с неверным кодом: 1"):
        assert fragment in summary.text, fragment
    assert "Боря" not in summary.text
