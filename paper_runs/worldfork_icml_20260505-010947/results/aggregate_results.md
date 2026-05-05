# Aggregate Results

Generated: 2026-05-05 02:58 UTC

## Completed Blocks

- P0 static card QA: PASS.
- P0 live setup validation: PASS on isolated `worldfork-icml` stack.
- Source URL fetch verification: 36 ok, 4 blocked/timed out.
- E1 smoke initialization: `resolved_003` completed in 146.20 seconds.
- E2 direct baselines: complete for 24 resolved cards x 2 conditions.
- E3 tick/report smoke: `resolved_003` ran one synchronous tick and generated reports in 408.49 seconds.
- E3 queue smoke: `resolved_004` ran through Celery `worldfork.execute_job`, completed one tick and generated reports after a 219.47 second job wait.
- E1 queued initializer batch: `resolved_001`, `resolved_002`, `resolved_005`, and `resolved_006` completed as parallel p1 jobs; all captured initialized actors, traits, graphs, sociology baseline, emotion baseline, logs, and workspace.
- E1 queued initializer batch: `resolved_007` through `resolved_014` completed as eight parallel p1 jobs; all captured initialized actors, traits, graphs, sociology baseline, emotion baseline, logs, and workspace.
- E1 add-on initialization screen: all 36 additional public cards now have live initialization evidence.
- E1 full initialization screen: all 108 public cards have live initialization evidence; the remaining 72 existing cards completed as queued p1 jobs in 1796.65 seconds wall time.

## Forecast Scores

| Condition | n | Mean Brier | Mean Log Score | Mean Unresolved Mass | Calls |
| --- | ---: | ---: | ---: | ---: | ---: |
| direct_llm | 24 | 0.242492 | 0.701698 | 0.000000 | 24 |
| structured_direct_llm | 24 | 0.238363 | 0.677498 | 0.000000 | 24 |

## Runtime Notes

- Direct baseline provider/model: `openai-codex` / `gpt-5.4`.
- Direct baseline mean latency: 6480.5 ms for `direct_llm`, 10046.9 ms for `structured_direct_llm`.
- Direct baseline reported cost is 0.0 because the Codex provider reports no dollar cost estimate in the current adapter.
- E3 tick/report smoke used runtime-only Codex routing because the OpenRouter key in the local environment is a placeholder. It produced 14/14 succeeded LLM calls, 174,427 reported tokens, 655.4009 aggregate LLM seconds, one report version, and one final report version.
- The E3 smoke used synchronous `run-until-complete`; Celery queues remained idle, so this result does not test queue-concurrency tuning.
- The queued E3 smoke produced 12/12 succeeded LLM calls, 143,700 reported tokens, 445.3739 aggregate LLM seconds, two report records, and four ledgers. Queue telemetry showed one active p1 task, so single-case queue execution is functional but not inherently parallel within the case.
- The E1 queued initializer batch produced four successful initialization jobs. Actor counts were 11, 7, 9, and 8 respectively. Because jobs were submitted before waiting, the last three had near-zero wait times after the first 148.14 second wait returned.
- The eight-case E1 initializer batch produced eight successful initialization jobs with actor counts 8, 9, 7, 7, 11, 14, 10, and 8 for `resolved_007` through `resolved_014`. Job elapsed times ranged from 112.37 to 188.80 seconds while p1 reported 8 active tasks.
- The remaining 22 additional cards completed as queued p1 jobs in 464.64 seconds wall time. Per-job elapsed times ranged from 109.49 to 463.97 seconds, including queue wait across three waves.
- The existing 72-card E1 initializer batch completed as queued p1 jobs in 1796.65 seconds wall time. The automated coverage table reports 108/108 succeeded initializations, mean actor count 8.76, mean trait count 8.82, mean graph edge count 33.99, and sociology plus emotion baselines present for 108/108 cases.

## Pending Results

- Semantic E1 rubric scoring on the 108 initialized public cards. The current `init_scores.csv` is an automated coverage table, not a human or LLM 0-4 quality rubric.
- Full E3 WorldFork short resolved no-branch and branching runs.
- E4 long-horizon audit runs.
- E5 social-state/emotion audit.
- Optional social ablation and branch-threshold sweep.
