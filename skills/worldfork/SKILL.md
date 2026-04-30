---
name: worldfork
description: Use when operating, validating, onboarding, or debugging the WorldFork branching simulation backend through its CLI/API, including initialization, watch streams, Atlas demos, reports, jobs, settings, and runtime health.
---

# WorldFork

WorldFork is a backend + worker + CLI product for branching social simulations.
The primary operator interface is the `worldfork` CLI, backed by the FastAPI
agent and runtime APIs.

## Start Here

Run discovery before making assumptions about the live API surface:

```bash
worldfork agent discover
worldfork status
```

Do not hardcode backend URLs. Use `WORLD_FORK_API_BASE`, `BACKEND_API_BASE`, or
the CLI `--base-url` flag when a non-default backend target is required.

If the user asks to set up WorldFork and the `worldfork` command is missing,
guide the operator through CLI installation from the repo root:

```bash
python3.11 -m pip install -e ./cli
worldfork --help
```

Then continue onboarding through environment setup, Docker Compose startup,
migrations, seeding, readiness checks, and a first Big Bang. Do not bypass the
CLI with Python module entrypoints for normal operation.

## Setup Onboarding

For a fresh local setup, guide the user through:

```bash
cp .env.example .env
python3.11 -m pip install -e ./cli
make build
make up
make migrate
make seed
worldfork status
worldfork query GET /readyz --no-api-prefix
```

Ask the operator to put `OPENROUTER_API_KEY` in `.env`. Live onboarding and
validation runs must use `google/gemini-3.1-flash-lite-preview`.

## Core Commands

```bash
worldfork init --name "Atlas onboarding" --scenario-file examples/test-big-bang.md
worldfork watch big-bang <big-bang-id>
worldfork watch multiverse <multiverse-id>
worldfork reports list <big-bang-id>
worldfork reports versions <report-id>
worldfork reports view <report-version-id>
worldfork reports render <report-version-id> --format pdf
worldfork jobs list --status failed
worldfork logs list --status failed
worldfork models defaults
worldfork settings show
```

`worldfork init` waits for backend initialization to complete and returns the
initialized workspace, initializer state, actors, traits, graph baseline,
sociology baseline, and emotion baseline. Use `--wait-timeout` for live
initializer calls.

`worldfork watch` streams workspace, tick, tool-call, and agent log state until
the selected Big Bang or multiverse reaches a terminal state. Use `--json-lines`
for machine-readable streams, `--once` for a snapshot, or `--no-stop` when a
long-lived watcher should continue after terminal state.

## Atlas Demo (Only run for onboarding, per user confirmation)

Atlas is the full onboarding simulation:

```bash
worldfork demo atlas
```

It creates the Atlas Resilience Crisis Big Bang, runs root and branch timelines,
permits God-agent-created branches under generous caps, generates structured
per-multiverse reports, generates the final cross-multiverse report, renders a
PDF artifact on demand, and audits that live LLM calls use
`google/gemini-3.1-flash-lite-preview`.

The default Atlas tick duration is 720 simulated minutes. If `--max-tick-index`
is omitted, Atlas derives it from `--horizon-days` and `--tick-duration-minutes`.

## Reports

Treat reports as database records first. A report is a logical slot, and each
generated revision is a `report_version` containing parsable JSON content,
source metadata, model metadata, source multiverse IDs, source config version,
and latest tick bindings.

Markdown and PDF files are artifacts: cached renders compiled from
`report_versions.content`. Regenerating or deleting a render must not mutate the
canonical report version.

## Validation

Use the maintained sweep before declaring the repo healthy:

```bash
./scripts/run_tests.sh all
make lint
docker compose config --quiet
```

For a real runtime smoke test with API credits, use only Gemini 3.1 Flash Lite:

```bash
worldfork smoke live
```
