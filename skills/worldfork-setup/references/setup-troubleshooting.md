# WorldFork Setup Troubleshooting

Use this reference when setup does not proceed cleanly. Prefer these checks
before inventing a workaround.

## CLI Resolution

Symptoms:

- `worldfork` is missing.
- `worldfork` imports an old package or raises `ModuleNotFoundError`.
- Help output does not include current commands such as `update`, `ledgers`, or
  `reports pack`.

Checks and fixes:

```bash
which -a worldfork || true
python3.11 -m pip install -e ./cli
worldfork --help
```

Temporary source-checkout fallback:

```bash
cd cli
uv run --extra dev worldfork --help
```

Use the fallback only to keep setup moving. The durable fix is the editable CLI
install from the repo root.

## Docker And Ports

Symptoms:

- `docker compose` cannot connect to Docker.
- API readiness fails.
- Postgres or Redis never becomes healthy.
- The API cannot bind port `8003`.

Checks:

```bash
docker compose ps
docker compose logs --tail=120 api worker_p0 worker_p1 worker_p2 worker_p3
lsof -nP -iTCP:8003 -sTCP:LISTEN || true
lsof -nP -iTCP:5433 -sTCP:LISTEN || true
lsof -nP -iTCP:6379 -sTCP:LISTEN || true
```

Port defaults:

- API: host `8003` to container `8000`
- Postgres: host `5433` to container `5432`
- Redis: host `6379`

If another local service owns a port, ask before changing Compose files or
stopping the other service.

## Environment And Provider Auth

Symptoms:

- `worldfork status` shows OpenRouter or OpenAI Codex unhealthy.
- Live smoke or Atlas fails before the first LLM call.
- Reports fail with provider auth errors.

Checks:

```bash
test -f .env && sed -n '1,120p' .env.example
worldfork settings llm
worldfork settings providers
worldfork settings model-routing
```

Do not print real secrets from `.env`. Confirm only whether the relevant
variables are present.

Expected first-run route policy:

- OpenRouter `deepseek/deepseek-v4-flash`: `cohort_agent`, `hero_agent`,
  action execution, and `event_summary`.
- OpenAI Codex `gpt-5.4`: `initializer_chunk_extractor`,
  `initializer_agent`, `god_agent`, `endpoint_ledger`, and `report_agent`.

Use `worldfork settings openai-codex-login` after the CLI is installed. It
writes `~/.worldfork/openai-codex-auth.json` by default. Operators can override
with `OPENAI_CODEX_OAUTH_TOKEN` or `OPENAI_CODEX_AUTH_FILE`.

## Database, Migrations, And Seed

Symptoms:

- Settings endpoints report missing rows.
- Zep settings report that they are not seeded.
- Model-routing output lacks persisted rows after startup.

Run:

```bash
make migrate
make seed
worldfork settings llm
```

`ZEP_ENABLED=false` is normal for local onboarding unless the user explicitly
wants Zep memory integration.

## Queue State

Symptoms:

- A reused checkout has old failed jobs.
- Watch output is confusing because old runs are still present.
- A smoke test is blocked by stale local queue state.

Inspect first:

```bash
worldfork jobs list --status failed
worldfork logs list --status failed
worldfork runs list
```

Only clear Redis after asking the user, and only for disposable local Docker
Compose Redis:

```bash
docker compose exec -T redis redis-cli FLUSHALL
```

Use `make clean-data` only when the user explicitly wants to delete local
Postgres and Redis volumes plus local run files.

## Skill Installation

For user-facing setup from GitHub:

```bash
npx skills add Hilo-Hilo/WorldFork/skills/worldfork-setup --all
npx skills add Hilo-Hilo/WorldFork/skills/worldfork --all
```

For local validation inside a checkout, prefer list-mode discovery so generated
agent runtime files are not written into the repo:

```bash
npx --yes skills add . --full-depth --list -y
npx --yes skills add ./skills/worldfork-setup --list -y
```

If testing unpublished branch changes, install from a local temporary copy of
the skill directory instead of the GitHub shorthand, because GitHub shorthand
resolves the default branch.
