"""Application settings, read from environment variables (and `.env` in development)."""

from pathlib import Path
from typing import Literal

from pydantic import SecretStr, field_validator
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
    # Reusing a just-rotated refresh token within this window returns its replacement instead
    # of revoking every session (a reload or a second tab can race a refresh).
    refresh_reuse_grace_seconds: int = 30
    cookie_secure: bool = True  # browsers accept Secure cookies on http://localhost too
    frontend_origin: str = "http://localhost:3000"
    # The browser path of the refresh endpoint. Browsers reach the API through the frontend's
    # /api proxy, so the refresh cookie goes to /api/auth/refresh and nowhere else. Clients
    # that call the API directly (tests, scripts) set "/auth".
    refresh_cookie_path: str = "/api/auth"
    # Comma-separated addresses allowed to set X-Forwarded-For (the local Next.js proxy). Never
    # "*": anyone could then pick the client address that login rate limiting counts.
    trusted_proxy_ips: str = "127.0.0.1"
    login_max_failures_per_user: int = 5
    login_max_failures_per_ip: int = 20
    login_window_minutes: int = 15
    login_attempt_retention_days: int = 30

    # --- De-identification ---
    deid_hmac_key: SecretStr = SecretStr("")  # keys pseudonyms and date shifts
    small_cell_threshold: int = 11  # de-identified counts 1..threshold-1 are shown as "<11"
    # A count is withheld if, with one shown to the same user within this many days over
    # overlapping filters, it would reveal fewer than small_cell_threshold patients.
    disclosure_window_days: int = 30

    # --- Document extraction (LLM) ---
    ocr_engine: Literal["tesseract", "docling"] = "tesseract"  # docling: opt-in dependency group
    documents_root: Path = Path("data/raw")  # where document source paths are resolved
    # LiteLLM model names: "openai/<model>" (paid) or "ollama/llama3.1:8b" (local, free).
    llm_model: str = "openai/gpt-6-luna"
    llm_model_pass_b: str = ""  # second extraction pass; empty = same model, other prompt
    llm_reasoning_effort: str = "low"  # ignored by models without reasoning
    llm_budget_usd: float = 3.0  # hard cap per batch; the batch stops when it is reached
    llm_concurrency: int = 4  # LLM calls in flight at once (per worker)
    llm_timeout_seconds: int = 60
    ollama_base_url: str = "http://localhost:11434"
    # Copilot literature retrieval: "openai/text-embedding-3-small" (paid, asked for 768
    # dimensions) or "ollama/nomic-embed-text" (local, free, 768 dimensions).
    embedding_model: str = "openai/text-embedding-3-small"
    # Judge of the copilot evaluation: deliberately another model than llm_model.
    eval_judge_model: str = "openai/gpt-5.6-luna"
    langfuse_public_key: SecretStr = SecretStr("")
    langfuse_secret_key: SecretStr = SecretStr("")
    # Document confidence below this goes to human review. 0.70: no auto-approved report was
    # wrong on the 100-report sample at 0.85, 0.70 or 0.50 (evals/reports/biopsy_extraction.md);
    # re-check on more data.
    review_threshold: float = 0.70
    qc_sample_rate: float = 0.10  # share of would-be auto-approved reports sent to review anyway
    review_timeout_days: int = 14  # an unanswered review expires; nothing is loaded

    # --- Demo users (`make seed-users`) ---
    demo_clinician_password: SecretStr = SecretStr("")
    demo_researcher_password: SecretStr = SecretStr("")
    demo_data_steward_password: SecretStr = SecretStr("")
    demo_admin_password: SecretStr = SecretStr("")

    @field_validator("trusted_proxy_ips")
    @classmethod
    def no_wildcard_proxy(cls, ips: str) -> str:
        """Refuse "*": X-Forwarded-For is only trusted from named proxy addresses."""
        if "*" in (ip.strip() for ip in ips.split(",")):
            raise ValueError("TRUSTED_PROXY_IPS must list proxy addresses, not '*'")
        return ips


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
