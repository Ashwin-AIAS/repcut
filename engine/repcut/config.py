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

    # --- Services ---
    engine_port: int = Field(default=8000, ge=1, le=65535)
    ui_port: int = Field(default=3000, ge=1, le=65535)
    engine_url: str = "http://localhost:8000"

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
    """Return the process-wide settings, parsed once."""
    return Settings()
