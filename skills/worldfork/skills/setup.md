# WorldFork Setup Module

Use this module for first-time install, preflight, provider setup, readiness checks, and Atlas onboarding.

## Setup Rules

- Treat setup as guided onboarding, not just command execution.
- Explain what each phase proves before and after running it.
- Ask before spending API credits, changing provider/model routes, clearing local data, or starting Atlas.
- If setup fails, read `../references/setup-troubleshooting.md` before guessing.
- If the user is new to WorldFork, read `../references/project-orientation.md` and explain the product briefly.

## Install The Single Skill

The public install path is one skill:

```bash
npx skills add Hilo-Hilo/WorldFork/skills/worldfork --all
```

Do not ask the user to install `worldfork-setup` or `worldfork-report`; those workflows now live inside this skill as modules.

## Preflight

```bash
pwd
python3.11 --version
node --version
uv --version
docker compose version
which -a worldfork || true
lsof -nP -iTCP:8003 -sTCP:LISTEN || true
lsof -nP -iTCP:5433 -sTCP:LISTEN || true
lsof -nP -iTCP:6379 -sTCP:LISTEN || true
```

Report only actionable caveats: missing tools, stale `worldfork` shims, Docker not running, or port conflicts.

## Local Runtime Setup

```bash
git clone https://github.com/Hilo-Hilo/WorldFork.git
cd WorldFork
python3.11 -m pip install -e ./cli
worldfork --help
cp .env.example .env
```

Set `OPENROUTER_API_KEY` in `.env`. Configure OpenAI Codex OAuth after the CLI is installed:

```bash
worldfork settings openai-codex-login
```

Start and prepare the stack:

```bash
make build
make up
make migrate
make seed
```

Verify readiness:

```bash
worldfork status
worldfork query GET /readyz --no-api-prefix
worldfork agent discover
worldfork setup
worldfork settings llm
```

## Provider Choice

Use `worldfork setup` as the provider-choice map. Explain the tradeoff:

- OpenRouter: cheap/fast high-volume cohort, hero, action, and simulation routes.
- OpenAI Codex OAuth: stronger initialization, God review, endpoint-ledger, event-summary, and report routes.
- Local OpenAI-compatible runtimes: lower cost, but only use after JSON-quality testing.

Recommended Atlas policy:

```text
openrouter/deepseek/deepseek-v4-flash for cohort/hero/action work
openai-codex/gpt-5.4 for initializer/God/endpoint-ledger/event-summary/report work
```

Inspect before changes:

```bash
worldfork settings llm
worldfork settings providers
worldfork settings model-routing
```

Patch routes only through `worldfork settings model-routing`.

## First Big Bang

```bash
worldfork init \
  --name "Atlas onboarding" \
  --scenario-file examples/test-big-bang.md \
  --max-ticks 4 \
  --tick-duration-minutes 720
```

`worldfork init` proves initialization and workspace creation. It does not prove the full tick/branch/report loop.

Inspect the initialized workspace:

```bash
worldfork watch big-bang <big-bang-id> --once
worldfork runs workspace <big-bang-id>
worldfork runs cost <big-bang-id>
worldfork ledgers list <big-bang-id>
worldfork logs list --status failed
```

## Atlas Demo

Ask before starting Atlas because it spends live API credits and can take significant time.

```bash
worldfork demo atlas
```

Narrate the phases: Big Bang initialization, tick execution, branch creation, endpoint-ledger updates, report generation, and how to inspect printed IDs.
