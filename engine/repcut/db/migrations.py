"""Bring a database up to the current schema, from inside the engine.

The engine migrates itself at startup. That is a deliberate choice for a
local-first single-user app, and it was forced by a real failure: the job worker
queries ``jobs`` the moment it starts, so on a ``$DATA_DIR`` nobody had migrated
the engine died during boot with ``no such table: jobs``. The alternatives were
both worse - serving 500s from every route that touches the database, or asking
a single user to remember a command before their own app works.

There is still exactly one schema authority, and it is Alembic. Nothing here
creates a table; it runs the same migrations ``make migrate`` runs, and
``upgrade head`` on an already-current database is a no-op.

Blocking, by nature: Alembic opens its own connection and runs its own event
loop. Callers use ``asyncio.to_thread``.
"""

from alembic import command
from alembic.config import Config

from repcut.config import REPO_ROOT, Settings
from repcut.logging import get_logger

logger = get_logger(__name__)

ALEMBIC_INI = REPO_ROOT / "engine" / "alembic.ini"
MIGRATIONS_DIR = REPO_ROOT / "engine" / "alembic"


def alembic_config(settings: Settings) -> Config:
    """An Alembic config pointed at ``settings``' database.

    The URL is set on the config rather than left to ``env.py``'s fallback, so a
    caller holding scratch settings - a test, the gate - migrates the database it
    means to rather than whichever one ``get_settings()`` resolves. ``env.py``
    already prefers a configured URL for exactly this reason.

    Paths are set programmatically too: ``script_location`` in the .ini is
    relative to the .ini, which only resolves when the process happens to be
    running from the repository root.
    """
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("sqlalchemy.url", settings.resolved_database_url)
    return config


def upgrade_to_head(settings: Settings) -> None:
    """Apply every pending migration. Idempotent; blocking - use a thread.

    Never logs the URL: it embeds ``$DATA_DIR``, which contains the OS username
    on this machine (`.claude/rules/secrets.md`).
    """
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    command.upgrade(alembic_config(settings), "head")
    logger.info("schema_up_to_date")


__all__ = ["ALEMBIC_INI", "MIGRATIONS_DIR", "alembic_config", "upgrade_to_head"]
