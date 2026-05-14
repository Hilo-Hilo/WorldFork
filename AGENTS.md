# AGENTS.md

WorldFork is a backend-first and CLI-first branching simulation system. The
primary interface for coding agents is the `worldfork` command, backed by
`/api/agent/*` and the canonical runtime API families.

The root package contains the backend service. The CLI package lives in `cli/`.
The optional Next.js dashboard lives in `frontend/` and wraps the same backend
APIs used by the CLI; it is useful for inspection, but headless operation should
still go through `worldfork`.

## Canonical Flow

```bash
worldfork agent discover
worldfork status
worldfork setup
worldfork settings llm
worldfork costs estimate
worldfork init --name "Atlas onboarding" --scenario-file examples/test-big-bang.md
worldfork watch big-bang <big-bang-id>
worldfork watch multiverse <multiverse-id>
worldfork runs list
worldfork runs workspace <big-bang-id>
worldfork runs estimate <big-bang-id>
worldfork runs cost <big-bang-id> --include-calls
worldfork ticks timing <tick-snapshot-id>
worldfork ticks cost <tick-snapshot-id> --include-calls
worldfork jobs list --status failed
worldfork logs list --status failed
worldfork ledgers list <big-bang-id>
worldfork ledgers path-mass <big-bang-id>
worldfork reports list <big-bang-id>
worldfork reports view <report-version-id>
worldfork reports render <report-version-id> --format pdf --output report.pdf
worldfork update --dry-run
worldfork smoke live
worldfork demo atlas
```

## Hard Rules

- Prefer the CLI. Use `worldfork query` only when no first-class command exposes the needed API operation.
- Put global flags before the command: `worldfork --verbosity summary runs list`.
- Start exploration with `--verbosity summary`; use `normal`, `full`, or `--json` only for a specific evidence gap.
- Use `--fields a,b,c` on large rows when only selected top-level keys are needed.
- Do not hardcode backend URLs. Use the CLI default, `--base-url`, `WORLD_FORK_API_BASE`, or `BACKEND_API_BASE`.
- Use bounded waits for queued work: `worldfork jobs wait <job-id> --timeout N --poll-interval 2`.
- `worldfork init` must wait for backend initialization to finish and return the initialized Big Bang/workspace state.
- Use `worldfork watch big-bang <id>` or `worldfork watch multiverse <id>` to stream activity instead of repeated ad hoc polling.
- Reports are structured database records first. Markdown/PDF outputs are generated on request from report versions.
- Endpoint ledgers answer endpoint status and evidence questions. Path mass and adjudication answer probability/distribution questions.
- Branch policy is authoritative: if a tick's `branch_score` crosses the configured threshold, the backend should create a branch even when the God-agent text/tool output says to continue.
- Use `worldfork update` for normal code updates. It must preserve local `.env`, run data, artifacts, reports, Docker override files, and local preferences; do not use destructive Git commands for normal updates.
- Do not clear Redis, delete run data, change model routes, start Atlas, or spend live API credits without user approval.
- The frontend exists but is optional. Do not make backend/runtime behavior depend on the dashboard.
- For first-time onboarding, use `worldfork setup` to show LLM provider options, ask which providers the user wants to configure, and explain each setup phase before and after running it.
- Live API-credit runs must use the configured default routing unless the user explicitly authorizes another policy. The common split is cheap/fast OpenRouter `deepseek/deepseek-v4-flash` for high-volume cohort, hero, action, timeline, predicate, event-summary, and single-universe report work; and OpenAI Codex `gpt-5.4` for initializer, God-review, endpoint-ledger, report fallback, and final multiverse report synthesis.

## Runtime Surface

Treat `backend/app/main.py`, the `app.*` package, `/api/agent/*`,
`/api/big-bangs`, `/api/multiverses`, `/api/ticks`, `/api/jobs`, `/api/logs`,
`/api/reports`, `/api/settings`, `/api/costs`, endpoint-ledger routes, and
observability routes as the canonical runtime surface.

Do not add new `/api/runs`, `/api/universes`, or singular `/api/multiverse`
routes. Keep compatibility aliases at the CLI layer when an operator shortcut is
still useful.

Long-running work should use persisted jobs where a job route exists. Canonical
job types include:

- `initialize_big_bang`
- `run_multiverse_tick`
- `simulate_multiverse_ticks`
- `run_big_bang_until_complete`
- `generate_multiverse_report`
- `generate_final_big_bang_report`
- `evaluate_endpoint_ledger`

## Setup

Install the single WorldFork operator skill:

```bash
npx skills add Hilo-Hilo/WorldFork/skills/worldfork --all
```

Then use the CLI-guided setup path:

```bash
worldfork setup
make build
make up
make migrate
make seed
worldfork status
worldfork agent discover
```

Do not install or reference the old separate `worldfork-setup` bootstrap skill.
The single `worldfork` skill contains setup, CLI, debug, report, documentation,
update, reinstall, and uninstall modules.

When running `worldfork demo atlas` for onboarding, narrate what the demo is
doing: Big Bang initialization, tick execution, branch creation,
endpoint-ledger updates, report generation, and how to inspect any printed Big
Bang, multiverse, job, log, ledger, or report IDs.

## Frontend

The frontend is an optional Next.js 15 dashboard in `frontend/`.

```bash
cd frontend
npm install
npm run dev
```

It expects the backend at `http://127.0.0.1:8003` by default and honors
`API_BASE_URL`, `NEXT_PUBLIC_API_URL`, `WORLD_FORK_API_BASE`, and
`BACKEND_API_BASE`. Client-side requests go through the `/backend/*` rewrite.
The dashboard uses polling, not WebSockets.

Frontend routes:

- `/` runs list
- `/input` scenario form
- `/dashboard?run=<big-bang-id>` live multiverse tree and logs
- `/report?run=<big-bang-id>` report viewer and render downloads

## Development

```bash
./scripts/run_tests.sh all
make lint
worldfork smoke live
```

`worldfork smoke live` spends real API credits and should only run after user
approval. For backend-only validation, prefer the maintained pytest layers:

```bash
./scripts/run_tests.sh unit
./scripts/run_tests.sh cli
./scripts/run_tests.sh integration
./scripts/run_tests.sh e2e
```

Frontend e2e tests are separate:

```bash
cd frontend
npm run test:e2e:install
npm run test:e2e
```

Atlas onboarding is a separate long-form demonstration:

```bash
worldfork demo atlas
```
