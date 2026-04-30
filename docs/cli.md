# CLI

The `worldfork` command is the primary operator and agent interface. It wraps the FastAPI backend and keeps agent workflows compact, inspectable, and easy to script.

## Global Options

Global options must appear before the command:

```bash
worldfork --json status
worldfork --verbosity summary runs list
worldfork --fields id,status,created_at jobs list
worldfork --base-url http://127.0.0.1:8003 status
```

| Option         | Purpose                                                    |
| -------------- | ---------------------------------------------------------- |
| `--base-url`   | Backend root URL                                           |
| `--api-prefix` | API prefix, default `/api`                                 |
| `--timeout`    | HTTP timeout in seconds                                    |
| `--json`       | Emit machine-readable JSON                                 |
| `--verbosity`  | Control agent endpoint detail: `summary`, `normal`, `full` |
| `--fields`     | Project large rows to selected top-level fields            |

## Discovery And Health

```bash
worldfork status
worldfork agent discover
worldfork query GET /readyz --no-api-prefix
```

`agent discover` returns the schema version, verbosity tiers, known job types, scenario-bank metadata, and a recommended command flow for agents.

## Create And Watch Runs

```bash
worldfork init --name "<name>" --scenario-file <scenario.md>
worldfork watch big-bang <big-bang-id>
worldfork watch multiverse <multiverse-id>
```

`init` blocks until initialization completes. `watch` polls workspace, tick, tool-call, and log surfaces until the selected Big Bang or multiverse reaches a terminal state.

Useful watch modes:

```bash
worldfork watch big-bang <id> --json-lines
worldfork watch big-bang <id> --once
worldfork watch big-bang <id> --no-stop
```

## Inspect Runtime State

```bash
worldfork runs list
worldfork runs workspace <big-bang-id>
worldfork universes trace <multiverse-id>
worldfork cohorts transcript <cohort-id> --universe-id <multiverse-id>
worldfork logs list --status failed
```

Use `--verbosity summary` first for agent work. Move to `normal` or `full` only when a specific task needs the extra fields.

## Jobs

```bash
worldfork jobs list
worldfork jobs wait <job-id> --timeout 300 --poll-interval 2
worldfork jobs pause <job-id>
worldfork jobs resume <job-id>
worldfork jobs interrupt <job-id>
worldfork jobs requeue <job-id>
worldfork jobs run <job-id>
```

Most long work is job-first. Use bounded waits instead of unbounded polling.

## Settings

```bash
worldfork settings show
worldfork settings patch --data '{"default_tick_duration_minutes":90}'
worldfork settings branch-policy
worldfork settings branch-policy --data @branch-policy.json
worldfork settings providers
worldfork settings model-routing
worldfork settings rate-limits
```

Settings commands wrap the mutable settings API. They are useful for validating that configuration changes persist and can be reread.

## Reports

```bash
worldfork reports list <big-bang-id>
worldfork reports versions <report-id>
worldfork reports view <report-version-id>
worldfork reports view <report-version-id> --format json
worldfork reports render <report-version-id> --format pdf
```

Report IDs refer to logical report slots. Report version IDs refer to a specific generated revision.

## Built-In Validation And Demo Commands

```bash
worldfork smoke live
worldfork demo atlas
```

`smoke live` validates a running backend using real OpenRouter calls. `demo atlas` runs the larger onboarding simulation and emits follow-up commands for watching and viewing reports.
