---
name: worldfork-setup
description: Use when helping a user install, preflight, configure LLM providers/model routing, verify, troubleshoot, and understand WorldFork for the first time; remove this temporary setup skill after onboarding is complete.
---

# WorldFork Setup

This is a temporary bootstrap skill for getting WorldFork installed, verified, and explained to a new operator. After setup and onboarding are complete, tell the user this skill can be removed. Keep the regular `worldfork` operator skill installed if the user will keep operating the project.

## Setup Rules

- Do not hardcode backend URLs. Use `WORLD_FORK_API_BASE`, `BACKEND_API_BASE`, or the `worldfork --base-url` option when the user targets a non-default API.
- Prefer the documented CLI and Make targets. Do not bypass the CLI with Python module entrypoints unless the user explicitly asks for low-level debugging.
- Before using real API credits for onboarding/smoke validation, confirm the effective routes use the default split unless the user explicitly authorizes another model mix: `openrouter/deepseek/deepseek-v4-flash` for cohort, hero, action, and event-summary work and `openai-codex/gpt-5.4` for initialization, God review, endpoint-ledger evaluation, and reports.
- If the repository is already checked out, use it. Do not clone another copy unless the current checkout is missing or unusable.
- If the user is new to the project or asks what WorldFork is, read `references/project-orientation.md` and explain the project before running demos.
- If setup fails, a command resolves strangely, Docker does not become ready, or provider auth is unclear, read `references/setup-troubleshooting.md` before guessing.

## Preflight

Before spending user time or API credits, establish the current local shape:

```bash
pwd
git status --short --branch
python3.11 --version
node --version
uv --version
docker compose version
which -a worldfork || true
lsof -nP -iTCP:8003 -sTCP:LISTEN || true
lsof -nP -iTCP:5433 -sTCP:LISTEN || true
lsof -nP -iTCP:6379 -sTCP:LISTEN || true
```

Report only actionable caveats to the user: missing tools, dirty source changes,
stale global `worldfork` shims, Docker not running, or host port conflicts.

## Install WorldFork

Use these steps for a normal local setup:

```bash
git clone https://github.com/Hilo-Hilo/WorldFork.git
cd WorldFork
python3.11 -m pip install -e ./cli
worldfork --help
cp .env.example .env
```

If `worldfork` still resolves to a stale global shim after installation, reinstall
the editable CLI and inspect `which -a worldfork`. While repairing a shim, use
this source-checkout fallback only to keep setup moving:

```bash
cd cli
uv run --extra dev worldfork --help
cd ..
```

Ask the user for their OpenRouter API key if it is not already configured, then set `OPENROUTER_API_KEY` in `.env`. Configure OpenAI Codex OAuth after the CLI is installed:

```bash
worldfork settings openai-codex-login
```

This writes the default auth file under `~/.worldfork/`. If the backend must use
a different token source, set `OPENAI_CODEX_OAUTH_TOKEN` or
`OPENAI_CODEX_AUTH_FILE` in `.env`. Tell the user to keep the default model
split for onboarding and validation unless they explicitly want to change
providers or route different agents to different models.

Start and prepare the stack:

```bash
make build
make up
make migrate
make seed
```

If this is a reused local checkout and stale queue/cache state might affect the first run, ask before clearing local Redis. Only run this against disposable local Docker Compose Redis:

```bash
docker compose exec -T redis redis-cli FLUSHALL
```

Verify readiness:

```bash
worldfork status
worldfork query GET /readyz --no-api-prefix
worldfork agent discover
worldfork settings llm
```

OpenRouter should show configured when `OPENROUTER_API_KEY` is present. OpenAI
Codex should show configured when OAuth is present through the default auth file,
`OPENAI_CODEX_OAUTH_TOKEN`, or `OPENAI_CODEX_AUTH_FILE`.

## Configure LLM Providers And Routes

Use the settings API/CLI layer for all provider and model changes. Do not edit LangGraph/domain code to point at a provider directly.

Start by inspecting the effective config:

```bash
worldfork settings llm
worldfork settings providers
worldfork settings model-routing
```

Default first-run policy:

- Onboarding/smoke runs: keep `cohort_agent`, `hero_agent`, action execution, and `event_summary` on `openrouter/deepseek/deepseek-v4-flash`, and keep initialization, God review, endpoint-ledger evaluation, and reports on `openai-codex/gpt-5.4`.
- Higher-quality runs can override the same routes through `worldfork settings model-routing`, but should preserve the distinction between frequent cohort calls and high-impact governance/report calls.

To configure OpenAI Codex OAuth, use the headless login command. This path works on machines without the Codex CLI installed:

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

Patch the route table with JSON. Keep entries explicit and restore prior rows after an experiment if the user only wanted a temporary test:

```bash
worldfork settings model-routing --data '{
  "entries": [
    {
      "job_type": "god_agent",
      "preferred_provider": "openai-codex",
      "preferred_model": "gpt-5.4",
      "fallback_provider": "openai-codex",
      "fallback_model": "gpt-5.4",
      "temperature": 0.2,
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
worldfork settings llm
```

For Kimi or other OpenAI-compatible providers, add a `settings providers` row with that provider name, `api_key_env`, base URL, and `payload.api` set to `openai-compatible`, then route individual `job_type` entries to it. For Claude or other non-OpenAI-compatible APIs, wait for or implement a provider adapter first. Provider adapters own request/response parsing; the rest of WorldFork should only consume structured outputs from the audited LLM layer.

If setup succeeds, install the regular operator skill. Read that skill if you need the ongoing operator workflow:

```bash
npx skills add Hilo-Hilo/WorldFork/skills/worldfork --all
```

## Onboard The User

After the stack is healthy, run discovery to see what commands are available and what they do. If you have not already done so, read `references/project-orientation.md`. Then give the user a short onboarding explanation of the core ideas:

- Big Bang: the initial scenario seed and workspace that defines the world.
- Run: a tracked execution of work against a Big Bang or multiverse.
- Multiverse: one timeline branch with its own state, ticks, events, and reports.
- Branching: creating alternate timelines from a decision point, agent review, or human intervention.
- Human intervention: an operator action that pauses, changes, branches, or reviews the simulation while keeping an audit trail.
- God agents: governance agents that inspect the world state, critique a timeline, and may propose or create additional branches.
- Review agent: the summarization/review layer that compares outcomes and produces structured reports across multiverses.
- Reports: versioned, structured records of what happened, with Markdown/PDF outputs rendered from the stored report content only on request.

Keep the onboarding practical. Show the user the commands they will actually use:

```bash
worldfork init --name "My first world" --scenario-file examples/test-big-bang.md
worldfork runs workspace <big-bang-id>
worldfork watch big-bang <big-bang-id>
worldfork watch multiverse <multiverse-id>
worldfork reports list <big-bang-id>
worldfork reports versions <report-id>
worldfork reports view <report-version-id>
worldfork reports render <report-version-id> --format pdf --output report.pdf
```

Clarify that `worldfork init` proves initialization and workspace creation. It
does not by itself prove the full tick/branch/report loop; use Atlas or a live
smoke for that.

## Offer The Atlas Demo

Ask the user whether they want to run the Atlas demo simulation before starting it. Briefly describe Atlas as a larger onboarding world: an Atlas Resilience Crisis scenario that creates a Big Bang, runs root and branch timelines, allows God-agent branching, generates per-multiverse reports, and finishes with a cross-multiverse outcome review.

If the user agrees, run:

```bash
worldfork demo atlas
```

Then show how to inspect the result:

```bash
worldfork runs list
worldfork watch big-bang <big-bang-id> --once
worldfork reports list <big-bang-id>
worldfork reports view <report-version-id>
```

## Remove This Setup Skill

When setup, verification, onboarding, and any requested Atlas demo are done, tell the user this bootstrap skill is no longer needed. Remove it with:

```bash
npx skills remove worldfork-setup -y
```

Do not remove the regular `worldfork` skill unless the user asks to uninstall WorldFork operator guidance too.
