# Aggregate Results

Generated: 2026-05-05 04:57 UTC

## Completed Blocks

- P0 static card QA: PASS.
- P0 live setup validation: PASS on isolated `worldfork-icml` stack.
- Source URL fetch verification: 36 ok, 4 blocked/timed out.
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

## Forecast Scores

| Condition | n | Mean Brier | Mean Log Score | Mean Unresolved Mass | Calls |
| --- | ---: | ---: | ---: | ---: | ---: |
| direct_llm | 24 | 0.242492 | 0.701698 | 0.000000 | 24 |
| structured_direct_llm | 24 | 0.238363 | 0.677498 | 0.000000 | 24 |
| worldfork_no_branch_short | 4 | 0.428713 | 1.637770 | 1.000000 | 4 |
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

## Pending Results

- Semantic E1 rubric scoring on the 108 initialized public cards. The current `init_scores.csv` is an automated coverage table, not a human or LLM 0-4 quality rubric.
- Full E3 WorldFork short resolved no-branch and branching runs.
- E4 long-horizon audit runs.
- E5 social-state/emotion audit.
- Optional social ablation and branch-threshold sweep.
