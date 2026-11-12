"""Helpers shared by the API tests (a module, so tests can import it)."""

from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy import Engine, text

from txlens_backend.api.deps import Role

API_TENANTS = ("center_a", "center_b", "center_c")
BASE_URL = "https://testserver"  # https, so the Secure cookies are sent back
PASSWORDS = {
    Role.CLINICIAN: "Clinician-test-pw-1",
    Role.RESEARCHER: "Researcher-test-pw-1",
    Role.DATA_STEWARD: "Steward-test-pw-1",
    Role.ADMIN: "Admin-test-pw-1",
}


def login(client: TestClient, role: Role, tenant: str = "center_a") -> Response:
    """Log in as the demo user for this role and center (cookies stay on the client)."""
    body = {"username": f"{role.value}.{tenant}", "password": PASSWORDS[role]}
    response: Response = client.post("/auth/login", json=body)
    return response


def set_cookies(response: Response) -> dict[str, str]:
    """Cookie name -> its full Set-Cookie header, from one response."""
    return {h.split("=", 1)[0]: h for h in response.headers.get_list("set-cookie")}


def new_flag(owner: Engine, tenant: str = "center_a") -> str:
    """An open surveillance flag of this center that nobody has ever reviewed (no review in
    the audit log either, so its history is only "raised"). Tests that review it should take
    it through the `fresh_flag` fixture, which puts it back afterwards. High-severity flags
    come first on purpose: a center has only a few, so a test that reviewed one and left it
    reviewed would make the worklist severity filter fail every time, not once in a while."""
    with owner.connect() as conn:
        flag_id = conn.execute(
            text("SELECT f.flag_id FROM app.surveillance_flag f WHERE f.tenant_id = :t "
                 "AND f.status = 'new' AND f.cleared_at IS NULL AND NOT EXISTS ("
                 "SELECT 1 FROM app.audit_log a WHERE a.object_type = 'surveillance_flag' "
                 "AND a.object_id = f.flag_id::text) "
                 "ORDER BY f.severity = 'high' DESC, f.flag_id LIMIT 1"),
            {"t": tenant},
        ).scalar_one()  # fmt: skip
    return str(flag_id)
