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
    """An open surveillance flag of this center that nobody has reviewed yet."""
    with owner.connect() as conn:
        flag_id = conn.execute(
            text("SELECT flag_id FROM app.surveillance_flag WHERE tenant_id = :t "
                 "AND status = 'new' AND cleared_at IS NULL ORDER BY flag_id LIMIT 1"),
            {"t": tenant},
        ).scalar_one()  # fmt: skip
    return str(flag_id)
