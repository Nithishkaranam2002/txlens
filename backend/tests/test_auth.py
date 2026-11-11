"""Login, cookies, rate limiting, refresh rotation, logout and token checks, through the API."""

from datetime import timedelta

import jwt
import pytest
from api_testutils import BASE_URL, PASSWORDS, login, set_cookies
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from txlens_backend.api.app import create_app
from txlens_backend.api.deps import ACCESS_COOKIE, CSRF_HEADER, REFRESH_COOKIE, Role
from txlens_backend.config import Settings
from txlens_backend.security import TokenType, encode, issue_token

pytestmark = pytest.mark.db


def test_login_sets_httponly_secure_strict_cookies(client: TestClient) -> None:
    response = login(client, Role.CLINICIAN)
    assert response.status_code == 200
    assert response.json()["role"] == "clinician"
    assert response.json()["tenant_id"] == "center_a"
    cookies = set_cookies(response)
    for name, path in ((ACCESS_COOKIE, "/"), (REFRESH_COOKIE, "/auth")):
        header = cookies[name].lower()
        assert "httponly" in header and "secure" in header and "samesite=strict" in header
        assert f"path={path};" in header + ";"
    assert "token" not in response.text  # tokens are never in the body


def test_me_needs_a_login(client: TestClient) -> None:
    assert client.get("/me").status_code == 401
    login(client, Role.RESEARCHER, "center_b")
    me = client.get("/me").json()
    assert (me["username"], me["role"], me["tenant_id"]) == (
        "researcher.center_b", "researcher", "center_b"
    )  # fmt: skip


def test_wrong_password_and_unknown_user_look_the_same(client: TestClient) -> None:
    wrong = client.post("/auth/login", json={"username": "clinician.center_a", "password": "x"})
    unknown = client.post("/auth/login", json={"username": "nobody", "password": "x"})
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json() == {"detail": "Invalid user name or password"}


def test_user_name_is_case_insensitive(client: TestClient) -> None:
    body = {"username": " Clinician.CENTER_A ", "password": PASSWORDS[Role.CLINICIAN]}
    assert client.post("/auth/login", json=body).status_code == 200


def test_too_many_failures_block_even_the_right_password(client: TestClient) -> None:
    for _ in range(5):
        client.post("/auth/login", json={"username": "admin.center_a", "password": "wrong"})
    blocked = login(client, Role.ADMIN)
    assert blocked.status_code == 429
    assert blocked.headers["Retry-After"] == "900"
    assert login(client, Role.CLINICIAN).status_code == 200  # other users are not affected


def test_a_successful_login_resets_the_user_count(client: TestClient) -> None:
    bad = {"username": "admin.center_b", "password": "wrong"}
    for _ in range(4):
        client.post("/auth/login", json=bad)
    assert login(client, Role.ADMIN, "center_b").status_code == 200
    for _ in range(4):
        client.post("/auth/login", json=bad)
    assert login(client, Role.ADMIN, "center_b").status_code == 200


def test_too_many_failures_from_one_address_are_blocked(client: TestClient) -> None:
    for n in range(20):
        client.post("/auth/login", json={"username": f"guess{n}", "password": "x"})
    assert login(client, Role.CLINICIAN).status_code == 429


def test_login_prunes_attempts_older_than_30_days(client: TestClient, api_owner: Engine) -> None:
    with api_owner.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO app.login_attempt (username, ip, succeeded, attempted_at) "
                "VALUES ('old', '1.2.3.4', false, now() - interval '31 days'), "
                "('recent', '1.2.3.4', false, now() - interval '29 days')"
            )
        )
    login(client, Role.CLINICIAN)
    with api_owner.connect() as conn:
        names = set(conn.execute(text("SELECT username FROM app.login_attempt")).scalars())
    assert "old" not in names and "recent" in names


def test_refresh_rotates_and_reuse_revokes_every_session(client: TestClient) -> None:
    login(client, Role.CLINICIAN, "center_c")
    first_refresh = client.cookies.get(REFRESH_COOKIE)
    rotated = client.post("/auth/refresh")
    assert rotated.status_code == 200
    second_refresh = client.cookies.get(REFRESH_COOKIE)
    assert second_refresh and second_refresh != first_refresh
    assert client.get("/me").status_code == 200

    # A thief replays the first (already rotated) refresh token.
    replay = client.post("/auth/refresh", headers={"Cookie": f"{REFRESH_COOKIE}={first_refresh}"})
    assert replay.status_code == 401
    # Every session of that user is now revoked, including the legitimate new one.
    legit = client.post("/auth/refresh", headers={"Cookie": f"{REFRESH_COOKIE}={second_refresh}"})
    assert legit.status_code == 401


def test_logout_revokes_the_refresh_token_and_clears_cookies(client: TestClient) -> None:
    login(client, Role.RESEARCHER)
    refresh_token = client.cookies.get(REFRESH_COOKIE)
    response = client.post("/auth/logout")
    assert response.status_code == 204
    for header in set_cookies(response).values():
        assert "max-age=0" in header.lower()
    again = client.post("/auth/refresh", headers={"Cookie": f"{REFRESH_COOKIE}={refresh_token}"})
    assert again.status_code == 401


@pytest.mark.parametrize("kind", ["expired", "wrong_key", "tampered_role", "alg_none", "refresh"])
def test_bad_access_tokens_are_rejected(
    api_app: FastAPI, api_settings: Settings, kind: str
) -> None:
    secret = api_settings.jwt_secret.get_secret_value()
    ttl = timedelta(seconds=-1) if kind == "expired" else timedelta(minutes=5)
    token_type: TokenType = "refresh" if kind == "refresh" else "access"
    claims = issue_token(token_type, "u", "center_a", "researcher", ttl)
    token = encode("k" * 40 if kind == "wrong_key" else secret, claims)
    if kind == "tampered_role":
        header, _, signature = token.split(".")
        payload = jwt.encode({"role": "clinician"}, "x" * 40, algorithm="HS256").split(".")[1]
        token = f"{header}.{payload}.{signature}"
    if kind == "alg_none":
        payload_dict = jwt.decode(token, options={"verify_signature": False})
        token = jwt.encode(payload_dict | {"role": "clinician"}, key=None, algorithm="none")
    with TestClient(api_app, base_url=BASE_URL) as fresh:
        assert fresh.get("/me", headers={"Cookie": f"{ACCESS_COOKIE}={token}"}).status_code == 401


def test_post_without_the_csrf_header_is_refused(api_app: FastAPI) -> None:
    with TestClient(api_app, base_url=BASE_URL) as bare:
        body = {"username": "clinician.center_a", "password": PASSWORDS[Role.CLINICIAN]}
        response = bare.post("/auth/login", json=body)
    assert response.status_code == 403
    assert "X-Requested-With" in response.json()["detail"]


def test_every_post_documents_the_csrf_header_for_swagger(api_app: FastAPI) -> None:
    for path, operations in api_app.openapi()["paths"].items():
        if "post" in operations:
            header = next(p for p in operations["post"]["parameters"] if p["in"] == "header")
            assert header["name"] == "X-Requested-With", path
            assert header["required"] is True, path
            assert header["schema"]["default"] == "XMLHttpRequest", path


def test_refresh_cookie_defaults_to_the_frontend_proxy_path(
    api_settings: Settings, api_app_engine: Engine
) -> None:
    assert Settings.model_fields["refresh_cookie_path"].default == "/api/auth"
    behind_proxy = api_settings.model_copy(update={"refresh_cookie_path": "/api/auth"})
    with TestClient(create_app(behind_proxy, api_app_engine), base_url=BASE_URL,
                    headers={CSRF_HEADER: "t"}) as client:  # fmt: skip
        cookies = set_cookies(login(client, Role.CLINICIAN, "center_b"))
    assert "path=/api/auth;" in cookies[REFRESH_COOKIE].lower() + ";"


def last_attempt_ip(owner: Engine, username: str) -> str:
    """The client address the API recorded for this user name's latest login attempt."""
    with owner.connect() as conn:
        ip: str = conn.execute(
            text("SELECT ip FROM app.login_attempt WHERE username = :u "
                 "ORDER BY attempted_at DESC LIMIT 1"), {"u": username},
        ).scalar_one()  # fmt: skip
    return ip


def test_spoofed_forwarded_for_does_not_change_the_rate_limited_address(
    client: TestClient, api_owner: Engine
) -> None:
    # The test client is not a trusted proxy, like any browser calling the API directly.
    spoofed = {"X-Forwarded-For": "203.0.113.9"}
    for n in range(20):
        client.post("/auth/login", json={"username": f"spoof{n}", "password": "x"},
                    headers=spoofed)  # fmt: skip
    assert last_attempt_ip(api_owner, "spoof0") == "testclient"
    # Rotating the spoofed address does not escape the per-address limit either.
    rotated = {"X-Forwarded-For": "198.51.100.7"}
    right = {"username": "clinician.center_a", "password": PASSWORDS[Role.CLINICIAN]}
    blocked = client.post("/auth/login", json=right, headers=rotated)
    assert blocked.status_code == 429


def test_forwarded_for_is_used_from_the_trusted_proxy(
    api_settings: Settings, api_app_engine: Engine, api_owner: Engine
) -> None:
    proxied = api_settings.model_copy(update={"trusted_proxy_ips": "testclient"})
    with TestClient(create_app(proxied, api_app_engine), base_url=BASE_URL,
                    headers={CSRF_HEADER: "t"}) as client:  # fmt: skip
        client.post("/auth/login", json={"username": "proxied", "password": "x"},
                    headers={"X-Forwarded-For": "203.0.113.9"})  # fmt: skip
    assert last_attempt_ip(api_owner, "proxied") == "203.0.113.9"
