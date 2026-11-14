# Security

TxLens holds only synthetic data. It is still built the way a system holding real patient data
should be: this page says what it protects, how, where in the code, and what is still open.
The Step 4.1 review added the findings table at the end.

## What we protect, and from whom

| Asset | Threat |
|---|---|
| Patient identifiers (names, MRNs, birth dates, real dates) | A researcher, data steward or admin seeing them; another center seeing them; a leak through logs, caches or LLM traces |
| One center's data | Another center's users (multi-tenancy) |
| Clinical decisions | An LLM inventing a result; an AI output without a source |
| Paid LLM budget, server CPU | A logged-in user (or stolen session) calling expensive endpoints in a loop |
| Sessions | Cross-site request forgery, token theft, password guessing |
| The copilot's SQL access | Prompt injection turning a question into a write, a cross-tenant read or a data dump |

Out of scope: physical access to the host, a compromised developer machine, and denial of
service beyond per-user limits (that belongs to the platform in front of the app).

## Controls and where they live

### Identity and sessions
- **Passwords:** Argon2id. An unknown user name is checked against a dummy hash, so both
  answer in the same time (`backend/src/txlens_backend/security.py`).
- **Tokens:** JWT (HS256 only, required claims checked) in httpOnly, Secure, SameSite=Strict
  cookies. Access tokens last 15 minutes. Refresh tokens are stored, rotated on use, and
  reusing an old one revokes every session of that user (`api/auth.py`).
- **Login guessing:** 5 failures per user name or 20 per client address in 15 minutes give
  429 with `Retry-After`. Counts live in Postgres (`app.login_attempt`).
- **Secrets** come only from the environment / `.env` (git-ignored). The app refuses to start
  with a secret shorter than 32 characters or still set to the `.env.example` placeholder
  (`config.require_secret`). Settings hold secrets as `SecretStr`, so they never print.
  `make demo` checks that they are set and never echoes them.

### Authorization and tenancy
- **Roles** are checked on every route (`api/deps.require_roles`). The permission matrix test
  (`backend/tests/test_permissions.py`) asserts the exact status code for every role on every
  route, and fails if a route is missing from the matrix.
- **Tenancy** comes only from the signed token. Every query runs as a non-superuser login
  inside `tenant_transaction`, so Postgres Row-Level Security applies. The API refuses to
  start under a role that can bypass RLS (`db.check_role_is_rls_bound`).
- **De-identification:** researchers and stewards get pseudonyms and shifted dates, never
  names, MRNs or birth dates. A scan test walks every endpoint as those roles and looks for
  any identifier (`backend/tests/test_deid_scan.py`).
- **Audit:** every write and every patient-level read leaves a row in `app.audit_log` in the
  same transaction. The app role may only insert. The permission matrix checks that each
  audited endpoint really wrote a row for its request id.

### Browser
- **CSRF:** SameSite=Strict cookies, plus a required `X-Requested-With` header on every POST.
  A cross-site form cannot add it, and a cross-site fetch with it needs a CORS preflight that
  only the frontend origin passes.
- **Same origin:** the browser only talks to the Next.js origin; `/api/*` is proxied to
  FastAPI (ADR 0011). CORS allows that one origin, GET and POST, and two headers.
- **Page headers** (`frontend/src/proxy.ts`, Next.js 16's Proxy): Content-Security-Policy
  (`default-src 'self'`, `frame-ancestors 'none'`, `object-src 'none'`, `base-uri 'self'`,
  `form-action 'self'`), `X-Frame-Options: DENY`, `nosniff`, a strict referrer policy,
  `Permissions-Policy` and `Cross-Origin-Opener-Policy`. **HSTS-ready:** with `TXLENS_HSTS=1`
  (only behind HTTPS) it adds `Strict-Transport-Security` and `upgrade-insecure-requests`.
  A Playwright test reads the real response headers and fails on any CSP violation the
  browser reports on the main pages (`frontend/e2e/security-headers.spec.ts`).
- **API headers** (`api/app.py`): `Cache-Control: no-store` (responses may hold PHI),
  `nosniff`, `X-Frame-Options: DENY`, no referrer, and `default-src 'none'` CSP (JSON needs
  no scripts). The Swagger page at `/docs` keeps the browser default CSP.

### Input
- Every request body is a Pydantic model: lengths, ranges, list sizes and enums are bounded
  (for example questions ≤ 2,000 characters, cohort rules ≤ 10 lab rules, survival covariates
  from a fixed list). Unknown fields are refused where a body drives queries.
- Paging is bounded (`limit` 1–200).

### Rate limits
- Per user, counted in Postgres (`app.rate_limit`, one atomic upsert per request, so every API
  process shares the count without Redis): **60 copilot questions per hour** (paid LLM calls)
  and **30 analyses per minute** (survival analyses and reports, on-demand risk scoring).
  Over the limit: 429 with `Retry-After` (`api/rate_limit.py`, configurable in Settings).

### The copilot (LLM + SQL)
- **Screen first:** questions asking for identifiers, other centers, writes, clinical advice or
  instruction overrides are refused by fixed rules before any model call
  (`copilot/screen.py`).
- **Untrusted text stays data:** tool results, abstracts and history are JSON-encoded inside
  `<data>` blocks that cannot be closed from inside; the system prompt says to ignore
  instructions there. The planner can only return one of a fixed set of typed actions.
- **SQL guard** (`copilot/sql_guard.py`, sqlglot): exactly one SELECT; an allow-list of syntax
  nodes and functions (so `pg_sleep`, `pg_read_file`, `set_config` and every other function
  not on the list are refused); only the copilot views and two vocabulary tables; aggregate
  output only, counted by distinct patient; LIMIT ≤ 200.
- **SQL execution** (`copilot/sql_exec.py`): a read-only transaction that is always rolled
  back, inside the caller's tenant (RLS), as `txlens_copilot`, a role that can read only those
  views, with a 5-second statement timeout. Small counts are suppressed, and a differencing
  guard stops two overlapping questions from revealing a small group.
- **Answers:** only PMIDs that retrieval returned can be cited, uncited literature claims are
  dropped, and numbers not found in tool results are flagged.

### LLM extraction
- The LLM never decides: it extracts with quotes, Python validates, and low confidence goes to
  human review. Text is minimized (identifiers replaced) before any model or trace sees it.
- A hard spending cap per batch is reserved atomically in Postgres before each call.

### Containers
- The API/worker image and the frontend image run as non-root users (uid 10001, `node`), with
  read-only root filesystems and a tmpfs for `/tmp` in compose. The runtime images hold no
  compiler and no package manager. Base images are pinned by digest.
- Every compose port is published on `127.0.0.1` only.

## Findings of the Step 4.1 review

Severity is for this app if it held real data. Status: **fixed** in Step 4.1 or 4.2, **accepted** with
a reason, or **future** (worth doing before real use, not needed for a demo).

| # | Severity | Finding | Status |
|---|---|---|---|
| 1 | High | No security headers on the frontend (no CSP, framing allowed) or the API | **Fixed**: page headers in `proxy.ts`, API headers middleware, Playwright header test |
| 2 | Medium | No limit on expensive endpoints: copilot (paid LLM), survival analysis/reports, risk scoring | **Fixed**: Postgres-backed per-user rate limits, 429 + `Retry-After` |
| 3 | Medium | `GET /review/items` (patients with items under review) wrote no audit row | **Fixed**, plus a test that every audited endpoint writes a row |
| 4 | Medium | Spark and R images ran as root; the API and frontend had no images | **Fixed**: new non-root images for API, worker and frontend; Spark/R images now run as a non-root user |
| 5 | Medium | No dependency audit | **Fixed**: CI job `dependency-audit` (pip-audit + pnpm audit) |
| 6 | Medium | Test databases could be left behind by a hung run, and test order hid shared-state bugs | **Fixed**: random test order in CI (pytest-randomly) and a 300 s per-test timeout |
| 7 | Low | Compose published Postgres, Temporal, Langfuse and MLflow on every interface | **Fixed**: `127.0.0.1` only |
| 8 | Low | Review decisions accepted any number of changed fields and unbounded values; survival cohort ids were unbounded | **Fixed**: ≤ 50 fields, values ≤ 2,000 characters, ids ≤ 64 characters |
| 9 | Low | CSP allows inline scripts (`'unsafe-inline'`) | **Accepted**: Next.js streams its page data as inline scripts (tested: without `'unsafe-inline'` every page fails the CSP-violation test). A per-request nonce would remove this, but nonces are incompatible with Partial Prerendering, which the app uses. Risk is reduced by React's escaping, no `dangerouslySetInnerHTML`, httpOnly cookies (a script cannot read the session) and `connect-src 'self'` (nowhere to send data) |
| 10 | Low | `/health` is public and says whether the database is reachable | **Accepted**: container health checks need it; it reveals nothing else |
| 11 | Low | filelock 3.x (CVE-2026-112907 … 112940: lock-file symlink and permission issues) | **Accepted**: every litellm release caps `filelock<4`. We never use filelock directly; the bugs need a local attacker who can create files in the lock directory, which in our containers is a single-user tmpfs. Revisit when litellm allows 4.2+ |
| 12 | Low | diskcache (PYSEC-2026-2447, pickle) and ragas (PYSEC-2026-3046) | **Accepted**: no fixed release; used only by the offline evaluation scripts (ragas), never by the app; the diskcache issue needs write access to its cache directory |
| 13 | Low | minimatch (moderate, ReDoS) via ESLint, deepmerge (low) via the shadcn CLI | **Accepted**: build-time tools only, with our own inputs; the minimatch fix is a new major that typescript-eslint does not accept yet, the deepmerge fix is not released |
| 14 | Info | Login rate limiting counts by client address, which behind a proxy is the forwarded address | **Accepted**: only listed proxies (`TRUSTED_PROXY_IPS`, never `*`) may set it |
| 15 | Info | No multi-factor authentication, no account lockout notification, no password rotation | **Future**: use the organization's identity provider (OIDC) before real use |
| 16 | Info | The demo ran over plain HTTP on localhost | **Fixed for the cloud demo** (Step 4.2, ADR 0021): Caddy terminates TLS with a Let's Encrypt certificate and redirects HTTP to HTTPS; `TXLENS_HSTS=1`, Secure cookies and `X-Robots-Tag: noindex`; `make cloud-smoke` checks each one. The local demo stays on HTTP, on 127.0.0.1 only |
| 17 | Info | The public demo has two guest accounts whose password is shared with visitors | **Accepted**: center A only, normal role permissions and rate limits, every action audited; the accounts expire (`expires_at`, refresh refused too), the password is made on the server and never stored in the repo, and `make cloud-guest-rotate` changes it and ends their sessions. Each guest login sees only its own copilot conversations, and `make cloud-reset` deletes guest conversations and saved cohorts |
| 18 | Info | A public copilot could let strangers spend money on LLM calls | **Fixed for the cloud demo**: no API key on the server (forced empty in every container, checked by the smoke test); answers come only from the LLM cache, and other questions get a fixed "live AI is disabled" message |

## Reporting a problem

This is a portfolio project with synthetic data. If you find a security problem, open a
GitHub issue without exploit details, or contact the owner through the profile linked in the
README, and it will be looked at.
