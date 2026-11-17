"""Хелперы для тестов бота: обновления Telegram и клиент вебхука."""

from itertools import count

from aiogram import methods

from app.models import ClubBot
from tests.telegram_fake import FakeTelegram

GUEST_ID = 555
_ids = count(1)


def _user(user_id: int, first_name: str = "Гость", username: str | None = "guest") -> dict:
    user = {"id": user_id, "is_bot": False, "first_name": first_name}
    if username:
        user["username"] = username
    return user


def message_update(text: str | None = None, *, user_id: int = GUEST_ID, **extra) -> dict:
    message = {
        "message_id": next(_ids),
        "date": 1_700_000_000,
        "chat": {"id": user_id, "type": "private"},
        "from": _user(user_id),
        **extra,
    }
    if text is not None:
        message["text"] = text
        if text.startswith("/"):
            command = text.split()[0]
            message["entities"] = [{"type": "bot_command", "offset": 0, "length": len(command)}]
    return {"update_id": next(_ids), "message": message}


def contact_update(phone: str, contact_user_id: int | None, user_id: int = GUEST_ID) -> dict:
    contact = {"phone_number": phone, "first_name": "Гость"}
    if contact_user_id is not None:
        contact["user_id"] = contact_user_id
    return message_update(user_id=user_id, contact=contact)


def web_app_data_update(data: str, user_id: int) -> dict:
    return message_update(user_id=user_id, web_app_data={"data": data, "button_text": "📷 Сканировать QR"})


def callback_update(data: str, user_id: int = GUEST_ID) -> dict:
    return {
        "update_id": next(_ids),
        "callback_query": {
            "id": str(next(_ids)),
            "from": _user(user_id),
            "chat_instance": "1",
            "data": data,
            "message": {
                "message_id": 99,
                "date": 1_700_000_000,
                "chat": {"id": user_id, "type": "private"},
                "from": {"id": 123456789, "is_bot": True, "first_name": "Test Club"},
                "text": "…",
            },
        },
    }


class BotClient:
    """Отправляет обновления в вебхук бота клуба и возвращает отправленные ботом сообщения."""

    def __init__(self, client, telegram: FakeTelegram, club_bot: ClubBot):
        self.client, self.telegram, self.club_bot = client, telegram, club_bot

    async def send(self, update: dict) -> list:
        self.telegram.calls.clear()
        response = await self.client.post(
            f"/tg/{self.club_bot.bot_id}",
            json=update,
            headers={"X-Telegram-Bot-Api-Secret-Token": self.club_bot.webhook_secret},
        )
        assert response.status_code == 200
        return self.telegram.of(methods.SendMessage)

    def edited(self) -> list:
        return self.telegram.of(methods.EditMessageText)
