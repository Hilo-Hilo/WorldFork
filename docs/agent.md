# WorldFork Agent Interface

The supported AI-agent interface is the `worldfork` CLI. It talks to concise backend contracts under `/api/agent/*` and avoids large raw payloads unless explicitly requested.

## Discovery

```bash
worldfork agent discover
worldfork status
```

`agent discover` returns the current schema version, supported verbosity tiers, known job types, scenario-bank metadata, and the recommended command flow.

## Context Control

Use summary output first:

```bash
worldfork --verbosity summary runs list
worldfork --verbosity summary universes trace <universe-id>
```

Use field projection when an agent needs only a few keys:

```bash
worldfork --fields id,status,created_at jobs list
worldfork --fields actor_id,actor_kind,name universes trace <universe-id>
```

## Async Work

The backend is job-first. Commands that enqueue long work should return a job id. Agents should use bounded waits:

```bash
worldfork jobs wait <job-id> --timeout 300 --poll-interval 2
```
