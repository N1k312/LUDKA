"""Фейковая сессия aiogram: записывает вызовы методов Telegram вместо отправки по сети."""

from aiogram import methods
from aiogram.client.session.base import BaseSession
from aiogram.types import File, User


class FakeTelegram(BaseSession):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list = []
        self.files: dict[str, bytes] = {}

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        if isinstance(method, methods.GetMe):
            return User(id=bot.id, is_bot=True, first_name="Test Club", username="test_club_bot")
        if isinstance(method, methods.GetFile):
            return File(file_id=method.file_id, file_unique_id=method.file_id, file_path=f"files/{method.file_id}")
        return True

    async def stream_content(self, url, headers=None, timeout=30, chunk_size=65536, raise_for_status=True):
        """Скачивание файла: содержимое берётся из self.files по file_id."""
        yield self.files.get(url.rsplit("/", 1)[-1], b"")

    async def close(self) -> None:
        pass

    def of(self, method_type) -> list:
        return [c for c in self.calls if isinstance(c, method_type)]
