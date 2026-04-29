# WorldFork Backend

This directory contains the canonical WorldFork runtime:

- FastAPI app mounted from `backend.app.main:app`
- `app.*` package imports via `PYTHONPATH=/app/backend`
- LangGraph-backed tick runtime
- SQLAlchemy models for Big Bangs, multiverses, ticks, jobs, reports, and logs
- Celery queue integration
- report/artifact storage

The root `pyproject.toml` is authoritative. There is no separate backend
package file.

## Local Backend Setup

The recommended path is the root Docker Compose stack:

```bash
cp .env.example .env
make build
make up
make migrate
make seed
```

The API listens on `http://127.0.0.1:8003`.

For direct local commands:

```bash
uv run ruff check backend/app backend/tests
uv run pytest -c pyproject.toml backend/tests/*.py backend/tests/unit -q
uv run python -m scripts.full_runtime_smoke
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

Report generation writes both Markdown and PDF artifacts. `reportlab` is a
runtime dependency and is installed through the root project metadata.

## Testing Notes

The maintained suite is driven from the repo root:

```bash
./scripts/run_tests.sh all
```

Use `backend/tests/COVERAGE.md` for the active test inventory. Disabled
pre-revamp suites have been removed rather than ignored.
