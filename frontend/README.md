# TxLens frontend

Next.js (App Router) + TypeScript + Tailwind + shadcn/ui, TanStack Query and Table, Plotly.
The browser talks only to this app; `/api/*` is rewritten to FastAPI (`BACKEND_URL`,
default `http://localhost:8000`). See the main README and ADR 0011/0012.

```bash
pnpm install
pnpm dev          # http://localhost:3000 (run `make api` too)
pnpm lint
pnpm typecheck
pnpm test         # Vitest
pnpm e2e          # Playwright: seeds txlens_e2e, starts API :8001 + app :3100 (needs `make up`)
pnpm gen:api      # regenerate src/lib/api/schema.d.ts from openapi.json (`make openapi`)
pnpm screenshots  # docs/images/app-*.png from a running stack (BASE_URL, default :3000)
```

Layout: `src/app` (routes; `(app)` = signed-in pages), `src/components` (`ui/` is shadcn),
`src/lib` (API client, auth, roles, queries, pure chart/cohort helpers), `e2e/` (Playwright).
