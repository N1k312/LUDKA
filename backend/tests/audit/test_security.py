"""Аудит 3/3 — БЕЗОПАСНОСТЬ: доступ, роли, подделка запросов, секреты, конфигурация.

Каждый тест — попытка атаки или утечки. Упавший тест = уязвимость.
"""

import re
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from aiogram import methods
from sqlalchemy import func, select, update

from app.bots.admin import PICK_ADMIN, PICK_SUPERADMIN, REDEEM_PREFIX, REMOVE_PREFIX
from app.bots.handlers import CONSENT_CALLBACK
from app.crypto import decrypt
from app.logging_setup import redact
from app.models import (
    Club,
    ClubStaff,
    GameConfig,
    Guest,
    PrizeCode,
    PrizeOption,
    RedemptionLog,
    StaffRole,
    Subscription,
    SubscriptionStatus,
)
from app.platform_bot.setup import webhook_secret
from app.services.codes import ALPHABET, generate_code
from app.services.redemption import FAILED_LIMIT, FAILED_WINDOW
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
from tests.test_miniapp import init_data

ROOT = Path(__file__).resolve().parents[3]
GUEST, OTHER_GUEST, ADMIN, SUPER, STRANGER = 555, 556, 1001, 1002, 4242
ENDPOINTS = [("GET", "/api/me"), ("GET", "/api/wheel"), ("POST", "/api/spin"),
             ("GET", "/api/prizes"), ("GET", "/api/rules")]


async def guest_with_code(session, club) -> PrizeCode:
    option = await session.scalar(select(PrizeOption).where(PrizeOption.club_id == club.id))
    guest = Guest(club_id=club.id, telegram_id=GUEST, phone="+77010001285", consent_at=datetime.now(UTC),
                  first_name="<b>Аня</b>", username="anya")
    session.add(guest)
    await session.flush()
    now = datetime.now(UTC)
    code = PrizeCode(code="GG-7K3M9Q", club_id=club.id, guest_id=guest.id, prize_option_id=option.id,
                     title=option.title, created_at=now, expires_at=now + timedelta(days=14))
    session.add(code)
    await session.flush()
    return code


# --- S1. API Mini App: доступ только с подлинным и свежим initData своего бота -----------


@pytest.mark.parametrize("method,path", ENDPOINTS)
@pytest.mark.parametrize("attack", ["нет заголовков", "мусор", "чужая подпись", "подмена user",
                                    "бот другого клуба", "просрочен", "токен пульта"])
async def test_s01_miniapp_auth_matrix(session, telegram, client, method, path, attack, monkeypatch):
    await club_with_bot(session, "А", TOKEN_A)
    await club_with_bot(session, "Б", TOKEN_B)
    good = init_data(TOKEN_A, user_id=GUEST)
    h = {
        "нет заголовков": {},
        "мусор": {"Authorization": "tma abc", "X-Bot-Id": str(BOT_A)},
        "чужая подпись": {"Authorization": f"tma {init_data(TOKEN_B, user_id=GUEST)}", "X-Bot-Id": str(BOT_A)},
        "подмена user": {"Authorization": "tma " + good.replace("555", "556"), "X-Bot-Id": str(BOT_A)},
        "бот другого клуба": {"Authorization": f"tma {good}", "X-Bot-Id": str(BOT_B)},
        "просрочен": {"Authorization": f"tma {init_data(TOKEN_A, auth_date=int(datetime.now(UTC).timestamp()) - 2 * 86400)}",
                      "X-Bot-Id": str(BOT_A)},
        "токен пульта": {"Authorization": f"tma {init_data(PLATFORM_TOKEN, user_id=GUEST)}", "X-Bot-Id": str(BOT_A)},
    }[attack]

    r = await client.request(method, path, headers=h)

    assert r.status_code == 401


# --- S2. Вебхуки: только с секретом именно этого бота ------------------------------------


@pytest.mark.parametrize("target,secret_of", [
    ("A", None), ("A", "wrong"), ("A", "B"), ("A", "platform"),
    ("platform", None), ("platform", "A"), ("platform", "wrong"),
])
async def test_s02_webhook_secret_matrix(session, telegram, client, platform_on, target, secret_of):
    _, bot_a = await club_with_bot(session, "А", TOKEN_A)
    _, bot_b = await club_with_bot(session, "Б", TOKEN_B)
    secrets = {"A": bot_a.webhook_secret, "B": bot_b.webhook_secret, "platform": webhook_secret(), "wrong": "x" * 43}
    path = "/tg/platform" if target == "platform" else f"/tg/{BOT_A}"
    headers = {"X-Telegram-Bot-Api-Secret-Token": secrets[secret_of]} if secret_of else {}
    telegram.calls.clear()

    r = await client.post(path, json=message_update("/start", user_id=OWNER), headers=headers)

    assert r.status_code == 403 and telegram.calls == []


# --- S3. Повышение прав: гость, админ, чужой суперадмин, посторонний -------------------


async def test_s03_privilege_escalation_in_club_bot(session, telegram, client):
    club, bot_a = await club_with_bot(session, "А", TOKEN_A)
    _, bot_b = await club_with_bot(session, "Б", TOKEN_B)
    code = await guest_with_code(session, club)
    session.add_all([ClubStaff(club_id=club.id, telegram_id=ADMIN, role=StaffRole.ADMIN),
                     ClubStaff(club_id=club.id, telegram_id=SUPER, role=StaffRole.OWNER)])
    await session.flush()
    a, b = BotClient(client, telegram, bot_a), BotClient(client, telegram, bot_b)

    attempts = [
        (a, callback_update(REDEEM_PREFIX + code.code, user_id=GUEST)),          # гость гасит свой код
        (a, callback_update(f"{REMOVE_PREFIX}{SUPER}", user_id=ADMIN)),        # админ снимает суперадмина
        (a, callback_update(f"{REMOVE_PREFIX}{ADMIN}", user_id=GUEST)),        # гость убирает админа
        (a, users_shared_update(PICK_ADMIN, STRANGER, user_id=ADMIN)),          # админ назначает админа
        (a, users_shared_update(PICK_SUPERADMIN, ADMIN, user_id=SUPER)),        # суперадмин — суперадмина
        (a, users_shared_update(PICK_ADMIN, STRANGER, user_id=GUEST)),          # гость назначает админа
        (b, callback_update(REDEEM_PREFIX + code.code, user_id=SUPER)),         # суперадмин А в клубе Б
        (b, users_shared_update(PICK_ADMIN, STRANGER, user_id=SUPER)),
    ]
    for bot, update_ in attempts:
        await bot.send(update_)

    await session.refresh(code)
    staff = dict((await session.execute(select(ClubStaff.telegram_id, ClubStaff.role))).all())
    assert code.redeemed_at is None
    assert staff == {ADMIN: StaffRole.ADMIN, SUPER: StaffRole.OWNER}


@pytest.mark.parametrize("action,arg", [
    ("new", 0), ("bot", 0), ("prize_del", -1), ("super", 0), ("sub_add", 30),
    ("sub_cancel_yes", 0), ("rules", 0), ("limit_set", 60), ("qr", 0),
])
async def test_s04_stranger_cannot_use_platform_bot(session, pbot, action, arg):
    club, _ = await club_with_bot(session, "А", TOKEN_A)
    prize = await session.scalar(select(PrizeOption).where(PrizeOption.club_id == club.id))
    clubs_before = await session.scalar(select(func.count()).select_from(Club))

    await pbot.send(nav(action, club.id, prize.id if arg == -1 else arg, user_id=STRANGER))
    await pbot.send(say("Захват", user_id=STRANGER))

    await session.refresh(prize)
    sub = await session.get(Subscription, club.id)
    await session.refresh(sub)
    assert prize.active and sub.status == SubscriptionStatus.TRIAL
    assert (await session.get(GameConfig, (club.id, "wheel"))).cooldown_seconds == 7 * 86400
    assert await session.scalar(select(func.count()).select_from(Club)) == clubs_before
    assert not pbot.telegram.of(methods.SendDocument)  # QR не выдан
    assert all(a.show_alert for a in pbot.answers())


# --- S4. Клиент не может выбрать приз или клуб ----------------------------------------


async def test_s05_client_cannot_choose_prize_or_club(session, telegram, client):
    club, _ = await club_with_bot(session, "А", TOKEN_A, prizes=(("Джекпот", 0, False), ("Пусто", 1, True)))
    other, _ = await club_with_bot(session, "Б", TOKEN_B, prizes=(("Чужой приз", 1, False),))
    session.add(Guest(club_id=club.id, telegram_id=GUEST, phone="+77010000000", consent_at=datetime.now(UTC)))
    await session.flush()
    jackpot = await session.scalar(select(PrizeOption).where(PrizeOption.title == "Джекпот"))

    r = await client.post(f"/api/spin?club_id={other.id}&prize_option_id={jackpot.id}",
                          json={"prize_option_id": jackpot.id, "club_id": other.id, "sector": 0},
                          headers={**api(TOKEN_A, BOT_A, GUEST), "X-Club-Id": str(other.id)})

    assert r.json()["sector"]["title"] == "Пусто" and r.json()["code"] is None


# --- S5. Инъекции: SQL в промокоде, HTML в именах --------------------------------------


@pytest.mark.parametrize("payload", ["GG-' OR 1=1 --", "'; DROP TABLE prize_codes; --", "GG-%", "GG-_____ _"])
async def test_s06_sql_like_codes_are_harmless(session, telegram, client, payload):
    club, bot_a = await club_with_bot(session, "А", TOKEN_A)
    await guest_with_code(session, club)
    session.add(ClubStaff(club_id=club.id, telegram_id=ADMIN, role=StaffRole.ADMIN))
    await session.flush()
    bot = BotClient(client, telegram, bot_a)

    await bot.send(message_update(payload, user_id=ADMIN))
    await bot.send(callback_update(REDEEM_PREFIX + payload, user_id=ADMIN))

    assert (await session.get(PrizeCode, "GG-7K3M9Q")).redeemed_at is None
    assert await session.scalar(select(func.count()).select_from(PrizeCode)) == 1


async def test_s07_html_in_names_is_escaped(session, telegram, client):
    club, bot_a = await club_with_bot(session, "<i>Клуб</i> & Co", TOKEN_A,
                                      prizes=(("<b>Приз</b>", 1, False),), rules="<a href='x'>правила</a>")
    await guest_with_code(session, club)
    session.add(ClubStaff(club_id=club.id, telegram_id=ADMIN, role=StaffRole.ADMIN))
    await session.flush()
    bot = BotClient(client, telegram, bot_a)

    sent = []
    sent += await bot.send(message_update("/start", user_id=OTHER_GUEST))
    sent += await bot.send(message_update("/rules", user_id=OTHER_GUEST))
    sent += await bot.send(message_update("GG-7K3M9Q", user_id=ADMIN))

    for m in sent:
        for raw in ("<i>Клуб</i>", "<b>Приз</b>", "<a href", "<b>Аня</b>"):
            assert raw not in m.text, f"неэкранированный HTML {raw!r} в: {m.text[:80]}"
    assert any("&lt;i&gt;Клуб&lt;/i&gt; &amp; Co" in m.text for m in sent)


# --- S6. Секреты: хранение, ответы, логи, чат ------------------------------------------


async def test_s08_secrets_at_rest_and_in_responses(session, telegram, client, platform_on):
    club, bot_a = await club_with_bot(session, "А", TOKEN_A)
    _, bot_b = await club_with_bot(session, "Б", TOKEN_B)
    await register(BotClient(client, telegram, bot_a), GUEST, "77010001285")
    h = api(TOKEN_A, BOT_A, GUEST)
    bodies = [(await client.post("/api/spin", headers=h)).text]
    bodies += [(await client.get(p, headers=h)).text for _, p in ENDPOINTS if p != "/api/spin"]
    to_guest = " ".join(m.text for m in telegram.of(methods.SendMessage))

    assert TOKEN_A not in bot_a.token_encrypted and decrypt(bot_a.token_encrypted) == TOKEN_A
    assert len(bot_a.webhook_secret) >= 32 and bot_a.webhook_secret != bot_b.webhook_secret
    assert webhook_secret() not in (bot_a.webhook_secret, bot_b.webhook_secret)
    for leak in (TOKEN_A, "77010001285", bot_a.webhook_secret):
        assert all(leak not in b for b in bodies), f"утечка {leak[:6]}… в API"
        assert leak not in to_guest, f"утечка {leak[:6]}… в сообщения гостю"


async def test_s09_phone_masked_for_admin(session, telegram, client):
    club, bot_a = await club_with_bot(session, "А", TOKEN_A)
    await guest_with_code(session, club)
    session.add(ClubStaff(club_id=club.id, telegram_id=ADMIN, role=StaffRole.ADMIN))
    await session.flush()

    [reply] = await BotClient(client, telegram, bot_a).send(message_update("GG-7K3M9Q", user_id=ADMIN))

    assert "…1285" in reply.text and "77010001285" not in reply.text and "7010001" not in reply.text


async def test_s10_token_message_deleted_in_platform_bot(session, pbot):
    club, _ = await club_with_bot(session, "А", TOKEN_A)
    await pbot.send(nav("bot", club.id))
    await pbot.send(say(TOKEN_A))

    assert pbot.telegram.of(methods.DeleteMessage)
    assert all(TOKEN_A not in t for t in pbot.texts())


@pytest.mark.parametrize("line", [
    f"POST https://api.telegram.org/bot{TOKEN_A}/sendMessage",
    f"token={PLATFORM_TOKEN} phone=+77010001285",
    "contact 77010001285 from user",
])
def test_s11_logs_are_redacted(line):
    cleaned = redact(line)
    for secret in (TOKEN_A, PLATFORM_TOKEN, "77010001285"):
        assert secret not in cleaned


# --- S7. Перебор промокодов ----------------------------------------------------------------


async def test_s12_bruteforce_is_limited_and_window_expires(session, telegram, client):
    club, bot_a = await club_with_bot(session, "А", TOKEN_A)
    await guest_with_code(session, club)
    session.add(ClubStaff(club_id=club.id, telegram_id=ADMIN, role=StaffRole.ADMIN))
    await session.flush()
    bot = BotClient(client, telegram, bot_a)
    for i in range(FAILED_LIMIT):
        await bot.send(message_update(f"GG-ZZZZ{ALPHABET[i]}Z", user_id=ADMIN))

    [blocked] = await bot.send(message_update("GG-7K3M9Q", user_id=ADMIN))  # даже верный код
    await session.execute(update(RedemptionLog).values(created_at=RedemptionLog.created_at - FAILED_WINDOW))
    [after] = await bot.send(message_update("GG-7K3M9Q", user_id=ADMIN))

    assert "Слишком много" in blocked.text and "действителен" in after.text


def test_s13_code_space_and_alphabet():
    assert not set("01OIL") & set(ALPHABET)
    assert len(ALPHABET) ** 6 > 800_000_000
    assert len({generate_code() for _ in range(5000)}) == 5000


# --- S8. Конфигурация развёртывания ------------------------------------------------------


def test_s14_ports_bound_to_localhost_only():
    mappings = []
    for f in (ROOT / "docker-compose.yml", ROOT / "deploy" / "compose.server.yml"):
        mappings += re.findall(r'^\s*-\s*"?([\d.:]+:\d+:\d+|\d+:\d+)"?\s*$', f.read_text(encoding="utf-8"), re.M)
    assert mappings, "не найдено ни одного проброса портов"
    exposed = [m for m in mappings if not m.startswith("127.0.0.1:")]
    assert not exposed, f"порты открыты во внешнюю сеть: {exposed}"


def test_s15_web_server_exposes_only_public_paths():
    caddy = (ROOT / "deploy" / "Caddyfile").read_text(encoding="utf-8")
    proxied = re.findall(r"handle\s+(\S+)\s*\{\s*reverse_proxy", caddy)
    assert sorted(proxied) == ["/api/*", "/health", "/tg/*"], proxied
    for private in ("/docs", "/openapi", "/redoc", ":8000 {"):
        assert private not in caddy


def test_s16_secrets_not_in_git():
    tracked = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.split()
    assert ".env" not in tracked and not any(p.endswith("/.env") for p in tracked)
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    for key in ("ENCRYPTION_KEY", "TEST_BOT_TOKEN", "PLATFORM_BOT_TOKEN", "PLATFORM_OWNER_IDS"):
        value = re.search(rf"^{key}=(.*)$", example, re.M)
        assert value and value.group(1).strip() == "", f"{key} в .env.example не пустой"
    # Настоящие секреты из .env не должны встречаться ни в истории git, ни в отслеживаемых файлах.
    env_file = ROOT / ".env"
    if not env_file.exists():
        pytest.skip(".env нет — сравнивать не с чем")
    env = dict(line.split("=", 1) for line in env_file.read_text(encoding="utf-8").splitlines()
               if "=" in line and not line.startswith("#"))
    secrets = {k: v.strip() for k, v in env.items()
               if k in ("TEST_BOT_TOKEN", "PLATFORM_BOT_TOKEN", "ENCRYPTION_KEY", "POSTGRES_PASSWORD") and len(v.strip()) > 8}
    assert secrets, "в .env не нашлось секретов для проверки"
    history = subprocess.run(["git", "log", "-p", "--all", "--format=%h"], cwd=ROOT,
                             capture_output=True, text=True, encoding="utf-8").stdout
    files = "".join((ROOT / p).read_text(encoding="utf-8", errors="ignore")
                    for p in tracked if (ROOT / p).is_file())
    leaked = [k for k, v in secrets.items() if v in history or v in files]
    assert not leaked, f"в git попали секреты из .env: {leaked}"


# --- S9. Подмена сообщений Telegram внутри вебхука ---------------------------------------


async def test_s17_consent_and_contact_of_other_user_rejected(session, telegram, client):
    from tests.botkit import contact_update

    _, bot_a = await club_with_bot(session, "А", TOKEN_A)
    bot = BotClient(client, telegram, bot_a)
    await bot.send(message_update("/start", user_id=GUEST))

    await bot.send(contact_update("77019999999", contact_user_id=OTHER_GUEST, user_id=GUEST))
    await bot.send(callback_update(CONSENT_CALLBACK, user_id=GUEST))
    spin = await client.post("/api/spin", headers=api(TOKEN_A, BOT_A, GUEST))

    guest = await session.scalar(select(Guest).where(Guest.telegram_id == GUEST))
    assert guest.phone is None and guest.consent_at is None
    assert spin.status_code == 403
