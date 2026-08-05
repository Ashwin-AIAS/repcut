"""Shared pytest fixtures. CPU only, no network, no real media."""

from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from repcut.config import Settings
from repcut.db import Base, create_engine, create_session_factory
from repcut.main import app


@pytest_asyncio.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    """In-process async client against the FastAPI app (no socket, no server)."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as async_client:
        yield async_client


@pytest_asyncio.fixture
async def db_engine(tmp_path: Path) -> AsyncIterator[AsyncEngine]:
    """A scratch database with the schema applied, thrown away after the test.

    Built from ``Base.metadata`` rather than by running migrations: this is the
    fast path for model behaviour, and ``test_migrations.py`` separately proves
    the migration and the models describe the same schema.
    """
    settings = Settings(
        data_dir=tmp_path,
        database_url=f"sqlite+aiosqlite:///{(tmp_path / 'test.db').as_posix()}",
    )
    engine = create_engine(settings)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    yield engine

    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(db_engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """One session against the scratch database."""
    factory = create_session_factory(db_engine)
    async with factory() as session:
        yield session
