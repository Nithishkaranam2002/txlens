"""Application settings, read from environment variables (and `.env` in development)."""

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Typed settings for the backend. Secrets are `SecretStr` so they never print in logs."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://txlens:txlens@localhost:5432/txlens"
    temporal_address: str = "localhost:7233"
    langfuse_host: str = "http://localhost:3001"
    openai_api_key: SecretStr = SecretStr("")


def get_settings() -> Settings:
    """Build settings from the current environment."""
    return Settings()
