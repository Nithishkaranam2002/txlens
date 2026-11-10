"""`make seed-users`: one demo user per role for each center, passwords from `.env`.

User names are `<role>.<tenant>`, e.g. `clinician.center_a`. All display names are made up.
Users are written through `tenant_transaction` as the app login, so RLS applies here too.
Re-running updates the names and passwords (each run makes a new password hash).
"""

from collections.abc import Mapping

from pydantic import SecretStr
from sqlalchemy import Engine, text

from txlens_backend.api.deps import Role
from txlens_backend.config import Settings, get_settings
from txlens_backend.db import make_engine, tenant_transaction
from txlens_backend.db_setup import center_tenants
from txlens_backend.security import hash_password

MIN_PASSWORD_LENGTH = 12
# Fictional people, one per (center, role).
DISPLAY_NAMES = {
    "center_a": ("Dr. Imra Vellacott", "Tobin Ashgrove", "Wenna Quillfeather", "Odric Halloway"),
    "center_b": ("Dr. Pell Marrowind", "Saffi Underbrook", "Corran Fieldmere", "Juno Tavistrand"),
    "center_c": ("Dr. Ysolt Brackwater", "Emeric Dunlowe", "Talia Fennimore", "Rudd Okenshaw"),
}
UPSERT = text(
    "INSERT INTO app.app_user (username, display_name, role, password_hash) "
    "VALUES (:username, :display_name, :role, :password_hash) "
    "ON CONFLICT (username) DO UPDATE SET display_name = EXCLUDED.display_name, "
    "role = EXCLUDED.role, password_hash = EXCLUDED.password_hash, is_active = true"
)


def demo_passwords(settings: Settings) -> dict[Role, str]:
    """Role -> password from DEMO_<ROLE>_PASSWORD; refuses missing or short ones."""
    secrets: Mapping[Role, SecretStr] = {
        Role.CLINICIAN: settings.demo_clinician_password,
        Role.RESEARCHER: settings.demo_researcher_password,
        Role.DATA_STEWARD: settings.demo_data_steward_password,
        Role.ADMIN: settings.demo_admin_password,
    }
    passwords = {role: secret.get_secret_value() for role, secret in secrets.items()}
    weak = [role for role, pw in passwords.items() if len(pw) < MIN_PASSWORD_LENGTH]
    if weak:
        names = ", ".join(f"DEMO_{role.upper()}_PASSWORD" for role in weak)
        raise SystemExit(f"Set {names} in .env (at least {MIN_PASSWORD_LENGTH} characters).")
    return passwords


def seed_users(engine: Engine, tenants: list[str], passwords: Mapping[Role, str]) -> list[str]:
    """Create or update the demo users; returns their user names."""
    created = []
    for tenant in tenants:
        with tenant_transaction(engine, tenant) as conn:
            for role, display_name in zip(Role, DISPLAY_NAMES[tenant], strict=True):
                username = f"{role.value}.{tenant}"
                conn.execute(
                    UPSERT,
                    {
                        "username": username,
                        "display_name": display_name,
                        "role": role.value,
                        "password_hash": hash_password(passwords[role]),
                    },
                )
                created.append(username)
    return created


def main() -> int:
    """Seed the demo users for every center."""
    settings = get_settings()
    engine = make_engine(settings.app_database_url.get_secret_value())
    names = seed_users(engine, list(center_tenants()), demo_passwords(settings))
    engine.dispose()
    print(f"{len(names)} demo users ready: {', '.join(names)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
