# LangGraph Runtime V2 Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Replace the current monolithic tick runner with a DB-canonical, queue-driven, LangGraph-based in-tick execution runtime that supports checkpointed pause/resume, first-class interventions, and maximal API/CLI observability.

**Architecture:** Keep PostgreSQL canonical for simulation truth, jobs, checkpoints, and logs. Use LangGraph only for agentic substep orchestration inside a multiverse tick. Keep queue control and API/CLI control-plane logic outside LangGraph. Default human interventions to branch-first behavior; permit direct edits only behind coherence-checked advanced commands.

**Tech Stack:** Python 3.11+, FastAPI, SQLAlchemy, PostgreSQL, Alembic, Redis/Celery-style worker queue, LangGraph, Click CLI, pytest.

---

## Preconditions / assumptions

- Canonical planning document: `~/worldfork-hilo/prd.md`
- Working branch: `revamp/langgraph-runtime-v2`
- Preserve conceptually:
  - `source_of_truth/*`
  - multiverse lineage model
  - graph/sociology domain concepts
  - report/version objects
  - current CLI mental model
- Backward compatibility is **not** a constraint. Architecture quality wins over preserving old schemas or old runs.

---

## Task 1: Freeze the rewrite boundary and quarantine duplicate runtime paths

**Objective:** Make the package/runtime boundary explicit before adding new code, so the rewrite does not deepen the existing split-brain between the live `app.*` path and the older mixed `backend.app.*` path.

**Files:**
- Modify: `backend/app/main.py`
- Modify: `README.md`
- Modify: `backend/README.md`
- Modify: `AGENTS.md`
- Inspect/decide fate of:
  - `backend/app/api/runs.py`
  - `backend/app/api/universes.py`
  - `backend/app/workers/*`
  - `backend/app/jobs/*`

**Step 1: Write failing documentation/test guard**

Create a small regression test that asserts the imported ASGI app only mounts the intended router set and does not accidentally expose both runtime families as equal canonical paths.

Suggested test file:
- Create: `backend/tests/integration/test_runtime_surface_selection.py`

Suggested assertions:
- `/api/agent/discover` exists
- `/api/jobs` exists
- `/api/runs` is either explicitly retained as a compatibility surface or explicitly marked transitional in the docs/test expectations

**Step 2: Run test to verify current ambiguity or missing rewrite contract**

Run:
```bash
uv run pytest backend/tests/integration/test_runtime_surface_selection.py -q
```

Expected initially: FAIL or require manual adjustment because the current product story still reflects mixed runtime surfaces.

**Step 3: Document the canonical rewrite boundary**

Add an explicit note in `README.md`, `backend/README.md`, and `AGENTS.md` that the rewrite branch treats:
- `app.*` + `/api/agent/*` + queue-controlled tick execution as canonical;
- duplicate/legacy runtime paths as transitional until removed.

**Step 4: Re-run the targeted test**

Run:
```bash
uv run pytest backend/tests/integration/test_runtime_surface_selection.py -q
```

Expected: PASS

**Step 5: Commit**

```bash
git add README.md backend/README.md AGENTS.md backend/app/main.py backend/tests/integration/test_runtime_surface_selection.py
git commit -m "docs: define canonical runtime surface for langgraph rewrite"
```

---

## Task 2: Add LangGraph dependency and runtime package skeleton

**Objective:** Introduce the minimal runtime package and dependency surface without changing behavior yet.

**Files:**
- Modify: `pyproject.toml`
- Modify: `backend/pyproject.toml`
- Create: `backend/app/runtime/__init__.py`
- Create: `backend/app/runtime/state.py`
- Create: `backend/app/runtime/enums.py`
- Create: `backend/app/runtime/policies.py`
- Create: `backend/app/runtime/cache.py`
- Create: `backend/app/runtime/validation.py`
- Create: `backend/app/runtime/graph_builder.py`
- Create: `backend/app/runtime/checkpoint_store.py`
- Test: `backend/tests/unit/test_runtime_imports.py`

**Step 1: Write failing import test**

Test should import the new runtime package and assert public symbols exist, for example:
- `TickRuntimeState`
- `NodeKind`
- `RetryPolicy`
- `build_tick_graph`

**Step 2: Run test to verify failure**

Run:
```bash
uv run pytest backend/tests/unit/test_runtime_imports.py -q
```

Expected: FAIL — modules not found.

**Step 3: Add dependency and skeleton files**

Add `langgraph` to the root `pyproject.toml`. If `backend/pyproject.toml` remains in repo, either add the same dependency there or add a comment that the root `pyproject.toml` is authoritative and `backend/pyproject.toml` is legacy.

In the new runtime package, define:
- `NodeKind` enum for checkpointable and observable node types
- `TickRuntimeState` pydantic model / typed dict for per-tick runtime state
- `RetryPolicy` and `RepairPolicy`
- `CacheKey` helper / cache policy stubs
- `ValidationResult` structure
- `build_tick_graph(...)` stub returning a graph object or placeholder interface

**Step 4: Re-run test**

Run:
```bash
uv run pytest backend/tests/unit/test_runtime_imports.py -q
```

Expected: PASS

**Step 5: Commit**

```bash
git add pyproject.toml backend/pyproject.toml backend/app/runtime backend/tests/unit/test_runtime_imports.py
git commit -m "feat: add langgraph runtime package skeleton"
```

---

## Task 3: Add canonical DB models for runtime execution metadata

**Objective:** Create durable runtime records for tick executions, checkpoints, node attempts, interventions, replays, and operation logs.

**Files:**
- Modify: `backend/app/db/models.py`
- Create: `backend/app/db/migrations/versions/0002_runtime_execution_metadata.py`
- Test: `backend/tests/unit/test_runtime_models.py`
- Test: `backend/tests/test_db_integrity.py`

**Step 1: Write failing model test**

Create tests asserting the new ORM models and constraints exist:
- `TickExecution`
- `TickCheckpoint`
- `ExecutionNode`
- `NodeAttempt`
- `Intervention`
- `ReplaySession`
- `OperationLog`

Key invariants to test:
- one active tick execution per `(multiverse_id, tick_index)` at a time
- checkpoint order is monotonic within one tick execution
- node attempts belong to execution nodes
- interventions can reference multiverse/tick/checkpoint/node attempt

**Step 2: Run tests to verify failure**

Run:
```bash
uv run pytest backend/tests/unit/test_runtime_models.py backend/tests/test_db_integrity.py -q
```

Expected: FAIL — models/migration missing.

**Step 3: Implement models and migration**

Add concrete columns for:
- status
- checkpoint key / node key
- attempt number
- provider/model metadata
- raw/validated artifact references
- started/finished/interrupted timestamps
- human actor / intervention reason / provenance fields
- replay anchor fields

Do **not** store canonical world state only in these models. These are execution metadata around `tick_snapshots`, not replacements for domain truth.

**Step 4: Re-run tests**

Run:
```bash
uv run pytest backend/tests/unit/test_runtime_models.py backend/tests/test_db_integrity.py -q
```

Expected: PASS

**Step 5: Commit**

```bash
git add backend/app/db/models.py backend/app/db/migrations/versions/0002_runtime_execution_metadata.py backend/tests/unit/test_runtime_models.py backend/tests/test_db_integrity.py
git commit -m "feat: add runtime execution metadata schema"
```

---

## Task 4: Implement queue control-plane model with DB-canonical job records

**Objective:** Make multiverse execution queue-driven with DB truth and external worker delivery.

**Files:**
- Modify: `backend/app/jobs/tasks.py`
- Modify: `backend/app/jobs/queues.py`
- Modify: `backend/app/api/jobs.py`
- Modify: `backend/app/workers/queues.py`
- Modify: `backend/app/workers/scheduler.py`
- Modify: `backend/app/workers/retries.py`
- Create: `backend/tests/integration/test_api_queue_control.py`
- Create: `backend/tests/e2e/test_multiverse_queue_lifecycle.py`

**Step 1: Write failing queue-control tests**

Cover:
- queue a multiverse tick job
- lease/claim job
- pause queued/running work
- resume paused work
- interrupt in-flight work by setting an interrupt request visible to worker/runtime
- requeue failed-but-retryable work

**Step 2: Run tests to verify failure**

Run:
```bash
uv run pytest backend/tests/integration/test_api_queue_control.py backend/tests/e2e/test_multiverse_queue_lifecycle.py -q
```

Expected: FAIL

**Step 3: Implement queue control-plane changes**

Unify on:
- DB job record as source of truth
- external worker queue for delivery
- explicit job state fields for `queued`, `running`, `paused`, `interrupt_requested`, `interrupted`, `failed`, `succeeded`
- per-job concurrency metadata and lease timestamps

Expose API operations for:
- pause
- resume
- interrupt
- requeue
- inspect queue health / capacity

**Step 4: Re-run tests**

Run:
```bash
uv run pytest backend/tests/integration/test_api_queue_control.py backend/tests/e2e/test_multiverse_queue_lifecycle.py -q
```

Expected: PASS

**Step 5: Commit**

```bash
git add backend/app/jobs/tasks.py backend/app/jobs/queues.py backend/app/api/jobs.py backend/app/workers/queues.py backend/app/workers/scheduler.py backend/app/workers/retries.py backend/tests/integration/test_api_queue_control.py backend/tests/e2e/test_multiverse_queue_lifecycle.py
git commit -m "feat: add db-canonical multiverse queue control"
```

---

## Task 5: Build the LangGraph tick runtime state machine

**Objective:** Replace monolithic actor iteration with a typed graph runtime for in-tick agentic execution.

**Files:**
- Modify: `backend/app/runtime/state.py`
- Modify: `backend/app/runtime/enums.py`
- Modify: `backend/app/runtime/graph_builder.py`
- Create: `backend/app/runtime/node_runner.py`
- Create: `backend/app/runtime/barriers.py`
- Create: `backend/app/runtime/control.py`
- Create: `backend/tests/unit/test_tick_runtime_graph.py`

**Step 1: Write failing graph-shape tests**

Assert the runtime graph can represent:
- observable-only nodes:
  - prompt assembly
  - validation
  - apply-state barrier
- checkpointable nodes:
  - cohort decision
  - hero decision
  - tool call
  - event generation
  - sociology update
  - graph update
  - god review
  - tick summary generation

**Step 2: Run tests to verify failure**

Run:
```bash
uv run pytest backend/tests/unit/test_tick_runtime_graph.py -q
```

Expected: FAIL

**Step 3: Implement runtime graph builder**

The graph should support:
- fan-out cohort nodes from last tick snapshot state
- fan-out hero nodes from last tick snapshot state
- deterministic barrier after actor calls
- downstream sequential checkpoints for event/sociology/graph/god/report phases
- injection of interrupt checks between checkpointable phases

**Step 4: Re-run test**

Run:
```bash
uv run pytest backend/tests/unit/test_tick_runtime_graph.py -q
```

Expected: PASS

**Step 5: Commit**

```bash
git add backend/app/runtime/state.py backend/app/runtime/enums.py backend/app/runtime/graph_builder.py backend/app/runtime/node_runner.py backend/app/runtime/barriers.py backend/app/runtime/control.py backend/tests/unit/test_tick_runtime_graph.py
git commit -m "feat: add langgraph tick runtime graph"
```

---

## Task 6: Implement call wrappers, validation nodes, cache policy, and escalation ladder

**Objective:** Make every LLM/tool step observable, validated, retryable, repairable, and cost-aware.

**Files:**
- Modify: `backend/app/runtime/validation.py`
- Modify: `backend/app/runtime/policies.py`
- Modify: `backend/app/runtime/cache.py`
- Create: `backend/app/runtime/call_wrapper.py`
- Modify: `backend/app/llm/audit.py`
- Modify: `backend/app/simulation/god_tools.py`
- Test: `backend/tests/unit/test_runtime_validation.py`
- Test: `backend/tests/unit/test_runtime_retry_policy.py`
- Test: `backend/tests/e2e/test_runtime_escalation_ladder.py`

**Step 1: Write failing tests**

Cover:
- same-prompt retry
- repair-prompt retry
- stronger-model escalation after repeated failures
- corrupt/truncated output handling
- cache hit provenance
- validation node policy result (`accept`, `retry`, `repair`, `escalate`, `human_intervention_required`)

**Step 2: Run tests to verify failure**

Run:
```bash
uv run pytest backend/tests/unit/test_runtime_validation.py backend/tests/unit/test_runtime_retry_policy.py backend/tests/e2e/test_runtime_escalation_ladder.py -q
```

Expected: FAIL

**Step 3: Implement wrapper + policies**

Requirements:
- transport/runtime validation near the call wrapper
- schema/completeness validation as explicit runtime node outcome
- prompt cache keyed by prompt+params+model
- record cache provenance on node attempts
- do not let queue layer choose model policy; keep that in runtime policy code

**Step 4: Re-run tests**

Run:
```bash
uv run pytest backend/tests/unit/test_runtime_validation.py backend/tests/unit/test_runtime_retry_policy.py backend/tests/e2e/test_runtime_escalation_ladder.py -q
```

Expected: PASS

**Step 5: Commit**

```bash
git add backend/app/runtime/validation.py backend/app/runtime/policies.py backend/app/runtime/cache.py backend/app/runtime/call_wrapper.py backend/app/llm/audit.py backend/app/simulation/god_tools.py backend/tests/unit/test_runtime_validation.py backend/tests/unit/test_runtime_retry_policy.py backend/tests/e2e/test_runtime_escalation_ladder.py
git commit -m "feat: add runtime validation cache and escalation policy"
```

---

## Task 7: Refactor tick execution into staged proposals plus controlled commits

**Objective:** Preserve partial work incrementally while applying canonical world-state mutations only at deterministic commit barriers.

**Files:**
- Modify: `backend/app/simulation/tick_runner.py`
- Modify: `backend/app/simulation/agent_engine.py`
- Modify: `backend/app/simulation/event_engine.py`
- Modify: `backend/app/simulation/sociology_engine.py`
- Modify: `backend/app/simulation/graph_engine.py`
- Modify: `backend/app/simulation/god_agent.py`
- Create: `backend/app/simulation/state_commit.py`
- Create: `backend/app/simulation/staged_proposals.py`
- Test: `backend/tests/integration/test_tick_runtime_commit_barriers.py`
- Test: `backend/tests/integration/test_tick_resume_from_checkpoint.py`

**Step 1: Write failing integration tests**

Assert that:
- actor work persists before canonical multiverse state commit
- interrupt after clean LLM return does not apply downstream state transitions
- resume continues from the first unfinished checkpoint
- canonical `multiverse.state` mutates only at defined barriers

**Step 2: Run tests to verify failure**

Run:
```bash
uv run pytest backend/tests/integration/test_tick_runtime_commit_barriers.py backend/tests/integration/test_tick_resume_from_checkpoint.py -q
```

Expected: FAIL

**Step 3: Implement staged proposal discipline**

Split current monolithic `run_next_tick(...)` responsibilities into:
- runtime execution orchestration
- staged proposal persistence
- canonical commit service
- final artifact/report assembly

Keep `tick_snapshots` canonical, but enrich them with execution references rather than stuffing everything into one opaque transaction.

**Step 4: Re-run tests**

Run:
```bash
uv run pytest backend/tests/integration/test_tick_runtime_commit_barriers.py backend/tests/integration/test_tick_resume_from_checkpoint.py -q
```

Expected: PASS

**Step 5: Commit**

```bash
git add backend/app/simulation/tick_runner.py backend/app/simulation/agent_engine.py backend/app/simulation/event_engine.py backend/app/simulation/sociology_engine.py backend/app/simulation/graph_engine.py backend/app/simulation/god_agent.py backend/app/simulation/state_commit.py backend/app/simulation/staged_proposals.py backend/tests/integration/test_tick_runtime_commit_barriers.py backend/tests/integration/test_tick_resume_from_checkpoint.py
git commit -m "feat: refactor tick execution into staged commits"
```

---

## Task 8: Add first-class API resources for checkpoints, node attempts, interventions, replays, and queue control

**Objective:** Expose the runtime as a powerful operator API rather than burying it in raw logs.

**Files:**
- Create: `backend/app/api/checkpoints.py`
- Create: `backend/app/api/node_attempts.py`
- Create: `backend/app/api/interventions.py`
- Create: `backend/app/api/replays.py`
- Create: `backend/app/api/queue.py`
- Modify: `backend/app/api/ticks.py`
- Modify: `backend/app/api/multiverses.py`
- Modify: `backend/app/api/agent.py`
- Modify: `backend/app/main.py`
- Modify: `backend/app/api/schemas.py`
- Test: `backend/tests/integration/test_api_checkpoints.py`
- Test: `backend/tests/integration/test_api_interventions.py`
- Test: `backend/tests/integration/test_api_replays.py`
- Test: `backend/tests/test_api_contracts.py`

**Step 1: Write failing API tests**

Cover:
- list/get checkpoints per tick
- list/get node attempts per checkpoint/tick
- create branch-first intervention
- create guarded in-place intervention
- create replay request
- queue pause/resume/interrupt endpoints
- agent projection fields for checkpoint-aware workspace summaries

**Step 2: Run tests to verify failure**

Run:
```bash
uv run pytest backend/tests/integration/test_api_checkpoints.py backend/tests/integration/test_api_interventions.py backend/tests/integration/test_api_replays.py backend/tests/test_api_contracts.py -q
```

Expected: FAIL

**Step 3: Implement routers and schemas**

Ensure `api/agent.py` gains summary/normal/full projections for the new surfaces without losing the current CLI mental model.

**Step 4: Re-run tests**

Run:
```bash
uv run pytest backend/tests/integration/test_api_checkpoints.py backend/tests/integration/test_api_interventions.py backend/tests/integration/test_api_replays.py backend/tests/test_api_contracts.py -q
```

Expected: PASS

**Step 5: Commit**

```bash
git add backend/app/api/checkpoints.py backend/app/api/node_attempts.py backend/app/api/interventions.py backend/app/api/replays.py backend/app/api/queue.py backend/app/api/ticks.py backend/app/api/multiverses.py backend/app/api/agent.py backend/app/main.py backend/app/api/schemas.py backend/tests/integration/test_api_checkpoints.py backend/tests/integration/test_api_interventions.py backend/tests/integration/test_api_replays.py backend/tests/test_api_contracts.py
git commit -m "feat: expose checkpoint intervention replay and queue APIs"
```

---

## Task 9: Extend the CLI into a first-class operator tool

**Objective:** Preserve the current CLI mental model while adding the new control surfaces the user explicitly asked for.

**Files:**
- Modify: `cli/src/worldfork_cli/main.py`
- Modify: `cli/src/worldfork_cli/client.py`
- Modify: `cli/src/worldfork_cli/output.py`
- Create: `cli/tests/test_cli_runtime_controls.py`

**Step 1: Write failing CLI tests**

Cover command families for:
- `worldfork pause ...`
- `worldfork resume ...`
- `worldfork interrupt ...`
- `worldfork inspect checkpoint ...`
- `worldfork diff ...`
- `worldfork fork ...`
- `worldfork inject ...`
- `worldfork logs stream ...`
- `worldfork export run-graph ...`

**Step 2: Run tests to verify failure**

Run:
```bash
uv run pytest cli/tests/test_cli_runtime_controls.py -q
```

Expected: FAIL

**Step 3: Implement CLI commands**

Keep the existing command style and global flags:
- `--verbosity`
- `--fields`
- `--json`

Prefer adding new groups/subcommands over inventing a separate admin CLI.

**Step 4: Re-run tests**

Run:
```bash
uv run pytest cli/tests/test_cli_runtime_controls.py -q
```

Expected: PASS

**Step 5: Commit**

```bash
git add cli/src/worldfork_cli/main.py cli/src/worldfork_cli/client.py cli/src/worldfork_cli/output.py cli/tests/test_cli_runtime_controls.py
git commit -m "feat: add runtime control commands to cli"
```

---

## Task 10: Implement branch-first intervention and guarded direct history editing

**Objective:** Make human interventions maximally recoverable without forcing every undo to become a new simulation by accident.

**Files:**
- Modify: `backend/app/branching/branch_engine.py`
- Modify: `backend/app/simulation/branch_engine.py`
- Create: `backend/app/simulation/intervention_service.py`
- Create: `backend/app/simulation/coherence_checks.py`
- Test: `backend/tests/integration/test_branch_first_interventions.py`
- Test: `backend/tests/e2e/test_direct_history_edit_guards.py`

**Step 1: Write failing tests**

Cover:
- default intervention creates branched multiverse
- operator can revert focus to original branch without destructive mutation
- advanced direct-edit command fails on coherence violations
- direct edit is logged and reversible when allowed

**Step 2: Run tests to verify failure**

Run:
```bash
uv run pytest backend/tests/integration/test_branch_first_interventions.py backend/tests/e2e/test_direct_history_edit_guards.py -q
```

Expected: FAIL

**Step 3: Implement services**

Rules:
- branch-first is default
- direct edits require explicit flag / API option
- coherence checks must validate upstream/downstream consistency
- every intervention writes an intervention record and operation logs

**Step 4: Re-run tests**

Run:
```bash
uv run pytest backend/tests/integration/test_branch_first_interventions.py backend/tests/e2e/test_direct_history_edit_guards.py -q
```

Expected: PASS

**Step 5: Commit**

```bash
git add backend/app/branching/branch_engine.py backend/app/simulation/branch_engine.py backend/app/simulation/intervention_service.py backend/app/simulation/coherence_checks.py backend/tests/integration/test_branch_first_interventions.py backend/tests/e2e/test_direct_history_edit_guards.py
git commit -m "feat: add branch-first interventions and guarded edits"
```

---

## Task 11: Add unified operation logs, replay provenance, and streaming observability

**Objective:** Make the backend maximally observable in real time and queryable later through API/CLI.

**Files:**
- Create: `backend/app/observability/operation_log.py`
- Modify: `backend/app/api/logs.py`
- Modify: `backend/app/api/websockets.py`
- Modify: `backend/app/observability/router.py`
- Modify: `backend/app/runtime/checkpoint_store.py`
- Modify: `backend/app/runtime/node_runner.py`
- Test: `backend/tests/integration/test_api_logs.py`
- Test: `backend/tests/integration/test_websockets.py`
- Test: `backend/tests/e2e/test_interrupt_resume_observability.py`

**Step 1: Write failing tests**

Cover:
- queue lifecycle log emission
- checkpoint creation/resume logs
- node attempt timelines
- human intervention logs
- replay provenance logs
- websocket/stream delivery of live execution updates

**Step 2: Run tests to verify failure**

Run:
```bash
uv run pytest backend/tests/integration/test_api_logs.py backend/tests/integration/test_websockets.py backend/tests/e2e/test_interrupt_resume_observability.py -q
```

Expected: FAIL

**Step 3: Implement unified operation logging**

Do not rely only on ad hoc job/llm logs. Add a normalized operation log layer with source type, object type, object id, event kind, timestamp, and payload.

**Step 4: Re-run tests**

Run:
```bash
uv run pytest backend/tests/integration/test_api_logs.py backend/tests/integration/test_websockets.py backend/tests/e2e/test_interrupt_resume_observability.py -q
```

Expected: PASS

**Step 5: Commit**

```bash
git add backend/app/observability/operation_log.py backend/app/api/logs.py backend/app/api/websockets.py backend/app/observability/router.py backend/app/runtime/checkpoint_store.py backend/app/runtime/node_runner.py backend/tests/integration/test_api_logs.py backend/tests/integration/test_websockets.py backend/tests/e2e/test_interrupt_resume_observability.py
git commit -m "feat: add unified runtime observability and streaming logs"
```

---

## Task 12: Replace old end-to-end tick flow and stabilize with focused regression suites

**Objective:** Make the new runtime the real path and prove pause/resume/recovery/queue/intervention behavior end-to-end.

**Files:**
- Modify: `backend/app/simulation/run_orchestrator.py`
- Modify: `backend/app/api/multiverses.py`
- Modify: `backend/tests/integration/test_tick_runner.py`
- Modify: `backend/tests/e2e/test_recursive_branch_e2e.py`
- Modify: `backend/tests/e2e/test_idempotency_e2e.py`
- Modify: `backend/tests/e2e/test_freeze_kill_e2e.py`
- Create: `backend/tests/e2e/test_langgraph_tick_runtime_e2e.py`
- Create: `backend/tests/e2e/test_human_intervention_recovery_e2e.py`

**Step 1: Write failing regression tests**

The new end-to-end suite should prove:
- queue-managed tick execution
- pause and resume from checkpoint
- interrupt after current LLM call return
- branch-first intervention
- guarded direct edit
- recoverability after simulated worker death/process termination
- API-accessible logs for all major stages

**Step 2: Run test subset to verify failure**

Run:
```bash
uv run pytest \
  backend/tests/integration/test_tick_runner.py \
  backend/tests/e2e/test_langgraph_tick_runtime_e2e.py \
  backend/tests/e2e/test_human_intervention_recovery_e2e.py \
  backend/tests/e2e/test_recursive_branch_e2e.py \
  -q
```

Expected: FAIL

**Step 3: Switch the runtime path**

Update orchestrators and API entrypoints so new multiverse tick execution routes through the new runtime by default.

Keep a short-lived escape hatch only if necessary for stabilization on this branch.

**Step 4: Re-run test subset**

Run:
```bash
uv run pytest \
  backend/tests/integration/test_tick_runner.py \
  backend/tests/e2e/test_langgraph_tick_runtime_e2e.py \
  backend/tests/e2e/test_human_intervention_recovery_e2e.py \
  backend/tests/e2e/test_recursive_branch_e2e.py \
  -q
```

Expected: PASS

**Step 5: Run broader safety suite**

Run:
```bash
uv run pytest \
  backend/tests/test_api_contracts.py \
  backend/tests/test_jobs.py \
  backend/tests/integration/test_api_jobs.py \
  backend/tests/integration/test_api_multiverse.py \
  backend/tests/integration/test_api_runs.py \
  cli/tests/test_cli.py \
  cli/tests/test_cli_runtime_controls.py \
  -q
```

Expected: PASS

**Step 6: Commit**

```bash
git add backend/app/simulation/run_orchestrator.py backend/app/api/multiverses.py backend/tests/integration/test_tick_runner.py backend/tests/e2e/test_recursive_branch_e2e.py backend/tests/e2e/test_idempotency_e2e.py backend/tests/e2e/test_freeze_kill_e2e.py backend/tests/e2e/test_langgraph_tick_runtime_e2e.py backend/tests/e2e/test_human_intervention_recovery_e2e.py
git commit -m "feat: switch multiverse tick execution to langgraph runtime"
```

---

## Task 13: Final cleanup — remove dead paths, tighten docs, and verify repo story

**Objective:** Finish the rewrite as a coherent product, not just a pile of working code.

**Files:**
- Modify: `README.md`
- Modify: `backend/README.md`
- Modify: `prd.md`
- Modify: `docs/agent.md`
- Delete or quarantine: any runtime paths proven obsolete during Tasks 1–12

**Step 1: Write cleanup checklist in docs**

Document:
- canonical architecture
- queue model
- operator flow
- checkpoint/intervention semantics
- direct-edit guardrails
- visualization deferred but data preserved

**Step 2: Run verification commands**

Run:
```bash
uv run ruff check .
uv run pytest -q
```

Expected: clean enough to ship this branch forward; if the repo still has known historical failures outside the rewrite scope, document the scoped green suites explicitly in `README.md` and the PR body.

**Step 3: Commit**

```bash
git add README.md backend/README.md prd.md docs/agent.md
git commit -m "docs: finalize langgraph runtime v2 architecture and workflow"
```

---

## Suggested execution order summary

1. Freeze boundary / docs
2. Add dependency + runtime skeleton
3. Add DB schema
4. Add queue control plane
5. Build runtime graph
6. Add validation/cache/escalation
7. Add staged commits + tick integration
8. Expose APIs
9. Expose CLI
10. Add interventions/guarded edits
11. Add unified observability
12. Switch runtime path and stabilize
13. Cleanup docs and dead code

## Minimal green suites after each major milestone

- **Runtime skeleton:**
  ```bash
  uv run pytest backend/tests/unit/test_runtime_imports.py -q
  ```

- **DB models:**
  ```bash
  uv run pytest backend/tests/unit/test_runtime_models.py backend/tests/test_db_integrity.py -q
  ```

- **Queue control:**
  ```bash
  uv run pytest backend/tests/integration/test_api_queue_control.py -q
  ```

- **Runtime graph:**
  ```bash
  uv run pytest backend/tests/unit/test_tick_runtime_graph.py -q
  ```

- **Resume/interrupt semantics:**
  ```bash
  uv run pytest backend/tests/integration/test_tick_resume_from_checkpoint.py backend/tests/e2e/test_interrupt_resume_observability.py -q
  ```

- **CLI/API operator surface:**
  ```bash
  uv run pytest backend/tests/test_api_contracts.py cli/tests/test_cli_runtime_controls.py -q
  ```

## Risks to watch

- letting LangGraph leak upward into API/domain ownership
- letting queue code pick model behavior or prompt policy
- applying canonical state mutations before checkpoint barriers
- preserving the current mixed runtime paths too long
- burying interventions in raw logs instead of explicit records
- adding direct-edit power without strong coherence checks

## Definition of done

The revamp is done when:
- multiverse ticks execute through the new runtime by default;
- pause/resume/interrupt work at checkpoint granularity;
- human interventions are recoverable and branch-first by default;
- logs, checkpoints, and node attempts are first-class API resources;
- CLI control commands exist for core operator workflows;
- queue control is API-visible and DB-canonical;
- process termination does not destroy recoverable execution state.
