"""Audit trail: who did what to which object, when, from which request.

Rows go into `app.audit_log` inside the same tenant transaction as the action, so an action
and its audit row are committed together or not at all. The app role can only add rows.
"""

from collections.abc import Mapping
from dataclasses import dataclass

from sqlalchemy import Connection, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql.expression import bindparam

INSERT = text(
    "INSERT INTO app.audit_log (action, object_type, object_id, details, user_id, request_id, ip) "
    "VALUES (:action, :object_type, :object_id, :details, :user_id, :request_id, :ip)"
).bindparams(bindparam("details", type_=JSONB))


@dataclass(frozen=True)
class RequestInfo:
    """Where a request came from: its id (echoed as X-Request-ID) and the client address."""

    request_id: str
    ip: str


def record(
    conn: Connection,
    info: RequestInfo,
    user_id: str | None,
    action: str,
    object_type: str,
    object_id: str | None = None,
    details: Mapping[str, object] | None = None,
) -> None:
    """Append one audit row in the connection's tenant."""
    conn.execute(
        INSERT,
        {
            "action": action,
            "object_type": object_type,
            "object_id": object_id,
            "details": dict(details or {}),
            "user_id": user_id,
            "request_id": info.request_id,
            "ip": info.ip,
        },
    )
