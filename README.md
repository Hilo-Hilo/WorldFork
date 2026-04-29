# WorldFork

WorldFork is a CLI-first, agent-operated backend for recursively branching social simulations. The primary interface is the `worldfork` command, which talks to the FastAPI backend over concise `/api/agent/*` contracts designed for AI agents and automation.

There is no web frontend in this product shape. The backend, job workers, source-of-truth assets, and CLI live in this monorepo.

## Runtime rewrite boundary

On `revamp/langgraph-runtime-v2`, the canonical runtime surface is:

- `backend/app/main.py`
- the `app.*` package
- `/api/agent/*`
- `/api/jobs*`
- queue-controlled tick execution paths mounted by the main FastAPI app

Legacy or duplicate runtime surfaces such as `/api/runs` are transitional only during the rewrite. They are not equal peers of the canonical `app.*` + agent-control-plane path and may be removed or re-homed as the runtime converges.

## Quickstart

```bash
cp .env.example .env
vim .env   # set provider keys as needed

make build
make up
make migrate
make seed
```

| Service | URL |
| --- | --- |
| API | http://localhost:8003 |
| API docs | http://localhost:8003/docs |
| Agent discovery | http://localhost:8003/api/agent/discover |

## CLI

```bash
uv run worldfork agent discover
uv run worldfork status
uv run worldfork runs list
uv run worldfork runs workspace <run-id>
uv run worldfork jobs list --status failed
uv run worldfork logs list --status failed
```

Large responses support `--verbosity summary|normal|full` and `--fields a,b,c` before the command:

```bash
uv run worldfork --verbosity summary runs list
uv run worldfork --fields id,status,created_at jobs list
```

## Development

```bash
uv run pytest -q
uv run ruff check .
```

## Reference

- Agent guide: `AGENTS.md`
- PRD: `prd-do-not-delete/prd.md`
