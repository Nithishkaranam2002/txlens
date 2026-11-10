"""Application settings, read from environment variables (and `.env` in development)."""

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


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


def get_settings() -> Settings:
    """Build settings from the current environment."""
    return Settings()
