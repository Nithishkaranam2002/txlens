import pytest

from txlens_backend.config import get_settings


def test_settings_read_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEMPORAL_ADDRESS", "temporal:7233")
    assert get_settings().temporal_address == "temporal:7233"


def test_api_key_is_hidden_when_printed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-secret")
    settings = get_settings()
    assert "sk-test-secret" not in repr(settings)
    assert settings.openai_api_key.get_secret_value() == "sk-test-secret"
