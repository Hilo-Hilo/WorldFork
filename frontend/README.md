# WorldFork frontend

Next.js 15 app implementing the WorldFork UI from the Claude Design handoff bundle.

## What's here

- `app/report/` — Page 3: report viewer (final Big Bang report + per-multiverse versions, comparison metrics, sparklines, actions)
- `app/page.tsx` — placeholder index that links to the report viewer
- `components/ReportPage.tsx` — the read column + nav + aside, ported from the design's `report.jsx`
- `app/globals.css` — design tokens + report-viewer styles, ported verbatim from `styles.css`, `dashboard.css`, and `report.css` in the handoff bundle

## What's intentionally not here yet

- No backend wiring — every value on the page is mock data baked into `ReportPage.tsx`. When the API is connected, swap the `VERSIONS`, `TOC`, `COMPARE_ROWS`, and inline report content for `useQuery` hooks against `GET /api/big-bangs/<id>/reports` and friends.
- No input page (page 1) and no run dashboard (page 2). The design handoff includes those; only the report viewer was requested first.
- No tests yet.

## Running

```bash
cd frontend
npm install
npm run dev
```

The dev server runs on `:3003` (matches the backend's CORS configuration in `.env.example`). Visit `/report` for the report viewer.

## Design source

Pulled from `https://api.anthropic.com/v1/design/h/xL3KwvfafVb69hZHcvUXBw`. The handoff bundle contained HTML/CSS/JS prototypes; this implementation matches the visual output but uses Next.js + TypeScript instead of in-browser Babel.
