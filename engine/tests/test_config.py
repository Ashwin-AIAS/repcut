"""Settings must never surface the Gemini key's value - only whether it is set.

Also covers the cloud-sync guard on DATA_DIR, whose whole point is that it
reports a provider label and never the path.
"""

from pathlib import Path

import pytest
from pydantic import SecretStr
from pydantic_settings import SettingsConfigDict
from structlog.testing import capture_logs

from repcut.config import (
    SYNC_ROOT_ENV_VARS,
    Settings,
    detect_sync_root,
    warn_if_data_dir_synced,
)

# Not a credential: a literal used to prove the value cannot escape Settings.
FAKE_KEY = "not-a-real-key-0000000000"

ENV_KEYS = (
    "GEMINI_API_KEY",
    "DATA_DIR",
    "REPCUT_GUIDE_PATH",
    "ENGINE_PORT",
    "UI_PORT",
    "ENGINE_URL",
    "LOG_LEVEL",
    "GEMINI_RPM_LIMIT",
    "GEMINI_DAILY_LIMIT",
    "TORCH_DEVICE",
)


class IsolatedSettings(Settings):
    """Settings that ignore the developer's .env, so tests are machine-independent."""

    model_config = SettingsConfigDict(env_file=None, extra="ignore", case_sensitive=False)


@pytest.fixture(autouse=True)
def _clear_engine_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove engine env vars so a developer's shell cannot change the outcome."""
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def test_secret_value_absent_from_model_dump() -> None:
    settings = IsolatedSettings(gemini_api_key=SecretStr(FAKE_KEY))

    assert FAKE_KEY not in str(settings.model_dump())
    assert FAKE_KEY not in str(settings.model_dump(mode="json"))
    assert FAKE_KEY not in settings.model_dump_json()


def test_secret_value_absent_from_repr() -> None:
    settings = IsolatedSettings(gemini_api_key=SecretStr(FAKE_KEY))

    assert FAKE_KEY not in repr(settings)
    assert FAKE_KEY not in str(settings)


def test_gemini_api_key_set_reflects_presence() -> None:
    assert IsolatedSettings(gemini_api_key=SecretStr(FAKE_KEY)).gemini_api_key_set is True
    assert IsolatedSettings().gemini_api_key_set is False
    assert IsolatedSettings(gemini_api_key=SecretStr("")).gemini_api_key_set is False
    assert IsolatedSettings(gemini_api_key=SecretStr("   ")).gemini_api_key_set is False


def test_defaults_match_env_example() -> None:
    settings = IsolatedSettings()

    assert settings.engine_port == 8000
    assert settings.ui_port == 3000
    assert settings.engine_url == "http://localhost:8000"
    assert settings.log_level == "INFO"
    assert settings.gemini_rpm_limit == 10
    assert settings.gemini_daily_limit == 1400
    assert settings.torch_device == "auto"
    assert settings.repcut_guide_path is None


def test_data_dir_is_absolute_regardless_of_cwd(monkeypatch: pytest.MonkeyPatch) -> None:
    """DATA_DIR=./data must not follow the process working directory."""
    monkeypatch.setenv("DATA_DIR", "./data")

    assert IsolatedSettings().data_dir.is_absolute()


def test_log_level_and_device_are_case_insensitive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_LEVEL", "debug")
    monkeypatch.setenv("TORCH_DEVICE", "AUTO")

    settings = IsolatedSettings()

    assert settings.log_level == "DEBUG"
    assert settings.torch_device == "auto"


@pytest.fixture
def _no_sync_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unset the sync clients' own env vars so the outcome is the path's alone."""
    for variable in SYNC_ROOT_ENV_VARS:
        monkeypatch.delenv(variable, raising=False)


@pytest.mark.usefixtures("_no_sync_env")
@pytest.mark.parametrize(
    ("folder", "expected"),
    [
        ("OneDrive", "onedrive"),
        ("OneDrive - Contoso", "onedrive"),
        ("Dropbox", "dropbox"),
        ("Dropbox (Personal)", "dropbox"),
        ("Google Drive", "google-drive"),
        ("My Drive", "google-drive"),
        ("iCloudDrive", "icloud"),
    ],
)
def test_sync_root_detected_by_folder_name(tmp_path: Path, folder: str, expected: str) -> None:
    assert detect_sync_root(tmp_path / folder / "repcut" / "data") == expected


@pytest.mark.usefixtures("_no_sync_env")
def test_unsynced_path_is_clear(tmp_path: Path) -> None:
    assert detect_sync_root(tmp_path / "repcut-data") is None


def test_renamed_sync_folder_detected_via_env_var(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A user who renamed the folder still gets caught - the client exports its root."""
    monkeypatch.setenv("ONEDRIVE", str(tmp_path / "Cloud"))

    assert detect_sync_root(tmp_path / "Cloud" / "repcut" / "data") == "onedrive"


def test_unreadable_sync_env_var_does_not_raise(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An env var pointing at an unmounted drive must not take startup down."""
    monkeypatch.setenv("ONEDRIVE", "Q:\\\\gone\\\\<>|")

    assert detect_sync_root(tmp_path / "repcut-data") is None


@pytest.mark.usefixtures("_no_sync_env")
def test_warning_reports_provider_and_never_the_path(tmp_path: Path) -> None:
    data_dir = tmp_path / "OneDrive" / "repcut" / "data"

    with capture_logs() as logs:
        provider = warn_if_data_dir_synced(data_dir)

    assert provider == "onedrive"
    assert len(logs) == 1
    assert logs[0]["event"] == "data_dir_under_cloud_sync"
    assert logs[0]["provider"] == "onedrive"

    # The path carries the OS username; only the label may be logged.
    rendered = str(logs[0])
    assert str(data_dir) not in rendered
    assert str(tmp_path) not in rendered
    assert tmp_path.name not in rendered


@pytest.mark.usefixtures("_no_sync_env")
def test_no_warning_when_data_dir_is_clear(tmp_path: Path) -> None:
    with capture_logs() as logs:
        assert warn_if_data_dir_synced(tmp_path / "repcut-data") is None

    assert logs == []
