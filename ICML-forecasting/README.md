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

## Agent handoff files added

This bundle now includes a full-cycle execution plan for running the WorldFork ICML forecasting-paper benchmark and writing the paper:

- `AGENT_HANDOFF_FULL_CYCLE_PLAN.md`: detailed coordinator/统筹 plan, setup, exact benchmark matrix, commands, scoring, and paper-writing instructions.
- `AGENT_BENCHMARK_RUN_MATRIX.json`: machine-readable case groups, experiment definitions, branch policies, and metrics.
- `AGENT_SCORING_RUBRIC.csv`: rubric rows for initialization, audit, social-state, emotion-observability, and forecast metrics.
- `AGENT_PAPER_DRAFT_SKELETON.md`: paper skeleton with the recommended claim, section outline, and result placeholders.

Keep `worldfork_additional_36_private_eval.jsonl` hidden from forecast-producing models until after predictions are saved.
