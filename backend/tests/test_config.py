import pytest
from pydantic import SecretStr, ValidationError

from txlens_backend.config import InsecureSettingError, Settings, get_settings, require_secret


def test_settings_read_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEMPORAL_ADDRESS", "temporal:7233")
    assert get_settings().temporal_address == "temporal:7233"


def test_api_key_is_hidden_when_printed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-secret")
    settings = get_settings()
    assert "sk-test-secret" not in repr(settings)
    assert settings.openai_api_key.get_secret_value() == "sk-test-secret"


def test_database_passwords_are_hidden_when_printed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://owner:pw-owner@db/x")
    monkeypatch.setenv("APP_DATABASE_URL", "postgresql+psycopg://api:pw-api@db/x")
    text = repr(get_settings())
    assert "pw-owner" not in text
    assert "pw-api" not in text


@pytest.mark.parametrize("value", ["", "too-short", "placeholder-generate-with-openssl-rand-64"])
def test_weak_secrets_are_refused(value: str) -> None:
    with pytest.raises(InsecureSettingError, match="JWT_SECRET"):
        require_secret("jwt_secret", SecretStr(value))


def test_strong_secret_is_returned() -> None:
    assert require_secret("jwt_secret", SecretStr("x" * 32)) == "x" * 32


@pytest.mark.parametrize("value", ["*", "127.0.0.1, *"])
def test_wildcard_trusted_proxy_is_refused(value: str) -> None:
    with pytest.raises(ValidationError, match="TRUSTED_PROXY_IPS"):
        Settings(trusted_proxy_ips=value)
