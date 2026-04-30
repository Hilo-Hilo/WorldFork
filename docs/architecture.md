# Architecture

WorldFork is backend-first and CLI-first. The backend owns simulation state and
the CLI provides the stable control surface for operators and agents.

## Runtime Stack

| Layer | Responsibility |
| --- | --- |
| FastAPI | Canonical HTTP API and agent discovery |
| Celery | Queue-backed execution for long-running jobs |
| Postgres | Durable Big Bang, multiverse, tick, job, report, and log state |
| Redis | Broker, result backend, and runtime coordination |
| LangGraph | Checkpointed tick graph execution |
| OpenRouter | LLM provider surface, defaulting to Gemini 3.1 Flash Lite |
| Artifact store | Cached JSON, Markdown, PDF, and audit payload files |
| `worldfork` CLI | Operator and AI-agent command surface |

## Core Concepts

### Big Bang

A Big Bang is the root scenario. It stores scenario input, simulation config,
model config, branch policy, initialization output, and the root multiverse.

### Multiverse

A multiverse is one timeline in the Big Bang tree. It can inherit ticks from a
parent, execute new ticks, branch into children, terminate, report, and later
continue with a higher tick limit.

### Tick

A tick is one simulation step. Tick execution records node attempts,
checkpoints, tool calls, graph updates, sociology signals, God-agent review,
and the final tick snapshot.

### Report

A report is a logical slot. A report version is a generated revision containing
structured content and metadata. Markdown and PDF outputs are cached artifacts
rendered from that version.

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
Branch decisions and terminal outcomes
      |
      v
Structured reports and render artifacts
```

## Branching

Branches are constrained by branch policy:

- maximum branch depth
- maximum active multiverses
- maximum branches per tick
- branch score threshold
- idle termination behavior

Child multiverses inherit parent ticks through lineage references and receive
their own executable state after the fork point.

## Jobs And Control

The backend models queued work as jobs. Operators can pause, resume, interrupt,
requeue, claim, or synchronously run jobs through the API and CLI. Agent code
should use `worldfork jobs wait` with a bounded timeout.

## Storage Boundaries

Postgres is canonical for domain state. Artifacts are cached files used for
rendering and audit payloads. A missing rendered artifact should be treated as
regenerable when the corresponding database report version still exists.

## Agent Surface

Agents should start with:

```bash
worldfork agent discover
worldfork status
```

The discovery route is the contract for supported commands, verbosity tiers,
known job types, and recommended flows.
