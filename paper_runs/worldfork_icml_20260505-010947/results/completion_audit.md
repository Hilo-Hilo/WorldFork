# ICML Completion Audit

Generated: 2026-05-05 12:20 UTC

## Stop Policy

Endpoint-ledger resolution, not a fixed tick number, is the stopping condition. Tick caps such as 16, 32, or 35 are maximum budgets. If the endpoint ledger naturally resolves with supported path mass before the cap, stop and freeze the forecast/report artifacts. If unresolved mass remains high, resume the existing Big Bang where possible rather than reinitializing. The 16-to-35 no-branch resume is the current example: it reused existing Big Bangs, but did not change frozen path-mass forecasts, so further no-branch ticks are not a good use of runtime.

## Requirement Status

| Block | Required scope | Current evidence | Status | Next action |
| --- | --- | --- | --- | --- |
| E0 card QA | 108 public cards plus 36 private eval rows | `results/card_quality_report.md`; `results/source_verification.*`; 36/40 source fetches ok | Mostly complete | Manual follow-up for 4 blocked/timed-out source URLs |
| E1 initialization screen | 108 live `worldfork init` rows, no ticks | `results/init_scores.csv`; `results/init_scores_summary.json`; 108/108 succeeded with graph/sociology/emotion coverage | Runtime complete; semantic rubric pending | Score semantic 0-4 initialization rubric if final paper needs quality claims |
| E2 direct baselines | 24 resolved cards x direct and structured-direct | `results/forecast_scores.csv`; 48 forecast rows | Complete | Use bootstrap intervals for uncertainty, avoid strong significance claims |
| E3 no-branch WorldFork | 24 resolved cards | `results/worldfork_default_route_16tick_scores.csv`; `results/worldfork_default_route_35tick_resume_scores.csv`; default DeepSeek cohort/hero route | Complete for no-branch evidence | Stop extending no-branch unless endpoint-ledger code changes |
| E3 branching WorldFork | 24 resolved cards, or core-12 fallback | One legacy Codex branching smoke row only | Pending | Run default-route branching, preferably core-12 first for fastest defensible fallback |
| E4 long-horizon audit | 18 cases, or minimum-6 fallback | No E4 runtime artifacts or `audit_scores.csv` | Pending | Run minimum-6 or full 18 with branching long-horizon policy; score audit rubrics |
| E5 social-state/emotion audit | Score long-horizon artifacts | No `social_state_scores.csv` | Pending | Score after E4 artifacts exist; do not claim validated psychology |
| Bootstrap intervals | Paired bootstrap over resolved cards | `results/bootstrap_intervals.json` | Complete for available E2/E3 no-branch rows | Extend after branching rows exist |
| Paper tables | Forecast, audit, social-state, cost/runtime | Table 2 populated with available scores and runtime/cost fields; Tables 3/4 remain placeholders | Partial | Fill Tables 3/4 only after E4/E5 scoring |
| Figures | Protocol diagram and optional cost/audit figures | LaTeX boxed protocol diagram; `paper/figures/` empty | Partial | Add polished Figure 1 asset and any score/cost plot after final rows |
| Anonymity/reproducibility | Anonymous paper plus artifact index | `paper/appendix_artifact_index.md`; no paper-body secrets/author IDs detected | Mostly complete | Keep raw local paths out of submission body |

## Current Forecast Result

The available scored result is a negative no-branch endpoint-closure result, not an accuracy win. Direct and structured-direct baselines outperform the current no-branch path-mass extraction on Brier/log score, while WorldFork exposes high unresolved mass: mean unresolved mass 0.923611 at both 16 and 35 ticks. This should be framed as auditability and failure visibility unless branching/E4 results support stronger claims.

## Fastest Non-Duplicative Path

1. Do not spend more runtime on the completed no-branch 16/35 Big Bangs unless endpoint-ledger logic changes.
2. Run E3 default-route branching on the core-12 fallback before the full 24, because branching is the missing forecast comparison and the one-case smoke shows it is the runtime multiplier.
3. In parallel only if capacity/provider budget allows, run E4 minimum-6 long-horizon branching. Otherwise run E3 branching first, then E4 minimum-6.
4. Score E5 from the E4 artifacts; do not start a separate run for E5 unless an explicit ablation is added safely.
5. Update Tables 3/4 and regenerate the PDF only after E4/E5 artifacts exist.
