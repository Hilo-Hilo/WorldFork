# WorldFork Backend

Backend-only core runtime for WorldFork's CLI-first product shape.

## Canonical runtime surface

For `revamp/langgraph-runtime-v2`, treat the following as canonical:

- `app.main:app`
- the `app.*` package
- `/api/agent/*`
- `/api/jobs*`
- queue-controlled tick execution paths mounted by the main FastAPI app

Legacy or duplicate runtime surfaces such as `/api/runs`, older worker entrypoints, and mixed alternate runtime paths are transitional while the rewrite is in progress. They should not be treated as equal long-term control planes.

## Local setup

1. Create PostgreSQL database and set `DATABASE_URL`.
2. Install dependencies from `backend/pyproject.toml`.
3. Run Alembic migrations from `backend/`.
4. Start the API with `uvicorn app.main:app --reload`.
5. Optional worker: `dramatiq app.jobs.workers`.

The service stores canonical platform state in PostgreSQL and large/raw payloads in the artifact store configured by `ARTIFACT_ROOT`.

## Sample run

After migrations, start the API and call:

```bash
curl -X POST http://localhost:8000/api/sample-runs
```

This creates a Big Bang, runs several backend ticks, creates multiverse/final reports, and writes artifacts under `ARTIFACT_ROOT`.

Big Bang creation is prose-first. Send `scenario_text` as one plain-text string; if a PDF is involved, convert it to text before calling the API. The backend preserves the full text, chunks long text into artifacts when needed, and initializes `M1:T0` from that corpus.
