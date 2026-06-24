import os
import sys
from collections.abc import AsyncIterator
from contextlib import suppress
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

os.environ.setdefault("POSTGRES_HOST", "localhost")
os.environ.setdefault("POSTGRES_PORT", "5432")
os.environ.setdefault("POSTGRES_USER", "payments")
os.environ.setdefault("POSTGRES_PASSWORD", "payments")
os.environ.setdefault("POSTGRES_DB", "payments")
os.environ.setdefault("RABBITMQ_HOST", "localhost")
os.environ.setdefault("RABBITMQ_PORT", "5672")
os.environ.setdefault("RABBITMQ_USER", "guest")
os.environ.setdefault("RABBITMQ_PASSWORD", "guest")
os.environ.setdefault("API_KEY", "test-api-key")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("LOG_LEVEL", "WARNING")

from alembic import command as alembic_command  # noqa: E402
from alembic.config import Config as AlembicConfig  # noqa: E402

from app.config import Settings, get_settings  # noqa: E402

get_settings.cache_clear()
settings = get_settings()


@pytest.fixture(scope="session")
def test_settings() -> Settings:
    return settings


@pytest.fixture(scope="session", autouse=True)
def _apply_migrations() -> None:
    try:
        cfg = AlembicConfig(str(PROJECT_ROOT / "alembic.ini"))
        cfg.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
        cfg.set_main_option("sqlalchemy.url", settings.database_url)
        alembic_command.upgrade(cfg, "head")
    except Exception:
        pass


@pytest_asyncio.fixture
async def db_engine() -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(settings.database_url)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def db_session_factory(db_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(bind=db_engine, expire_on_commit=False, autoflush=False)


@pytest_asyncio.fixture
async def db_clean(db_engine: AsyncEngine) -> AsyncIterator[None]:
    async with db_engine.begin() as conn:
        await conn.execute(text("TRUNCATE TABLE outbox, payments RESTART IDENTITY CASCADE"))
    yield
    async with db_engine.begin() as conn:
        await conn.execute(text("TRUNCATE TABLE outbox, payments RESTART IDENTITY CASCADE"))


@pytest_asyncio.fixture
async def http_client() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(
        base_url=f"http://localhost:{settings.api_port}",
        timeout=10.0,
    ) as client:
        yield client


@pytest_asyncio.fixture
async def api_client() -> AsyncIterator[httpx.AsyncClient]:
    from app.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver", timeout=10.0
    ) as client:
        yield client


@pytest.fixture()
def api_headers() -> dict[str, str]:
    return {"X-API-Key": settings.api_key}


@pytest_asyncio.fixture
async def webhook_receiver_clear() -> AsyncIterator[None]:
    async with httpx.AsyncClient(
        base_url=f"http://localhost:{settings.webhook_receiver_port}",
        timeout=5.0,
    ) as client:
        with suppress(httpx.RequestError):
            await client.delete("/received")
        yield
