# WorldFork LangGraph Runtime V2 PRD

**Version:** 4.0 — revamp/langgraph-runtime-v2  
**Document type:** Backend revamp PRD  
**Primary backend language:** Python  
**Primary database:** PostgreSQL  
**Canonical source of truth:** PostgreSQL for durable simulation state and execution metadata  
**Primary runtime orchestration layer:** LangGraph for in-tick agent/LLM workflow only  
**Primary control plane:** API first, CLI as power-tool operator surface

This document intentionally removes superseded legacy V1 product/UI specification content. The revamp requirements and architecture below are the only authoritative scope for `revamp/langgraph-runtime-v2`.

## 0A. Revamp Discovery Addendum — 2026-04-28

This section captures current user-stated preferences for the next backend revamp before implementation planning is finalized.

### Confirmed preferences so far

- The next iteration should be a **major revamp** of the existing backend, not a small patch.
- The backend should use **LangGraph** as part of the new orchestration/runtime design.
- The new design should support **more granular execution and resumability** than the current coarse tick-boundary flow.
- Requirements gathering should happen through an **in-depth clarification pass** before branching or implementation.
- User preferences and clarified requirements should be recorded directly in `prd.md` as the canonical planning document for this revamp.

### Clarified answers from Batch 1 (partial)

1. **Primary objective of adopting LangGraph**
   - The LangGraph revamp is expected to unlock **all** of the following simultaneously:
     - better pause/resume;
     - better observability;
     - easier branching/forking;
     - cleaner orchestration;
     - more deterministic recovery;
     - more human control;
     - easier scaling;
     - a cleaner mental model for the codebase.

2. **LangGraph scope preference**
   - Current preference: **LangGraph should orchestrate only the agent/LLM workflow pieces**, not necessarily the entire backend runtime.

3. **Execution granularity preference so far**
   - The system should model multiple nested execution units distinctly rather than flattening everything into one opaque tick job.
   - A **multiverse tick** should be the fundamental execution unit.
   - A tick should support **partial completion accounting**, e.g. `4/16 cohorts updated in this tick`.
   - The operator should be able to inspect progress inside a tick rather than only seeing terminal tick outcomes.
   - LangGraph should ideally take advantage of **prompt caching and/or LLM call caching** if applicable.
   - Exact boundaries between node types, phases, and persisted checkpoints still need clarification.

4. **Pause/resume semantics so far**
   - Highest-priority controls:
     - **hard-stop ASAP**;
     - **resume from the exact checkpoint** rather than merely restarting the whole tick.

5. **Per-tick checkpoint vs observable boundaries**
   - Within one multiverse tick, the following should be **checkpoint/resume boundaries**:
     - each cohort decision;
     - each hero decision;
     - each tool call;
     - event generation;
     - sociology update;
     - graph update;
     - God-agent review;
     - report / tick-summary generation.
   - The following should be **observable-only**, not necessarily checkpoint boundaries:
     - prompt/context assembly;
     - validation/parsing/cleanliness checks for LLM output;
     - application of accepted state changes.

6. **Partial completion storage model**
   - Preferred model: **hybrid**.
   - Interpretation so far:
     - partially completed work should be persisted incrementally enough for resume and operator visibility;
     - canonical world mutation should remain controlled so that interruptions do not force unsafe half-applied state.

7. **Hard-stop semantics**
   - Required behavior:
     - stop after the currently running LLM call returns;
     - store the LLM output if it completed cleanly;
     - do **not** process/apply that output further once the stop has been requested;
     - mark all unfinished downstream nodes as interrupted / unfinished;
     - persist whatever completed so far;
     - do not apply any further state transitions after the stop request is honored.
   - If an interrupted or partially returned LLM output is corrupt/malformed:
     - discard the corrupt output payload;
     - mark the output explicitly as corrupt/interrupted.
   - After each LLM and/or tool call, the system should run a **cleanliness/completeness/format validation step** to detect malformed or partial outputs. Exact placement in LangGraph/runtime still needs design, but the requirement is explicit.

8. **Resume behavior**
   - Preferred behavior: resume from **wherever execution was interrupted**, i.e. continue from the first unfinished node rather than replaying the whole tick by default.

9. **Human operator control model**
   - The following are all considered **core** capabilities for the revamp:
     - approve/reject next phase;
     - edit prompt packet before resume;
     - inject manual event;
     - force branch from checkpoint;
     - replay one node with modified config;
     - freeze/kill multiverse mid-tick;
     - inspect every prompt/response/tool/result;
     - manually edit world state.

10. **Rewrite appetite**

- Current preference: **keep concepts, rewrite most implementation**.

11. **Compatibility constraints**

- Current preference: **everything can be changed** if needed for the better architecture. There are no hard backward-compatibility constraints currently stated for CLI, API, schema, jobs, or traces.

12. **Success criteria / target end-state**

- The desired backend should have all wanted features **maximally integrated**.
- It should exhibit:
  - high logical order;
  - maximum observability;
  - a **super powerful** CLI and API surface;
  - runs that are maximally recoverable and intervention-friendly;
  - human changes that are fully recoverable unless explicitly deleted;
  - maximal logs with time traces showing what happened, when, at what stage, including human interventions, in realtime;
  - logs that are fully accessible via API;
  - very strong, fail-safe, easily recoverable, easily navigable multiverse timeline version control;
  - reversible human interventions that do not require simulating another universe just to undo a change;
  - logical file hierarchy, conceptual hierarchy, and visualization.

13. **Proposed LangGraph ownership boundary (assistant proposal)**

    **LangGraph should own:**

- cohort decision execution;
- hero decision execution;
- LLM/tool-call wrappers and validation nodes;
- per-tick agentic substep orchestration;
- checkpoint metadata for resumability inside a tick;
- retry / repair / model-escalation policy for node-level LLM execution;
- prompt-cache integration in the agentic execution sublayer.

**Why:**

- These are the parts that are currently monolithic and hardest to resume cleanly. In the current backend, `run_next_tick(...)` orchestrates most of the tick in one transactional function, and `run_agent_decisions(...)` iterates through actors inline. That is exactly the surface where LangGraph adds the most value: node-level checkpointing, observable substeps, resumability, retries, and richer control flow.
- These parts are naturally graph-shaped because they involve dependent LLM decisions, validation, tool execution, and recovery policies.

**LangGraph should NOT own:**

- the canonical PostgreSQL data model;
- the authoritative world-state mutation engine;
- the event ledger as a persistence system;
- multiverse lineage / branch metadata persistence;
- the public API layer;
- the CLI layer;
- source-of-truth taxonomy loading/versioning;
- job-queue capacity management and worker scheduling;
- report storage as a persistence concern;
- workspace / product-level CRUD unrelated to agentic execution.

**Why:**

- The user wants process-termination durability and recoverability first. Keeping PostgreSQL canonical is the cleanest way to guarantee persistence across worker death or process termination.
- CLI/API should remain stable product surfaces, not indirect reflections of an orchestration library's internals.
- Branch/version-control semantics and ledger semantics are domain concepts broader than LLM orchestration; they should not be trapped inside a graph runtime.
- Queueing, concurrency ceilings, pause/resume dispatch, and retry leasing are infrastructure concerns. They need to remain controllable by the API and job system even if LangGraph internals change later.
- Source-of-truth files and domain schemas are product invariants, not execution-graph concerns.

14. **Canonical state model**

- Preferred model: **A — PostgreSQL is canonical; LangGraph checkpoints are resumability metadata.**
- Reason for selection:
  - the highest-priority requirement is persistence and recoverability after process termination;
  - DB-canonical state provides the strongest durability boundary;
  - LangGraph can then be treated as an execution engine whose checkpoints augment, rather than replace, domain persistence.

15. **Versioning / what should be versioned**

- Canonical world state snapshots: **yes**.
- Event ledger: **yes, combined with canonical world-state snapshots**.
- Prompt packets: **yes, associated with world state**.
- Raw LLM outputs: **yes, associated with world state**.
- Validated structured outputs: **yes, associated with world state**.
- Tool call inputs/outputs: **yes, associated with world state**.
- Human interventions: **yes**.
- Branch / fork metadata: **yes**.
- Diff views between checkpoints: **no as a separately versioned source; generate from underlying versioned data**.
- Undo history for human edits: **yes**.

16. **Reversibility model for human interventions**

- Preferred model:
  - a human edit should **by default create a branched multiverse**;
  - undo should usually mean reverting the operator viewport to the original pre-intervention multiverse rather than mutating history in place.
- If direct CLI history editing exists, it may edit multiverse history in place, but it must run **coherence checks** to ensure upstream/downstream edit consistency.
- Architectural implication:
  - branch-first intervention is preferred over destructive mutation;
  - direct mutation remains possible but should be treated as an advanced, coherence-checked operation.

17. **Operator surface priority**

- Highest-priority operator surface: **API first**.
- CLI should still be extremely powerful, but API is the first-class control plane.

18. **CLI philosophy**

- Preferred model: **hybrid B/C** — a fully featured control surface and a local power tool.
- Essential commands requested so far:
  - pause;
  - resume;
  - interrupt;
  - inspect;
  - diff;
  - fork;
  - inject;
  - stream logs;
  - export run graph.

19. **API resource philosophy**

- The API should expose the following as first-class resources:
  - runs;
  - multiverses;
  - ticks;
  - logs;
  - replays;
  - branch operations.
- Additional likely implied resources from other requirements, even if not yet explicitly confirmed:
  - checkpoints;
  - interventions;
  - execution nodes / node attempts.

20. **Concurrency model**

- Preferred model: **parallel by default with deterministic commit barriers**.
- Desired information boundary:
  - each cohort/hero should operate on information from the **last tick**, not on partially updated peer outputs from the current tick unless a later phase explicitly consumes them.
- Architectural implication:
  - fan out cohort/hero decisions in parallel;
  - apply accepted state transitions only at deterministic synchronization barriers.

21. **Caching philosophy**

- The requested cache priority is specifically **prompt/LLM caching to reduce API cost**.
- This should live in the LangGraph-adjacent execution sublayer and is **not the primary architecture decision right now**.
- Current interpretation: treat caching as an execution optimization, not the core product abstraction.

22. **Retry / failure policy**

- Default requested behavior:
  1.  auto-retry with the same prompt;
  2.  after some threshold, auto-retry with a repair prompt;
  3.  after additional failures, escalate to a more powerful model;
  4.  if still failing after configured attempts, require human intervention.
- Desired intervention mechanism:
  - health-check / CLI-visible reminder so the operator is prompted to inspect and fix the failure.

23. **Branching authority**

- God-agent: **core**.
- Human operator from any checkpoint: **core**.
- Automatic failure-recovery branch: **no**.
- Experimental “what-if” branch without touching canonical timeline: **no** as a separate core requirement right now.
- Branch from human-intervention undo point: **no** as a separate core requirement right now.

24. **Migration strategy appetite**

- Preferred strategy: **C — big rewrite branch, break aggressively, then stabilize**.

25. **Data migration tolerance**

- Current preference: **preserve nothing**.
- Priority is future architecture correctness, not backward compatibility with old run data.

26. **Visualization priorities**

- Visualization is important across the board, but it is **later** relative to the backend revamp itself.
- Current interpretation:
  - preserve/log the underlying data needed for full visualization now;
  - build the visualization surfaces later.

27. **Job-queue requirements**

- The revamp should include a job-queue layer where each multiverse is added to a queue.
- The queue layer should support settings for **max concurrent jobs**.
- The queue layer should manage:
  - retries;
  - retry logic coordination;
  - pause/resume logic;
  - API-exposed queue management.
- The queue layer should **not** manage:
  - model selection;
  - token cap;
  - temperature;
  - other LLM-behavior policy knobs.
- If a job fails:
  - the job manager should surface the failure;
  - the job implementation / execution layer should own recovery logic and re-queue itself appropriately.
- The queue should be exposed and controllable via API.

28. **First-class API resources**

- The following should all be explicit first-class API resources:
  - checkpoints;
  - interventions;
  - node attempts.

29. **Queue implementation choice**

- Preferred model: **C — DB-canonical job records plus an external worker queue**.
- Interpretation:
  - durable truth about jobs lives in PostgreSQL;
  - queue delivery/execution may use an external worker system;
  - this preserves process-termination durability while still allowing a real execution queue.

30. **Direct history edits**

- Preferred model: **A — yes, but heavily guarded**.
- Interpretation:
  - direct in-place history edit commands may exist;
  - they should be advanced operations with strong coherence validation and safety checks;
  - branch-first remains the default intervention model.

31. **Concepts to preserve through the rewrite**

- Preserve all of the following conceptually, even if implementations are heavily rewritten:
  - source_of_truth system;
  - graph / sociology domain concepts;
  - report / version objects;
  - multiverse lineage model;
  - existing CLI mental model.

### Remaining design work after branching

- Exact PostgreSQL schema for checkpoints, node attempts, interventions, and replay metadata.
- Exact boundaries between staged proposal storage and canonical state commits for each checkpointable step.
- Precise API shapes and CLI command contracts for queue control, checkpoint inspection, replay, intervention, and history editing.
- Exact queue technology and worker topology for the rewrite branch implementation.
- Which current modules should be deleted immediately versus harvested for concepts/tests/reference.

## 0B. Proposed Revamp Backend Architecture

This architecture section is the concrete target for the `revamp/langgraph-runtime-v2` rewrite branch. It translates the discovery answers above into explicit ownership boundaries, runtime components, and execution flow.

### 0B.1 Architectural thesis

WorldFork should be rebuilt around a **DB-canonical simulation kernel** with a **LangGraph-based agentic runtime sublayer** and an **API-controlled job queue**.

That means:

- PostgreSQL remains the source of truth for durable simulation state, run history, jobs, interventions, and observability metadata.
- LangGraph is introduced narrowly and deliberately for **in-tick agentic orchestration**, not as the owner of the whole backend.
- A queue/worker layer is responsible for claiming, pausing, resuming, retrying, and limiting concurrent multiverse work.
- API and CLI remain the operator control plane.

### 0B.2 Ownership boundaries

#### Canonical domain layer — owns truth

The canonical domain layer owns:

- Big Bang records;
- multiverse lineage;
- tick snapshots;
- event ledger/history;
- graph/sociology state;
- reports and report versions;
- interventions and reversibility metadata;
- job records;
- durable logs and replay references.

This layer persists in PostgreSQL and must survive process termination cleanly.

#### LangGraph runtime layer — owns agentic execution

The LangGraph runtime layer owns:

- cohort decision execution;
- hero decision execution;
- tool-call execution steps that are part of agentic flow;
- validation / cleanliness / completeness checks around LLM outputs;
- per-node retry / repair / model-escalation logic;
- checkpoint bookkeeping for resumability inside one tick;
- prompt/LLM cache integration used to reduce provider cost.

This layer must never become the sole source of truth for the simulation. It executes, checkpoints, and reports progress, but canonical simulation history is committed into PostgreSQL.

#### Queue/worker layer — owns delivery and capacity

The queue/worker layer owns:

- enqueuing work per multiverse;
- max concurrent work settings;
- leases / claims / heartbeats for running jobs;
- interrupt, pause, and resume delivery semantics;
- requeueing eligible work;
- surfacing stuck/failed jobs back to API consumers.

It does **not** own model choice, prompt construction policy, token cap policy, or temperature policy.

#### API/CLI layer — owns operator control

The API and CLI own:

- starting/stopping work;
- inspecting runs/multiverses/ticks/checkpoints/logs;
- initiating interventions;
- replay requests;
- branch operations;
- queue control;
- health and recovery commands.

The API is the primary control plane. The CLI is a power-tool client over that control plane.

### 0B.3 Core runtime objects

The rewrite should converge on these runtime concepts:

1. **Run / Big Bang** — top-level simulation root.
2. **Multiverse** — one lineage path through the scenario tree.
3. **Tick snapshot** — canonical tick record for one multiverse tick.
4. **Tick execution** — one runtime attempt to execute a tick.
5. **Checkpoint** — a persisted resume boundary inside a tick.
6. **Execution node** — one logical step in the agentic runtime graph.
7. **Node attempt** — one concrete execution attempt for a node, including retries/escalations.
8. **Intervention** — a human action that changes, pauses, replays, edits, or branches runtime behavior.
9. **Replay** — a replay/re-execution request anchored to a tick/checkpoint/node attempt.
10. **Operation log** — an append-only, time-indexed trace of queue, runtime, and human actions.

### 0B.4 Canonical persistence model

The rewrite should treat existing durable concepts as the base and add new execution metadata around them.

#### Existing canonical records to preserve conceptually

- `big_bangs`
- `multiverses`
- `multiverse_lineage_edges`
- `tick_snapshots`
- `events`, `event_revisions`, `event_logs`, `event_summaries`
- `tool_calls`
- `reasoning_traces`
- graph / sociology / report tables
- job records

#### New canonical records to add

At minimum, the rewrite should introduce durable records for:

- tick executions;
- checkpoints;
- execution nodes;
- node attempts;
- interventions;
- replay requests / replay sessions;
- operation logs / live execution logs;
- cache provenance metadata where useful.

The database, not the in-memory graph runtime, is the durability boundary.

### 0B.5 Tick execution model

A **multiverse tick** is the fundamental scheduling unit.

Inside a tick, execution is split into observable and checkpointable substeps.

#### Observable-only substeps

These should be logged and inspectable, but are not required to be standalone resume boundaries:

- prompt/context assembly;
- validation/parsing/cleanliness checks;
- application of accepted state changes.

#### Checkpointable substeps

These are required resume boundaries:

- each cohort decision;
- each hero decision;
- each tool call;
- event generation;
- sociology update;
- graph update;
- God-agent review;
- report/tick-summary generation.

### 0B.6 Parallelism model

Within a tick:

- cohort and hero decisions should run **in parallel by default**;
- they must operate on information from the **last completed tick**, not on partially mutated current-tick peer outputs;
- accepted results flow through **deterministic commit barriers** before downstream phases consume them.

This means the runtime is parallel at the node-attempt level, but state visibility remains disciplined.

### 0B.7 Partial completion and commit discipline

The system should use a **hybrid** partial-completion model:

- runtime work products persist incrementally for observability and resumability;
- canonical world-state mutation occurs only at defined commit points;
- a stop request must not force unsafe half-applied domain state.

Practically:

- raw outputs, parsed outputs, validation results, and node statuses should persist as execution metadata as soon as they are known;
- canonical `multiverse.state`, event state, graph state, and report state should commit only at controlled boundaries.

### 0B.8 Interrupt / pause / resume semantics

#### Pause request

When a pause/interrupt request is issued:

- let the currently running LLM call return if possible;
- store the output if it completed cleanly;
- do not process/apply that output further once the stop is being honored;
- mark unfinished downstream nodes as interrupted or unfinished;
- persist completed execution metadata;
- stop any further state transitions.

If the returned payload is corrupt or incomplete:

- discard the unusable payload body;
- mark the node attempt explicitly as corrupt/interrupted.

#### Resume request

On resume:

- continue from the first unfinished checkpointed node;
- do not replay the whole tick by default;
- keep full provenance for resumed work.

### 0B.9 Validation and repair model

Every LLM/tool step should pass through explicit post-call validation.

There are two layers:

1. **call-wrapper validation**
   - transport/runtime failures;
   - timeout/truncation/corrupt-body detection;
   - provider/model metadata capture.

2. **graph-level validation node**
   - schema checks;
   - completeness checks;
   - cleanliness/format checks;
   - policy decision: accept, retry, repair, escalate, or require human intervention.

This validation path is part of the observable runtime history.

### 0B.10 Retry and escalation ladder

Default node-attempt policy:

1. retry with the same prompt;
2. retry with repair logic/prompt;
3. escalate to a stronger model;
4. surface for required human intervention.

Queue infrastructure may re-deliver work, but node-level semantic recovery belongs to the execution layer.

### 0B.11 Branching and intervention semantics

#### Default rule

Human intervention should **default to creating a branched multiverse**.

This keeps history coherent and makes undo simpler.

#### Undo model

Undo usually means:

- return the operator’s viewport/control focus to the original pre-intervention multiverse;
- or branch again from an earlier safe point;
- not mutate the past invisibly.

#### Advanced direct edits

Direct in-place history edits may exist, but must be:

- explicitly requested;
- heavily guarded;
- coherence-checked against upstream/downstream history;
- fully logged and reversible.

### 0B.12 API surface model

The API should expose first-class resources for:

- runs;
- multiverses;
- ticks;
- checkpoints;
- node attempts;
- interventions;
- logs;
- replays;
- branch operations;
- queue/job control.

All major logs and execution traces should be queryable via API.

### 0B.13 CLI surface model

The `worldfork` CLI should preserve its current mental model while gaining stronger operator commands.

Core command families should include:

- `pause`
- `resume`
- `interrupt`
- `inspect`
- `diff`
- `fork`
- `inject`
- `stream logs`
- `export run-graph`

The CLI should remain a power tool, not a toy wrapper.

### 0B.14 Queue model

Queue architecture should be:

- **DB-canonical job records**;
- **external worker queue for delivery/execution**;
- API-controlled queue operations.

Each multiverse should be schedulable as queue-managed work, with concurrency ceilings and explicit runtime status exposed back through the API.

### 0B.15 Observability model

The new backend should produce maximal, time-indexed observability.

That includes:

- queue lifecycle events;
- checkpoint creation/resume events;
- node-attempt timelines;
- prompt and response references;
- validation outcomes;
- retries/escalations;
- state-commit boundaries;
- branch creation provenance;
- human interventions;
- replay provenance.

Visualization can come later, but the underlying records must exist now.
