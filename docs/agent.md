# WorldFork Agent Interface

The supported AI-agent interface is the `worldfork` CLI backed by
`/api/agent/*`. Agents should start with summary output, project fields when
possible, and use bounded waits for asynchronous jobs.

## Discovery

```bash
uv run worldfork agent discover
uv run worldfork status
```

`agent discover` returns the schema version, supported verbosity tiers,
recommended command flow, known job types, and service metadata.

## Context Control

Use summary output first:

```bash
uv run worldfork --verbosity summary runs list
uv run worldfork --verbosity summary runs workspace <big-bang-id>
```

Use field projection when only a few top-level keys are needed:

```bash
uv run worldfork --fields id,status,created_at jobs list
uv run worldfork --fields id,status,name runs list
```

Emit JSON when another tool will parse the output:

```bash
uv run worldfork --json status
```

## Async Work

The backend is job-first. Commands that enqueue long work should return a job
id. Use bounded waits:

```bash
uv run worldfork jobs wait <job-id> --timeout 300 --poll-interval 2
```

Job control commands:

```bash
uv run worldfork jobs pause <job-id>
uv run worldfork jobs resume <job-id>
uv run worldfork jobs interrupt <job-id>
uv run worldfork jobs requeue <job-id>
uv run worldfork jobs run <job-id>
```

## Runtime Inspection

Useful agent commands:

```bash
uv run worldfork runs list
uv run worldfork runs workspace <big-bang-id>
uv run worldfork jobs list --status failed
uv run worldfork logs list --status failed
uv run worldfork models defaults
```

Direct API query escape hatch:

```bash
uv run worldfork query GET /api/agent/discover
uv run worldfork query GET /readyz --no-api-prefix
```

## Live Smoke

For full-system validation with real OpenRouter credits:

```bash
uv run python -m scripts.full_runtime_smoke
```

The harness verifies Gemini 3.1 Flash Lite model use, settings mutation,
runtime checkpoints, manual intervention, queue control, reports, logs, and
final readiness.
