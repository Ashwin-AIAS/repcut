"""Application settings.

Every value comes from the repo-root ``.env`` (or the process environment) via
pydantic-settings. Nothing here is hardcoded to a machine, and the Gemini key is
held as a ``SecretStr`` so it cannot be printed, logged or serialised by
accident. Only ``gemini_api_key_set`` may ever leave this module.
"""

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

import structlog
from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# engine/repcut/config.py -> engine/repcut -> engine -> repo root.
REPO_ROOT = Path(__file__).resolve().parents[2]

LogLevel = Literal["CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"]
TorchDevicePreference = Literal["auto", "cuda", "cpu"]

# Folder names that mark a cloud-sync root, mapped to the label that gets logged.
# Matched case-insensitively against each component of the resolved path, so
# "OneDrive", "OneDrive - Contoso" and "Dropbox (Personal)" all match.
SYNC_ROOT_FOLDERS: dict[str, str] = {
    "onedrive": "onedrive",
    "dropbox": "dropbox",
    "google drive": "google-drive",
    "googledrive": "google-drive",
    "my drive": "google-drive",
    "icloud drive": "icloud",
    "iclouddrive": "icloud",
}

# Windows points these at the sync root itself, which catches a folder the user
# renamed away from the names above.
SYNC_ROOT_ENV_VARS: dict[str, str] = {
    "ONEDRIVE": "onedrive",
    "ONEDRIVECONSUMER": "onedrive",
    "ONEDRIVECOMMERCIAL": "onedrive",
}


def _split_csv(raw: str) -> list[str]:
    """Split a comma-separated setting into stripped, non-empty entries."""
    return [entry.strip() for entry in raw.split(",") if entry.strip()]


class Settings(BaseSettings):
    """Runtime configuration for the Repcut engine."""

    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Secrets ---
    gemini_api_key: SecretStr | None = None

    # --- Paths (machine-local, never exposed over the API) ---
    data_dir: Path = Field(default=Path("data"))
    repcut_guide_path: Path | None = None

    # Empty means "derive it from data_dir". Set only to point the engine or a
    # migration at a scratch database; never at a URL carrying credentials.
    database_url: str | None = None

    # --- Services ---
    # The interface uvicorn binds. Loopback by default and read by `make dev`,
    # so the bind address is one value in one place rather than a flag someone
    # types differently each time. See repcut.security.warn_if_bound_publicly.
    engine_host: str = "127.0.0.1"
    engine_port: int = Field(default=8000, ge=1, le=65535)
    ui_port: int = Field(default=3000, ge=1, le=65535)
    engine_url: str = "http://localhost:8000"

    # --- Network boundary (see repcut.security for why these exist) ---
    # Comma-separated, and empty by default: the loopback names are always
    # allowed, so a working setup never needs to touch these. They exist for the
    # deliberate case - reaching the engine from a phone on the same wifi to test
    # a portrait export - which should be a decision someone made in .env, not a
    # side effect of how uvicorn happened to be started.
    extra_allowed_hosts: str = ""
    extra_allowed_origins: str = ""

    # --- Runtime ---
    log_level: LogLevel = "INFO"
    gemini_rpm_limit: int = Field(default=10, ge=1)
    gemini_daily_limit: int = Field(default=1400, ge=1)
    torch_device: TorchDevicePreference = "auto"

    @field_validator("log_level", mode="before")
    @classmethod
    def _normalise_log_level(cls, value: object) -> object:
        """Accept ``info`` / ``Info`` in .env without failing validation."""
        if isinstance(value, str):
            return value.strip().upper()
        return value

    @field_validator("torch_device", mode="before")
    @classmethod
    def _normalise_torch_device(cls, value: object) -> object:
        """Accept ``AUTO`` / ``Cuda`` in .env without failing validation."""
        if isinstance(value, str):
            return value.strip().lower()
        return value

    @model_validator(mode="after")
    def _anchor_relative_paths(self) -> "Settings":
        """Resolve relative paths against the repo root, not the process cwd.

        ``.env.example`` ships ``DATA_DIR=./data``. Anchoring on cwd would put
        the data directory somewhere different depending on where uvicorn or
        pytest was launched from.
        """
        if not self.data_dir.is_absolute():
            object.__setattr__(self, "data_dir", (REPO_ROOT / self.data_dir).resolve())
        return self

    @property
    def resolved_database_url(self) -> str:
        """SQLAlchemy URL for the project database.

        Never log this. It embeds the absolute ``data_dir``, which on Windows
        contains the OS username (secrets.md). ``as_posix`` because a SQLite URL
        takes forward slashes on every platform.
        """
        if self.database_url:
            return self.database_url
        return f"sqlite+aiosqlite:///{(self.data_dir / 'repcut.db').as_posix()}"

    @property
    def extra_allowed_hosts_list(self) -> list[str]:
        """``EXTRA_ALLOWED_HOSTS`` split into entries, blanks dropped.

        A plain comma-separated string rather than a ``list[str]`` field:
        pydantic-settings parses list-typed fields from the environment as JSON,
        so the obvious ``EXTRA_ALLOWED_HOSTS=192.168.1.40`` fails to parse and
        the fix is to write JSON in a .env file. A setting people get wrong is a
        setting people disable.
        """
        return _split_csv(self.extra_allowed_hosts)

    @property
    def extra_allowed_origins_list(self) -> list[str]:
        """``EXTRA_ALLOWED_ORIGINS`` split into entries, blanks dropped.

        Each entry must be a full origin - ``http://192.168.1.40:3000`` - because
        that is what a browser sends and what CORS compares against. A bare
        hostname silently matches nothing.
        """
        return _split_csv(self.extra_allowed_origins)

    @property
    def gemini_api_key_set(self) -> bool:
        """Whether a non-empty Gemini key is configured. Never the value itself."""
        if self.gemini_api_key is None:
            return False
        return bool(self.gemini_api_key.get_secret_value().strip())


def detect_sync_root(path: Path) -> str | None:
    """Return the cloud-sync provider whose folder contains ``path``, else None.

    Returns a provider **label only** — never the path and never a component of
    it, because those carry the OS username. See .claude/rules/secrets.md.
    """
    resolved = path.expanduser().resolve()

    for variable, provider in SYNC_ROOT_ENV_VARS.items():
        configured = os.environ.get(variable, "").strip()
        if not configured:
            continue
        try:
            if resolved.is_relative_to(Path(configured).expanduser().resolve()):
                return provider
        except OSError:
            # A sync client pointing its env var at an unmounted drive or a
            # malformed path must not take engine startup down. Fall through to
            # matching on folder names instead.
            continue

    for part in resolved.parts:
        normalized = part.casefold()
        for folder, provider in SYNC_ROOT_FOLDERS.items():
            if normalized == folder or normalized.startswith((f"{folder} ", f"{folder}-")):
                return provider
    return None


def warn_if_data_dir_synced(data_dir: Path) -> str | None:
    """Warn at startup when DATA_DIR sits inside a cloud-sync folder.

    Not a style preference. A synced data directory breaks the pipeline three
    ways: Files-On-Demand leaves placeholder stubs that ffprobe cannot read, the
    sync agent contends for a lock on the part-file an interrupted upload is
    resuming, and every intermediate render is pushed into cloud storage quota —
    which also carries footage off the machine, against P4.

    Returns the provider label, or None when the directory is clear.
    """
    provider = detect_sync_root(data_dir)
    if provider is None:
        return None
    logger: structlog.stdlib.BoundLogger = structlog.get_logger(__name__)
    logger.warning(
        "data_dir_under_cloud_sync",
        provider=provider,
        impact=(
            "placeholder files break ffprobe, the sync agent locks in-flight "
            "uploads, and renders are pushed into cloud quota"
        ),
        fix="set DATA_DIR in .env to a path outside the synced folder",
    )
    return provider


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings, parsed once, and check DATA_DIR.

    The sync guard fires here, not at engine startup. Hanging it off FastAPI's
    lifespan made it unreachable from everything except a real ASGI server:
    httpx's ``ASGITransport`` implements the HTTP scope only and never opens a
    lifespan scope, so no test, no gate and no ``alembic upgrade`` ever ran it.
    A DATA_DIR inside OneDrive therefore survived a full green test run in
    silence - which is worse than having no guard, because the gate reads as
    evidence the path was checked.

    Resolving settings is the one thing every entry point does before it can
    touch DATA_DIR, so that is where the check belongs. ``lru_cache`` keeps it
    to a single warning per process. Constructing ``Settings`` directly stays
    silent, which is what tests and fixtures want.
    """
    settings = Settings()
    warn_if_data_dir_synced(settings.data_dir)
    return settings
