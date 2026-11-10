"""Password hashing (Argon2id) and signed tokens (JWT, HS256).

Access tokens are short-lived and carry who the user is: id, tenant, role. Refresh tokens
are long-lived, carry only id and tenant, and are also recorded in `app.refresh_token` so
they can be rotated and revoked. Both travel in httpOnly cookies, never in JavaScript.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

ALGORITHM = "HS256"
TokenType = Literal["access", "refresh"]
_hasher = PasswordHasher()  # Argon2id with the library's OWASP-aligned defaults
# Verified when the user does not exist, so a wrong user name takes as long as a wrong password.
_DUMMY_HASH = _hasher.hash("not-a-real-password")


class InvalidTokenError(Exception):
    """The token is missing, expired, tampered with, or of the wrong type."""


@dataclass(frozen=True)
class TokenClaims:
    """What a verified token says."""

    user_id: str
    tenant_id: str
    role: str
    jti: str
    token_type: TokenType
    expires_at: datetime


def hash_password(password: str) -> str:
    """An Argon2id hash (salted; the parameters are stored inside the hash)."""
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    """True if the password matches. With no hash (unknown user) it still spends the time."""
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerificationError, InvalidHashError):
        return False


def issue_token(
    token_type: TokenType, user_id: str, tenant_id: str, role: str, ttl: timedelta
) -> TokenClaims:
    """New claims with a fresh id (jti) and expiry; sign them with `encode`."""
    expires = datetime.now(UTC).replace(microsecond=0) + ttl
    return TokenClaims(user_id, tenant_id, role, str(uuid.uuid4()), token_type, expires)


def encode(secret: str, claims: TokenClaims) -> str:
    """The signed JWT for these claims."""
    payload = {
        "sub": claims.user_id,
        "tenant": claims.tenant_id,
        "role": claims.role,
        "jti": claims.jti,
        "typ": claims.token_type,
        "iat": datetime.now(UTC),
        "exp": claims.expires_at,
    }
    return jwt.encode(payload, secret, algorithm=ALGORITHM)


def decode(secret: str, token: str | None, expected: TokenType) -> TokenClaims:
    """Verified claims. Only HS256 is accepted (never `none`), and expiry is enforced."""
    if not token:
        raise InvalidTokenError("no token")
    try:
        payload = jwt.decode(
            token,
            secret,
            algorithms=[ALGORITHM],
            options={"require": ["sub", "tenant", "role", "jti", "typ", "exp", "iat"]},
        )
    except jwt.PyJWTError as error:
        raise InvalidTokenError(str(error)) from error
    if payload["typ"] != expected:
        raise InvalidTokenError(f"expected a {expected} token")
    return TokenClaims(
        str(payload["sub"]),
        str(payload["tenant"]),
        str(payload["role"]),
        str(payload["jti"]),
        expected,
        datetime.fromtimestamp(int(payload["exp"]), UTC),
    )
