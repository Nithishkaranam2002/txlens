"""`make prune-login-attempts`: delete login attempts older than the retention period.

Each login also prunes, so this is for quiet periods or a scheduled job.
"""

from txlens_backend.api.auth import prune_login_attempts
from txlens_backend.config import get_settings
from txlens_backend.db import make_engine


def main() -> int:
    """Prune old login attempts as the app login."""
    settings = get_settings()
    engine = make_engine(settings.app_database_url.get_secret_value())
    with engine.begin() as conn:
        deleted = prune_login_attempts(conn, settings.login_attempt_retention_days)
    engine.dispose()
    print(
        f"Deleted {deleted} login attempts older than {settings.login_attempt_retention_days} days."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
