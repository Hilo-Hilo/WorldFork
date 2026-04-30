<div align="center">

# WorldFork

**Agent-operated social simulation infrastructure for branching timelines.**

![WorldFork](docs/images/readme.png)

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
| Artifact store | Cached Markdown/PDF renders and audit payloads |
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
- Structured, versioned report records for multiverse and final Big Bang outcomes.
- Markdown and PDF report artifacts rendered on demand from structured report content.
- OpenRouter model routing, defaulting to `google/gemini-3.1-flash-lite-preview`.

## Quickstart

### 1. Install prerequisites

You need:

- Docker Desktop or compatible Docker Compose
- Python 3.11+
- an OpenRouter API key

### 2. Install the CLI

Install the CLI from this monorepo:

```bash
python3.11 -m pip install -e ./cli
worldfork --help
```

### Optional: install the agent skill

The generic WorldFork operator skill teaches agent runtimes to use the
WorldFork CLI, initialization, watch, report, job, and Atlas onboarding flows
without hardcoded backend URLs.

Install it directly from the repository skill folder:

```bash
npx --yes skills add Hilo-Hilo/WorldFork/skills/worldfork --all
```

The full GitHub URL works too:

```bash
npx --yes skills add https://github.com/Hilo-Hilo/WorldFork/tree/main/skills/worldfork --all
```

For local development from this checkout, install from a temporary copy of the
skill package. This avoids the `skills` installer replacing `./skills/worldfork`
with an installed-agent symlink when `--all` targets this repository:

```bash
tmpdir="$(mktemp -d)"
cp -R ./skills/worldfork "$tmpdir/worldfork"
npx --yes skills add "$tmpdir/worldfork" --all
```

The installer creates local agent configuration output such as `.agents/`,
agent-specific skill symlinks, and `skills-lock.json`. Treat those as local
runtime setup files unless you intentionally want to version agent install
state.

To refresh an installed skill, run `npx skills update worldfork -y` or rerun
one of the install commands above. Restart Codex or your agent runtime after
installing or updating the skill.

### 3. Configure environment

```bash
cp .env.example .env
```

Set `OPENROUTER_API_KEY` in `.env`. The default model settings already point at:

```text
google/gemini-3.1-flash-lite-preview
```

### 4. Start the stack

```bash
make build
make up
make migrate
make seed
```

### 5. Verify readiness

```bash
worldfork status
worldfork query GET /readyz --no-api-prefix
```

Expected readiness checks:

```json
{"ok":true,"checks":{"database":true,"redis":true,"openrouter":true,"zep":true}}
```

## Service Endpoints

The CLI uses `WORLD_FORK_API_BASE`, `BACKEND_API_BASE`, or `--base-url` to
choose the backend. Agent instructions should pass paths through the CLI rather
than hardcoding a host URL.

| Service | Path |
| --- | --- |
| Readiness | `/readyz` |
| OpenAPI docs | `/docs` |
| Agent discovery | `/api/agent/discover` |
| Postgres | Docker Compose service `db` |
| Redis | Docker Compose service `redis` |

## CLI

Use global options before the command.

```bash
worldfork agent discover
worldfork status
worldfork init --name "Atlas onboarding" --scenario-file examples/test-big-bang.md
worldfork watch big-bang <big-bang-id>
worldfork watch multiverse <multiverse-id>
worldfork runs list
worldfork runs workspace <big-bang-id>
worldfork jobs list --status failed
worldfork logs list --status failed
worldfork models defaults
worldfork settings show
worldfork settings patch --data '{"default_tick_duration_minutes":90}'
worldfork reports list <big-bang-id>
worldfork reports versions <report-id>
worldfork reports view <report-version-id>
worldfork reports render <report-version-id> --format pdf
worldfork smoke live
worldfork demo atlas
```

For agent workflows, keep responses small by default:

```bash
worldfork --verbosity summary runs list
worldfork --fields id,status,created_at jobs list
worldfork --json status
```

`worldfork init` blocks until the backend initialization request has returned.
The command then fetches and returns the initialized workspace, initializer
state, actors, traits, graph baseline, sociology baseline, and emotion
baseline. Use `--wait-timeout` for long live initializer calls.

`worldfork watch big-bang <id>` and `worldfork watch multiverse <id>` stream
near-real-time activity from the API by polling workspace, tick, tool-call, and
agent log surfaces. Use `--json-lines` for machine-readable event streams,
`--once` for a single snapshot, or `--no-stop` to keep watching after a terminal
state.

Direct API escape hatch:

```bash
worldfork query GET /api/agent/discover
worldfork query GET /readyz --no-api-prefix
```

## Testing

The maintained test sweep is:

```bash
./scripts/run_tests.sh all
```

That runs:

1. root regression tests + unit tests
2. CLI package tests
3. integration tests
4. e2e tests

Useful focused commands:

```bash
./scripts/run_tests.sh unit
./scripts/run_tests.sh cli
./scripts/run_tests.sh integration
./scripts/run_tests.sh e2e
make lint
docker compose config --quiet
```

Live full-runtime smoke, using real OpenRouter credits:

```bash
worldfork smoke live
```

The smoke harness validates:

- Gemini 3.1 Flash Lite model configuration and audited LLM calls
- settings PATCH + GET + restoration
- Big Bang pause/resume behavior
- root and branch tick execution
- runtime checkpoints and node attempts
- manual branch intervention plus operation log
- job pause and synchronous run
- structured report versions and on-demand Markdown/PDF renders
- log endpoints and final readiness

## Atlas Onboarding Demo

The canonical long-form onboarding demo is:

```text
examples/test-big-bang.md
```

Run the full Atlas multiverse demonstration against the local backend:

```bash
worldfork demo atlas
```

Atlas is not the smoke test and is not intended to be artificially tiny. It is
the onboarding run that demonstrates what WorldFork can do: it creates the
Atlas Resilience Crisis Big Bang, runs a root timeline, creates a manual
transparency branch, allows God-agent-spawned branches under generous safety
caps, drains every discovered timeline to terminal state, generates structured
per-multiverse reports, generates the final report-agent summary across all
terminal multiverses, renders the final PDF artifact on demand, and audits that
all LLM calls used `google/gemini-3.1-flash-lite-preview`.

The defaults are intentionally much larger than smoke-test defaults:

```bash
worldfork demo atlas \
  --tick-duration-minutes 720 \
  --horizon-days 30 \
  --max-active-multiverses 64 \
  --max-branch-depth 8 \
  --max-branches-per-tick 8 \
  --completion-max-requests 1000
```

Atlas defaults to 720 minutes per tick, or 12 simulated hours. Unless
`--max-tick-index` is supplied explicitly, the command derives it from the
target horizon: `ceil(horizon_days * 1440 / tick_duration_minutes)`. With the
default 30-day horizon and 12-hour ticks, Atlas runs to tick index 60.

At completion the command prints the Big Bang ID, terminal multiverse count,
final report version ID, and ready-to-run `worldfork reports view`,
`worldfork reports render`, and `worldfork watch` commands.

## Reports And Artifacts

Reports are database records first. A `report` is the logical slot, such as
"M1 multiverse report" or "final Big Bang report." Each generated revision is a
`report_version`, and that version stores parsable JSON content, source
metadata, the report-agent model, source multiverse IDs, source multiverse
version, source config version, and latest tick binding.

Markdown and PDF files are artifacts. They are cached render outputs generated
from `report_versions.content`; deleting or regenerating a render does not
change the canonical report version. Use `worldfork reports view` for the
current Markdown render and `worldfork reports render --format pdf` when a PDF
artifact is needed.

To continue a completed multiverse after it reaches `max_ticks`, create a new
continuation with a larger `max_ticks`, then run or queue ticks for that same
multiverse. The continuation increments `multiverse.version`, links back to the
source report version when available, and stores a per-multiverse runtime
override so sibling timelines keep their original tick limits.

## Project Layout

```text
backend/app/           FastAPI app, runtime, jobs, simulation, storage
backend/tests/         root regressions, unit, integration, e2e
cli/                   standalone Python CLI package
skills/worldfork/      generic installable agent skill package
examples/              runnable sample Big Bang dossiers
source_of_truth/       prompt, report, and policy templates
scripts/               local test and smoke-test harnesses
infra/                 Docker and Alembic infrastructure
docs/                  operator and agent documentation
prd.md                 product requirements source
```

## Development Loop

```bash
make up
./scripts/run_tests.sh all
make lint
worldfork smoke live
```

When a local test run touches Redis queues, clear them before a live smoke:

```bash
docker compose exec -T redis redis-cli FLUSHALL
```

## Documentation

- [Agent interface](docs/agent.md)
- [Backend notes](backend/README.md)
- [Test coverage](backend/tests/COVERAGE.md)
- [Reporting guide](docs/reporting.md)
- [Product requirements](prd.md)

## Status

WorldFork is a backend + workers + CLI project. The current runtime is built,
tested, Dockerized, and live-smoke verified against OpenRouter with Gemini 3.1
Flash Lite.
