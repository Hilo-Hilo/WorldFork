# WorldFork

WorldFork is agent-operated social simulation infrastructure for branching
timelines. It exposes a FastAPI backend, Celery workers, durable Postgres and
Redis state, an artifact store, and the `worldfork` CLI for agent and operator
workflows. The repo is a monorepo: the backend service is packaged from the
root, the installable CLI is packaged from `cli/`, and the generic agent skill
is packaged from `skills/worldfork/`.

```{toctree}
:maxdepth: 2
:caption: Guides

agent
reporting
release
```
