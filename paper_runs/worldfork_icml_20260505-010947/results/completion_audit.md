# ICML Completion Audit

Generated: 2026-05-06 00:59 UTC

## Stop Policy

Endpoint-ledger resolution, not a fixed tick number, is the stopping condition. Tick caps such as 16, 32, or 35 are maximum budgets. If the endpoint ledger naturally resolves with supported path mass before the cap, stop and freeze the forecast/report artifacts. If unresolved mass remains high, resume the existing Big Bang where possible rather than reinitializing. For the main E3 core-12 comparison, use branching path-mass aggregation across explicit yes/no candidate endpoints; keep the highest-probability single-path view as a diagnostic only.

## Requirement Status

| Block | Required scope | Current evidence | Status | Next action |
| --- | --- | --- | --- | --- |
| E0 card QA | 108 public cards plus 36 private eval rows | `results/card_quality_report.md`; `results/source_verification.*`; 39/40 source URLs verified ok after browser follow-up; the remaining Reuters URL is gated, but the same case has a separate primary court-source row marked ok | Mostly complete | Keep one gated Reuters URL as a limitation; no forecast-producing model used private resolution data |
| E1 initialization screen | 108 live `worldfork init` rows, no ticks | `results/init_scores.csv`; `results/init_scores_summary.json`; 108/108 succeeded; automated evidence-grounded 0-4 rubric proxy mean 3.884, 108/108 pass, 0 critical failures | Complete for automated scoring | Label as structural proxy, not human semantic adjudication |
| E2 direct baselines | 24 resolved cards x direct and structured-direct | `results/forecast_scores.csv`; `results/forecast_scores_deepseek_v4_flash.csv`; GPT-5.4 and DeepSeek v4 Flash direct/structured calls are scored | Complete | Use bootstrap intervals for uncertainty, avoid strong significance claims |
| E3 single-path proxy | Core-12 fallback | `raw/E3_worldfork_deadline_aware_branching_core12_most_probable_path_proxy/worldfork_predictions.jsonl`; `results/worldfork_deadline_aware_branching_core12_most_probable_path_proxy_scores.csv`; selects highest-probability multiverse per card and scores its terminal ledger | Diagnostic complete | Do not use as the main E3 row; it is a same-run ablation showing that discarding branch mass is harmful |
| E3 branching WorldFork | 24 resolved cards, or core-12 fallback | `raw/E3_worldfork_deadline_aware_branching_core12_posthoc_fixed/worldfork_predictions.jsonl`; `results/worldfork_deadline_aware_branching_core12_posthoc_fixed_scores.csv`; 12/12 rows scored from branching path-mass aggregate over the same runs | Core-12 fallback complete | Treat full 24-card branching as optional extension; use core-12 as the fastest defensible branching evidence |
| E3 direct-prior blend | Existing E2 direct calls paired with E3 core-12 branching aggregate | `results/e3_direct_prior_blend_alpha_grid.csv`; `results/e3_direct_prior_blend_best.csv`; no new LLM calls or simulations | Complete on core-12 | Use fixed/equal blend as a sensitivity result; treat in-sample tuned alpha as diagnostic because leave-one-out is worse |
| E4 long-horizon audit | 18 cases, or minimum-6 fallback | No E4 runtime artifacts or `audit_scores.csv` | Pending | Run minimum-6 or full 18 with branching long-horizon policy; score audit rubrics |
| E5 social-state/emotion audit | Score long-horizon artifacts | No `social_state_scores.csv` | Pending | Score after E4 artifacts exist; do not claim validated psychology |
| Bootstrap intervals | Paired bootstrap over resolved cards | `results/bootstrap_intervals.json` | Complete for available E2/E3 no-branch rows | Extend to branching core-12 only if comparing over matched IDs |
| Paper tables | Forecast, audit, social-state, cost/runtime | Table 2 populated with available scores and runtime/cost fields; Tables 3/4 remain placeholders | Partial | Fill Tables 3/4 only after E4/E5 scoring |
| Figures | Protocol diagram and optional cost/audit figures | `paper/figures/figure1_protocol_flow.svg`; LaTeX boxed protocol diagram | Partial | Add score/cost plot after corrected E3 and E4/E5 final rows |
| Anonymity/reproducibility | Anonymous paper plus artifact index | `paper/appendix_artifact_index.md`; required manifest aliases now exist: `manifests/worldfork_manifest.jsonl`, `manifests/scoring_manifest.jsonl`, `manifests/worldfork_long_horizon_manifest.jsonl`; no paper-body secrets/author IDs detected | Mostly complete | Keep raw local paths out of submission body |

## Current Forecast Result

The main E3 result is now the core-12 branching path-mass aggregate: mean Brier 0.224597, mean log score 0.613304, and mean unresolved mass 0.0. On the same 12 IDs, GPT-5.4 direct is Brier 0.219867 / log 0.673259 and GPT-5.4 structured direct is Brier 0.215517 / log 0.637058. DeepSeek v4 Flash direct is Brier 0.211408 / log 0.628736, and DeepSeek structured direct is Brier 0.194950 / log 0.591768. A 50/50 blend of the DeepSeek structured direct prior with the E3 branching path-mass aggregate scores Brier 0.190560 / log 0.553376. The best in-sample Brier blend for that same pair uses alpha=0.70 and scores Brier 0.187705 / log 0.555867, but leave-one-out alpha tuning scores Brier 0.232795 / log 0.672304, so tuned alpha should be labeled diagnostic rather than claimed as robust.

## Fastest Non-Duplicative Path

1. Use E3 branching path-mass aggregation as the canonical WorldFork row.
2. Use direct-prior blends as sensitivity rows over the already completed E2 calls; prefer fixed/equal blend unless a larger validation set supports tuning alpha.
3. Keep the highest-probability single-path proxy as a diagnostic ablation only.
4. Only run full 24-card E3 branching if extra wall time is available.
5. Skip E4/E5 unless the paper needs an auditability-only section; they are not required for the branching-vs-direct forecasting claim.
