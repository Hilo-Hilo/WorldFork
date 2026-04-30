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
startup, migrations, seeding, readiness verification, and the onboarding demo.
Use google/gemini-3.1-flash-lite-preview for live API-credit runs.
```

## Complete Manual Setup

Use this path when you want to run each command yourself.

### Prerequisites

- Docker Desktop or another Docker Compose runtime
- Python 3.11 or newer
- Node.js 20 or newer for `npx skills`
- An OpenRouter API key

### Configure Environment

Copy the example environment:

```bash
cp .env.example .env
```

Set `OPENROUTER_API_KEY` in `.env`.

WorldFork defaults to `google/gemini-3.1-flash-lite-preview` for the default,
fallback, initializer, God-agent, cohort, hero, event-summary, and report-agent
model slots. Keep that model for cheap onboarding and validation runs unless
you intentionally change the environment.

### Install The CLI

Install the CLI package from the monorepo:

```bash
python3.11 -m pip install -e ./cli
worldfork --help
```

The CLI chooses its backend target in this order:

1. `--base-url`
2. `WORLD_FORK_API_BASE`
3. `BACKEND_API_BASE`
4. `http://127.0.0.1:8003`

### Start The Stack

```bash
make build
make up
make migrate
make seed
```

Use `make logs` if the API or worker fails to start.

### Verify Readiness

```bash
worldfork status
worldfork query GET /readyz --no-api-prefix
```

Readiness should show the database and Redis checks as healthy. OpenRouter is
healthy when `OPENROUTER_API_KEY` is set and reachable.

### First Big Bang

```bash
worldfork init \
  --name "Atlas onboarding" \
  --scenario-file examples/test-big-bang.md \
  --max-ticks 4 \
  --tick-duration-minutes 720
```

The command waits for backend initialization to return and then prints the
initialized workspace, actors, traits, graph baseline, sociology baseline, and
emotion baseline.

### Stop Or Reset

Stop services without deleting data:

```bash
make down
```

Stop services and remove Docker volumes:

```bash
make clean-data
```
