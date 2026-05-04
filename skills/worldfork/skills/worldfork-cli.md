# WorldFork CLI Module

Use this module for canonical command patterns and API-safe operation.

## Global Flags

Global flags go before the command:

```bash
worldfork --json status
worldfork --verbosity summary runs list
worldfork --fields id,status,created_at jobs list
worldfork --base-url http://127.0.0.1:8003 status
```

## Command Map

```bash
worldfork status
worldfork agent discover
worldfork setup
worldfork init --name "<name>" --scenario-file <scenario.md>
worldfork watch big-bang <big-bang-id>
worldfork watch multiverse <multiverse-id>
worldfork runs list
worldfork runs workspace <big-bang-id>
worldfork runs cost <big-bang-id> --include-calls
worldfork runs estimate <big-bang-id>
worldfork ticks timing <tick-snapshot-id>
worldfork ticks cost <tick-snapshot-id> --include-calls
worldfork jobs list
worldfork jobs wait <job-id> --timeout 300 --poll-interval 2
worldfork logs list --status failed
worldfork ledgers list <big-bang-id>
worldfork ledgers path-mass <big-bang-id>
worldfork reports list <big-bang-id>
worldfork reports pack <big-bang-id> --mode summary
worldfork reports view <report-version-id>
worldfork reports render <report-version-id> --format pdf --output report.pdf
worldfork smoke live
worldfork demo atlas
```

## Direct API Escape Hatch

Use `query` only when no first-class command exists:

```bash
worldfork query GET /api/agent/discover
worldfork query GET /readyz --no-api-prefix
```

Keep API paths canonical: `/api/big-bangs`, `/api/multiverses`, `/api/ticks`, `/api/jobs`, `/api/logs`, `/api/reports`, and `/api/agent/*`.
