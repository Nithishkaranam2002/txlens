# ADR 0011: Same-origin frontend: Next.js rewrites, cookie sessions, trusted proxy

**Status:** accepted (2026-11-11)

## Context
The API keeps its tokens in httpOnly, Secure, SameSite=Strict cookies (ADR 0007). The Next.js
frontend must call it from the browser with those cookies. It must also refresh an expired
access token without bothering the user, and it must not weaken the API's CSRF and login
rate-limit defences.

## Options compared

| | **Next.js rewrites `/api/*` → FastAPI** (chosen) | Browser calls the API origin (CORS) | Next.js route handlers as a BFF | `proxy.ts` (middleware) auth guard |
|---|---|---|---|---|
| Cookies are first-party | Yes, one origin | No, cross-site; SameSite=Strict blocks them | Yes | n/a |
| CORS preflights | None | On every POST | None | n/a |
| Code to maintain | One config entry | CORS setup on both sides | One handler per endpoint | Per-route checks |
| Typed client still matches the API | Yes, same paths under `/api` | Yes | Only if handlers mirror the API | n/a |
| Can tell "expired but refreshable" from "logged out" | In the browser (client guard) | Same | Yes | No: the refresh cookie only goes to `/api/auth` |

## Decision
- `next.config.ts` rewrites `/api/:path*` to `${BACKEND_URL}/:path*`. `BACKEND_URL` is
  server-only (default `http://localhost:8000`) and is read when the app is built.
- **Refresh cookie path.** The browser sees the refresh endpoint at `/api/auth/refresh`, so
  the API now sets the refresh cookie on `REFRESH_COOKIE_PATH` (default `/api/auth`). Tests
  and scripts that call the API directly set `/auth`.
- **Auth guard.** It runs in the browser, not in `proxy.ts`. A 401 triggers one shared
  `POST /api/auth/refresh` and one retry. If that still gives 401, the browser goes to
  `/login?next=<path>` with a full page load, which drops every cached response.
- **CSRF.** The fetch wrapper adds `X-Requested-With` to every non-GET request, so the API's
  CSRF check stays as it is.
- **Trusted proxy.** The API trusts `X-Forwarded-For` only from `TRUSTED_PROXY_IPS` (default
  `127.0.0.1`, the local Next.js server). Settings refuse `*`. Uvicorn runs with
  `--no-proxy-headers`, so the app's own middleware is the only place that reads the header.
  - A test checks that a client calling the API directly with a spoofed `X-Forwarded-For`
    is still rate-limited under its real address.
  - A second test checks that the configured proxy's header is used.

## Consequences
- **Finding: Next.js does not append the client address.** Its rewrite proxy sets
  `X-Forwarded-For` only when the request has none (Next 16.4,
  `base-server.js`: `req.headers['x-forwarded-for'] ??= socket.remoteAddress`). A browser
  that sends its own `X-Forwarded-For` to Next has that value passed on unchanged.
  - The API takes the rightmost address that is not a trusted proxy. So the result is only
    as trustworthy as whatever sits in front of Next.
  - **Deployed** behind an ingress that appends the real client address (Azure Container
    Apps does), the API rate-limits the real client.
  - **Locally**, with no ingress, a client on the network could pick its own per-address
    bucket. The per-user-name limit still applies.
  - Fix for any deployment without such an ingress: put a reverse proxy in front of Next that
    overwrites `X-Forwarded-For`.
- `BACKEND_URL` is part of the build. A frontend image built for one API address must be
  rebuilt for another (or the rewrite must read the variable at start, if Next supports it
  later).
- Refresh-token rotation and navigation can race. If a page is reloaded while a refresh is
  in flight, the browser can drop the rotated cookie and replay the old token. A 30-second
  reuse grace period (ADR 0007) now returns the same replacement in that case, instead of
  revoking every session.
- Next 16 keeps visited routes mounted but hidden (`<Activity>`). Tests and landmarks must
  use the visible `main`, and there is now only one element with `id="main"`.
