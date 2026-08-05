"""Async engine and session factory.

The URL is never logged. On Windows it embeds the absolute ``DATA_DIR``, which
contains the OS username (secrets.md).
"""

from sqlalchemy import event
from sqlalchemy.engine.interfaces import DBAPIConnection
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import ConnectionPoolEntry

from repcut.config import Settings


def _apply_sqlite_pragmas(dbapi_connection: DBAPIConnection, _record: ConnectionPoolEntry) -> None:
    """Set the per-connection pragmas SQLite does not default to.

    ``foreign_keys`` is OFF by default in SQLite, which would make every
    ``ondelete`` rule in ``models.py`` decorative - including the RESTRICT that
    stops a referenced blob being deleted. WAL lets the ingest worker write
    while the API reads instead of returning SQLITE_BUSY, and the busy timeout
    covers the moments it still has to wait.
    """
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
    finally:
        cursor.close()


def create_engine(settings: Settings) -> AsyncEngine:
    """Build the async engine, creating ``DATA_DIR`` if this is a first run."""
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    engine = create_async_engine(settings.resolved_database_url, echo=False)
    event.listen(engine.sync_engine, "connect", _apply_sqlite_pragmas)
    return engine


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Session factory for request and job scopes.

    ``expire_on_commit=False`` because an expired attribute reloads itself on
    access, and under asyncio that reload is I/O in a property read - it raises
    instead of blocking. Committed objects stay usable.
    """
    return async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
