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
    "scenes",
    "gemini_scene_cache",
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


def test_scene_and_gemini_cache_keys_exist(tmp_path: Path) -> None:
    """The two unique keys amendment 008 turns on, asserted against the migration.

    ``scenes`` cannot reuse ``derived_artifacts``' key (a clip has N scenes,
    not one); ``gemini_scene_cache`` folds ``video_hash`` into ``scene_id``
    rather than storing it again. Both must exist on a fresh clone, not only
    on the models the tests build from.
    """
    db_path = tmp_path / "scenes.db"
    command.upgrade(_config(db_path), "head")

    connection = sqlite3.connect(db_path)
    try:
        schema = {
            row[0]
            for row in connection.execute(
                "SELECT sql FROM sqlite_master WHERE name IN ('scenes', 'gemini_scene_cache')"
            )
        }
    finally:
        connection.close()

    statements = " ".join(schema)
    assert "uq_scenes_key" in statements
    assert "uq_gemini_scene_cache_key" in statements
    assert "end_seconds > start_seconds" in statements


def test_the_resume_lookup_index_is_partial(tmp_path: Path) -> None:
    """A fresh clone must get the index, and get it scoped to in-progress only.

    Asserted against the migration rather than the models: the models are what
    tests build from, the migration is what a real database is built from, and
    only one of those is what ships.
    """
    db_path = tmp_path / "resume.db"
    command.upgrade(_config(db_path), "head")

    connection = sqlite3.connect(db_path)
    try:
        (statement,) = next(
            connection.execute(
                "SELECT sql FROM sqlite_master "
                "WHERE type = 'index' AND name = 'uq_upload_sessions_in_progress'"
            )
        )
    finally:
        connection.close()

    assert "UNIQUE INDEX" in statement
    assert "(project_id, declared_sha256)" in statement
    assert "WHERE status = 'in_progress'" in statement


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
