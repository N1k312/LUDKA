"""Фейковая сессия aiogram: записывает вызовы методов Telegram вместо отправки по сети."""

from aiogram import methods
from aiogram.client.session.base import BaseSession
from aiogram.types import User


class FakeTelegram(BaseSession):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list = []

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        if isinstance(method, methods.GetMe):
            return User(id=bot.id, is_bot=True, first_name="Test Club", username="test_club_bot")
        return True

    async def stream_content(self, *args, **kwargs):  # pragma: no cover
        raise NotImplementedError

    async def close(self) -> None:
        pass

    def of(self, method_type) -> list:
        return [c for c in self.calls if isinstance(c, method_type)]
