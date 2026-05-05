# Appendix Artifact Index

Generated: 2026-05-05 01:53 UTC

## Core Package

- Public cases: `cases/existing_72/`, `cases/additional_36/`
- Benchmark manifest: `manifests/benchmark_case_manifest.jsonl`
- Run manifest: `manifests/run_manifest.jsonl`
- Direct baseline manifest: `manifests/direct_baseline_manifest.jsonl`
- Failures and open risks: `failures.md`

## Validation Results

- Card QA: `results/card_quality_report.md`
- Resolution source inventory: `results/resolution_sources.csv`
- Source fetch verification: `results/source_verification.csv`, `results/source_verification.md`
- Forecast scores: `results/forecast_scores.csv`
- Aggregate result summary: `results/aggregate_results.md`

## Live Runtime Evidence

- E1 initializer smoke: `raw/E1_init_smoke/resolved_003/`
- E2 direct baselines: `raw/E2_direct_baselines/direct_predictions.jsonl`
- E3 synchronous tick/report smoke: `raw/E3_tick_smoke/resolved_003/`
- E3 queued tick/report smoke: `raw/E3_queue_smoke/resolved_004/`

## Paper Artifacts

- Markdown draft: `paper/paper_draft.md`
- ICML LaTeX source: `paper/latex/main.tex`
- Compiled PDF: `paper/latex/main.pdf`
- Tables: `paper/tables/`
- ETA snapshot: `paper/eta.md`

## Anonymity Notes

- The paper source contains no author names, local usernames, non-anonymized repository URLs, or API keys.
- Runtime setup evidence references local ports and generated IDs. These are reproducibility artifacts, not submission-body author identifiers.
- Private resolution data is not included in `cases/` and was used only after direct baseline forecasts were frozen.
