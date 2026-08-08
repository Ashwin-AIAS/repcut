"""Request-scoped dependencies, resolved from state the lifespan installed.

State hangs off ``app.state`` rather than module globals so a test can stand the
engine up against a scratch ``$DATA_DIR`` without monkeypatching imports, and so
two apps in one process cannot share a database by accident.
"""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.requests import HTTPConnection

from repcut.config import Settings
from repcut.jobs import JobQueue

# HTTPConnection, not Request: it is the base of both Request and WebSocket, and
# `/ws/jobs` needs the same session factory and job queue an HTTP route does.
# Typing these as Request would make the socket reach for state through a
# parallel path that could drift from this one.


def get_settings_from_state(connection: HTTPConnection) -> Settings:
    """The settings this app was started with."""
    settings: Settings = connection.app.state.settings
    return settings


def get_session_factory(connection: HTTPConnection) -> async_sessionmaker[AsyncSession]:
    """The session factory bound to this app's engine."""
    factory: async_sessionmaker[AsyncSession] = connection.app.state.session_factory
    return factory


async def get_session(connection: HTTPConnection) -> AsyncIterator[AsyncSession]:
    """One session per request, closed when the response is done."""
    async with get_session_factory(connection)() as session:
        yield session


def get_job_queue(connection: HTTPConnection) -> JobQueue:
    """The single in-process job worker."""
    queue: JobQueue = connection.app.state.job_queue
    return queue


SettingsDep = Annotated[Settings, Depends(get_settings_from_state)]
SessionDep = Annotated[AsyncSession, Depends(get_session)]
JobQueueDep = Annotated[JobQueue, Depends(get_job_queue)]


__all__ = [
    "JobQueueDep",
    "SessionDep",
    "SettingsDep",
    "get_job_queue",
    "get_session",
    "get_session_factory",
    "get_settings_from_state",
]
