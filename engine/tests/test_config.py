"""Settings must never surface the Gemini key's value - only whether it is set."""

import pytest
from pydantic import SecretStr
from pydantic_settings import SettingsConfigDict

from repcut.config import Settings

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
