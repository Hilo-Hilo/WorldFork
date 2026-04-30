# WorldFork Agent Interface

The supported AI-agent interface is the `worldfork` CLI backed by
`/api/agent/*`. Agents should start with summary output, project fields when
possible, and use bounded waits for asynchronous jobs.

Agents should not hardcode backend host URLs. Use the CLI's configured base URL
or pass `--base-url` when the environment requires a specific target.

Agents should assume the `worldfork` command is installed. In a source checkout
the CLI package lives in `cli/`; install it once before starting agent work.

## Discovery

```bash
worldfork agent discover
worldfork status
```

`agent discover` returns the schema version, supported verbosity tiers,
recommended command flow, known job types, and service metadata.

## Context Control

Use summary output first:

```bash
worldfork --verbosity summary runs list
worldfork --verbosity summary runs workspace <big-bang-id>
```

Use field projection when only a few top-level keys are needed:

```bash
worldfork --fields id,status,created_at jobs list
worldfork --fields id,status,name runs list
```

Emit JSON when another tool will parse the output:

```bash
worldfork --json status
```

## Async Work

The backend is job-first. Commands that enqueue long work should return a job
id. Use bounded waits:

```bash
worldfork jobs wait <job-id> --timeout 300 --poll-interval 2
```

Job control commands:

```bash
worldfork jobs pause <job-id>
worldfork jobs resume <job-id>
worldfork jobs interrupt <job-id>
worldfork jobs requeue <job-id>
worldfork jobs run <job-id>
```

## Runtime Inspection

Useful agent commands:

```bash
worldfork runs list
worldfork runs workspace <big-bang-id>
worldfork init --name "<name>" --scenario-file <scenario.md>
worldfork watch big-bang <big-bang-id>
worldfork watch multiverse <multiverse-id>
worldfork jobs list --status failed
worldfork logs list --status failed
worldfork models defaults
worldfork settings show
```

`init` waits for initialization to complete, then returns the initialized
workspace plus initialization artifacts/state. `watch` streams near-real-time
state/log updates from the API; use `--json-lines` when another agent should
consume the output.

Direct API query escape hatch:

```bash
worldfork query GET /api/agent/discover
worldfork query GET /readyz --no-api-prefix
```

## Live Smoke

For full-system validation with real OpenRouter credits:

```bash
worldfork smoke live
```

The harness verifies Gemini 3.1 Flash Lite model use, settings mutation,
runtime checkpoints, manual intervention, queue control, reports, logs, and
final readiness.

## Atlas Onboarding

Atlas is the onboarding demo, not the smoke test. It runs a larger multiverse
simulation with generous branch safety caps, terminal timeline draining,
per-multiverse reports, and a final report-agent summary across all terminal
multiverses.

```bash
worldfork demo atlas
```

Atlas defaults to 720 minutes per tick, or 12 simulated hours, and derives
`max_tick_index` from the target horizon when it is not supplied explicitly.
The default 30-day horizon therefore runs to tick index 60.

The command prints the final report version ID and follow-up `worldfork`
commands for viewing the report, rendering the PDF, and watching the run.
