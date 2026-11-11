# ADR 0012: A typed API client generated from the OpenAPI schema

**Status:** accepted (2026-11-11)

## Context
The frontend calls about 20 FastAPI endpoints. Their shapes differ by role (an identified or
de-identified patient), and the backend will keep changing. Hand-written TypeScript types
drift silently. We want a broken contract to fail the build, not to show up in a browser.

## Options compared

| | **openapi-typescript + openapi-fetch** (chosen) | orval / hey-api (generated functions + hooks) | Hand-written types and fetch |
|---|---|---|---|
| Generated code | One `.d.ts` file of types (no runtime) | Many files of functions and hooks | None |
| Runtime size | ~6 kB wrapper | Larger; each call is generated code | Smallest |
| Fits our own TanStack Query hooks | Yes, we write the hooks | Generates its own hooks | Yes |
| Catches API drift | At type-check time | At type-check time | No |
| Custom refresh/CSRF logic | One `fetch` wrapper | Per-generator mutator config | Yes |

## Decision
- `txlens-export-openapi` prints the schema without a database. `make openapi` writes
  `frontend/openapi.json` and runs `pnpm gen:api` to regenerate `src/lib/api/schema.d.ts`.
- The export uses the schema before the Swagger tweak (ADR 0007). In Swagger the CSRF header
  is shown as required so "Try it out" sends it. In the generated types it stays optional,
  because the client's fetch wrapper adds it to every POST.
- CI regenerates both files and fails on any difference, so the schema, the generated types
  and the code that uses them cannot drift apart.
- Query hooks live in `src/lib/queries.ts` and call `unwrap(...)`. `unwrap` turns an error
  response into an `ApiError` carrying the status, the message and the `X-Request-ID`.

## Consequences
- openapi-fetch turns tuple types in responses into plain arrays. One helper accepts both
  (`describeRules` in `lib/cohort-form.ts`).
- Union responses (an identified or a de-identified patient) need a small type guard
  (`isIdentified`).
- Changing an API model means running `make openapi` and committing two generated files.
