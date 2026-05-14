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
worldfork setup
```

`agent discover` returns the schema version, verbosity tiers, known job types, scenario-bank metadata, and a recommended command flow for agents.
`setup` returns a first-run provider map, current provider status when the
backend is reachable, and the recommended Atlas model-routing profile. Add
`--include-patch` only when you need the full Atlas routing PATCH payload.

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
worldfork cohorts transcript <cohort-id> --multiverse-id <multiverse-id>
worldfork ticks timing <tick-snapshot-id>
worldfork ticks cost <tick-snapshot-id> --include-calls
worldfork runs cost <big-bang-id> --include-calls
worldfork runs estimate <big-bang-id>
worldfork logs list --status failed
```

Use `--verbosity summary` first for agent work. Move to `normal` or `full` only when a specific task needs the extra fields.

`ticks timing` is the detailed checkpoint and LLM-call timing surface for one
tick. `ticks cost` shows observed tick cost, and `runs cost` aggregates observed
run cost. `runs estimate` projects future cost/time from the current run state.

## Cost Estimation

```bash
worldfork costs estimate
worldfork runs estimate <big-bang-id>
worldfork runs cost <big-bang-id> --include-calls
worldfork ticks cost <tick-snapshot-id> --include-calls
```

Cost output is in USD. Actuals come from audited LLM calls when provider usage is
available. Estimates use configured routes, model pricing, observed call shapes,
remaining ticks, branch caps, and cohort parallelism.

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
For first-run onboarding, start with `worldfork setup` so the agent can explain
OpenRouter, OpenAI Codex OAuth, OpenAI API, Anthropic-through-OpenRouter, and
local/Ollama tradeoffs before asking the user which providers to configure.

## Reports

```bash
worldfork reports list <big-bang-id>
worldfork reports versions <report-id>
worldfork reports view <report-version-id>
worldfork reports view <report-version-id> --format json
worldfork reports render <report-version-id> --format pdf --output report.pdf
worldfork reports generate multiverse <multiverse-id> --title "M1 report" --summary "Terminal timeline report."
worldfork reports generate final <big-bang-id> --title "Final report" --summary "Cross-multiverse review."
worldfork reports pack <big-bang-id> --mode summary
worldfork reports adjudicate <big-bang-id>
worldfork reports adjudication <big-bang-id>
```

Report IDs refer to logical report slots. Report version IDs refer to a specific generated revision.

`reports pack` returns the structured evidence pack used by report generation.
`reports adjudicate` computes retained timeline/path-mass adjudication, and
`reports adjudication` reads the latest adjudication.

## Endpoint Ledgers

```bash
worldfork ledgers list <big-bang-id>
worldfork ledgers list <big-bang-id> --multiverse-id <multiverse-id>
worldfork ledgers view <ledger-version-id>
worldfork ledgers path-mass <big-bang-id>
worldfork ledgers evaluate <big-bang-id> --wait --timeout 120
worldfork ledgers evaluate <big-bang-id> --multiverse-id <multiverse-id>
```

Ledgers track endpoint statuses and evidence. Path mass is the deterministic
branch-probability accounting layer used for final distributions.

## Update A Checkout

```bash
worldfork update --dry-run
worldfork update --yes
```

`update` fetches the current branch from the selected remote and fast-forwards the local checkout only when it is safe. It refuses dirty tracked files, diverged history, and remote changes to protected local config/data paths such as `.env`, `runs/`, `artifacts/`, and Docker override files. It does not run migrations, seed the database, delete files, or overwrite local runtime data.

## Built-In Validation And Demo Commands

```bash
worldfork smoke live
worldfork demo atlas
```

`smoke live` validates a running backend using the configured live model split. `demo atlas` runs the larger onboarding simulation and emits follow-up commands for watching and viewing reports.

For the full runtime sequence behind these commands, see
[Runtime Walkthrough](runtime.md).
