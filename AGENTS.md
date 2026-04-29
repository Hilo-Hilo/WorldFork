# AGENTS.md

WorldFork is now a CLI-first backend product. The primary interface for coding agents is the `worldfork` command, backed by `/api/agent/*`.

## Canonical Flow

```bash
uv run worldfork agent discover
uv run worldfork status
uv run worldfork runs list
uv run worldfork runs workspace <run-id>
uv run worldfork jobs list --status failed
uv run worldfork logs list --status failed
```

## Hard Rules

- Put global flags before the command: `worldfork --verbosity summary runs list`.
- Start exploration with `--verbosity summary`.
- Use `--fields a,b,c` on large rows when only specific top-level keys are needed.
- Mutations are job-first; use `worldfork jobs wait <job-id> --timeout N` for bounded waits.
- Do not assume a web frontend exists. This repo is backend + workers + CLI.
- On `revamp/langgraph-runtime-v2`, treat `backend/app/main.py`, the `app.*` package, `/api/agent/*`, and `/api/jobs*` as the canonical runtime surface.
- Treat `/api/runs` and other duplicate runtime families as transitional until they are explicitly re-homed or deleted.

## Development

```bash
uv run pytest -q
uv run ruff check .
```
