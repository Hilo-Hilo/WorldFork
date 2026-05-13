# WorldFork frontend

Next.js 15 app — three pages from the Claude Design handoff, **wired to the live backend** with polling-based "live" updates.

## Pages

| Route | Backend wiring |
|---|---|
| `/` | Polls `GET /api/agent/runs` every 3s, lists existing Big Bangs, click → dashboard |
| `/input` | `POST /api/big-bangs` on submit, redirects to `/dashboard?run=<id>` on success. Real file upload reads markdown content. The form keeps scenario context separate from the required report question and optional resolution/supporting questions. Live `GET /readyz` indicator in the topbar. |
| `/dashboard?run=<id>` | Polls `GET /api/big-bangs/<id>`, `GET /api/big-bangs/<id>/multiverses`, `GET /api/agent/logs` every 2–2.5s. Builds the multiverse tree from `parent_multiverse_id` + `fork_tick_index`. Pause/Resume/Run-to-completion buttons hit the corresponding POST endpoints. |
| `/report?run=<id>` | Fetches report status, reports + versions, renders markdown via `react-markdown`, and shows single-report/predicate/final-report progress while generation is running. Export .pdf / .md hit `POST /api/report-versions/<id>/render` and trigger a browser download. |

## Live updates

No WebSockets — the backend has Redis pub/sub publishers but no WebSocket route handler at the API edge. Polling intervals are tuned for "feels live" without burning bandwidth:

- Run dashboard: 2 s (status + multiverses + logs)
- Runs list / readyz: 3 s
- Report markdown: never (only fetched on selection change)

To swap to WebSockets later, replace the `refetchInterval` queries with WebSocket subscribers — the React Query keys are already structured around (`bigBang`, `multiverses`, `reports`) so the cache layer doesn't change.

## Cost guardrails

The input form defaults to:

- `use_initializer_agent: false` — zero LLM calls during init
- `max_ticks: 4`
- `max_active_multiverses: 6`, `max_branch_depth: 2`, `max_branches_per_tick: 2`

A "use tiny demo (~1 KB)" button loads a built-in scenario well under the 18 000-char chunker budget. A warning banner appears if you flip the initializer on with a scenario over the budget.

The same demo button also fills a sample primary question, resolution criteria,
and supporting questions. Manual submissions require a primary question so the
final report has an explicit forecast contract to answer.

## Architecture

- **Server-side fetch** uses `API_BASE_URL` (defaults `http://127.0.0.1:8003`) — talks to the backend directly.
- **Client-side fetch** goes through a Next.js rewrite at `/backend/*` → backend, sidestepping CORS and WSL/Windows host quirks. See `next.config.mjs`.
- **TanStack Query** is the data layer. Defaults: `refetchInterval: 2 s`, `staleTime: 1 s`, `retry: 1`.
- **No Tailwind, no UI library.** The design's oklch palette and hairline-border CSS lives in `app/globals.css`.

## Run locally

```bash
cd frontend
npm install
npm run dev      # http://localhost:3003
```

Backend must be reachable at `http://127.0.0.1:8003` (override via `API_BASE_URL=...`).

## E2E tests

Smoke suite in `tests/e2e/smoke.spec.ts` covers home, input form, dashboard with a real run, report viewer with a real markdown render. **Zero LLM calls** — only reads existing data.

```bash
npm run test:e2e:install   # one-time: download chromium
npm run test:e2e
```

Set `WORLDFORK_FE_URL` if your frontend isn't on `:3003` (the WSL→Windows interop case wants the Windows IP).

## Files

```
frontend/
  app/
    layout.tsx              fonts + global css + QueryClientProvider
    page.tsx                runs list (live polled)
    globals.css             design tokens + all three page stylesheets
    input/page.tsx          thin route wrapper
    dashboard/page.tsx      thin route wrapper, reads ?run=
    report/page.tsx         thin route wrapper, reads ?run=
  components/
    Providers.tsx           TanStack Query provider
    InputPage.tsx           wired to POST /api/big-bangs
    DashboardPage.tsx       wired to multiverses + tree layout + log strip
    ReportPage.tsx          wired to report list + markdown + render endpoint
  lib/
    api.ts                  typed fetch wrappers, server vs client base URL
    types.ts                response shapes
  tests/e2e/
    smoke.spec.ts           Playwright walk-through (zero-LLM)
  next.config.mjs           /backend/* rewrite → backend
  playwright.config.ts
```
