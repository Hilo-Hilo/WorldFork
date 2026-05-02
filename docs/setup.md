# Setup

WorldFork has two setup paths:

1. Agent-guided setup, which is the preferred onboarding path.
2. Complete manual setup, for operators who want every command.

## Agent-Guided Setup

Paste this prompt into your agent:

```text
Run these two commands to install the WorldFork skills, then use the
worldfork-setup skill to set up WorldFork on this computer:

npx skills add Hilo-Hilo/WorldFork/skills/worldfork-setup --all
npx skills add Hilo-Hilo/WorldFork/skills/worldfork --all

After installing them, use the setup skill to guide me through prerequisites,
.env configuration with OPENROUTER_API_KEY, CLI installation, Docker Compose
startup, migrations, seeding, readiness verification, a short explanation of
Big Bangs, multiverses, ticks, branches, endpoint ledgers, and reports, and the
onboarding demo if I confirm I want to spend live API credits.
Use the default OpenRouter `deepseek/deepseek-v4-flash` plus OpenAI Codex `gpt-5.4` split for live API-credit runs.
```

## Complete Manual Setup

Use this path when you want to run each command yourself.

### Prerequisites

- Docker Desktop or another Docker Compose runtime
- Python 3.11 or newer
- `uv`
- Node.js 20 or newer for `npx skills`
- An OpenRouter API key

### Configure Environment

Copy the example environment:

```bash
cp .env.example .env
```

Set `OPENROUTER_API_KEY` in `.env`.

WorldFork defaults cohort, hero, action, and event-summary work to `deepseek/deepseek-v4-flash` through OpenRouter. Initializer, God-review, endpoint-ledger, and report routes default to `gpt-5.4` through `openai-codex`.

### Install The CLI

Install the CLI package from the monorepo:

```bash
python3.11 -m pip install -e ./cli
worldfork --help
```

If `worldfork` resolves to an old global shim, reinstall the editable CLI and
check `which -a worldfork`. While repairing a shim, use the source-checkout
fallback from `cli/`:

```bash
uv run --extra dev worldfork --help
```

The CLI chooses its backend target in this order:

1. `--base-url`
2. `WORLD_FORK_API_BASE`
3. `BACKEND_API_BASE`
4. `http://127.0.0.1:8003`

After the CLI is installed, configure OpenAI Codex OAuth:

```bash
worldfork settings openai-codex-login
```

This writes the default auth file under `~/.worldfork/`. Operators may instead
set `OPENAI_CODEX_OAUTH_TOKEN` or `OPENAI_CODEX_AUTH_FILE` in `.env`.

### Start The Stack

```bash
make build
make up
make migrate
make seed
```

Use `make logs` if the API or worker fails to start.

If startup or readiness fails, check Docker Desktop first, then check for host
port conflicts on `8003`, `5433`, and `6379`.

### Update Later

Use the CLI updater for normal code refreshes:

```bash
worldfork update --dry-run
worldfork update --yes
```

The updater only fast-forwards the source checkout. It does not overwrite `.env`, local Docker overrides, run folders, artifacts, or database state, and it does not run migrations unless a future command explicitly adds that behavior.

### Verify Readiness

```bash
worldfork status
worldfork query GET /readyz --no-api-prefix
```

Readiness should show the database and Redis checks as healthy. OpenRouter is healthy when `OPENROUTER_API_KEY` is set, and OpenAI Codex is healthy when OAuth auth is present.

Confirm the effective model and provider routes before spending live API credits:

```bash
worldfork settings llm
```

### First Big Bang

```bash
worldfork init \
  --name "Atlas onboarding" \
  --scenario-file examples/test-big-bang.md \
  --max-ticks 4 \
  --tick-duration-minutes 720
```

The command waits for backend initialization to return and then prints the initialized workspace, actors, traits, graph baseline, sociology baseline, and emotion baseline.
This verifies setup and initialization. Use `worldfork demo atlas` for a full
tick, branch, endpoint-ledger, and report demonstration.

Inspect the initialized workspace:

```bash
worldfork watch big-bang <big-bang-id> --once
worldfork runs workspace <big-bang-id>
worldfork logs list --status failed
```

### Stop Or Reset

Stop services without deleting data:

```bash
make down
```

Stop services and remove Docker volumes:

```bash
make clean-data
```
