# ADR 0007: Authentication with short-lived JWTs in httpOnly cookies

**Status:** accepted (2026-11-10)

## Context
The API needs login for four roles (clinician, researcher, data_steward, admin) across
several centers. Each user belongs to exactly one center (tenant). The frontend is a Next.js
app on its own origin. Every request must know the user's tenant before it touches the
database, because Row-Level Security (ADR 0004) depends on it. There is no Redis (stack
rule), and the API will run as several replicas on Azure Container Apps.

## Options compared

| | **JWT access + refresh in httpOnly cookies** (chosen) | JWT in `localStorage` + `Authorization` header | Server sessions (session id cookie + session table) |
|---|---|---|---|
| Readable by injected JavaScript (XSS) | No | **Yes**: a stolen token works from anywhere | No |
| CSRF exposure | Yes, closed by SameSite=Strict + a custom header | None | Yes, same defence |
| DB lookup per request | None (signature check only) | None | One per request |
| Logout / revoke | Refresh tokens revoked in DB; access tokens live ≤ 15 min | Same | Immediate |
| Works across replicas | Yes (stateless) | Yes | Yes (shared table) |

## Decision
- **Access token**: HS256 JWT, 15 minutes, claims `sub` (user id), `tenant`, `role`, `jti`,
  `typ`, `iat`, `exp`. Decoding pins the algorithm (so `alg: none` is refused) and requires
  every claim.
- **Refresh token**: JWT, 7 days, also stored by id in `app.refresh_token` (RLS). Each refresh
  rotates it. If a token that was already rotated is used again, every session of that user
  is revoked, because that pattern means the token was stolen.
- **Reuse grace period** (added 2026-11-11, `REFRESH_REUSE_GRACE_SECONDS`, default 30):
  - *Why:* a page reload or a second tab can send the old refresh token just after a
    rotation, before the browser has stored the new cookie. Without a grace period that
    honest race logged the user out everywhere.
  - *Within the window:* if the old token was rotated (not revoked by logout) less than 30
    seconds ago, and its replacement has not itself been rotated, revoked or expired, the
    API returns that same replacement refresh token (same id and expiry) with a new access
    token. This is audited as `refresh_grace`.
  - *Outside the window:* any other reuse still triggers theft detection. That includes
    reuse after 30 seconds, reuse after the replacement was used, and reuse of a logged-out
    token.
  - *Locking:* the old token's row is locked (`FOR UPDATE`), so two simultaneous refreshes
    of the same token also get the same replacement.
- **Cookies**: `txlens_access` (Path=/) and `txlens_refresh` (Path=/auth), both `HttpOnly`,
  `Secure` and `SameSite=Strict`. Tokens never appear in response bodies.
- **CSRF**: besides SameSite=Strict, every POST must carry `X-Requested-With`. A cross-site
  form cannot add that header, and a cross-site `fetch` that adds it needs a CORS preflight,
  which only the frontend's origin passes.
- **Passwords**: Argon2id (`argon2-cffi` defaults). An unknown user name still runs one
  Argon2 verify, so response time does not reveal which user names exist. Wrong user name and
  wrong password give the same 401 message.
- **Pre-login lookup**: before login there is no tenant, so RLS hides every user.
  `app.find_login(username)` is a `SECURITY DEFINER` function owned by `txlens_auth`, a
  NOLOGIN role whose only right is a SELECT policy on the login columns. EXECUTE is revoked
  from PUBLIC and granted only to `txlens_app`. The users table itself stays under RLS.
- **Rate limiting**: `app.login_attempt` in Postgres allows 5 failures per user name (counted
  since that user's last success) and 20 per client address in 15 minutes, then returns 429
  with Retry-After. It works across replicas without Redis. Rows older than 30 days are
  pruned on each login and by `make prune-login-attempts`.
- **Tenant**: comes only from the verified token. No endpoint accepts a tenant id.

## Consequences
- A role change or deactivation takes effect at the next refresh (≤ 15 minutes). Immediate
  revocation would need a per-request check or a deny-list.
- `JWT_SECRET` must be at least 32 random characters; the app refuses to start otherwise.
  Rotating it logs everyone out.
- The login-attempt table has no tenant (a failed login may name no user). It holds user
  names and client addresses, never patient data.
- If the frontend and API move to different sites, SameSite=Strict has to be revisited.
- The grace period gives a thief who holds the old token 30 seconds to obtain the current
  refresh token, but only while that replacement is unused. As soon as either side uses it
  again, reuse is detected as before. Setting the window to 0 restores strict rotation.
