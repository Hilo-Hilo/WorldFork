# WorldFork Backend

This directory contains the canonical WorldFork runtime:

- FastAPI app mounted from `backend.app.main:app`
- `app.*` package imports via `PYTHONPATH=/app/backend`
- LangGraph-backed tick runtime
- SQLAlchemy models for Big Bangs, multiverses, ticks, jobs, reports, and logs
- Celery queue integration
- report/artifact storage

The root `pyproject.toml` is the backend service package. The installable CLI
is intentionally packaged separately in `../cli`, and the generic agent skill
is packaged in `../skills/worldfork`.

## Local Backend Setup

The recommended path is the root Docker Compose stack:

```bash
cp .env.example .env
make build
make up
make migrate
make seed
```

The CLI selects the API base from `WORLD_FORK_API_BASE`, `BACKEND_API_BASE`, or
the `--base-url` flag. Agent-facing instructions should use root-relative paths
through `worldfork query` instead of hardcoding a host URL.

Install the local CLI before using operator commands:

```bash
python3.11 -m pip install -e ./cli
```

For direct local commands:

```bash
make lint
./scripts/run_tests.sh unit
./scripts/run_tests.sh cli
worldfork smoke live
```

## Runtime Surface

Canonical:

- `/readyz`
- `/api/agent/*`
- `/api/big-bangs`
- `/api/multiverses`
- `/api/ticks`
- `/api/jobs`
- `/api/logs`

Compatibility routes remain mounted where needed for older CLI/API contracts,
but they are transitional and new code should target the canonical routes above.

Mounted compatibility routes include `/api/runs`, `/api/universes`,
`/api/multiverse`, selected legacy `/api/jobs` shapes, and selected legacy
settings/logs integrations.

## Reports

Report generation writes canonical structured content to `report_versions`.
Markdown and PDF files are cached artifacts rendered from that structured
content on demand. `reportlab` is a runtime dependency for PDF rendering and is
installed through the root project metadata.

Useful report endpoints:

```bash
GET  /api/big-bangs/{big_bang_id}/reports
GET  /api/reports/{report_id}/versions
GET  /api/report-versions/{report_version_id}
GET  /api/report-versions/{report_version_id}/markdown
POST /api/report-versions/{report_version_id}/render
```

## Testing Notes

The maintained suite is driven from the repo root:

```bash
./scripts/run_tests.sh all
```

Use `backend/tests/COVERAGE.md` for the active test inventory. Disabled
pre-revamp suites have been removed rather than ignored.
