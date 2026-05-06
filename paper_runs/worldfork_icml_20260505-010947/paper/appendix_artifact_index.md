# Appendix Artifact Index

Generated: 2026-05-06 00:59 UTC

## Core Package

- Public cases: `cases/existing_72/`, `cases/additional_36/`
- Benchmark manifest: `manifests/benchmark_case_manifest.jsonl`
- Run manifest: `manifests/run_manifest.jsonl`
- Direct baseline manifest: `manifests/direct_baseline_manifest.jsonl`
- WorldFork manifest alias: `manifests/worldfork_manifest.jsonl`
- Scoring manifest: `manifests/scoring_manifest.jsonl`
- Long-horizon manifest: `manifests/worldfork_long_horizon_manifest.jsonl`
- Failures and open risks: `failures.md`

## Validation Results

- Card QA: `results/card_quality_report.md`
- Resolution source inventory: `results/resolution_sources.csv`
- Source fetch verification: `results/source_verification.csv`, `results/source_verification.md`
- Forecast scores: `results/forecast_scores.csv`
- DeepSeek v4 Flash forecast scores: `results/forecast_scores_deepseek_v4_flash.csv`
- WorldFork default-route 16-tick scores: `results/worldfork_default_route_16tick_scores.csv`
- WorldFork default-route 35-tick resume scores: `results/worldfork_default_route_35tick_resume_scores.csv`
- WorldFork E3 core-12 same-run comparison scores: `results/e3_core12_comparison_scores.csv`
- WorldFork E3 highest-probability single-path diagnostic scores: `results/worldfork_deadline_aware_branching_core12_most_probable_path_proxy_scores.csv`
- WorldFork deadline-aware branching core-12 posthoc fixed scores: `results/worldfork_deadline_aware_branching_core12_posthoc_fixed_scores.csv`
- E3 direct-prior blend alpha grid and best rows: `results/e3_direct_prior_blend_alpha_grid.csv`, `results/e3_direct_prior_blend_best.csv`
- Bootstrap intervals for available E2/E3 no-branch rows: `results/bootstrap_intervals.json`
- Table 2 runtime/cost summary: `results/table2_runtime_cost_summary.csv`
- E1 automated rubric proxy and preserved coverage table: `results/init_scores.csv`, `results/init_coverage_scores.csv`
- Completion audit and remaining gap map: `results/completion_audit.md`
- Aggregate result summary: `results/aggregate_results.md`

## Live Runtime Evidence

- E1 initializer smoke: `raw/E1_init_smoke/resolved_003/`
- E2 direct baselines: `raw/E2_direct_baselines/direct_predictions.jsonl`
- E3 synchronous tick/report smoke: `raw/E3_tick_smoke/resolved_003/`
- E3 queued tick/report smoke: `raw/E3_queue_smoke/resolved_004/`
- E3 default-route no-branch 16-tick run: `raw/E3_worldfork_default_route_16tick/`
- E3 default-route no-branch 35-tick resume: `raw/E3_worldfork_default_route_35tick_resume/`
- E3 deadline-aware branching core12 source run: `raw/E3_worldfork_deadline_aware_branching_core12/`
- E3 deadline-aware branching core12 highest-probability single-path proxy: `raw/E3_worldfork_deadline_aware_branching_core12_most_probable_path_proxy/`
- E3 deadline-aware branching core12 posthoc fixed predictions: `raw/E3_worldfork_deadline_aware_branching_core12_posthoc_fixed/`
- E4 minimum-6 long-horizon run: `raw/E4_minimum_long_horizon_6/` (live/partial until terminal path-mass, cost, timing, and audit capture)

## Paper Artifacts

- Markdown draft: `paper/paper_draft.md`
- ICML LaTeX source: `paper/latex/main.tex`
- Compiled PDF: `paper/latex/main.pdf`
- Forecast scoring table: `paper/tables/table2_forecast_scoring.csv`
- Protocol Figure 1 asset: `paper/figures/figure1_protocol_flow.svg`
- Remaining table placeholders pending E4/E5 scoring: `paper/tables/table3_audit_metrics_placeholder.csv`, `paper/tables/table4_social_state_placeholder.csv`
- ETA snapshot: `paper/eta.md`

## Anonymity Notes

- The paper source contains no author names, local usernames, non-anonymized repository URLs, or API keys.
- Runtime setup evidence references local ports and generated IDs. These are reproducibility artifacts, not submission-body author identifiers.
- Private resolution data is not included in `cases/` and was used only after direct baseline forecasts were frozen.
