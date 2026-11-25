"""Гостю бот пишет без эмодзи — в текстах и на кнопках (решение владельца 2026-11-25).

Админ-панель и пульт эмодзи сохраняют — здесь проверяется только то, что видит гость.
"""

import re

from aiogram import methods

from app.bots.admin import REDEEM_PREFIX
from app.models import ClubStaff, StaffRole
from tests.audit.conftest import BOT_A, TOKEN_A, api, club_with_bot, register
from tests.botkit import BotClient, callback_update, contact_update, message_update

GUEST, OTHER, ADMIN = 555, 556, 5001
EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-➿⭐⏩-⏺]")


def guest_texts(telegram, chat_id: int) -> list[str]:
    """Тексты сообщений и подписи кнопок, ушедшие в чат гостя."""
    out = []
    for call in telegram.calls:
        if isinstance(call, (methods.SendMessage, methods.EditMessageText, methods.SendPhoto)):
            if getattr(call, "chat_id", None) not in (chat_id, None):
                continue
            out.append(getattr(call, "text", None) or getattr(call, "caption", None) or "")
            markup = call.reply_markup
            rows = getattr(markup, "inline_keyboard", None) or getattr(markup, "keyboard", None) or []
            out += [b.text for row in rows for b in row]
    return out


async def test_guest_never_sees_emoji(session, telegram, client):
    # Эмодзи приза (поле emoji, его задаёт клуб в редакторе) гостю не показывается.
    # Название и тексты клуба выводятся как написаны — это решение самого клуба.
    club, club_bot = await club_with_bot(session, "Arena", TOKEN_A, prizes=(("1 час игры", 1, False),))
    session.add(ClubStaff(club_id=club.id, telegram_id=ADMIN, role=StaffRole.ADMIN))
    await session.flush()
    bot = BotClient(client, telegram, club_bot)
    seen: list[str] = []

    async def step(update, chat=GUEST):
        await bot.send(update)
        seen.extend(guest_texts(telegram, chat))

    await step(message_update("/start desk", user_id=OTHER), OTHER)                  # первый экран
    await step(contact_update("77010000001", contact_user_id=999, user_id=OTHER), OTHER)  # чужой контакт
    await register(bot, GUEST, "77010001285")
    await step(message_update("/start", user_id=GUEST))                              # приветствие
    await step(message_update("привет", user_id=GUEST))                              # любой текст
    await step(message_update("/rules", user_id=GUEST))                              # правила
    telegram.calls.clear()
    code = (await client.post("/api/play/wheel", headers=api(TOKEN_A, BOT_A, GUEST))).json()["code"]["code"]
    seen.extend(guest_texts(telegram, GUEST))                                        # промокод в чат
    await bot.send(callback_update(REDEEM_PREFIX + code, user_id=ADMIN))
    seen.extend(guest_texts(telegram, GUEST))                                        # «Приз получен»

    assert seen, "гостю ничего не отправлено — тест ничего не проверил"
    with_emoji = [t for t in seen if EMOJI.search(t)]
    assert not with_emoji, with_emoji
