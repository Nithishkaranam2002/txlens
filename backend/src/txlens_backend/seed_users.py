"""`make seed-users`: one demo user per role for each center, passwords from `.env`.

User names are `<role>.<tenant>`, e.g. `clinician.center_a`. All display names are made up.
Users are written through `tenant_transaction` as the app login, so RLS applies here too.
Re-running updates the names and passwords (each run makes a new password hash).

`txlens-seed-users --guests`: the public demo's guest accounts, guest.clinician and
guest.researcher in center_a, with GUEST_PASSWORD and an expiry (GUEST_EXPIRES_AT). Their
existing sessions end, so a new password takes effect at once.
"""

import argparse
from collections.abc import Mapping, Sequence
from datetime import datetime

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


GUEST_TENANT = "center_a"
GUESTS = {"guest.clinician": (Role.CLINICIAN, "Guest clinician"),
          "guest.researcher": (Role.RESEARCHER, "Guest researcher")}  # fmt: skip
GUEST_UPSERT = text(
    "INSERT INTO app.app_user (username, display_name, role, password_hash, expires_at) "
    "VALUES (:username, :display_name, :role, :password_hash, :expires_at) "
    "ON CONFLICT (username) DO UPDATE SET display_name = EXCLUDED.display_name, "
    "role = EXCLUDED.role, password_hash = EXCLUDED.password_hash, "
    "expires_at = EXCLUDED.expires_at, is_active = true RETURNING user_id"
)
END_SESSIONS = text(
    "UPDATE app.refresh_token SET revoked_at = now() "
    "WHERE user_id = ANY(:ids) AND revoked_at IS NULL"
)


def seed_guests(engine: Engine, password: str, expires_at: datetime) -> list[str]:
    """Create or update the guest accounts and end their sessions; returns their names."""
    if len(password) < MIN_PASSWORD_LENGTH:
        raise SystemExit(f"Set GUEST_PASSWORD in .env (at least {MIN_PASSWORD_LENGTH} characters).")
    with tenant_transaction(engine, GUEST_TENANT) as conn:
        ids = [
            str(conn.execute(GUEST_UPSERT, {
                "username": username, "display_name": display_name, "role": role.value,
                "password_hash": hash_password(password), "expires_at": expires_at,
            }).scalar_one())
            for username, (role, display_name) in GUESTS.items()
        ]  # fmt: skip
        conn.execute(END_SESSIONS, {"ids": ids})
    return list(GUESTS)


def main(argv: Sequence[str] | None = None) -> int:
    """Seed the demo users for every center, or (--guests) the guest accounts."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--guests", action="store_true", help="the guest accounts only")
    args = parser.parse_args(argv)
    settings = get_settings()
    engine = make_engine(settings.app_database_url.get_secret_value())
    if args.guests:
        if settings.guest_expires_at is None:
            raise SystemExit("Set GUEST_EXPIRES_AT in .env (ISO date and time).")
        names = seed_guests(engine, settings.guest_password.get_secret_value(),
                            settings.guest_expires_at)  # fmt: skip
        until = settings.guest_expires_at.isoformat()
        print(f"Guest accounts ready until {until}: {', '.join(names)}")
    else:
        names = seed_users(engine, list(center_tenants()), demo_passwords(settings))
        print(f"{len(names)} demo users ready: {', '.join(names)}")
    engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
