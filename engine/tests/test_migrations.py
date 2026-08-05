"""Migrations must round-trip, and must not drift from the models.

Gate criterion 1 of Prompt 02. These tests are synchronous: Alembic's command
API drives ``env.py``, which runs its own event loop.
"""

import sqlite3
from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext

from repcut.db import Base

ENGINE_ROOT = Path(__file__).resolve().parents[1]

EXPECTED_TABLES = {
    "projects",
    "media_blobs",
    "media_files",
    "derived_artifacts",
    "upload_sessions",
    "jobs",
}


def _config(db_path: Path) -> Config:
    """Alembic config pointed at a scratch database.

    The URL is set here rather than in the .ini, which ships empty so no machine
    path is ever committed.
    """
    config = Config(str(ENGINE_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ENGINE_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{db_path.as_posix()}")
    return config


def _tables(db_path: Path) -> set[str]:
    connection = sqlite3.connect(db_path)
    try:
        rows = connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        return {name for (name,) in rows}
    finally:
        connection.close()


def test_migrations_round_trip(tmp_path: Path) -> None:
    """upgrade head -> downgrade base -> upgrade head, exit 0 each time."""
    db_path = tmp_path / "roundtrip.db"
    config = _config(db_path)

    command.upgrade(config, "head")
    assert _tables(db_path) >= EXPECTED_TABLES

    command.downgrade(config, "base")
    # alembic_version survives a downgrade to base; nothing of ours may.
    assert _tables(db_path) & EXPECTED_TABLES == set()

    command.upgrade(config, "head")
    assert _tables(db_path) >= EXPECTED_TABLES


def test_key_columns_and_constraints_exist(tmp_path: Path) -> None:
    """Both frame rates, and the two unique keys the amendment turns on."""
    db_path = tmp_path / "columns.db"
    command.upgrade(_config(db_path), "head")

    connection = sqlite3.connect(db_path)
    try:
        blob_columns = {row[1] for row in connection.execute("PRAGMA table_info(media_blobs)")}
        schema = {
            row[0]
            for row in connection.execute(
                "SELECT sql FROM sqlite_master WHERE name IN ('media_files', 'derived_artifacts')"
            )
        }
    finally:
        connection.close()

    assert {"fps_source", "fps_normalized"} <= blob_columns
    statements = " ".join(schema)
    assert "uq_media_files_project_sha256" in statements
    assert "uq_derived_artifacts_key" in statements


def test_models_have_not_drifted_from_the_migration(tmp_path: Path) -> None:
    """A model edited without a migration is a bug that only appears on a fresh clone."""
    db_path = tmp_path / "drift.db"
    command.upgrade(_config(db_path), "head")

    engine = sa.create_engine(f"sqlite:///{db_path.as_posix()}")
    try:
        with engine.connect() as connection:
            context = MigrationContext.configure(connection)
            difference = compare_metadata(context, Base.metadata)
    finally:
        engine.dispose()

    assert difference == []
