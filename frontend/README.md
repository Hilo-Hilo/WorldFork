# WorldFork frontend

Next.js 15 app implementing all three pages from the Claude Design handoff bundle.

## Pages

| Route | What it is | Source in handoff |
|---|---|---|
| `/` | Index — links the three surfaces | (new) |
| `/input` | Configure a scenario run. Drop-zone for `.md`, initializer prompt, simulation config, branch policy accordion. | `Input Page.html`, `app.jsx`, `components.jsx`, `states.jsx` |
| `/input?state=loading` | Initializer agent running with stage list + live log | same |
| `/input?state=error&error=parse` | Parse failure | same |
| `/input?state=error&error=llm` | OpenRouter 503 | same |
| `/dashboard` | Run dashboard with multiverse tree (SVG), multiverse rail, tick progress, recent events, log strip | `Run Dashboard.html`, `dashboard.jsx`, `tree.jsx` |
| `/dashboard?orientation=vertical` | Tree pivots to vertical | same |
| `/dashboard?scores=off` | Hide tick + score chips on tree nodes | same |
| `/report` | Final Big Bang report viewer (versions nav, TOC, read column with sparklines + comparison table, right rail with metrics + actions) | `Report Viewer.html`, `report.jsx` |

## What's intentionally not here yet

- **No backend wiring.** Every value on every page is mock data. When the API is connected, swap component-local state for `useQuery` against the relevant endpoints (`POST /api/big-bangs`, `GET /api/big-bangs/<id>/multiverses`, `GET /api/ticks/<id>/runtime`, `GET /api/big-bangs/<id>/reports`).
- **No live updates.** The plan from earlier conversation is to add 1s polling via TanStack Query when wiring goes in. WebSocket bridge is a separate backend PR.
- **No tests.**

## Stack

- Next.js 15.1 + React 18 + TypeScript
- No Tailwind, no UI library — the design ships its own oklch palette + hairline-border CSS
- Inter Tight (UI) + JetBrains Mono (numerics, IDs, log lines), pulled from Google Fonts in `app/layout.tsx`
- All design tokens + page styles live in a single `app/globals.css` (concatenated from `styles.css` + `dashboard.css` + `report.css`)

## Files

```
frontend/
  app/
    layout.tsx            root layout, fonts, global css
    page.tsx              index linking the three pages
    globals.css           design tokens + all three page stylesheets, ported verbatim
    input/page.tsx        thin route wrapper (reads ?state= and ?error=)
    dashboard/page.tsx    thin route wrapper (reads ?orientation= and ?scores=)
    report/page.tsx       thin route wrapper
  components/
    InputPage.tsx         page 1 component (Topbar, DropZone, NumberField, accordion, LoadingState, ErrorState)
    DashboardPage.tsx     page 2 component (top bar, multiverse rail, MultiverseTree SVG, detail panel, log strip)
    ReportPage.tsx        page 3 component (versions nav, TOC, read column with Spark, compare metrics)
```

## Running

```bash
cd frontend
npm install
npm run dev
```

Dev server runs on `:3003` (matches the backend's CORS configuration).

## Design source

Pulled from `https://api.anthropic.com/v1/design/h/xL3KwvfafVb69hZHcvUXBw`. The handoff bundle was HTML/CSS/JSX prototypes; this implementation matches the visual output but uses Next.js + TypeScript instead of in-browser Babel.

The chat transcript (`worldfork-ui/chats/chat1.md` in the bundle) records the design intent: minimal + tech-focused, dark-mode default, oklch palette, single restrained teal accent, Inter Tight + JetBrains Mono, hairline borders. None of that drifted in the port.
