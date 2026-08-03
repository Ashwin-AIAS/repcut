"""Application settings.

Every value comes from the repo-root ``.env`` (or the process environment) via
pydantic-settings. Nothing here is hardcoded to a machine, and the Gemini key is
held as a ``SecretStr`` so it cannot be printed, logged or serialised by
accident. Only ``gemini_api_key_set`` may ever leave this module.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# engine/repcut/config.py -> engine/repcut -> engine -> repo root.
REPO_ROOT = Path(__file__).resolve().parents[2]

LogLevel = Literal["CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"]
TorchDevicePreference = Literal["auto", "cuda", "cpu"]


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


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings, parsed once."""
    return Settings()
