"""Экземпляры aiogram.Bot для ботов всех клубов.

Все боты работают в одном процессе и используют одну HTTP-сессию.
Токены расшифровываются только здесь и только в памяти.
"""

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.session.base import BaseSession
from aiogram.enums import ParseMode

from app.crypto import decrypt
from app.models import ClubBot


class BotRegistry:
    def __init__(self) -> None:
        self._bots: dict[int, tuple[str, Bot]] = {}
        self._session: BaseSession | None = None

    @property
    def session(self) -> BaseSession:
        if self._session is None:
            self._session = AiohttpSession()
        return self._session

    def use_session(self, session: BaseSession) -> None:
        """Подменить HTTP-сессию (в тестах — фейковой, без обращений к Telegram)."""
        self._session = session
        self._bots.clear()

    def bot_for_token(self, token: str) -> Bot:
        return Bot(
            token=token,
            session=self.session,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )

    def get(self, club_bot: ClubBot) -> Bot:
        cached = self._bots.get(club_bot.bot_id)
        if cached and cached[0] == club_bot.token_encrypted:
            return cached[1]
        bot = self.bot_for_token(decrypt(club_bot.token_encrypted))
        self._bots[club_bot.bot_id] = (club_bot.token_encrypted, bot)
        return bot

    def for_token(self, token: str) -> Bot:
        """Кэшированный бот по токену (для бота платформы, токен которого в .env)."""
        key = -abs(hash(token))  # отрицательный ключ не пересекается с id ботов клубов
        cached = self._bots.get(key)
        if cached is None:
            cached = (token, self.bot_for_token(token))
            self._bots[key] = cached
        return cached[1]

    def forget(self, bot_id: int) -> None:
        self._bots.pop(bot_id, None)

    async def close(self) -> None:
        self._bots.clear()
        if self._session is not None:
            await self._session.close()
            self._session = None


registry = BotRegistry()
