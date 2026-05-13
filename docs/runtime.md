# Runtime Walkthrough

This page explains how WorldFork moves from a scenario dossier to a running
branching simulation. It is written for operators and agents who need to inspect
or debug a live run without guessing which backend component owns each phase.

Use the CLI first. The API paths below are the canonical backend surfaces behind
the CLI and are useful when a first-class command does not expose a narrow enough
view.

## One Run In One Diagram

```text
worldfork init
      |
      v
POST /api/big-bangs
      |
      v
blocking Big Bang initializer
      |
      +-- source-of-truth snapshot
      +-- optional chunk extraction
      +-- initializer agent JSON
      +-- T0 actors, cohorts, heroes, graphs, events, ledgers
      |
      v
root multiverse M1
      |
      v
tick runtime graph
      |
      +-- parallel cohort decisions
      +-- sequential hero decisions
      +-- due event execution and aggregate event summary
      +-- sociology, graph, split, merge, emergence, branch pressure
      +-- LangGraph God review and audited JSON tool calls
      +-- endpoint ledger update
      +-- dynamic tool-call checkpoint replay
      +-- final state commit
      |
      v
branches, ledgers, reports, costs, and terminal outcomes
```

## Big Bang Initialization

`worldfork init --name ... --scenario-file ...` is a blocking initializer command.
The CLI builds a payload with scenario text, simulation config, model config,
branch policy, optional manual actors/cohorts/heroes, and
`use_initializer_agent=true` by default. It posts that payload to
`POST /api/big-bangs`.

The backend route calls the canonical initializer synchronously. The initializer:

1. Creates a `BigBang` row in `draft` status.
2. Stores a source-of-truth snapshot for the scenario.
3. Builds a plain-text corpus from the scenario text.
4. Runs the initializer agent when `use_initializer_agent` is enabled.
5. Normalizes initializer JSON and merges it with any manual payload entries.
6. Writes config artifacts and config-version rows.
7. Creates the root multiverse `M1`.
8. Writes T0 actor, cohort, hero, graph, emotion, sociology, and event state.
9. Creates the initial `TickSnapshot` for T0.
10. Seeds the endpoint ledger.
11. Returns the initialized Big Bang; the route commits after success.

The initializer agent uses the audited LLM route `initializer_agent`. The Atlas
routing profile sends initializer work through OpenRouter
`deepseek/deepseek-v4-pro`, while high-volume cohort and hero work can use
OpenRouter `deepseek/deepseek-v4-flash`.

Large scenario text may be chunked before the initializer agent runs. Chunk
extraction uses the audited route `initializer_chunk_extractor`; each chunk
summary is persisted as an artifact before the final initializer prompt is built.

## What The Initializer Must Produce

The initializer output is structured JSON, not prose. The required high-level
sections include:

| Section | Purpose |
| --- | --- |
| `simulation_brief` | Compact scenario and stakes summary |
| `actors` | Named actor archetypes |
| `population_archetypes` | Population groups with totals |
| `cohort_states` | Initial cohort states and population representation |
| `hero_archetypes` and `hero_states` | Named individual actors and T0 state |
| `trait_vectors` | Actor traits used by later prompts and graphs |
| `graph_edges` | Initial relationship, influence, conflict, trust, and social edges |
| `emotion_observations` | T0 affective baseline |
| `sociology_baseline` | T0 sociology graph signals |
| `sociology_prompt_influences` | Signals made available to later prompts |
| `channels` | Communication channels such as media or social channels |
| `initial_events` | Queued or already-known seed events |
| `branch_hypotheses` | Plausible divergence hypotheses |
| `merge_hypotheses` | Plausible convergence hypotheses |
| `important_questions` | Open uncertainties for later agents |
| `endpoint_ledger` | Terminal predicate ledger seed |
| `risk_flags` | Initialization risks or ambiguities |

Population is first-class. Population archetypes carry `population_total`.
Cohort states carry `represented_population`,
`population_share_of_archetype`, and `representation_mode`. Later sociology,
branching, split, merge, and report logic can use those fields to reason about
population-weighted effects instead of treating every cohort as equal size.

## Initialization Inspection

Use these CLI commands after `worldfork init`:

```bash
worldfork runs workspace <big-bang-id>
worldfork watch big-bang <big-bang-id> --once
worldfork logs list --status failed
```

Useful direct API surfaces:

```text
GET /api/big-bangs/{id}/initialization
GET /api/big-bangs/{id}/initialization/corpus
GET /api/big-bangs/{id}/initialization/actors
GET /api/big-bangs/{id}/initialization/traits
GET /api/big-bangs/{id}/initialization/graphs
GET /api/big-bangs/{id}/initialization/emotion-baseline
GET /api/big-bangs/{id}/initialization/sociology-baseline
GET /api/big-bangs/{id}/initialization/audit
```

The audit surface includes initializer LLM calls and artifacts. Raw scenario text
and raw LLM payload paths are debug-gated.

## Tick Clock And Simulation Time

Tick duration is part of simulation config. The CLI exposes:

```bash
worldfork init --tick-duration-minutes 720 --max-ticks 60
```

Runtime prompt context carries a clock with current tick, tick duration, elapsed
minutes, previous tick duration, and scheduling horizon. Actor and governance
prompts should therefore be able to reason about:

```text
elapsed simulation time = current_tick * tick_duration_minutes
time since an event = current_tick - event_tick, converted through tick duration
```

The configured tick duration is stored with the Big Bang config and copied into
the runtime clock used during prompt construction.

## One Tick, Stage By Stage

The canonical one-tick executor is `run_next_tick`. A tick is a checkpointed
runtime graph, not a loose collection of independent Celery phase tasks.

| Order | Stage | What happens |
| --- | --- | --- |
| 1 | Create or resume tick execution | Reuse an unfinished `running` or `provisional` tick, or create the next tick snapshot and execution rows |
| 2 | Build shared prompt context | Construct actor-safe context with clock, compact state, sociology influences, and budgeted event queue context |
| 3 | Cohort decisions | Run pending cohort actor decisions in parallel batches |
| 4 | Hero decisions | Run hero actor decisions sequentially |
| 5 | Actor barrier | Ensure all actor decisions are complete before downstream state changes |
| 6 | Event/action phase | Execute due queued events and process proposed actions |
| 7 | Sociology update | Update sociology signals from actor/event results |
| 8 | Graph and branch pressure | Update graph layers and generate split, merge, emergence, branch, and idle signals |
| 9 | God review | Run the governance agent loop over the provisional tick bundle |
| 10 | Endpoint ledger update | Apply God-agent endpoint updates into a new ledger version |
| 11 | Dynamic tool-call checkpoints | Reconcile audited God JSON tool calls with runtime checkpoints |
| 12 | Tick summary | Persist the tick-level summary |
| 13 | State commit | Write the final bundle and update multiverse state |

The tick runtime persists `TickExecution`, `ExecutionNode`, `TickCheckpoint`, and
`NodeAttempt` rows. Completed checkpoint payloads are durable, so a failed or
interrupted tick can resume without replaying completed stages.

## Cohort And Hero Decisions

Cohort decisions are the high-volume parallel phase. The runtime batches pending
cohort nodes with `settings.max_parallel_cohort_decisions`, which defaults to
`16`. Each cohort worker uses its own database session and releases its database
connection before waiting on the LLM.

Hero decisions run after cohort checkpoints and are currently sequential in the
canonical runner.

The important queue implication is that the job queue usually sees one
`run_multiverse_tick` or `run_big_bang_until_complete` job, while multiple cohort
LLM calls execute concurrently inside that job.

## Event Queue And Event Summary

Actor prompts receive an event queue context filtered for visibility and actor
relevance. The event queue can contain:

| Category | Meaning |
| --- | --- |
| Visible events | Public or actor-visible historical events |
| Past events | Executed events already known to the runtime |
| Due events | Queued events with `scheduled_tick <= current tick` |
| Upcoming events | Future queued events within the prompt budget |
| Actor-owned events | Events created by or targeted to the actor |

Actor decisions may enqueue new events. Due events are marked executed during the
event/action phase, given actual impact, and written to the event log.

Event summary is aggregate at the tick level. The event-summary LLM call receives
the set of executed events for the tick plus local tick context, reasons about
their combined effects and causal interactions, and then persists per-event
summary rows for compatibility with report and evidence surfaces.

Future queued events are inherited by child branches.

## God Review And JSON Tool Calls

God review is the governance phase. It is implemented as a small LangGraph agent
loop:

1. Call the God model with a budgeted provisional tick bundle.
2. Normalize and prepare JSON tool calls.
3. Execute and audit tool calls.
4. Repair and repeat only when tool execution requires it.

God review can use tool calls such as:

| Tool | Effect |
| --- | --- |
| `create_branch` | Create a child multiverse and split path probability |
| `split_cohort` | Replace one cohort with multiple population-conserving child cohorts |
| `merge_cohorts` | Combine cohorts and carry summed represented population |
| `kill_hero` | Mark a hero/actor state as killed |
| `terminate_timeline` | Set multiverse status to `terminated` |
| `freeze_timeline` | Set multiverse status to `frozen` |
| `mark_ready_for_report` | Mark a multiverse reportable |

Tool calls are idempotent by key. The God loop executes and audits tools, then
the tick runtime creates dynamic tool-call checkpoint nodes. If a checkpoint
replays a tool that already ran, idempotency links to the existing result instead
of duplicating side effects.

Branching can also be policy-assisted. If branch pressure exceeds the configured
threshold and the God output does not explicitly create a branch, the runtime can
add a heuristic branch tool call under branch-policy caps.

## Cohort Split And Merge Semantics

`split_cohort` requires at least two children. The child cohorts must conserve
the parent `represented_population`; the God agent supplies the proposed child
states, rationale, and proportions through the JSON tool call. The runtime audits
the request before mutating state.

`merge_cohorts` creates a new cohort whose represented population is the sum of
the merged source cohorts. Source actor states are marked as merged so later
prompts and reports can distinguish lineage from active cohorts.

This means the sociology engine can propose pressure or candidates, but durable
mutation is controlled by the God-review/tool-call path.

## Branches And Inheritance

`create_branch` creates a child `Multiverse`, writes a lineage edge, and splits
path probability according to branch policy and tool-call payload. A child
inherits:

| Inherited data | Behavior |
| --- | --- |
| Parent ticks | Stored compactly through lineage references and hydrated on read |
| Future queued events | Copied into the child timeline |
| Cohort and hero states | Latest active states are copied at fork time |
| Graph edges | Current graph context is copied |
| Prompt influences | Relevant sociology prompt influences are copied |

After the fork point, each child has its own executable state.

## Endpoint Ledgers And Path Mass

Endpoint ledgers track terminal predicates and evidence. They are not the same
thing as branch/path probability.

During normal ticks, God review may emit endpoint updates. Those updates are
merged into a new multiverse-scoped `EndpointLedgerVersion`.

At report time, endpoint ledgers are evaluated again as needed. A final Big Bang
report also runs timeline adjudication so retained timelines can be compared by
effective path mass. Endpoint status answers the yes/no/unresolved question;
path mass answers how much retained branch probability is attached to that
status.

Useful CLI commands:

```bash
worldfork ledgers list <big-bang-id>
worldfork ledgers view <ledger-version-id>
worldfork ledgers path-mass <big-bang-id>
worldfork ledgers evaluate <big-bang-id> --wait --timeout 120
worldfork reports adjudicate <big-bang-id>
worldfork reports adjudication <big-bang-id>
```

## Job Queue And Celery

`/api/jobs` is the canonical queue surface. Jobs are persisted in Postgres and
executed by Celery task `worldfork.execute_job`.

Canonical job types include:

| Job type | Queue | Purpose |
| --- | --- | --- |
| `initialize_big_bang` | `p1` | Queue-backed initialization path |
| `run_multiverse_tick` | `p0` | Run or resume one tick for one multiverse |
| `simulate_multiverse_ticks` | `p0` | Run multiple ticks for one multiverse |
| `run_big_bang_until_complete` | `p1` | Drain active multiverses until terminal, then report |
| `generate_multiverse_report` | `p2` | Generate one terminal multiverse report |
| `generate_final_big_bang_report` | `p2` | Generate final cross-multiverse report |
| `evaluate_endpoint_ledger` | `p2` | Re-evaluate endpoint ledgers |

Celery is configured with queues `p0`, `p1`, `p2`, `p3`, and `dead_letter`.
Workers acknowledge late, reject tasks on worker loss, and use a prefetch
multiplier of `1` so a worker does not reserve a large hidden backlog.

The canonical persisted job path is separate from older envelope-style worker
tasks that still exist for legacy/split-task deployments.

## Job Lifecycle

A queued job is claimed, executed, and then marked `succeeded`, `failed`,
`interrupted`, or `cancelled`.

Important controls:

| CLI command | Effect |
| --- | --- |
| `worldfork jobs wait <job-id>` | Poll until the job reaches a terminal state or timeout |
| `worldfork jobs pause <job-id>` | Pause queued work or request interruption for running work |
| `worldfork jobs interrupt <job-id>` | Interrupt queued/paused work or request interruption for running work |
| `worldfork jobs resume <job-id>` | Move paused/interrupted work back to queued |
| `worldfork jobs requeue <job-id>` | Retry failed/interrupted retryable work and increment attempt |
| `worldfork jobs run <job-id>` | Execute a job inline through the API |

Running jobs use leases and heartbeats. Stale running jobs can be reclaimed after
the lease window. Tick execution also has stale-execution reclamation, which
marks stale runtime rows failed and marks stale running LLM calls failed.

## Resume And Interrupt Semantics

A tick can be interrupted after the actor barrier and before downstream phases,
tool calls, and tick summary. If interruption happens, unfinished nodes and
checkpoints are marked interrupted, and the tick remains resumable.

When a tick resumes, completed checkpoints are skipped. The runtime continues at
the first unfinished checkpoint. This is why a failed parallel cohort batch can
keep successful sibling cohort decisions and only retry the missing or failed
checkpoint.

## Observability

Use watch for live state:

```bash
worldfork watch big-bang <big-bang-id>
worldfork watch multiverse <multiverse-id>
```

Use timing and cost surfaces for detailed inspection:

```bash
worldfork ticks timing <tick-snapshot-id>
worldfork ticks cost <tick-snapshot-id> --include-calls
worldfork runs cost <big-bang-id> --include-calls
worldfork runs estimate <big-bang-id>
worldfork costs estimate
```

Useful direct API surfaces:

```text
GET /api/ticks/{tick_snapshot_id}/runtime
GET /api/ticks/{tick_snapshot_id}/timing
GET /api/ticks/{tick_snapshot_id}/cost
GET /api/ticks/{tick_snapshot_id}/tool-calls
GET /api/ticks/{tick_snapshot_id}/god-review
GET /api/ticks/{tick_snapshot_id}/events
GET /api/ticks/{tick_snapshot_id}/social
GET /api/ticks/{tick_snapshot_id}/graph-deltas
GET /api/ticks/{tick_snapshot_id}/sociology-signals
GET /api/ticks/{tick_snapshot_id}/emotion-observability
GET /api/agent/runs/{run_id}/cost
POST /api/agent/runs/{run_id}/cost-estimate
GET /api/report-versions/{report_version_id}/cost
```

Timing payloads include stage durations, checkpoint durations, attempt timings,
LLM timing summaries, and cost summaries. LLM calls are audited with provider,
model, token usage when available, request artifact IDs, response artifact IDs,
and cost data when the provider reports it.
