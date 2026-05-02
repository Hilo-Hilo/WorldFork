---
name: worldfork
description: Use when operating, validating, onboarding, configuring LLM providers/model routing, or debugging the WorldFork branching simulation backend through its CLI/API, including initialization, watch streams, Atlas demos, reports, jobs, settings, and runtime health.
---

# WorldFork

WorldFork is a backend + worker + CLI product for branching social simulations. Its core framing is a Monte Carlo tree search of the real world. The primary operator interface is the `worldfork` CLI, backed by the FastAPI agent and runtime APIs.

## Start Here

Run discovery before making assumptions about the live API surface:

```bash
worldfork agent discover
worldfork status
```

Use this project model when explaining WorldFork to a user: a Big Bang is the
root scenario, each multiverse is one timeline, ticks are checkpointed runtime
steps, branches create alternate timelines from decision points, endpoint
ledgers track terminal outcomes and path mass, and reports are structured
database versions that can be rendered on request.

Do not hardcode backend URLs. Use `WORLD_FORK_API_BASE`, `BACKEND_API_BASE`, or the CLI `--base-url` flag when a non-default backend target is required.

If the user asks to set up WorldFork and the `worldfork` command is missing, guide the operator through CLI installation from the repo root:

```bash
python3.11 -m pip install -e ./cli
worldfork --help
```

Then continue onboarding through environment setup, Docker Compose startup, migrations, seeding, readiness checks, and a first Big Bang. Do not bypass the CLI with Python module entrypoints for normal operation.

For normal repo updates, use the CLI updater instead of ad hoc destructive Git commands:

```bash
worldfork update --dry-run
worldfork update --yes
```

The updater fast-forwards code only. It refuses dirty tracked files, diverged branches, and remote edits to protected local config/data paths such as `.env`, Docker override files, `runs/`, and `artifacts/`.

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

Ask the operator to put `OPENROUTER_API_KEY` in `.env` and configure OpenAI Codex OAuth with `worldfork settings openai-codex-login` or `codex login --device-auth`. Live onboarding and validation runs should use the default split unless the user explicitly authorizes another route policy: `openrouter/deepseek/deepseek-v4-flash` for cohort, hero, action, and event-summary work and `openai-codex/gpt-5.4` for initialization, God review, endpoint-ledger evaluation, and reports.

## LLM Providers And Routing

Inspect the effective provider, route catalog, persisted rows, and rate limits before changing models:

```bash
worldfork settings llm
worldfork settings providers
worldfork settings model-routing
worldfork settings rate-limits
```

WorldFork routes audited LLM calls by stable route names. Configure routes through `worldfork settings model-routing`; do not bypass this layer in LangGraph/domain code. The important audited routes are:

- `initializer_chunk_extractor`
- `initializer_agent`
- `god_agent`
- `cohort_agent`
- `hero_agent`
- `event_summary`
- `report_agent`
- `endpoint_ledger`

For onboarding and live smoke tests, keep the default split unless the user explicitly authorizes another model: `cohort_agent`, `hero_agent`, action execution, and `event_summary` on `openrouter/deepseek/deepseek-v4-flash`; `initializer_chunk_extractor`, `initializer_agent`, `god_agent`, `report_agent`, and `endpoint_ledger` on `openai-codex/gpt-5.4`.

To use OpenAI Codex OAuth, run the headless login flow and then enable/configure the provider. This does not require the Codex CLI to be installed:

```bash
worldfork settings openai-codex-login
worldfork settings providers --data '{
  "providers": [
    {
      "provider": "openai-codex",
      "base_url": "https://chatgpt.com/backend-api/codex",
      "api_key_env": "OPENAI_CODEX_OAUTH_TOKEN",
      "default_model": "gpt-5.4",
      "fallback_model": null,
      "json_mode_required": true,
      "tool_calling_enabled": false,
      "enabled": true,
      "extra_headers": {},
      "payload": {"api": "openai-codex-responses", "auth_mode": "oauth"}
    }
  ]
}'
```

Patch route rows with the provider/model mix the user wants:

```bash
worldfork settings model-routing --data '{
  "entries": [
    {
      "job_type": "initializer_agent",
      "preferred_provider": "openai-codex",
      "preferred_model": "gpt-5.4",
      "fallback_provider": "openai-codex",
      "fallback_model": "gpt-5.4",
      "temperature": 0.3,
      "top_p": 1.0,
      "max_tokens": 8192,
      "max_concurrency": 2,
      "requests_per_minute": 20,
      "tokens_per_minute": 200000,
      "timeout_seconds": 300,
      "retry_policy": "exponential_backoff",
      "payload": {}
    },
    {
      "job_type": "cohort_agent",
      "preferred_provider": "openrouter",
      "preferred_model": "deepseek/deepseek-v4-flash",
      "temperature": 0.8,
      "top_p": 1.0,
      "max_tokens": 4096,
      "max_concurrency": 16,
      "requests_per_minute": 120,
      "tokens_per_minute": 400000,
      "timeout_seconds": 90,
      "retry_policy": "exponential_backoff",
      "payload": {}
    }
  ]
}'
```

Re-run `worldfork settings llm` after any change and verify `effective_model_routing`. When validating a run, inspect LLM audit logs with provider/model fields:

```bash
worldfork --verbosity normal --fields id,source,status,message,provider,model,big_bang_id logs list --source llm
```

For Kimi or other OpenAI-compatible providers, add a `settings providers` row with a new provider name, `api_key_env`, base URL, and `payload.api` set to `openai-compatible`, then route individual jobs to it. For Claude or other non-OpenAI-compatible APIs, wait for or implement a provider adapter and keep the rest of the system pointed at the audited LLM routing layer.

## Core Commands

```bash
worldfork init --name "Atlas onboarding" --scenario-file examples/test-big-bang.md
worldfork watch big-bang <big-bang-id>
worldfork watch multiverse <multiverse-id>
worldfork reports list <big-bang-id>
worldfork reports versions <report-id>
worldfork reports view <report-version-id>
worldfork reports render <report-version-id> --format pdf --output report.pdf
worldfork jobs list --status failed
worldfork logs list --status failed
worldfork models defaults
worldfork settings show
worldfork settings llm
worldfork update --dry-run
```

`worldfork init` waits for backend initialization to complete and returns the initialized workspace, initializer state, actors, traits, graph baseline, sociology baseline, and emotion baseline. Use `--wait-timeout` for live initializer calls.

`worldfork watch` streams workspace, tick, tool-call, and agent log state until the selected Big Bang or multiverse reaches a terminal state. Use `--json-lines` for machine-readable streams, `--once` for a snapshot, or `--no-stop` when a long-lived watcher should continue after terminal state.

## Atlas Demo (Only run for onboarding, per user confirmation)

Atlas is the full onboarding simulation:

```bash
worldfork demo atlas
```

It creates the Atlas Resilience Crisis Big Bang, runs root and branch timelines, permits God-agent-created branches under generous caps, generates structured per-multiverse reports, generates the final cross-multiverse report, can render a PDF on demand, and audits that live LLM calls use the configured approved route policy.

The default Atlas tick duration is 720 simulated minutes. If `--max-tick-index` is omitted, Atlas derives it from `--horizon-days` and `--tick-duration-minutes`.

## Reports

Treat reports as database records first. A report is a logical slot, and each generated revision is a `report_version` containing parsable JSON content, source metadata, model metadata, source multiverse IDs, source config version, and latest tick bindings.

Markdown and PDF outputs are ephemeral renders compiled from `report_versions.content` only when requested. Rendering or deleting local output must not mutate the canonical report version.

## Validation

Use the maintained sweep before declaring the repo healthy:

```bash
./scripts/run_tests.sh all
make lint
docker compose config --quiet
```

For a real runtime smoke test with API credits, use the configured default
split unless the user explicitly authorizes another policy:

```bash
worldfork smoke live
```

The default split is OpenRouter `deepseek/deepseek-v4-flash` for cohort, hero,
action, and event-summary routes, and OpenAI Codex `gpt-5.4` for initializer,
God review, endpoint-ledger, and report routes.
