"""Application settings, read from environment variables (and `.env` in development)."""

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

MIN_SECRET_LENGTH = 32


class Settings(BaseSettings):
    """Typed settings for the backend. Secrets are `SecretStr` so they never print in logs."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Owner role: runs migrations. Superuser locally, so it is never used for app queries.
    database_url: SecretStr = SecretStr("postgresql+psycopg://txlens:txlens@localhost:5432/txlens")
    # App login role (member of txlens_app): no superuser, cannot bypass Row-Level Security.
    app_database_url: SecretStr = SecretStr(
        "postgresql+psycopg://txlens_api:txlens_api@localhost:5432/txlens"
    )
    temporal_address: str = "localhost:7233"
    langfuse_host: str = "http://localhost:3001"
    openai_api_key: SecretStr = SecretStr("")

    # --- API auth ---
    jwt_secret: SecretStr = SecretStr("")  # signs access and refresh tokens (HS256)
    access_token_minutes: int = 15
    refresh_token_days: int = 7
    cookie_secure: bool = True  # browsers accept Secure cookies on http://localhost too
    frontend_origin: str = "http://localhost:3000"
    login_max_failures_per_user: int = 5
    login_max_failures_per_ip: int = 20
    login_window_minutes: int = 15
    login_attempt_retention_days: int = 30

    # --- De-identification ---
    deid_hmac_key: SecretStr = SecretStr("")  # keys pseudonyms and date shifts
    small_cell_threshold: int = 11  # de-identified counts 1..threshold-1 are shown as "<11"

    # --- Demo users (`make seed-users`) ---
    demo_clinician_password: SecretStr = SecretStr("")
    demo_researcher_password: SecretStr = SecretStr("")
    demo_data_steward_password: SecretStr = SecretStr("")
    demo_admin_password: SecretStr = SecretStr("")


class InsecureSettingError(ValueError):
    """A secret is missing, too short, or still the placeholder from .env.example."""


def require_secret(name: str, value: SecretStr) -> str:
    """The secret's value; refuses empty, short or placeholder secrets."""
    secret = value.get_secret_value()
    if len(secret) < MIN_SECRET_LENGTH or secret.startswith("placeholder"):
        raise InsecureSettingError(
            f"{name.upper()} must be a random value of at least {MIN_SECRET_LENGTH} characters "
            "(generate one with `openssl rand -base64 48`)."
        )
    return secret


def get_settings() -> Settings:
    """Build settings from the current environment."""
    return Settings()
