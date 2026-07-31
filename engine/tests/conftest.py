"""Shared pytest fixtures. CPU only, no network, no real media."""

from collections.abc import AsyncIterator

import httpx
import pytest_asyncio

from repcut.main import app


@pytest_asyncio.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    """In-process async client against the FastAPI app (no socket, no server)."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as async_client:
        yield async_client
