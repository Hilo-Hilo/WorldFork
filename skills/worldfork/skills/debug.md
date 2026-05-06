# WorldFork Debug Module

Use this module when a run, tick, job, provider call, queue, or report is stuck or failed.

## First Questions To Answer

- Which checkout owns the running backend?
- Is the target a Big Bang, multiverse, tick snapshot, job, or LLM call?
- Is the system still running, failed, interrupted, paused, or waiting on provider I/O?
- Is there a resumable tick checkpoint or a failed job that must be requeued?

## Safe First Commands

```bash
worldfork status
worldfork agent discover
worldfork --verbosity summary runs list
worldfork jobs list --status failed
worldfork logs list --status failed
```

For a Big Bang:

```bash
worldfork runs workspace <big-bang-id>
worldfork watch big-bang <big-bang-id> --once
worldfork runs cost <big-bang-id> --include-calls
```

For a multiverse or tick:

```bash
worldfork watch multiverse <multiverse-id> --once
worldfork ticks timing <tick-snapshot-id>
worldfork ticks cost <tick-snapshot-id> --include-calls
worldfork query GET /api/ticks/<tick-snapshot-id>/runtime
worldfork query GET /api/ticks/<tick-snapshot-id>/god-review
worldfork query GET /api/ticks/<tick-snapshot-id>/tool-calls
```

## Queue Model

The canonical job surface is `/api/jobs`. One tick job can run many cohort LLM calls concurrently inside the job. Do not assume there is one Celery task per cohort.

Use:

```bash
worldfork jobs wait <job-id> --timeout 300 --poll-interval 2
worldfork jobs resume <job-id>
worldfork jobs requeue <job-id>
worldfork jobs interrupt <job-id>
worldfork jobs run <job-id>
```

`resume` moves paused/interrupted work back to queued without incrementing attempt. `requeue` retries failed/interrupted retryable work and increments attempt.

## Tick Resume Model

A tick with status `running` or `provisional` may be resumable. Completed checkpoints are skipped on resume. Successful cohort siblings can remain committed even if one cohort checkpoint failed.

Typical blocker categories:

- provider timeout or auth failure;
- DB connection pool exhaustion during parallel cohort fan-out;
- failed JSON validation in an actor, event summary, God review, ledger, or report call;
- interrupted job with unfinished checkpoints;
- stale execution reclaimed after lease/stale timeout.

## Provider And LLM Audit

```bash
worldfork settings llm
worldfork --verbosity normal --fields id,source,status,message,provider,model,big_bang_id logs list --source llm
```

LLM audit rows can include provider, model, request artifact, response artifact, token usage, timing, and cost when available.

## Operational Caps Worth Knowing

Three runtime caps surface in symptoms that look like simulation bugs but are infrastructure limits. If you see any of these, check the cap before debugging the simulation logic.

- **Celery `task_time_limit = 21600s` (6h hard / 5h35 soft).** The orchestrator runs every active multiverse sequentially per round; an 8-multiverse run with ~12 ticks each easily needs 80–120 min. If `worldfork jobs list` shows a `run_big_bang_until_complete` job that died with `SoftTimeLimitExceeded`, you blew the cap. Symptoms: orchestrator stops mid-run, no obvious error in the run logs, individual tick checkpoints intact. Mitigation: cap branching with `max_active_multiverses`, or bump the limit further in `backend/app/workers/celery_app.py` (and matching test).
- **DB pool size 20 + 20 overflow per process.** The per-tick cohort fan-out opens up to `max_parallel_cohort_decisions` (default 16) Sessions concurrently. If logs show `QueuePool limit of size N overflow M reached, connection timed out, timeout 30.00`, you've exhausted the pool. Either lower fan-out concurrency or bump pool size in `backend/app/db/session.py`. Postgres `max_connections` (default 100) is the upper bound across all worker processes.
- **Next.js dev proxy 5-minute timeout** on `/backend/*` rewrites. The frontend's `next.config.mjs` sets `experimental.proxyTimeout: 300_000` because the default 30s drops connections during initializer or run-until-complete POSTs and surfaces a generic "Internal Server Error" with **no backend traceback**. If the frontend reports a generic 500 after exactly 30s and the API container shows no POST entry, suspect the proxy timeout — restart the frontend dev server after editing `next.config.mjs` (hot reload doesn't pick it up).
