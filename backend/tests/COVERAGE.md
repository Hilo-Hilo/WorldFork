# WorldFork Test Coverage

The active suite tracks the current backend + workers + CLI product shape. The old pre-revamp initializer/tick-runner e2e files were removed, so collected tests now reflect maintained behavior.

Run the full maintained sweep:

```bash
./scripts/run_tests.sh all
```

## Layers

| Layer | Paths | Purpose |
| --- | --- | --- |
| Root regression + unit | `backend/tests/*.py`, `backend/tests/unit/` | invariants, storage, runtime safety, unit logic |
| Integration | `backend/tests/integration/` | ASGI routes, ORM behavior, and canonical control-plane contracts |
| E2E | `backend/tests/e2e/` | full-app behavior with mocked external providers |
| Live smoke | `scripts/full_runtime_smoke.py` | Docker stack + real OpenRouter Gemini calls |

## Root Regression Tests

| File | Coverage |
| --- | --- |
| `test_api_contracts.py` | OpenAPI/API contract invariants |
| `test_db_integrity.py` | DB integrity helpers |
| `test_jobs.py` | job type validation and task dispatch |
| `test_labels.py` | stable labels for multiverses and ticks |
| `test_llm_security.py` | public response sanitization and LLM safety boundaries |
| `test_multiverse_continuation.py` | continuation versioning and per-multiverse runtime overrides |
| `test_report_artifact_consistency.py` | structured report versions, on-demand PDF rendering, and artifact cleanup |
| `test_simulation_resilience.py` | initializer/runtime tolerance for malformed model data |
| `test_storage_config.py` | storage paths, PDF path traversal, artifact config |
| `test_timeline_safety.py` | checkpoint resume, branch inheritance, manual/runtime safety |

## Unit Tests

| File                          | Coverage                                   |
| ----------------------------- | ------------------------------------------ |
| `test_active_selection.py`    | actor activity scoring                     |
| `test_attention.py`           | attention math                             |
| `test_belief.py`              | belief drift and bounded-confidence kernel |
| `test_branch_policy.py`       | branch budget and cooldown policy          |
| `test_celery_setup.py`        | Celery serializer, broker, queues          |
| `test_divergence.py`          | divergence scoring                         |
| `test_export.py`              | run-folder zip and import verification     |
| `test_expression.py`          | expression and spiral-of-silence gates     |
| `test_god_agent.py`           | God-agent payload invariants               |
| `test_graphs.py`              | graph persistence and multiplex layers     |
| `test_ledger.py`              | ledger writes and Merkle chain             |
| `test_memory_local.py`        | local memory provider                      |
| `test_memory_zep.py`          | Zep degraded-mode behavior                 |
| `test_metrics.py`             | metrics endpoint                           |
| `test_models.py`              | SQLAlchemy model constraints               |
| `test_openrouter_provider.py` | OpenRouter parsing and JSON repair         |
| `test_prompt_builder.py`      | prompt packet assembly                     |
| `test_provider_policy.py`     | provider routing and fallback policy       |
| `test_rate_limits.py`         | provider rate limiting                     |
| `test_runtime_imports.py`     | runtime import surface                     |
| `test_runtime_models.py`      | runtime ORM models                         |
| `test_schemas.py`             | Pydantic schema invariants                 |
| `test_simulation_metrics.py`  | simulation metric helpers                  |
| `test_sot_loader.py`          | source-of-truth loader                     |
| `test_split_merge.py`         | split/merge conservation                   |
| `test_thresholds.py`          | mobilization thresholds                    |
| `test_tick_runtime_graph.py`  | dynamic runtime graph nodes                |
| `test_tool_parser.py`         | structured tool-output parsing             |
| `test_trust.py`               | trust graph helpers                        |
| `test_validators.py`          | simulation validators                      |
| `test_webhooks.py`            | webhook signing and retry                  |

## Integration Tests

| File | Coverage |
| --- | --- |
| `test_api_integrations.py` | Zep and webhook integration routes |
| `test_api_jobs.py` | canonical jobs monitor and queue control routes |
| `test_api_logs.py` | logs API across current and historical table shapes |
| `test_api_multiverse.py` | canonical multiverse tick route guards |
| `test_api_queue_control.py` | canonical queue control routes |
| `test_api_settings.py` | settings GET/PATCH and provider routing config |
| `test_branch_engine.py` | branch-engine copy-on-write behavior |
| `test_lineage.py` | lineage cache, descendants, pruning |
| `test_runtime_surface_selection.py` | canonical route contract |

## E2E Tests

| File                                   | Coverage                         |
| -------------------------------------- | -------------------------------- |
| `test_idempotency_e2e.py`              | worker idempotency key dedupe    |
| `test_multiverse_queue_lifecycle.py`   | queued multiverse tick lifecycle |
| `test_provider_fallback_e2e.py`        | provider fallback on failures    |
| `test_provider_rate_limit_fallback.py` | rate-limit fallback behavior     |
| `test_queue_dead_letter.py`            | dead-letter queue routing        |

## Live Full Runtime Smoke

```bash
worldfork smoke live
```

This intentionally uses real API credits and asserts that audited LLM calls use the default OpenRouter model:

```text
openrouter/deepseek/deepseek-v4-flash
```

It covers settings mutation/restoration, live ticks, runtime checkpoints, manual intervention, job control, structured reports, on-demand PDFs, log endpoints, and final readiness.

## Markers

| Marker            | Meaning                                        |
| ----------------- | ---------------------------------------------- |
| `e2e`             | end-to-end suite                               |
| `slow`            | slower tests, usually filterable locally       |
| `requires_broker` | needs a real broker and skips when unavailable |

The default `run_tests.sh all` sweep is offline except for any explicitly requested runtime smoke outside pytest.
