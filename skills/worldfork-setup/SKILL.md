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
- Treat setup as a guided onboarding session, not just an installation checklist. Welcome the user to WorldFork, keep the tone upbeat and practical, and explain what each phase proves before and after you run it.
- Be proactive with commentary. Before long commands, say what you are about to verify; after output returns, translate it into a plain-language status. During long-running commands, give a short progress update about every 30 seconds.
- Teach concepts at the moment they become relevant. Keep explanations short, but make sure the user understands Big Bangs, multiverses, ticks, branches, endpoint ledgers, God agents, and reports before asking them to run Atlas.
- Always ask before spending live API credits, changing provider/model routes, clearing local data, or starting the Atlas demo.

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
worldfork setup
worldfork settings llm
```

OpenRouter should show configured when `OPENROUTER_API_KEY` is present. OpenAI
Codex should show configured when OAuth is present through the default auth file,
`OPENAI_CODEX_OAUTH_TOKEN`, or `OPENAI_CODEX_AUTH_FILE`.

## Choose LLM Providers For Onboarding

Use the setup helper as the agent-facing provider map:

```bash
worldfork setup
```

If the backend is not up yet, use the offline form after the CLI is installed:

```bash
worldfork setup --offline
```

Explain the available choices and ask the user which ones they want to configure:

- OpenRouter: best default for cheap, fast, high-volume cohort, hero, action, and event-summary routes.
- OpenAI Codex OAuth: best default for stronger initialization, God review, endpoint-ledger evaluation, and report routes.
- OpenAI API: optional OpenAI-compatible direct API route if the user prefers `OPENAI_API_KEY` over Codex OAuth.
- Anthropic models: route through OpenRouter with `anthropic/*` model IDs in this build; do not promise a direct Anthropic adapter unless the runtime has gained one.
- Local OpenAI-compatible runtimes: Ollama, vLLM, LM Studio, and LocalAI can be routed to any agent type by model-routing rows. Use them after testing structured JSON quality; they can reduce cost but may lower Atlas accuracy.

Use these provider configuration patterns after the user chooses:

```bash
# OpenAI Codex OAuth
worldfork settings openai-codex-login

# OpenAI API direct
worldfork settings providers --data '{
  "providers": [
    {
      "provider": "openai",
      "base_url": "https://api.openai.com/v1",
      "api_key_env": "OPENAI_API_KEY",
      "default_model": "gpt-4o-mini",
      "fallback_model": null,
      "json_mode_required": true,
      "tool_calling_enabled": true,
      "enabled": true,
      "extra_headers": {},
      "payload": {"api": "openai-compatible"}
    }
  ]
}'

# Ollama local OpenAI-compatible endpoint.
# Use http://localhost:11434/v1 only when the backend itself is not in Docker.
worldfork settings providers --data '{
  "providers": [
    {
      "provider": "ollama",
      "base_url": "http://host.docker.internal:11434/v1",
      "api_key_env": "none",
      "default_model": "llama3.1:8b",
      "fallback_model": null,
      "json_mode_required": true,
      "tool_calling_enabled": false,
      "enabled": true,
      "extra_headers": {},
      "payload": {"api": "ollama-openai"}
    }
  ]
}'

# vLLM OpenAI-compatible endpoint. Replace base_url/model with the served values.
worldfork settings providers --data '{
  "providers": [
    {
      "provider": "vllm",
      "base_url": "http://host.docker.internal:8000/v1",
      "api_key_env": "none",
      "default_model": "local-model",
      "fallback_model": null,
      "json_mode_required": true,
      "tool_calling_enabled": false,
      "enabled": true,
      "extra_headers": {},
      "payload": {"api": "vllm-openai"}
    }
  ]
}'
```

For OpenRouter, set `OPENROUTER_API_KEY` in `.env`; the seeded provider row
already points at OpenRouter. For Anthropic-family models, keep the provider as
OpenRouter or a clearly named OpenRouter-backed provider such as
`openrouter-claude`, then use model IDs such as
`anthropic/claude-3-5-sonnet` in `worldfork settings model-routing`. Local
OpenAI-compatible providers can use `api_key_env: "none"`; if a strict local
proxy rejects bearer headers, add `"omit_auth_header": true` to that provider
row payload.

For the Atlas demo, recommend the `atlas-fast-governed` split emitted by
`worldfork setup`: OpenRouter `deepseek/deepseek-v4-flash` for frequent
cohort/hero/timeline calls and OpenAI Codex `gpt-5.4` for initialization, God
review, endpoint-ledger, and report calls. If the user chooses different
providers, keep the same principle: cheap/fast for high-volume simulation work,
stronger/slower for governance and summaries.

After configuration, verify the live provider state:

```bash
worldfork settings llm
worldfork settings provider-test openrouter
worldfork settings provider-test openai-codex
```

Only test providers the user actually configured.

## Configure LLM Providers And Routes

Use the settings API/CLI layer for all provider and model changes. Do not edit LangGraph/domain code to point at a provider directly.

Start by inspecting the effective config:

```bash
worldfork settings llm
worldfork settings providers
worldfork settings model-routing
```

Also run `worldfork setup` when deciding first-run provider/model choices. If
the user approves applying the standard Atlas route policy, rerun
`worldfork setup --include-patch` and use
`recommended_atlas_profile.model_routing_patch` as the starting point unless
the user explicitly chooses another cost/quality profile.

Default first-run policy:

- Onboarding/smoke runs: keep `cohort_agent`, `hero_agent`, action execution, and `event_summary` on `openrouter/deepseek/deepseek-v4-flash`, and keep initialization, God review, endpoint-ledger evaluation, and reports on `openai-codex/gpt-5.4`.
- Higher-quality runs can override the same routes through `worldfork settings model-routing`, but should preserve the distinction between frequent cohort calls and high-impact governance/report calls.

Use `cohort_agent` and `hero_agent` when explaining or editing audited actor routes. The canonical internal worker job for a single actor decision is `actor_deliberation_call`.

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

After the stack is healthy, warmly welcome the user into WorldFork as an
operator. Run discovery to see what commands are available and what they do. If
you have not already done so, read `references/project-orientation.md`. Then
give the user a short onboarding explanation of the core ideas:

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

As you show these commands, explain what the user should expect to see:

- `worldfork init` creates a Big Bang and waits for initialized actors and world state.
- `worldfork watch big-bang` shows the live event/tick/log stream for the whole run.
- `worldfork watch multiverse` focuses on one timeline branch.
- `worldfork reports list/view` reads structured report records; Markdown/PDF renders are generated only on request.
- `worldfork setup` helps choose and verify the provider/model split before live demos.

## Offer The Atlas Demo

Ask the user whether they want to run the Atlas demo simulation before starting it. Briefly describe Atlas as a larger onboarding world: an Atlas Resilience Crisis scenario that creates a Big Bang, runs root and branch timelines, allows God-agent branching, generates per-multiverse reports, and finishes with a cross-multiverse outcome review.

If the user agrees, run:

```bash
worldfork demo atlas
```

Narrate the demo while it runs. Keep commentary short, but explain the current
phase and why it matters:

- Before starting: confirm the provider/model split, the expected live API-credit use, and that Atlas is the full tick/branch/report demonstration.
- During Big Bang creation: explain that the initializer is turning a scenario dossier into actors, cohorts, baseline state, and the root timeline.
- During ticks: explain that each tick advances actor decisions, events, sociology/graph updates, God-agent review, endpoint-ledger updates, and snapshots.
- During branching: explain that child multiverses are alternate timelines from meaningful decision points, with lineage preserved.
- During reports: explain that reports are structured database versions first; Markdown/PDF are optional renders generated later.
- If the command runs for more than about 30 seconds without finishing, give a brief update on what long-running LLM/worker phase is likely active and how you will inspect it.
- When the CLI prints Big Bang, multiverse, report, job, or log IDs, tell the user what each ID is for and which command can inspect it.

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
