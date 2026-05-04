# WorldFork Runtime Model Module

Use this module when explaining how WorldFork components relate.

## Mental Model

WorldFork is a Monte Carlo tree search over social scenarios:

1. Big Bang initializes the root world.
2. Multiverse `M1` is the root timeline.
3. Ticks advance one timeline through checkpointed stages.
4. Branches create child timelines with inherited history.
5. Endpoint ledgers track terminal hypotheses and evidence.
6. Path mass tracks branch probability across retained timelines.
7. Reports compare terminal or report-ready timelines.

## Runtime Chain

```text
scenario dossier
  -> Big Bang initializer
  -> root multiverse M1 and T0 state
  -> tick runtime graph
  -> cohort/hero decisions
  -> event execution and aggregate event summary
  -> sociology and graph updates
  -> God review JSON tool calls
  -> endpoint ledger update
  -> branch/split/merge/kill/terminate/report-ready tools
  -> final tick snapshot
  -> reports and final distribution
```

## Population Matters

Cohorts carry `represented_population`, `population_share_of_archetype`, and `representation_mode`. Splits must conserve represented population, merges sum represented population, and sociology/report reasoning should not treat every cohort as equal size.

## Stop Conditions

A multiverse can stop because it reaches the configured tick limit, is terminated/frozen by tool call, becomes report-ready, or is otherwise no longer active. Endpoint ledgers may remain unresolved when the tick horizon is insufficient.
