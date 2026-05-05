# Failures and Open Risks

## Current Blockers

- The default host ports `8003/5433/6379` were already occupied by a separate `main-work` WorldFork stack. This run uses an isolated `worldfork-icml` compose project on `18045/15445/16445`.
- Existing live smoke rows used a runtime-only model-routing override that sent all job types to `openai-codex` / `gpt-5.4`. These rows are valid smoke/ablation evidence only. Future default ICML E3/E4/E5 runs should route `cohort_agent` and `hero_agent` to OpenRouter `deepseek/deepseek-v4-flash`, with governance/report routes on a strong configured model, and should capture `worldfork settings llm` before and after each batch.
- Four resolution-source URLs did not fetch cleanly in automated verification: Oscars returned 403, Reuters returned 401, and two Paramount IR URLs timed out. These need manual/source-redundancy review before final card QA is treated as complete.

## Not Yet Complete

- Semantic E1 rubric scoring on 108 initialized public cards. Runtime initialization coverage is complete; the current `init_scores.csv` is an automated coverage table, not a final 0-4 quality rubric.
- E3 full WorldFork short resolved no-branch and branching runs. One synchronous tick/report smoke completed for `resolved_003` in 408.49 seconds, one queued tick/report smoke completed for `resolved_004` after a 219.47 second job wait, four scored no-branch validations completed 8 queued ticks each, and one scored branching validation completed 27 tick snapshots across four multiverses for `resolved_001`; this is still not full coverage.
- Current E3 score rows are extraction-path smoke evidence, not strong forecast evidence. Posthoc big-bang endpoint-ledger aggregation now populates endpoint path-mass rows, but all captured endpoint statuses remain `insufficient_ticks`, so unresolved mass is still 1.0. The next quality fix is longer horizon/more ticks under the default DeepSeek cohort/hero route.
- Current branching policy expands runtime substantially: the first scored branching validation took a 2131.68 second queued run wait and 27 tick snapshots for one card, despite `max_total_ticks=8`. Full branching coverage should either budget for this expansion or run a tighter branch/tick policy first.
- E4 long-horizon audit runs.
- E5 social-state/emotion audit.
- Optional social ablation and branch-threshold sweep.

## Fixed During This Run

- Parameterized compose host ports with default-preserving `WORLDFORK_API_PORT`, `WORLDFORK_POSTGRES_PORT`, and `WORLDFORK_REDIS_PORT`.
- Removed two lingering hidden-file legacy provider example lines from `.env.example`.
- Fixed `/api/jobs/workers` and `/api/jobs/queues` observability by importing the Celery app module instead of the package-exported Celery object.
- Fixed the E3 God-review Codex retry blocker by serializing audited assistant-history turns as user-context input for the Responses endpoint. The original `resolved_001` no-branch run was interrupted after repeated 400 responses; a fresh retry completed 8 ticks and produced scoreable path-mass artifacts under `raw/E3_worldfork_short_retry_a4ae2ca/worldfork_no_branch_short/resolved_001/`.
- Added and validated a batched E3 runner. A three-case no-branch batch overlapped three p1 jobs and completed all three 8-tick runs, confirming queueing improves throughput across independent cases.
- Added a posthoc E3 endpoint-ledger refresh helper that aggregates endpoint ledgers across Big Bang multiverses before forecast scoring.
- Refreshed the OpenRouter provider registry after route edits and captured a passing `worldfork settings provider-test openrouter` artifact for the default DeepSeek cohort/hero route.
