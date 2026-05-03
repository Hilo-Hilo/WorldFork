# WorldFork Project Orientation

Use this reference when the user is new to WorldFork or when setup work would be
easier if the agent first explains the product.

## One-Sentence Model

WorldFork is a Monte Carlo tree search of the real world: a backend-first,
CLI-first system for turning one scenario into a tree of auditable simulated
timelines, then comparing those timelines through structured reports.

## Core Terms

- Big Bang: the root scenario and workspace. It stores scenario input,
  initialization output, config, model routing, branch policy, and the root
  multiverse.
- Multiverse: one timeline branch. It has state, ticks, lineage, reports, and
  a terminal or active lifecycle.
- Tick: one checkpointed runtime step. It records actor decisions, events,
  sociology and graph changes, God-agent review, endpoint-ledger updates, logs,
  tool calls, and the final tick snapshot.
- Branch: an alternate timeline created from a decision point, God-agent review,
  or human intervention. Child timelines inherit history up to the fork point.
- Endpoint ledger: a structured record of terminal hypotheses, branch/path
  mass, and later post-simulation endpoint evaluations.
- Report: a structured database record first. Markdown and PDF are renders
  generated from report versions only when requested.

## Repo Map

- `backend/app`: FastAPI app, domain services, simulation runtime, workers,
  LLM routing, reports, jobs, and storage.
- `cli`: the `worldfork` command used by humans and agents.
- `skills`: installable agent guidance for setup, operation, reporting, and
  full validation.
- `docs`: setup, CLI, architecture, demos, testing, reporting, and release docs.
- `examples`: runnable scenario dossiers.
- `source_of_truth`: schemas, prompt policy, and simulation vocabulary.
- `scripts`: maintained test, live smoke, and demo harnesses.
- `infra`: Dockerfiles, Postgres init, and migration infrastructure.

## First-User Explanation

Keep the user-facing explanation concrete:

1. The user provides a scenario dossier.
2. WorldFork initializes actors, cohorts, relationships, stakes, and baseline
   sociology.
3. The runtime advances timelines in ticks.
4. Important decisions or high-branch-score situations can create child
   timelines.
5. Operators inspect live state through `worldfork watch`, `runs`, `jobs`,
   `logs`, `ledgers`, and `reports`.
6. Reports compare what happened across timelines without requiring local
   Markdown or PDF files to be stored permanently.

During setup, keep the user oriented and welcome. Explain each phase before
running it, interpret the output afterwards, and use `worldfork setup` as the
provider-choice map before asking which LLM providers they want to configure.
For Atlas, explain that the recommended pattern is cheap/fast models for
high-volume cohort and timeline work plus stronger models for initialization,
God review, endpoint ledgers, and reports.

## What Setup Should Prove

A successful first setup proves:

- the local Docker stack is healthy;
- the CLI can reach the intended backend;
- the OpenRouter and OpenAI Codex routes are configured as intended;
- `worldfork agent discover` describes the live API surface;
- `worldfork setup` exposes provider options and the recommended Atlas routing profile;
- a Big Bang can initialize from `examples/test-big-bang.md`;
- the user knows the difference between a setup smoke and the full Atlas demo.

Do not imply that `worldfork init` alone ran a full simulation. It validates
initialization and workspace creation. Use `worldfork demo atlas` for a full
tick, branch, endpoint-ledger, and report demonstration.
