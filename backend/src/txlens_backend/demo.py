"""`txlens-demo`: checks and summaries for `make demo`, `make demo-snapshot`, `make demo-restore`.

- `check`: the secrets the demo needs are set and strong. Names a problem, never a value.
- `has-model`: exit 0 if a rejection-risk model is registered as champion.
- `counts`: row counts of the tables a demo needs, as JSON (snapshot manifest, round-trip test).
- `status`: what is loaded and what is still pending (no API key, no cached answer), then the
  URLs and the demo user names. Passwords stay in `.env` and are never printed.
- `reset-sessions`: forget every login attempt and refresh token, e.g. after restoring a
  snapshot onto a server, so no session or IP address from the machine it came from carries over.
- `drop-surveillance-after DATE`: delete surveillance flags and runs dated after the data
  cut-off (a run "as of today" on data that ends earlier), so a run as of the cut-off can
  rebuild the worklist. Refuses if someone already acknowledged or resolved one of them.

Runs as the database owner (superuser), so counts cover every tenant.
"""

import argparse
import json
from collections import Counter
from collections.abc import Sequence
from datetime import date

from sqlalchemy import Connection, bindparam, create_engine, text
from sqlalchemy.dialects.postgresql import JSONB

from txlens_backend.config import InsecureSettingError, Settings, get_settings, require_secret
from txlens_backend.documents.llm import key_missing
from txlens_backend.risk.registry import champion_version
from txlens_backend.seed_users import MIN_PASSWORD_LENGTH

# Table -> what it shows in the demo. Counted for the snapshot manifest and the round trip.
TABLES = {
    "cdm.person": "patients",
    "cdm.measurement": "lab results",
    "cdm.note": "clinical notes",
    "app.surveillance_flag": "surveillance flags",
    "app.risk_prediction": "risk scores",
    "app.document": "biopsy reports",
    "app.note_processed": "notes through NLP",
    "lit.abstract": "literature abstracts",
    "app.llm_cache": "cached LLM answers",
    "app.app_user": "users",
    "vocab.concept": "vocabulary concepts",
}
FINAL_DOCUMENT_STATUSES = ("extracted", "approved", "rejected", "in_review", "review_expired")
# The owner bypasses RLS, so the audit row names its center explicitly.
RESET_AUDIT = text(
    "INSERT INTO app.audit_log (tenant_id, action, object_type, details) "
    "VALUES (:tenant_id, 'sessions_reset', 'deployment', :details)"
).bindparams(bindparam("details", type_=JSONB))
DROP_AUDIT = text(
    "INSERT INTO app.audit_log (tenant_id, action, object_type, details) "
    "VALUES (:tenant_id, 'surveillance_dropped_after_cutoff', 'surveillance_run', :details)"
).bindparams(bindparam("details", type_=JSONB))


def problems(settings: Settings) -> list[str]:
    """What is missing or weak in the settings the demo needs (names only)."""
    found = []
    for name in ("jwt_secret", "deid_hmac_key"):
        try:
            require_secret(name, getattr(settings, name))
        except InsecureSettingError:
            found.append(f"{name.upper()}: set a random value of at least 32 characters")
    for role in ("clinician", "researcher", "data_steward", "admin"):
        password = getattr(settings, f"demo_{role}_password").get_secret_value()
        if len(password) < MIN_PASSWORD_LENGTH:
            found.append(f"DEMO_{role.upper()}_PASSWORD: at least {MIN_PASSWORD_LENGTH} characters")
    return found


def counts(conn: Connection) -> dict[str, int]:
    """Rows per demo table."""
    return {table: int(conn.execute(text(f"SELECT count(*) FROM {table}")).scalar_one())
            for table in TABLES}  # fmt: skip


def pending(conn: Connection) -> dict[str, int]:
    """AI work not done yet: documents without a final status, notes saved rules-only, and
    abstracts without an embedding."""
    final = {"s": list(FINAL_DOCUMENT_STATUSES)}
    documents = text("SELECT count(*) FROM app.document WHERE status <> ALL(:s)")
    notes = text("SELECT count(*) FROM app.note_processed WHERE nlp_system NOT LIKE '%+llm-%'")
    return {
        "biopsy reports": conn.execute(documents, final).scalar_one(),
        "notes (rules only)": conn.execute(notes).scalar_one(),
    }


def reset_sessions(conn: Connection) -> dict[str, int]:
    """Delete all login attempts and refresh tokens, with one audit row per center. Returns the
    deleted counts."""
    tokens = conn.execute(text(
        "DELETE FROM app.refresh_token RETURNING tenant_id"
    )).scalars().all()  # fmt: skip
    attempts = conn.execute(text("DELETE FROM app.login_attempt")).rowcount
    per_tenant = Counter(tokens)
    tenants = conn.execute(text("SELECT tenant_id FROM app.tenant ORDER BY tenant_id")).scalars()
    for tenant_id in tenants.all():
        details = {"refresh_tokens": per_tenant[tenant_id],
                   "login_attempts_all_centers": attempts}  # fmt: skip
        conn.execute(RESET_AUDIT, {"tenant_id": tenant_id, "details": details})
    return {"refresh_tokens": len(tokens), "login_attempts": attempts}


class ReviewedFlagsError(RuntimeError):
    """Flags a person already acted on: they are never deleted by a clean-up."""


def drop_surveillance_after(conn: Connection, cutoff: date) -> dict[str, int]:
    """Delete surveillance flags and runs dated after `cutoff`, with one audit row per center.
    Returns the deleted counts. Raises ReviewedFlagsError if a person acted on one of them."""
    reviewed = conn.execute(text(
        "SELECT count(*) FROM app.surveillance_flag "
        "WHERE as_of > :c AND status_changed_at IS NOT NULL"
    ), {"c": cutoff}).scalar_one()  # fmt: skip
    if reviewed:
        raise ReviewedFlagsError(f"{reviewed} flags after {cutoff} were reviewed; not deleted")
    flags = Counter(conn.execute(text(
        "DELETE FROM app.surveillance_flag WHERE as_of > :c RETURNING tenant_id"
    ), {"c": cutoff}).scalars())  # fmt: skip
    runs = Counter(conn.execute(text(
        "DELETE FROM app.surveillance_run WHERE as_of > :c RETURNING tenant_id"
    ), {"c": cutoff}).scalars())  # fmt: skip
    for tenant_id in sorted(set(flags) | set(runs)):
        details = {"cutoff": cutoff.isoformat(), "flags": flags[tenant_id],
                   "runs": runs[tenant_id]}  # fmt: skip
        conn.execute(DROP_AUDIT, {"tenant_id": tenant_id, "details": details})
    return {"flags": flags.total(), "runs": runs.total()}


def users(conn: Connection) -> list[tuple[str, str]]:
    """(user name, role) of every active user, by center then role."""
    rows = conn.execute(text(
        "SELECT username, role FROM app.app_user WHERE is_active ORDER BY tenant_id, role"
    )).all()  # fmt: skip
    return [(row.username, row.role) for row in rows]


def status_lines(
    conn: Connection, settings: Settings, web: str, api: str, temporal: str
) -> list[str]:
    """The summary `make demo` prints at the end."""
    loaded = counts(conn)
    lines = ["", "TxLens demo is ready (synthetic data only).", ""]
    lines += [f"  {TABLES[t]:<22} {n:>10,}" for t, n in loaded.items()]
    waiting = {k: v for k, v in pending(conn).items() if v}
    if waiting:
        why = ("no OPENAI_API_KEY and no cached answer" if key_missing(settings, settings.llm_model)
               else "not processed yet")  # fmt: skip
        lines += ["", f"  Pending AI work ({why}):"]
        lines += [f"    {name:<20} {n:>10,}" for name, n in waiting.items()]
    lines += ["", f"  App:        {web}", f"  API docs:   {api}/docs",
              f"  Temporal:   {temporal}", "", "  Demo users (password: the "
              "DEMO_<ROLE>_PASSWORD value in .env):"]  # fmt: skip
    lines += [f"    {name:<28} {role}" for name, role in users(conn)]
    return lines


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check", help="are the demo's secrets set? (names problems, never values)")
    sub.add_parser("has-model", help="exit 0 if a risk model is registered as champion")
    sub.add_parser("counts", help="row counts of the demo tables, as JSON")
    sub.add_parser("reset-sessions", help="delete all login attempts and refresh tokens")
    drop = sub.add_parser("drop-surveillance-after", help="delete flags and runs after a date")
    drop.add_argument("cutoff", type=date.fromisoformat, help="the data cut-off, YYYY-MM-DD")
    status = sub.add_parser("status", help="what is loaded and pending; URLs and users")
    status.add_argument("--web", default="http://localhost:3000")
    status.add_argument("--api", default="http://localhost:8000")
    status.add_argument("--temporal", default="http://localhost:8233")
    args = parser.parse_args(argv)
    settings = get_settings()
    if args.command == "check":
        found = problems(settings)
        for problem in found:
            print(f"  .env {problem}")
        return 1 if found else 0
    if args.command == "has-model":
        return 0 if champion_version(settings.mlflow_tracking_uri) else 1
    engine = create_engine(settings.database_url.get_secret_value())
    if args.command == "reset-sessions":
        with engine.begin() as conn:
            deleted = reset_sessions(conn)
        engine.dispose()
        print(f"    deleted {deleted['refresh_tokens']} refresh tokens and "
              f"{deleted['login_attempts']} login attempts")  # fmt: skip
        return 0
    if args.command == "drop-surveillance-after":
        try:
            with engine.begin() as conn:
                dropped = drop_surveillance_after(conn, args.cutoff)
        except ReviewedFlagsError as error:
            print(f"    refused: {error}")
            return 1
        finally:
            engine.dispose()
        print(f"    deleted {dropped['flags']} flags and {dropped['runs']} runs after "
              f"{args.cutoff.isoformat()}")  # fmt: skip
        return 0
    with engine.connect() as conn:
        if args.command == "counts":
            print(json.dumps(counts(conn), sort_keys=True))
        else:
            print("\n".join(status_lines(conn, settings, args.web, args.api, args.temporal)))
    engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
