"""Общие фикстуры тестов.

Тесты работают с отдельной базой <имя>_test в том же PostgreSQL: она
пересоздаётся при каждом запуске и наполняется миграциями Alembic.
Каждый тест выполняется в транзакции, которая откатывается в конце.
"""

import asyncio
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from app.bots.registry import registry
from app.config import get_settings
from app.db import get_session
from app.main import app
from tests.telegram_fake import FakeTelegram

BACKEND_DIR = Path(__file__).resolve().parents[1]


def _test_db_url() -> str:
    url = make_url(get_settings().database_url)
    return url.set(database=f"{url.database}_test").render_as_string(hide_password=False)


async def _recreate_database(url: str) -> None:
    target = make_url(url)
    admin = create_async_engine(target.set(database="postgres"), isolation_level="AUTOCOMMIT")
    async with admin.connect() as conn:
        await conn.execute(text(f'DROP DATABASE IF EXISTS "{target.database}" WITH (FORCE)'))
        await conn.execute(text(f'CREATE DATABASE "{target.database}"'))
    await admin.dispose()


def alembic_config(url: str) -> Config:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return cfg


@pytest.fixture(scope="session")
def test_db_url() -> str:
    url = _test_db_url()
    asyncio.run(_recreate_database(url))
    command.upgrade(alembic_config(url), "head")
    return url


@pytest.fixture(scope="session")
async def engine(test_db_url: str) -> AsyncIterator[AsyncEngine]:
    eng = create_async_engine(test_db_url)
    yield eng
    await eng.dispose()


@pytest.fixture
def telegram(monkeypatch) -> Iterator[FakeTelegram]:
    """Все боты ходят в фейковый Telegram; PUBLIC_BASE_URL — https."""
    fake = FakeTelegram()
    registry.use_session(fake)
    monkeypatch.setattr(get_settings(), "public_base_url", "https://example.test")
    yield fake
    registry.use_session(None)


@pytest.fixture
async def client(session: AsyncSession) -> AsyncIterator[AsyncClient]:
    """HTTP-клиент приложения, работающий в транзакции теста."""

    async def override_session():
        yield session

    app.dependency_overrides[get_session] = override_session
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
async def session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """Сессия внутри внешней транзакции, которая откатывается после теста."""
    async with engine.connect() as conn:
        trans = await conn.begin()
        async with AsyncSession(
            bind=conn, expire_on_commit=False, join_transaction_mode="create_savepoint"
        ) as s:
            yield s
        await trans.rollback()
