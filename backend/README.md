# WorldFork Backend

This directory contains the canonical WorldFork runtime: FastAPI, Celery, Postgres models, Redis-backed queues, LangGraph tick execution, report generation, and artifact storage.

The root `pyproject.toml` packages the backend service. The CLI is intentionally packaged separately in `../cli`, and the generic agent skill lives in `../skills/worldfork`.

## Local Setup

Use the root Docker Compose stack:

```bash
cp .env.example .env
make build
make up
make migrate
make seed
```

Install the CLI before operating the backend:

```bash
python3.11 -m pip install -e ./cli
worldfork status
```

The CLI selects the backend from `WORLD_FORK_API_BASE`, `BACKEND_API_BASE`, or `--base-url`. Agent-facing instructions should pass root-relative paths through `worldfork query` instead of hardcoding a host URL.

## Runtime Surface

Canonical API families:

| Route              | Purpose                                            |
| ------------------ | -------------------------------------------------- |
| `/readyz`          | Readiness checks                                   |
| `/api/agent/*`     | Agent discovery, compact run/job/log surfaces      |
| `/api/big-bangs`   | Big Bang creation, lifecycle, final reports        |
| `/api/multiverses` | Timeline execution, lineage, continuation, reports |
| `/api/ticks`       | Tick snapshots and runtime artifacts               |
| `/api/jobs`        | Queue control plane                                |
| `/api/logs`        | Audit, request, error, and webhook logs            |
| `/api/reports`     | Report versions and rendered artifacts             |

Compatibility routes remain mounted where older API contracts need them, but new code should target the canonical surface above.

## Runtime State

Postgres is the source of truth for Big Bangs, multiverses, ticks, jobs, reports, LLM calls, operation logs, and lineage. Artifacts are cached files for JSON payloads, Markdown renders, PDF renders, and audit evidence.

Report generation writes structured content to `report_versions`. Markdown and PDF files are regenerated from that structured content on demand.

## Validation

From the repo root:

```bash
./scripts/run_tests.sh all
make lint
worldfork smoke live
```

`worldfork smoke live` uses real API credits and should use the default OpenRouter smart/fast split: `moonshotai/kimi-k2.6` for initializer, God-review, endpoint-ledger, and report work, and `deepseek/deepseek-v4-flash` for cohort, hero, action, and event-summary work.

## More Documentation

- `docs/setup.md`
- `docs/architecture.md`
- `docs/reporting.md`
- `backend/tests/COVERAGE.md`
