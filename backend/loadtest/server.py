"""Сервер для нагрузочного теста: отдельная база и заглушка Telegram.

Рабочий бот и его база не затрагиваются: адрес базы берётся из LOADTEST_DB
(по умолчанию — временный контейнер на 127.0.0.1:55432, см. docs/testing.md),
бот платформы и туннель выключены, а Telegram заменён заглушкой, которая
отвечает с задержкой LOADTEST_TG_DELAY секунд (как настоящий Telegram) и
никуда ничего не отправляет.

Запуск (из backend/): .venv/Scripts/python -m loadtest.server
"""

import asyncio
import os
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
PORT = int(os.environ.get("LOADTEST_PORT", "8765"))
DB_URL = os.environ.get("LOADTEST_DB", "postgresql+asyncpg://load:load@127.0.0.1:55432/load")
TG_DELAY = float(os.environ.get("LOADTEST_TG_DELAY", "0.15"))

os.environ.update(
    DATABASE_URL=DB_URL,
    PLATFORM_BOT_TOKEN="",
    PLATFORM_OWNER_IDS="",
    TUNNEL_METRICS_URL="",
    TEST_BOT_TOKEN="",
    PUBLIC_BASE_URL="https://loadtest.invalid",
)

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402

from tests.telegram_fake import FakeTelegram  # noqa: E402


class SlowTelegram(FakeTelegram):
    """Заглушка Telegram с задержкой ответа; вызовы не копятся в памяти."""

    async def make_request(self, bot, method, timeout=None):
        await asyncio.sleep(TG_DELAY)
        result = await super().make_request(bot, method, timeout)
        self.calls.clear()
        return result


def migrate() -> None:
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    cfg.set_main_option("sqlalchemy.url", DB_URL.replace("%", "%%"))
    command.upgrade(cfg, "head")


def main() -> None:
    import uvicorn

    from app import db
    from app.bots.registry import registry
    from app.main import app

    if pool := os.environ.get("LOADTEST_POOL"):  # «размер:запас», например «20:20» — для сравнения
        from sqlalchemy.ext.asyncio import create_async_engine

        size, overflow = (int(x) for x in pool.split(":"))
        db.engine = create_async_engine(DB_URL, pool_pre_ping=True, pool_size=size, max_overflow=overflow)
        db.SessionLocal.configure(bind=db.engine)
    migrate()
    registry.use_session(SlowTelegram())
    # Как в рабочем контейнере: один процесс uvicorn.
    host = os.environ.get("LOADTEST_HOST", "127.0.0.1")  # в контейнере — 0.0.0.0
    uvicorn.run(app, host=host, port=PORT, log_level="warning", access_log=False)


if __name__ == "__main__":
    main()
