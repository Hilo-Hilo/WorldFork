<div align="center">

# WorldFork

**Agent-operated social simulation infrastructure for branching timelines.**

[![Python](https://img.shields.io/badge/python-3.11%2B-3776AB)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/api-FastAPI-009688)](https://fastapi.tiangolo.com/)
[![LangGraph](https://img.shields.io/badge/runtime-LangGraph-1F2937)](https://langchain-ai.github.io/langgraph/)
[![OpenRouter](https://img.shields.io/badge/LLM-OpenRouter-7C3AED)](https://openrouter.ai/)
[![Tests](https://img.shields.io/badge/tests-unit%20%7C%20integration%20%7C%20e2e-2563EB)](#testing)
[![Docs](https://img.shields.io/badge/docs-Read%20the%20Docs-0A7B83)](https://worldfork.readthedocs.io/en/latest/)

WorldFork runs recursive "what happens next?" simulations where cohorts,
heroes, events, memory, graph pressure, God-agent review, manual intervention,
and report generation all stay inspectable through a backend + CLI control
plane.

</div>

---

## Why WorldFork Exists

Most simulations flatten into one timeline. WorldFork keeps the fork.

It models a scenario as a Big Bang, executes ticks through a checkpointed
runtime graph, allows controlled branching at decision points, and keeps enough
metadata around for agents and humans to audit what happened later.

The current product shape is intentionally backend-first:

| Layer | Role |
| --- | --- |
| FastAPI backend | canonical API, agent discovery, runtime control |
| Celery workers | queue-backed execution for long-running jobs |
| Postgres + Redis | durable state, jobs, queues, rate limits |
| Artifact store | Markdown/PDF reports and structured payloads |
| `worldfork` CLI | primary interface for agents and operators |

There is no web frontend in this repo.

## Current Runtime

The canonical runtime is the LangGraph-backed tick engine under `app.*`.

```text
Big Bang
  |
  v
Multiverse M1
  |
  v
Tick runtime graph
  |
  +-- actor decisions
  +-- event generation
  +-- sociology update
  +-- graph update
  +-- God review
  +-- dynamic tool-call checkpoints
  +-- tick summary
  |
  v
final tick snapshot + reports + audit logs
```

Key properties:

- Checkpointed tick execution with persisted nodes, attempts, and checkpoints.
- Resume-safe tool calls after interruption.
- Manual intervention support through auditable branch creation.
- Queue control for pause, resume, interrupt, requeue, and synchronous debug run.
- Markdown and PDF report artifacts for multiverse and final Big Bang reports.
- OpenRouter model routing, defaulting to `google/gemini-3.1-flash-lite-preview`.

## Quickstart

### 1. Install prerequisites

You need:

- Docker Desktop or compatible Docker Compose
- `uv`
- an OpenRouter API key

### 2. Configure environment

```bash
cp .env.example .env
```

Set `OPENROUTER_API_KEY` in `.env`. The default model settings already point at:

```text
google/gemini-3.1-flash-lite-preview
```

### 3. Start the stack

```bash
make build
make up
make migrate
make seed
```

### 4. Verify readiness

```bash
curl http://127.0.0.1:8003/readyz
uv run worldfork status
```

Expected readiness checks:

```json
{"ok":true,"checks":{"database":true,"redis":true,"openrouter":true,"zep":true}}
```

## Service URLs

| Service | URL |
| --- | --- |
| API | http://127.0.0.1:8003 |
| OpenAPI docs | http://127.0.0.1:8003/docs |
| Agent discovery | http://127.0.0.1:8003/api/agent/discover |
| Postgres | localhost:5433 |
| Redis | localhost:6379 |

## CLI

Use global options before the command.

```bash
uv run worldfork agent discover
uv run worldfork status
uv run worldfork runs list
uv run worldfork runs workspace <big-bang-id>
uv run worldfork jobs list --status failed
uv run worldfork logs list --status failed
```

For agent workflows, keep responses small by default:

```bash
uv run worldfork --verbosity summary runs list
uv run worldfork --fields id,status,created_at jobs list
uv run worldfork --json status
```

Direct API escape hatch:

```bash
uv run worldfork query GET /api/agent/discover
uv run worldfork query GET /readyz --no-api-prefix
```

## Testing

The maintained test sweep is:

```bash
./scripts/run_tests.sh all
```

That runs:

1. root regression tests + unit tests
2. integration tests
3. e2e tests

Useful focused commands:

```bash
./scripts/run_tests.sh unit
./scripts/run_tests.sh integration
./scripts/run_tests.sh e2e
uv run ruff check .
docker compose config --quiet
```

Live full-runtime smoke, using real OpenRouter credits:

```bash
uv run python -m scripts.full_runtime_smoke
```

The smoke harness validates:

- Gemini 3.1 Flash Lite model configuration and audited LLM calls
- settings PATCH + GET + restoration
- Big Bang pause/resume behavior
- root and branch tick execution
- runtime checkpoints and node attempts
- manual branch intervention plus operation log
- job pause and synchronous run
- Markdown/PDF report artifacts
- log endpoints and final readiness

## Sample Big Bang

The canonical long-form branching demo is:

```text
examples/test-big-bang.md
```

Run a cheap live demonstration against the local backend:

```bash
uv run python -m scripts.run_test_big_bang
```

The sample creates the Atlas Resilience Crisis Big Bang, runs a root tick,
creates a manual transparency branch, runs a child branch tick, verifies runtime
checkpoints and lineage, generates a final report, and audits that all LLM calls
used `google/gemini-3.1-flash-lite-preview`.

## Project Layout

```text
backend/app/          FastAPI app, runtime, jobs, simulation, storage
backend/tests/        root regressions, unit, integration, e2e
cli/src/worldfork_cli agent/operator CLI
examples/             runnable sample Big Bang dossiers
source_of_truth/      prompt, report, and policy templates
scripts/              local test and smoke-test harnesses
infra/                Docker and Alembic infrastructure
docs/                 operator and agent documentation
prd-do-not-delete/    protected product requirements source
```

## Development Loop

```bash
make up
./scripts/run_tests.sh all
uv run ruff check .
uv run python -m scripts.full_runtime_smoke
```

When a local test run touches Redis queues, clear them before a live smoke:

```bash
docker compose exec -T redis redis-cli FLUSHALL
```

## Documentation

- [Agent interface](docs/agent.md)
- [Backend notes](backend/README.md)
- [Test coverage](backend/tests/COVERAGE.md)
- [Protected PRD](prd-do-not-delete/prd.md)

## Status

WorldFork is a backend + workers + CLI project. The current runtime is built,
tested, Dockerized, and live-smoke verified against OpenRouter with Gemini 3.1
Flash Lite.
