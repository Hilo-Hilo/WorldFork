# AGENTS.md

WorldFork is a CLI-first backend product. The primary interface for coding agents is the `worldfork` command, backed by `/api/agent/*` and the canonical runtime API families.

In this monorepo, the backend service package lives at the root and the CLI package lives in `cli/`. Install the local CLI once before using the runtime; normal operational workflows should go through the `worldfork` command.

## Canonical Flow

```bash
worldfork agent discover
worldfork status
worldfork init --name "Atlas onboarding" --scenario-file examples/test-big-bang.md
worldfork watch big-bang <big-bang-id>
worldfork watch multiverse <multiverse-id>
worldfork runs list
worldfork runs workspace <big-bang-id>
worldfork jobs list --status failed
worldfork logs list --status failed
worldfork models defaults
worldfork settings show
worldfork update --dry-run
worldfork smoke live
worldfork demo atlas
worldfork reports list <big-bang-id>
worldfork reports view <report-version-id>
worldfork reports render <report-version-id> --format pdf --output report.pdf
```

## Hard Rules

- Put global flags before the command: `worldfork --verbosity summary runs list`.
- Start exploration with `--verbosity summary`.
- Use `--fields a,b,c` on large rows when only specific top-level keys are needed.
- Mutations are job-first; use `worldfork jobs wait <job-id> --timeout N` for bounded waits.
- `worldfork init` must wait for backend initialization to finish and return the initialized state.
- Use `worldfork watch big-bang <id>` or `worldfork watch multiverse <id>` to stream event logs, ticks, tool calls, and agent logs.
- Reports are structured database records first; Markdown/PDF renders are generated on request and are not backend-cached.
- Use `worldfork update` to pull code updates. It must preserve local `.env`, run data, artifacts, and Docker override files; do not use destructive Git commands for normal updates.
- Do not assume a web frontend exists. This repo is backend + workers + CLI.
- Do not hardcode backend URLs. Use the CLI default, `--base-url`, `WORLD_FORK_API_BASE`, or `BACKEND_API_BASE`.
- Live API-credit runs must use the configured default split unless the user explicitly authorizes a different model: OpenRouter `deepseek/deepseek-v4-flash` for cohort, hero, action, and event-summary work, and OpenAI Codex `gpt-5.4` for initializer, God-review, endpoint-ledger, and report work.

## Runtime Surface

Treat `backend/app/main.py`, the `app.*` package, `/api/agent/*`, `/api/big-bangs`, `/api/multiverses`, `/api/ticks`, `/api/jobs`, `/api/logs`, and `/api/reports` as the canonical runtime surface.

Do not add new `/api/runs`, `/api/universes`, or singular `/api/multiverse` routes. Those compatibility surfaces have been removed; use the canonical Big Bang, multiverse, job, log, report, and agent routes instead.

## Setup

For first-time setup, install the setup and operator skills:

```bash
npx skills add Hilo-Hilo/WorldFork/skills/worldfork-setup --all
npx skills add Hilo-Hilo/WorldFork/skills/worldfork --all
```

Use `worldfork-setup` for onboarding, then remove that temporary bootstrap skill when setup is complete:

```bash
npx skills remove worldfork-setup -y
```

Keep the regular `worldfork` skill installed for ongoing operation.

## Development

```bash
./scripts/run_tests.sh all
make lint
worldfork smoke live
```

Atlas onboarding is a separate long-form demonstration:

```bash
worldfork demo atlas
```
