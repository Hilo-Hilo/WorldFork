# Agent Interface

Agents operate WorldFork through the `worldfork` CLI. The CLI is backed by `/api/agent/*` and the canonical runtime APIs.

## Rules

- Start with `worldfork agent discover` and `worldfork status`.
- Do not hardcode backend host URLs. Use the CLI default, `--base-url`, `WORLD_FORK_API_BASE`, or `BACKEND_API_BASE`.
- Put global flags before the command.
- Use `--verbosity summary` first.
- Use `--fields a,b,c` when only selected top-level fields are needed.
- Use bounded waits for jobs.
- Prefer `watch` for live run state instead of repeated ad hoc queries.
- Treat reports as structured database records; Markdown/PDF outputs are artifacts for a report version.

## Discovery

```bash
worldfork agent discover
worldfork status
```

`agent discover` returns the schema version, supported verbosity tiers, recommended command flow, known job types, scenario metadata, and service metadata.

## Context Control

Use summary output first:

```bash
worldfork --verbosity summary runs list
worldfork --verbosity summary runs workspace <big-bang-id>
```

Project large rows when a task only needs a few fields:

```bash
worldfork --fields id,status,created_at jobs list
worldfork --fields id,status,name runs list
```

Emit JSON when another tool will parse output:

```bash
worldfork --json status
```

## Create And Watch

```bash
worldfork init --name "<name>" --scenario-file <scenario.md>
worldfork watch big-bang <big-bang-id>
worldfork watch multiverse <multiverse-id>
```

`init` waits for initialization to complete, then returns the initialized workspace plus initialization artifacts and state. `watch` streams workspace, tick, tool-call, and log updates until the selected target is terminal.

Use machine-readable event streams when an agent needs to consume watch output:

```bash
worldfork watch big-bang <big-bang-id> --json-lines
```

## Jobs

```bash
worldfork jobs list --status failed
worldfork jobs wait <job-id> --timeout 300 --poll-interval 2
worldfork jobs pause <job-id>
worldfork jobs resume <job-id>
worldfork jobs interrupt <job-id>
worldfork jobs requeue <job-id>
worldfork jobs run <job-id>
```

If a command enqueues long work, capture the job ID and use a bounded wait. Do not spin forever.

## Runtime Inspection

```bash
worldfork runs list
worldfork runs workspace <big-bang-id>
worldfork universes trace <multiverse-id>
worldfork cohorts transcript <cohort-id> --universe-id <multiverse-id>
worldfork logs list --status failed
worldfork models defaults
worldfork settings show
```

## Reports

```bash
worldfork reports list <big-bang-id>
worldfork reports versions <report-id>
worldfork reports view <report-version-id>
worldfork reports view <report-version-id> --format json
worldfork reports render <report-version-id> --format pdf
```

Use `reports view` before rendering a PDF. Rendering is an artifact operation; it does not change the canonical report version.

## Direct API Escape Hatch

Use `query` only when a first-class CLI command does not exist:

```bash
worldfork query GET /api/agent/discover
worldfork query GET /readyz --no-api-prefix
```

## Live Runs

Use only Gemini 3.1 Flash Lite for live OpenRouter validation:

```text
google/gemini-3.1-flash-lite-preview
```

Full live smoke:

```bash
worldfork smoke live
```

Atlas onboarding:

```bash
worldfork demo atlas
```

Atlas is a demonstration, not a minimal smoke test. It runs a larger branching simulation, drains terminal timelines, generates per-multiverse reports, and generates a final cross-multiverse report-agent summary.
