# WorldFork Additional 36 Forecast Benchmark Cards

Generated on: 2026-05-04

This bundle adds **36 cards** to the existing 72 WorldFork benchmark prompts:

- **24 resolved forecast cards** for Brier/log-score evaluation.
- **8 longform dossier cards** for source-grounded world initialization, endpoint ledgers, branching, and report grounding.
- **4 adversarial/calibration cards** for uncertainty honesty, prompt-injection resistance, numerical consistency, and authority fidelity.

## Files

- `worldfork_additional_36_public.jsonl`: model-facing cards. Use this for WorldFork and baseline runs.
- `worldfork_additional_36_private_eval.jsonl`: hidden resolutions and gold rubrics. Do **not** expose this to the model.
- `worldfork_additional_36_legacy_schema.jsonl`: same public cards converted to the existing minimal repo schema: `case_id`, `category`, `difficulty`, `expected_focus`, `prompt`.
- `worldfork_additional_36_bundle.json`: manifest + public cards + private eval in one JSON file for convenience.

## Suggested scoring

### Resolved forecast cards

For each public card with `benchmark_role = resolved_forecast`, ask each system for a probability distribution over `yes` and `no`.
Use the private eval file only after forecasts are saved.

Binary Brier score:

```text
Brier = (p_yes - y)^2
```

where `y = 1` if the hidden resolution is `yes`, else `0`.

Clamped log score:

```text
log_score = -log(clip(p_yes, 0.01, 0.99))      if y = 1
log_score = -log(clip(1 - p_yes, 0.01, 0.99))  if y = 0
```

### Longform and adversarial cards

Use the `gold_checklists` in the private eval file. Recommended dimensions:

- actor recall
- authority fidelity
- constraint preservation
- endpoint coverage
- uncertainty honesty
- report grounding
- branch locality
- forbidden-error avoidance

## Leakage note

The resolved cards are a retrospective pilot. They use partial entity masking to reduce leakage, but they are not leakage-proof. In the paper, describe them as a small retrospective resolved-card pilot and avoid claiming definitive real-world forecasting validity.

## Default runtime policy

For WorldFork runtime experiments, the default ICML approach is:

- `cohort_agent`: OpenRouter `deepseek/deepseek-v4-flash`
- `hero_agent`: OpenRouter `deepseek/deepseek-v4-flash`
- governance/report routes: a strong configured model, such as OpenAI Codex `gpt-5.4` or an OpenRouter-hosted Kimi/Claude model

Codex-only runtime rows should be labeled as smoke or ablation evidence and kept separate from the default ICML route-policy rows.

Before launching default-route rows, run `worldfork settings provider-test openrouter`. If OpenRouter is configured but not registered, refresh the provider settings or restart the backend, then test again.

## ICML E3/E4 runtime settings

E3/E4 batch runs can create database connection pressure before Postgres CPU,
RAM, or storage are saturated. The main multiplier is:

```text
Celery worker processes * SQLAlchemy pool size/overflow * in-job cohort parallelism
```

For experiment stacks, prefer the ICML compose override after all active jobs
have drained:

```bash
docker compose -p worldfork-icml \
  -f docker-compose.yml \
  -f infra/icml/docker-compose.icml.yml \
  up -d --build
```

The override raises local Postgres `max_connections` to `250`, sets conservative
`shared_buffers=256MB` and `work_mem=8MB` defaults, adds an
idle-in-transaction timeout, uses smaller worker SQLAlchemy pools, and sets
`WORLDFORK_ICML_MAX_PARALLEL_COHORT_DECISIONS=8` by default.

For high-concurrency runs, add the PgBouncer overlay:

```bash
docker compose -p worldfork-icml \
  -f docker-compose.yml \
  -f infra/icml/docker-compose.icml.yml \
  -f infra/icml/docker-compose.pgbouncer.yml \
  up -d --build
```

PgBouncer runs in transaction-pooling mode and routes app containers through
`pgbouncer:6432`. The async URL disables the asyncpg prepared-statement cache
because transaction pooling can reuse server connections across clients.

Use generic env overrides rather than editing secrets:

```text
WORLDFORK_POSTGRES_MAX_CONNECTIONS=250
SQLALCHEMY_WORKER_SYNC_POOL_SIZE=2
SQLALCHEMY_WORKER_SYNC_MAX_OVERFLOW=4
SQLALCHEMY_WORKER_ASYNC_POOL_SIZE=2
SQLALCHEMY_WORKER_ASYNC_MAX_OVERFLOW=4
SQLALCHEMY_API_ASYNC_POOL_SIZE=4
SQLALCHEMY_API_ASYNC_MAX_OVERFLOW=8
WORLDFORK_ICML_MAX_PARALLEL_COHORT_DECISIONS=8
```

Before changing any of these settings on a live ICML stack, confirm queues are
idle with `worldfork jobs queues` or `GET /api/jobs/queues`.

## Agent handoff files added

This bundle now includes a full-cycle execution plan for running the WorldFork ICML forecasting-paper benchmark and writing the paper:

- `AGENT_HANDOFF_FULL_CYCLE_PLAN.md`: detailed coordinator/统筹 plan, setup, exact benchmark matrix, commands, scoring, and paper-writing instructions.
- `AGENT_BENCHMARK_RUN_MATRIX.json`: machine-readable case groups, experiment definitions, branch policies, and metrics.
- `AGENT_SCORING_RUBRIC.csv`: rubric rows for initialization, audit, social-state, emotion-observability, and forecast metrics.
- `AGENT_PAPER_DRAFT_SKELETON.md`: paper skeleton with the recommended claim, section outline, and result placeholders.

Keep `worldfork_additional_36_private_eval.jsonl` hidden from forecast-producing models until after predictions are saved.
