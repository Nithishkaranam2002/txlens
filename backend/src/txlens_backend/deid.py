"""De-identification for researchers and data stewards.

- **Pseudonym**: `R-` + the first 16 hex characters of HMAC-SHA256(key, "pid:tenant:person").
  Without the key nobody can recompute it, unlike a plain hash of a small id space, which
  anyone can brute-force. The same patient always gets the same pseudonym, so results can be
  joined across requests, and rotating the key unlinks all previous exports.
- **Date shift**: every date of one patient moves by the same number of days (±1..182, never
  0), derived from the same key. Intervals (e.g. "days since transplant") stay exact; calendar
  dates are no longer real.
- **Age band**: 5-year bands at a reference date, top-coded at 90+ (HIPAA Safe Harbor).
"""

import hashlib
import hmac
from datetime import date, timedelta

PREFIX = "R-"
PSEUDONYM_HEX = 16
MAX_SHIFT_DAYS = 182
TOP_AGE = 90


def _digest(key: str, label: str, tenant_id: str, person_id: int) -> bytes:
    message = f"{label}:{tenant_id}:{person_id}".encode()
    return hmac.new(key.encode(), message, hashlib.sha256).digest()


def pseudonym(key: str, tenant_id: str, person_id: int) -> str:
    """The patient's stable research id, e.g. 'R-3f9a0c17b2d84e61'."""
    return PREFIX + _digest(key, "pid", tenant_id, person_id).hex()[:PSEUDONYM_HEX]


# The same pseudonym, computed in Postgres (pgcrypto), to find a patient by it. The key is a
# bind parameter, never stored in the database.
PSEUDONYM_SQL = (
    f"'{PREFIX}' || left(encode(hmac('pid:' || {{tenant}} || ':' || {{person}}, "
    f":deid_key, 'sha256'), 'hex'), {PSEUDONYM_HEX})"
)


def pseudonym_sql(tenant_column: str, person_column: str) -> str:
    """SQL expression for the pseudonym of a row (needs the `:deid_key` parameter)."""
    return PSEUDONYM_SQL.format(tenant=tenant_column, person=person_column)


def date_shift_days(key: str, tenant_id: str, person_id: int) -> int:
    """This patient's shift in days: in -182..-1 or 1..182, the same on every call."""
    n = int.from_bytes(_digest(key, "shift", tenant_id, person_id)[:4], "big") % (
        2 * MAX_SHIFT_DAYS
    )
    return n - MAX_SHIFT_DAYS if n < MAX_SHIFT_DAYS else n - MAX_SHIFT_DAYS + 1


def shift(value: date | None, days: int) -> date | None:
    """A date moved by `days` (None stays None)."""
    return None if value is None else value + timedelta(days=days)


def age_band(year_of_birth: int, on: date) -> str:
    """'45-49' style band of the age reached in `on`'s year; '90+' at 90 and over."""
    age = max(on.year - year_of_birth, 0)
    if age >= TOP_AGE:
        return f"{TOP_AGE}+"
    low = age // 5 * 5
    return f"{low}-{low + 4}"


def suppress(count: int, threshold: int) -> int | str:
    """Small-cell suppression: 1..threshold-1 becomes '<threshold'; 0 and larger stay."""
    return f"<{threshold}" if 0 < count < threshold else count
