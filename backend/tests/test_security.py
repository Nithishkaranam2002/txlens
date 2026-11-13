"""Password hashing and signed tokens."""

from datetime import UTC, datetime, timedelta

import jwt
import pytest

from txlens_backend.security import (
    InvalidTokenError,
    decode,
    encode,
    hash_password,
    issue_token,
    verify_password,
)

SECRET = "s" * 40


def test_password_hash_is_salted_argon2id_and_verifies() -> None:
    first, second = hash_password("correct horse"), hash_password("correct horse")
    assert first.startswith("$argon2id$")
    assert first != second  # random salt
    assert verify_password(first, "correct horse")
    assert not verify_password(first, "wrong")


def test_unknown_user_and_garbage_hash_never_verify() -> None:
    assert not verify_password(None, "not-a-real-password")  # the dummy hash's own password
    assert not verify_password("not-a-hash", "anything")


def test_token_round_trip() -> None:
    claims = issue_token("access", "u1", "center_a", "clinician", timedelta(minutes=5))
    decoded = decode(SECRET, encode(SECRET, claims), "access")
    assert decoded == claims


def test_expired_token_is_rejected() -> None:
    claims = issue_token("access", "u1", "center_a", "clinician", timedelta(seconds=-1))
    with pytest.raises(InvalidTokenError, match="expired"):
        decode(SECRET, encode(SECRET, claims), "access")


def test_token_signed_with_another_key_is_rejected() -> None:
    claims = issue_token("access", "u1", "center_a", "clinician", timedelta(minutes=5))
    with pytest.raises(InvalidTokenError, match="Signature"):
        decode(SECRET, encode("k" * 40, claims), "access")


def test_tampered_payload_is_rejected() -> None:
    claims = issue_token("access", "u1", "center_a", "researcher", timedelta(minutes=5))
    header, _, signature = encode(SECRET, claims).split(".")
    forged = jwt.encode({"role": "clinician"}, "x" * 40, algorithm="HS256").split(".")[1]
    with pytest.raises(InvalidTokenError):
        decode(SECRET, f"{header}.{forged}.{signature}", "access")


def test_unsigned_alg_none_token_is_rejected() -> None:
    now = datetime.now(UTC)
    payload = {"sub": "u1", "tenant": "center_b", "role": "clinician", "jti": "j", "typ": "access",
               "iat": now, "exp": now + timedelta(minutes=5)}  # fmt: skip
    token = jwt.encode(payload, key="", algorithm="none")
    with pytest.raises(InvalidTokenError):
        decode(SECRET, token, "access")


def test_refresh_token_cannot_be_used_as_access_token() -> None:
    claims = issue_token("refresh", "u1", "center_a", "clinician", timedelta(days=1))
    with pytest.raises(InvalidTokenError, match="access"):
        decode(SECRET, encode(SECRET, claims), "access")


def test_missing_token_is_rejected() -> None:
    with pytest.raises(InvalidTokenError):
        decode(SECRET, None, "access")
