# WorldFork Accuracy Improvement Advice

Generated: 2026-04-30 13:03:58 PDT

Primary log: `REPRODUCIBILITY_LOG.md`

## Current Evidence Base

- Completed records: 109
- Overall top-outcome match rate: 0.8073
- Overall mean TVD: 0.3639
- Gemini-only completed runs: 109

## Recommendations

1. Treat endpoint priors as first-class scenario fields. Prompt guidance already mentions endpoint priors, so the novel step is durable schema/state: explicit authority, switching costs, coercive capacity, legal veto points, and credible remaining alternatives that survive across init, ticks, branching, and reports.

2. Add an endpoint coverage ledger. Each initializer terminal option should remain a tracked object marked `active`, `weakened`, `eliminated`, or `realized`, with tick/event/God-review evidence for every transition.

3. Separate process states from endpoint states with a report gate. Final reports should emit `unresolved`, `process_only`, `terminal_claimed`, or `terminal_verified`, and should not silently map committees, pauses, audits, negotiations, or nonterminal litigation to final outcomes.

4. Weight endpoint probabilities by authority proximity, not branch score alone. A late event from a powerless cohort should count less than a decision event from an actor with encoded veto/control authority.

5. Add a contradiction pass before endpoint selection. The report agent should ask what evidence would make the leading endpoint wrong and whether that evidence is still live in the trace.

6. Use outcome-label-aware scoring. Current reports already contain endpoint prose and outcome conclusions, but benchmark scoring still falls back to markdown token overlap. Add stable machine labels, probabilities, supporting tick IDs, blockers, and remaining uncertainty.

7. Add branch-drain and partial-report semantics. The runtime should either drain active branches before final reports or produce an explicit partial/nonterminal report that lists active multiverses. The local harness now has a retry path, but the backend should expose this as first-class operational behavior.

8. Tune default evaluation settings toward `short_high_detail_default`. It was the best cost/reliability compromise in this run; reserve `long_high_detail_strict` for smaller pilots with branch-drain safeguards.

## Potential Paths To Explore

- Compare initializer-agent runs against manual-cohort runs on a 5-event pilot to quantify whether the initializer preserves hidden endpoint priors or adds generic sociology noise.
- Add an endpoint histogram object to report content with stable labels, probability, supporting ticks, and a short causal rationale.
- Add a benchmark mode that accepts hidden expected distributions and emits evaluation-ready run metadata without exposing hidden outcomes to the simulation prompt.
- Add synthetic calibration tests: authority-dominant, switching-cost-dominant, coalition-fracture-dominant, and process-only scenarios. Assert structured endpoint state and probabilities, not prompt substrings.
