# Architecture

WorldFork is backend-first and CLI-first. Conceptually, it is a Monte Carlo tree search of the real world: the backend owns the branching search tree, simulated rollouts, terminal outcome evidence, and reports, while the CLI provides the stable control surface for operators and agents.

## Runtime Stack

| Layer | Responsibility |
| --- | --- |
| FastAPI | Canonical HTTP API and agent discovery |
| Celery | Queue-backed execution for long-running jobs |
| Postgres | Durable Big Bang, multiverse, tick, job, report, and log state |
| Redis | Broker, result backend, and runtime coordination |
| LangGraph | Checkpointed tick graph execution |
| OpenRouter | LLM provider surface for low-cost cohort/hero/action routes and supported Kimi/Claude governance/report routes |
| OpenAI Codex | Supported strong governance/report route for initializer, God-review, endpoint-ledger, and report work |
| Artifact store | Non-regenerable evidence and audit payload files |
| `worldfork` CLI | Operator and AI-agent command surface |

## Core Concepts

### Big Bang

A Big Bang is the root scenario and the root node of the search tree. It stores scenario input, simulation config, model config, branch policy, initialization output, and the root multiverse.

### Multiverse

A multiverse is one timeline node in the Big Bang tree. It can inherit ticks from a parent, execute new ticks, branch into children, terminate, report, and later continue with a higher tick limit.

### Tick

A tick is one simulation step. Tick execution records node attempts, checkpoints, tool calls, graph updates, sociology signals, God-agent review, and the final tick snapshot.

Tick execution is a checkpointed runtime graph. Cohort decisions run in bounded
parallel batches, hero decisions run after cohort checkpoints, and downstream
event, sociology, graph, God-review, endpoint-ledger, tool-call, summary, and
commit stages run in order. See [Runtime Walkthrough](runtime.md) for the full
stage order and resume behavior.

### Endpoint Ledger

An endpoint ledger tracks terminal predicates and evidence for a multiverse or
Big Bang. Endpoint status answers whether a terminal condition is `yes`, `no`,
`unresolved`, or unresolved due to insufficient ticks. Branch/path probability is
accounted for separately through path mass and final timeline adjudication.

### Cost Summary

Cost summaries are derived from audited LLM calls and model price estimates. They
are available at run, tick, report-version, and estimate surfaces so operators
can inspect actual and projected spend in USD.

### Report

A report is a logical slot. A report version is a generated revision containing structured content and metadata. Markdown and PDF outputs are ephemeral renders generated from that version on request.

## Data Flow

```text
Scenario dossier
      |
      v
Big Bang initialization
      |
      v
Root multiverse
      |
      v
Tick runtime graph
      |
      +-- events
      +-- actor/cohort/hero state
      +-- sociology and graph pressure
      +-- God-agent review
      +-- tool-call checkpoints
      |
      v
Branch decisions, endpoint ledgers, and terminal outcomes
      |
      v
Structured reports and ephemeral renders
```

## Initialization

`worldfork init` posts to `POST /api/big-bangs` and waits for the blocking
initializer to finish. Initialization snapshots the source scenario, optionally
chunks long scenario text, calls the initializer agent, validates structured JSON,
and persists root `M1` state with T0 cohorts, heroes, graphs, events, endpoint
ledgers, and config artifacts.

The initializer output is population-aware. Population archetypes include
population totals, and cohort states include represented population, population
share, and representation mode. That population data flows into later sociology,
split, merge, and report reasoning.

## Branching

Branches are constrained by branch policy:

- maximum branch depth
- maximum active multiverses
- maximum branches per tick
- branch score threshold
- idle termination behavior

Child multiverses inherit parent ticks through lineage references and receive their own executable state after the fork point.

## Jobs And Control

The backend models queued work as jobs. Operators can pause, resume, interrupt, requeue, claim, or synchronously run jobs through the API and CLI. Agent code should use `worldfork jobs wait` with a bounded timeout.

The queue sees one tick job per multiverse tick. Cohort fan-out happens inside
that job through bounded in-process parallelism rather than as one Celery job per
cohort. This keeps checkpoint/resume semantics tied to the tick execution while
still allowing high-volume cohort LLM calls to run concurrently.

## Storage Boundaries

Postgres is canonical for domain state. Artifacts store non-regenerable evidence and audit payloads. Report renders are regenerable from the corresponding database report version and should be generated only on request.

## Agent Surface

Agents should start with:

```bash
worldfork agent discover
worldfork status
```

The discovery route is the contract for supported commands, verbosity tiers, known job types, and recommended flows.
