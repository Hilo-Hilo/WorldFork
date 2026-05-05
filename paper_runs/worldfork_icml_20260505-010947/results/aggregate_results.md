# Aggregate Results

Generated: 2026-05-05 01:31 UTC

## Completed Blocks

- P0 static card QA: PASS.
- P0 live setup validation: PASS on isolated `worldfork-icml` stack.
- Source URL fetch verification: 36 ok, 4 blocked/timed out.
- E1 smoke initialization: `resolved_003` completed in 146.20 seconds.
- E2 direct baselines: complete for 24 resolved cards x 2 conditions.

## Forecast Scores

| Condition | n | Mean Brier | Mean Log Score | Mean Unresolved Mass | Calls |
| --- | ---: | ---: | ---: | ---: | ---: |
| direct_llm | 24 | 0.242492 | 0.701698 | 0.000000 | 24 |
| structured_direct_llm | 24 | 0.238363 | 0.677498 | 0.000000 | 24 |

## Runtime Notes

- Direct baseline provider/model: `openai-codex` / `gpt-5.4`.
- Direct baseline mean latency: 6480.5 ms for `direct_llm`, 10046.9 ms for `structured_direct_llm`.
- Direct baseline reported cost is 0.0 because the Codex provider reports no dollar cost estimate in the current adapter.

## Pending Results

- Full E1 initialization screen on 108 public cards.
- E3 WorldFork short resolved no-branch and branching runs.
- E4 long-horizon audit runs.
- E5 social-state/emotion audit.
- Optional social ablation and branch-threshold sweep.
