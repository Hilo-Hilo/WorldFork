# Aggregate Results

Generated: 2026-05-05 01:53 UTC

## Completed Blocks

- P0 static card QA: PASS.
- P0 live setup validation: PASS on isolated `worldfork-icml` stack.
- Source URL fetch verification: 36 ok, 4 blocked/timed out.
- E1 smoke initialization: `resolved_003` completed in 146.20 seconds.
- E2 direct baselines: complete for 24 resolved cards x 2 conditions.
- E3 tick/report smoke: `resolved_003` ran one synchronous tick and generated reports in 408.49 seconds.
- E3 queue smoke: `resolved_004` ran through Celery `worldfork.execute_job`, completed one tick and generated reports after a 219.47 second job wait.

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

## Pending Results

- Full E1 initialization screen on 108 public cards.
- Full E3 WorldFork short resolved no-branch and branching runs.
- E4 long-horizon audit runs.
- E5 social-state/emotion audit.
- Optional social ablation and branch-threshold sweep.
