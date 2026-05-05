# Failures and Open Risks

## Current Blockers

- The default host ports `8003/5433/6379` were already occupied by a separate `main-work` WorldFork stack. This run uses an isolated `worldfork-icml` compose project on `18045/15445/16445`.
- Existing live smoke rows used a runtime-only model-routing override that sent all job types to `openai-codex` / `gpt-5.4`. These rows are valid smoke/ablation evidence only. Future default ICML E3/E4/E5 runs should route `cohort_agent` and `hero_agent` to OpenRouter `deepseek/deepseek-v4-flash`, with governance/report routes on a strong configured model, and should capture `worldfork settings llm` before and after each batch.
- Four resolution-source URLs did not fetch cleanly in automated verification: Oscars returned 403, Reuters returned 401, and two Paramount IR URLs timed out. These need manual/source-redundancy review before final card QA is treated as complete.
- The 35-tick no-branch resume did not change any frozen path-mass forecast row relative to the 16-tick source predictions. Treat this as a diagnostic/negative result for endpoint-ledger closure, not as evidence that more ticks improve accuracy.
- Tick caps are not stopping targets. If a ledger resolves naturally before 16, 32, or 35 ticks, freeze the row; if unresolved mass remains high, resume the existing Big Bang instead of reinitializing. The completed 16-to-35 no-branch resume shows that more ticks can add runtime/report evidence without changing the extracted forecast distribution.

## Not Yet Complete

- Semantic E1 rubric scoring on 108 initialized public cards. Runtime initialization coverage is complete; the current `init_scores.csv` is an automated coverage table, not a final 0-4 quality rubric.
- E3 default-route no-branch full coverage is complete at 16 ticks and through a 35-tick resume. It remains weak forecast evidence because the 35-tick extracted forecasts are unchanged from 16 ticks and mean unresolved mass remains 0.923611.
- E3 default-route branching full coverage is not complete.
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
- Completed and scored 24-row default-route no-branch E3 outputs at both the 16-tick cap and 35-tick resume cap. The 35-tick resume reused existing Big Bangs rather than reinitializing them, recovered stale/soft-limit job records from live state, and captured endpoint-ledger/path-mass artifacts for all rows.
