# Aggregate Results

Generated: 2026-05-06 00:59 UTC

## Completed Blocks

- P0 static card QA: PASS.
- P0 live setup validation: PASS on isolated `worldfork-icml` stack.
- Source URL fetch verification: 39 ok, 1 gated Reuters URL with a separate primary court-source row already ok for the same case.
- E1 smoke initialization: `resolved_003` completed in 146.20 seconds.
- E2 direct baselines: complete for 24 resolved cards x 2 conditions.
- E3 tick/report smoke: `resolved_003` ran one synchronous tick and generated reports in 408.49 seconds.
- E3 queue smoke: `resolved_004` ran through Celery `worldfork.execute_job`, completed one tick and generated reports after a 219.47 second job wait.
- E3 scored no-branch validation: after fixing audited Codex assistant-history serialization, `resolved_001` / `worldfork_no_branch_short` completed 8 queued ticks and produced a scoreable path-mass forecast.
- E3 scored branching validation: `resolved_001` / `worldfork_branching_short` completed with four multiverses, 27 tick snapshots, and a scoreable path-mass forecast after a 2131.68 second queued run wait.
- E3 batched no-branch run: `resolved_003`, `resolved_005`, and `resolved_007` initialized and ran concurrently through p1. All three completed 8 ticks; run waits were 648.90, 709.14, and 754.11 seconds after a shared batched submission.
- E1 queued initializer batch: `resolved_001`, `resolved_002`, `resolved_005`, and `resolved_006` completed as parallel p1 jobs; all captured initialized actors, traits, graphs, sociology baseline, emotion baseline, logs, and workspace.
- E1 queued initializer batch: `resolved_007` through `resolved_014` completed as eight parallel p1 jobs; all captured initialized actors, traits, graphs, sociology baseline, emotion baseline, logs, and workspace.
- E1 add-on initialization screen: all 36 additional public cards now have live initialization evidence.
- E1 full initialization screen: all 108 public cards have live initialization evidence; the remaining 72 existing cards completed as queued p1 jobs in 1796.65 seconds wall time.
- E1 automated initialization rubric proxy: `results/init_scores.csv` now includes 10 rubric dimensions. Mean score is 3.884, 108/108 rows pass, and 0 critical failures are flagged. This is an evidence-grounded structural proxy, not a human semantic adjudication.
- E3 deadline-aware branching core-12 completed at the 16-tick cap. The canonical E3 score view is branching path-mass aggregation across explicit yes/no candidate endpoints.
- E3 same-run single-path proxy: for each core-12 card, selected the multiverse with maximum stored path probability and scored its terminal candidate ledger. This produced 12 score rows with zero unresolved mass and is kept as a diagnostic only.
- E3 same-run branching aggregate: scored candidate endpoint path mass across all multiverses from the same core-12 runs, also with zero unresolved mass.
- E3 direct-prior blend: reused existing E2 GPT-5.4 and DeepSeek v4 Flash direct-call predictions with the E3 branching aggregate; no new LLM calls or simulations were launched.
- Forecast bootstrap intervals are available in `results/bootstrap_intervals.json` for the completed direct, structured-direct, 16-tick no-branch, and 35-tick no-branch rows. They cover the available E2/E3 no-branch evidence only; E3 branching core-12 has a separate score file and E4/E5 rows remain pending.

## Forecast Scores

| Condition | n | Mean Brier | Mean Log Score | Mean Unresolved Mass | Calls |
| --- | ---: | ---: | ---: | ---: | ---: |
| direct_llm | 24 | 0.242492 | 0.701698 | 0.000000 | 24 |
| structured_direct_llm | 24 | 0.238363 | 0.677498 | 0.000000 | 24 |
| direct_llm_core12 | 12 | 0.219867 | 0.673259 | 0.000000 | 12 |
| structured_direct_llm_core12 | 12 | 0.215517 | 0.637058 | 0.000000 | 12 |
| direct_llm_deepseek_v4_flash_core12 | 12 | 0.211408 | 0.628736 | 0.000000 | 12 |
| structured_direct_llm_deepseek_v4_flash_core12 | 12 | 0.194950 | 0.591768 | 0.000000 | 12 |
| worldfork_branching_aggregate_core12 | 12 | 0.224597 | 0.613304 | 0.000000 | captured in artifacts |
| worldfork_plus_structured_direct_llm_deepseek_v4_flash_equal_blend_core12 | 12 | 0.190560 | 0.553376 | 0.000000 | reused artifacts |
| worldfork_plus_structured_direct_llm_deepseek_v4_flash_best_brier_in_sample_core12 | 12 | 0.187705 | 0.555867 | 0.000000 | diagnostic tuned alpha |
| worldfork_plus_structured_direct_llm_deepseek_v4_flash_leave_one_out_brier_tuned_core12 | 12 | 0.232795 | 0.672304 | 0.000000 | leave-one-out |
| worldfork_single_path_proxy_core12_diagnostic | 12 | 0.416667 | 1.924683 | 0.000000 | captured in artifacts |
| worldfork_no_branch_short_16tick | 24 | 0.425347 | 1.686438 | 0.923611 | captured in artifacts |
| worldfork_no_branch_short_35tick_resume | 24 | 0.425347 | 1.686438 | 0.923611 | captured in artifacts |
| worldfork_no_branch_short_smoke | 4 | 0.428713 | 1.637770 | 1.000000 | 4 |
| worldfork_branching_short | 1 | 1.000000 | 4.605170 | 1.000000 | 1 |

## Runtime Notes

- Direct baseline provider/model: `openai-codex` / `gpt-5.4`.
- Direct baseline mean latency: 6480.5 ms for `direct_llm`, 10046.9 ms for `structured_direct_llm`.
- Direct baseline reported cost is 0.0 because the Codex provider reports no dollar cost estimate in the current adapter.
- E3 tick/report smoke used runtime-only Codex routing from the earlier smoke configuration. It produced 14/14 succeeded LLM calls, 174,427 reported tokens, 655.4009 aggregate LLM seconds, one report version, and one final report version. Treat these as Codex-only smoke/ablation rows; future default ICML runtime rows should use OpenRouter `deepseek/deepseek-v4-flash` for `cohort_agent` and `hero_agent`.
- The E3 smoke used synchronous `run-until-complete`; Celery queues remained idle, so this result does not test queue-concurrency tuning.
- The queued E3 smoke produced 12/12 succeeded LLM calls, 143,700 reported tokens, 445.3739 aggregate LLM seconds, two report records, and four ledgers. Queue telemetry showed one active p1 task, so single-case queue execution is functional but not inherently parallel within the case.
- The E1 queued initializer batch produced four successful initialization jobs. Actor counts were 11, 7, 9, and 8 respectively. Because jobs were submitted before waiting, the last three had near-zero wait times after the first 148.14 second wait returned.
- The eight-case E1 initializer batch produced eight successful initialization jobs with actor counts 8, 9, 7, 7, 11, 14, 10, and 8 for `resolved_007` through `resolved_014`. Job elapsed times ranged from 112.37 to 188.80 seconds while p1 reported 8 active tasks.
- The remaining 22 additional cards completed as queued p1 jobs in 464.64 seconds wall time. Per-job elapsed times ranged from 109.49 to 463.97 seconds, including queue wait across three waves.
- The existing 72-card E1 initializer batch completed as queued p1 jobs in 1796.65 seconds wall time. The automated coverage table reports 108/108 succeeded initializations, mean actor count 8.76, mean trait count 8.82, mean graph edge count 33.99, and sociology plus emotion baselines present for 108/108 cases.
- The first E3 runner validation on `resolved_001` / `worldfork_no_branch_short` initialized successfully but did not reach scoreable path-mass artifacts. The queued run job entered a God-review Codex 400 retry loop, was marked `interrupt_requested`, and the isolated p1 worker was restarted. The root cause was audited Codex assistant-history serialization: assistant turns were sent as assistant messages containing `input_text`, which the Responses endpoint rejected. After converting assistant history into user-context input, a fresh retry Big Bang (`e8bc081f-804f-4584-887d-625adae385e7`) completed 8 ticks with run job `7b85f6d2-7b41-4852-86af-28037552acec`.
- Posthoc big-bang endpoint-ledger aggregation now populates endpoint path-mass rows for the scored no-branch validations, producing normalized yes/no forecasts of 0.0, 0.5, 0.333333, and 0.142857 across the four completed cards. The unresolved mass is still 1.0 because the captured endpoint statuses remain `insufficient_ticks`, so these are pipeline-validating smoke scores rather than evidence of final WorldFork accuracy.
- The scored branching validation extracted a normalized yes/no forecast of 0.0 with unresolved mass 1.0. It exercised the branch policy (`max_active_multiverses=4`) and expanded one nominal 8-tick case into 27 tick snapshots, so full branching E3 runtime is materially higher than the no-branch ETA unless the branch policy or tick cap is tightened.
- The batched E3 runner confirms Celery can reduce wall time across independent no-branch cases: three 8-tick run jobs overlapped on p1 instead of running serially. The score rows are still weak forecast evidence because the captured endpoint statuses are `insufficient_ticks`; the next quality fix is more ticks/longer horizon under the default DeepSeek cohort/hero route.
- The main E3 WorldFork comparison should use the core-12 branching path-mass aggregate, not the highest-probability single-path proxy. The branch aggregate has mean Brier 0.224597 and mean log score 0.613304 with zero unresolved mass. The best fixed sensitivity row is the 50/50 DeepSeek structured direct prior plus branch aggregate blend at Brier 0.190560 and log score 0.553376. The best in-sample tuned blend reaches Brier 0.187705, but leave-one-out tuning degrades to Brier 0.232795, so tuned alpha is diagnostic only.
- The 35-tick resume exposed two recoverable runtime issues rather than a Postgres capacity failure: one T35 run job went stale after the multiverse completed and one hit the worker soft time limit before final closure. Both were recovered from live Big Bang state, and `resolved_020` required a second terminal probe with `max_total_ticks=2` to generate a final report. Queue telemetry and pg_stat_activity stayed below pressure limits after the worker pool tuning.
- Endpoint-ledger resolution is now the operational stopping condition. Tick counts such as 16, 32, and 35 should be treated as caps: stop early when a ledger resolves naturally, and resume the existing Big Bang only when unresolved path mass remains material.

## Pending Results

- Human or LLM semantic adjudication for E1, if the final paper needs claims beyond the automated structural rubric proxy now recorded in `init_scores.csv`.
- Full 24-card E3 default-route branching remains optional; the deadline-aware branching core-12 fallback and direct-prior blend sensitivity rows are complete with posthoc fixed ledger scoring.
- E4 long-horizon audit runs.
- E5 social-state/emotion audit.
- Optional social ablation and branch-threshold sweep.
