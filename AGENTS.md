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
worldfork smoke live
worldfork demo atlas
worldfork reports list <big-bang-id>
worldfork reports view <report-version-id>
worldfork reports render <report-version-id> --format pdf
```

## Hard Rules

- Put global flags before the command: `worldfork --verbosity summary runs list`.
- Start exploration with `--verbosity summary`.
- Use `--fields a,b,c` on large rows when only specific top-level keys are needed.
- Mutations are job-first; use `worldfork jobs wait <job-id> --timeout N` for bounded waits.
- `worldfork init` must wait for backend initialization to finish and return the initialized state.
- Use `worldfork watch big-bang <id>` or `worldfork watch multiverse <id>` to stream event logs, ticks, tool calls, and agent logs.
- Reports are structured database records first; Markdown/PDF files are render artifacts for a specific report version.
- Do not assume a web frontend exists. This repo is backend + workers + CLI.
- Do not hardcode backend URLs. Use the CLI default, `--base-url`, `WORLD_FORK_API_BASE`, or `BACKEND_API_BASE`.
- Live API-credit runs must use `google/gemini-3.1-flash-lite-preview` unless the user explicitly authorizes a different model.

## Runtime Surface

Treat `backend/app/main.py`, the `app.*` package, `/api/agent/*`, `/api/big-bangs`, `/api/multiverses`, `/api/ticks`, `/api/jobs`, `/api/logs`, and `/api/reports` as the canonical runtime surface.

`/api/runs` is a transitional compatibility and compact-inspection surface. It remains documented for current agent workflows, but new feature work should prefer the canonical Big Bang, multiverse, job, log, report, and agent routes unless the CLI discovery contract says otherwise.

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
