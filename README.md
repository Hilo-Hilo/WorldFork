<div align="center">

# WorldFork

**Branching social simulation infrastructure for agents, operators, and auditable multiverse runs.**

![WorldFork](docs/images/readme.png)

[![Python](https://img.shields.io/badge/python-3.11%2B-3776AB)](https://www.python.org/) [![FastAPI](https://img.shields.io/badge/api-FastAPI-009688)](https://fastapi.tiangolo.com/) [![LangGraph](https://img.shields.io/badge/runtime-LangGraph-1F2937)](https://langchain-ai.github.io/langgraph/) [![OpenRouter](https://img.shields.io/badge/LLM-OpenRouter-7C3AED)](https://openrouter.ai/) [![Docs](https://img.shields.io/badge/docs-Read%20the%20Docs-0A7B83)](https://worldfork.readthedocs.io/en/latest/)

WorldFork turns one scenario into many inspectable timelines. Each run keeps the ticks, branches, agent reviews, manual interventions, logs, and final reports tied back to durable state.

</div>

---

## Why WorldFork

Most simulations answer "what happens next?" once. WorldFork keeps asking that question across forks.

Start with a **Big Bang** scenario, run it through checkpointed ticks, let the God agent and operators create meaningful branches, then compare the terminal multiverses through structured reports. The result is a backend-first control plane for exploring how social systems diverge under pressure.

| You need to | WorldFork gives you |
| --- | --- |
| Explore alternative futures | Branching multiverses with lineage and inherited ticks |
| Audit what happened | Persisted runtime checkpoints, LLM calls, jobs, logs, and artifacts |
| Let agents operate safely | A compact `worldfork` CLI and `/api/agent/*` discovery surface |
| Compare outcomes | Versioned multiverse and final Big Bang reports |
| Keep live runs bounded | Queue controls, interruption, continuation, and runtime limits |

## What Is In This Repo

WorldFork is a monorepo with three installable surfaces:

| Surface | Location | Purpose |
| --- | --- | --- |
| Backend service | `backend/app` | FastAPI API, simulation runtime, jobs, storage, reports |
| CLI package | `cli/` | `worldfork` command for operators and agents |
| Agent skill | `skills/worldfork/` | Generic skill that teaches agents how to use WorldFork |

The runtime stack is Docker Compose, FastAPI, Celery, Postgres, Redis, LangGraph, and OpenRouter. There is no web frontend in this repository yet.

## Setup

WorldFork has two supported setup paths. The recommended path is agent-guided: paste one prompt into your agent and let it install the skills, guide setup, verify the stack, and run onboarding. Use the complete manual path when you want to run every command yourself.

### Agent-Guided Setup (Recommended)

Paste this prompt into your agent:

```text
Install and run the WorldFork setup skill:

npx skills add Hilo-Hilo/WorldFork/skills/worldfork-setup --all
```

`worldfork-setup` is a temporary bootstrap skill — once onboarding is done, remove it and install the persistent operator skill:

```bash
npx skills remove worldfork-setup -y
npx skills add Hilo-Hilo/WorldFork/skills/worldfork --all
```

### Complete Manual Setup

Prerequisites:

- Docker Desktop or another Docker Compose runtime (Compose v2)
- Python 3.11 or newer
- One of `uv`, `pipx`, or a Python venv (modern Debian/Ubuntu blocks plain `pip install` via PEP 668)
- Node.js 20 or newer for `npx skills`
- An OpenRouter API key
- `make` is optional. The Make targets below all wrap `docker compose ...`; if `make` is unavailable, run the equivalent `docker compose` commands directly (shown as fallbacks).

Configure the environment:

```bash
cp .env.example .env
```

Set `OPENROUTER_API_KEY` in `.env`. Keep the default cheap onboarding model:

```text
google/gemini-3.1-flash-lite-preview
```

The `NEXT_PUBLIC_*` and `OPENROUTER_HTTP_REFERER` entries in `.env.example` are scaffolding for a future frontend and can be left at their defaults.

Install the CLI. Recommended (works on PEP 668 systems out of the box):

```bash
uv tool install ./cli                      # or: pipx install ./cli
worldfork --help
```

Plain `pip` still works inside a virtualenv:

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e ./cli
```

Start the backend:

```bash
make build && make up && make migrate && make seed
```

Without `make`, the equivalents are:

```bash
docker compose build
docker compose up -d
# alembic head + bootstrap legacy tables (what `make migrate` runs):
docker compose exec -T -w /app/backend api alembic -c alembic.ini upgrade head
docker compose exec -T api python -c "import backend.app.models; from app.db.session import engine; from backend.app.models.base import Base as LegacyBase; names={'big_bang_runs','run_results','settings_branch_policy','settings_global','settings_model_routing','settings_provider','settings_rate_limit','settings_zep'}; LegacyBase.metadata.create_all(bind=engine, tables=[LegacyBase.metadata.tables[n] for n in names if n in LegacyBase.metadata.tables])"
docker compose exec -T api python -m backend.app.scripts.seed
```

Verify readiness:

```bash
worldfork status
worldfork query GET /readyz --no-api-prefix
```

A healthy stack returns:

```json
{ "ok": true, "checks": { "database": true, "redis": true, "openrouter": true, "zep": true } }
```

If `openrouter` reports `false`, your key is missing or invalid. If `database` or `redis` flip false, check `docker compose ps` — every service except `api`/`worker_*`/`beat` should be `healthy`.

Use `worldfork --base-url http://host:port ...` when targeting a non-default deployment.

Create and initialize a first Big Bang:

```bash
worldfork init \
  --name "Atlas onboarding" \
  --scenario-file examples/test-big-bang.md \
  --max-ticks 4 \
  --tick-duration-minutes 720
```

Watch the run and inspect outcomes:

```bash
worldfork watch big-bang <big-bang-id>
worldfork reports list <big-bang-id>
worldfork reports view <report-version-id>
worldfork reports render <report-version-id> --format pdf
```

Run the full onboarding demo when you want the larger branch-and-report showcase:

```bash
worldfork demo atlas
```

> **Cost note.** `worldfork demo atlas` defaults to a 30-day horizon at 12-hour ticks (~60 ticks per timeline), up to 64 active multiverses, and branch depth 8. Even on `gemini-3.1-flash-lite-preview` that is many thousands of LLM calls. For a tight first run, scope it down:
>
> ```bash
> worldfork demo atlas \
>   --horizon-days 4 \
>   --max-active-multiverses 6 \
>   --max-branch-depth 2 \
>   --max-branches-per-tick 2
> ```

Tear down when you are done:

```bash
make down              # or: docker compose down
make clean-data        # also removes volumes and ./runs/*
```

## Core Commands

| Command | Use it for |
| --- | --- |
| `worldfork status` | Backend and queue health |
| `worldfork agent discover` | Agent-facing API contract and recommended flow |
| `worldfork init ...` | Create a Big Bang and wait for initialized state |
| `worldfork watch big-bang <id>` | Stream run activity until completion |
| `worldfork watch multiverse <id>` | Stream one timeline's ticks and logs |
| `worldfork runs list` | Find recent Big Bangs |
| `worldfork jobs list --status failed` | Inspect queue failures |
| `worldfork settings show` | Read mutable runtime settings |
| `worldfork reports view <id>` | View a report version as Markdown |
| `worldfork smoke live` | Run the full live runtime smoke test |
| `worldfork demo atlas` | Run the full Atlas onboarding demo |

Use global flags before the command:

```bash
worldfork --verbosity summary runs list
worldfork --fields id,status,created_at jobs list
worldfork --json status
```

## Architecture At A Glance

```text
Scenario dossier
      |
      v
Big Bang
      |
      v
Multiverse tree
      |
      v
Tick runtime graph
      |
      +-- actors and cohorts
      +-- events and sociology
      +-- graph pressure
      +-- God-agent review
      +-- tool-call checkpoints
      |
      v
Reports, artifacts, logs, and lineage
```

Reports are database records first. Markdown and PDF files are render artifacts compiled from structured `report_versions.content`, so they can be regenerated without changing the canonical report version.

## Documentation

| Guide | What it covers |
| --- | --- |
| [Setup](docs/setup.md) | Local environment, Docker stack, CLI, skill install |
| [CLI](docs/cli.md) | Command reference and agent-safe usage patterns |
| [Architecture](docs/architecture.md) | Runtime model, storage layers, jobs, reports |
| [Demos](docs/demos.md) | Atlas onboarding, live smoke, expected outputs |
| [Reporting](docs/reporting.md) | Report versions, artifacts, continuation semantics |
| [Agent Interface](docs/agent.md) | Rules and examples for AI agents operating WorldFork |
| [Testing](docs/testing.md) | Maintained validation commands and live smoke scope |
| [Backend Notes](backend/README.md) | Backend package and API details |
| [Contributing](CONTRIBUTING.md) | Branch policy, PR workflow, review expectations |
| [Agents Guide](AGENTS.md) | Conventions for AI agents committing to this repo |

## Project Layout

```text
backend/app/           FastAPI app, runtime, jobs, storage, reports
backend/tests/         unit, integration, e2e, and regression tests
cli/                   standalone Python CLI package
skills/worldfork/      installable generic agent skill
examples/              runnable scenario dossiers
source_of_truth/       prompt, report, and policy templates
scripts/               local validation and demo harnesses
docs/                  Sphinx documentation
prd.md                 product requirements source
```

## Status

WorldFork is backend-first and CLI-first. The current system is Dockerized, tested across unit/integration/e2e layers, and live-smoke validated against OpenRouter using `google/gemini-3.1-flash-lite-preview`.
