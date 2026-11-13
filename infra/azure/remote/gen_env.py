"""Write the VM's .env: new random secrets once, never printed. Runs on the VM with python3.

Starts from .env.example (so every setting has its default) and fills in fresh values for
every password and key. Secrets that already exist are kept, so running it again only updates
the domain. The OpenAI and Langfuse keys are always empty: the cloud demo answers from the
LLM cache and cannot spend money. Once the data is restored, DATA_CUTOFF is set to the
extract date in data/raw/extract.json, so surveillance never runs past the end of the data.

Guest demo accounts: GUEST_PASSWORD and GUEST_EXPIRES_AT (GUEST_DAYS from now, 30 by default)
are made once and kept; `--rotate-guest DAYS` replaces both (`make cloud-guest-rotate`).

Usage: python3 gen_env.py <checkout folder> <domain> [--rotate-guest DAYS]
"""

import argparse
import json
import os
import secrets
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROLES = ("CLINICIAN", "RESEARCHER", "DATA_STEWARD", "ADMIN")
ALWAYS_EMPTY = ("OPENAI_API_KEY", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY",
                "LANGFUSE_NEXTAUTH_SECRET", "LANGFUSE_SALT", "NCBI_API_KEY")  # fmt: skip


def parse(text: str) -> dict[str, str]:
    """KEY=value lines of an env file (comments and blanks skipped)."""
    pairs = (line.split("=", 1) for line in text.splitlines() if "=" in line)
    return {key.strip(): value for key, value in pairs if not key.lstrip().startswith("#")}


def new_secrets() -> dict[str, str]:
    """Fresh values for every secret (URL-safe, so they fit in database URLs)."""
    values = {
        "POSTGRES_PASSWORD": secrets.token_urlsafe(32),
        # Kept in .env too, so a re-run can rebuild APP_DATABASE_URL with the same password.
        "APP_DB_PASSWORD": secrets.token_urlsafe(32),
        "JWT_SECRET": secrets.token_urlsafe(48),
        "DEID_HMAC_KEY": secrets.token_urlsafe(48),
    }
    values |= {f"DEMO_{role}_PASSWORD": secrets.token_urlsafe(18) for role in ROLES}
    values["GUEST_PASSWORD"] = secrets.token_urlsafe(18)
    return values


def guest_expiry(days: int) -> str:
    """GUEST_EXPIRES_AT `days` from now, in UTC, to the second."""
    return (datetime.now(UTC) + timedelta(days=days)).replace(microsecond=0).isoformat()


def guests(existing: dict[str, str], rotate_days: int | None) -> dict[str, str]:
    """The guest expiry (kept, or new on a first run) and, when rotating, a new password."""
    if rotate_days is not None:
        return {"GUEST_PASSWORD": secrets.token_urlsafe(18),
                "GUEST_EXPIRES_AT": guest_expiry(rotate_days)}  # fmt: skip
    if existing.get("GUEST_EXPIRES_AT"):
        return {"GUEST_EXPIRES_AT": existing["GUEST_EXPIRES_AT"]}
    return {"GUEST_EXPIRES_AT": guest_expiry(int(existing.get("GUEST_DAYS") or 30))}


def data_cutoff(folder: Path, existing: dict[str, str]) -> dict[str, str]:
    """DATA_CUTOFF from the restored data's extract date (before a restore: the value kept)."""
    extract = folder / "data" / "raw" / "extract.json"
    if extract.exists():
        return {"DATA_CUTOFF": json.loads(extract.read_text())["extract_date"]}
    return {"DATA_CUTOFF": existing["DATA_CUTOFF"]} if existing.get("DATA_CUTOFF") else {}


def settings(existing: dict[str, str], domain: str) -> dict[str, str]:
    """Every value this script owns: kept secrets (or new ones), plus the cloud settings."""
    fresh = new_secrets()
    keep = {k: existing[k] for k in fresh if existing.get(k)}
    chosen = fresh | keep
    owner, app = chosen["POSTGRES_PASSWORD"], chosen["APP_DB_PASSWORD"]
    return (
        chosen
        | {
            "POSTGRES_USER": "txlens",
            "POSTGRES_DB": "txlens",
            "DATABASE_URL": f"postgresql+psycopg://txlens:{owner}@localhost:5432/txlens",
            "APP_DATABASE_URL": f"postgresql+psycopg://txlens_api:{app}@localhost:5432/txlens",
            "TXLENS_DOMAIN": domain,
            "FRONTEND_ORIGIN": f"https://{domain}",
            "COOKIE_SECURE": "true",
            "DEMO_GUIDE": "1",
        }
        | dict.fromkeys(ALWAYS_EMPTY, "")
    )


def render(template: str, values: dict[str, str]) -> str:
    """The template with owned keys replaced in place and missing ones appended."""
    lines, seen = [], set()
    for line in template.splitlines():
        key = line.split("=", 1)[0].strip()
        if "=" in line and not line.lstrip().startswith("#") and key in values:
            seen.add(key)
            line = f"{key}={values[key]}"
        lines.append(line)
    extra = [f"{k}={v}" for k, v in values.items() if k not in seen]
    header = "# --- Cloud demo (infra/azure/remote/gen_env.py) ---"
    return "\n".join([*lines, "", header, *extra]) + "\n"


def main(argv: list[str]) -> int:
    """Entry point."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("folder", type=Path)
    parser.add_argument("domain")
    parser.add_argument("--rotate-guest", type=int, metavar="DAYS", default=None)
    args = parser.parse_args(argv)
    folder, domain = args.folder, args.domain
    env = folder / ".env"
    existing = parse(env.read_text()) if env.exists() else {}
    values = (settings(existing, domain) | data_cutoff(folder, existing)
              | guests(existing, args.rotate_guest))  # fmt: skip
    content = render((folder / ".env.example").read_text(), values)
    tmp = folder / ".env.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(content)
    tmp.replace(env)
    state = "kept the existing secrets" if existing else "generated new secrets"
    if args.rotate_guest is not None:
        state += "; new guest password"
    print(f"    .env for {domain}: {state}, guests until {values['GUEST_EXPIRES_AT']} "
          "(values not shown)")  # fmt: skip
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
