# Failures and Open Risks

## Current Blockers

- The default host ports `8003/5433/6379` were already occupied by a separate `main-work` WorldFork stack. This run uses an isolated `worldfork-icml` compose project on `18045/15445/16445`.
- The local OpenRouter environment value is a placeholder, so live smoke runs currently use a runtime-only model-routing override that sends all job types to `openai-codex` / `gpt-5.4`. This is valid for smoke evidence but must be reported as the runtime condition for any scored live run until a real OpenRouter key is configured.
- Four resolution-source URLs did not fetch cleanly in automated verification: Oscars returned 403, Reuters returned 401, and two Paramount IR URLs timed out. These need manual/source-redundancy review before final card QA is treated as complete.

## Not Yet Complete

- Semantic E1 rubric scoring on 108 initialized public cards. Runtime initialization coverage is complete; the current `init_scores.csv` is an automated coverage table, not a final 0-4 quality rubric.
- E3 full WorldFork short resolved no-branch and branching runs. One synchronous tick/report smoke completed for `resolved_003` in 408.49 seconds, one queued tick/report smoke completed for `resolved_004` after a 219.47 second job wait, and one scored no-branch validation completed 8 queued ticks for `resolved_001`; this is still not full coverage.
- E4 long-horizon audit runs.
- E5 social-state/emotion audit.
- Optional social ablation and branch-threshold sweep.

## Fixed During This Run

- Parameterized compose host ports with default-preserving `WORLDFORK_API_PORT`, `WORLDFORK_POSTGRES_PORT`, and `WORLDFORK_REDIS_PORT`.
- Removed two lingering hidden-file legacy provider example lines from `.env.example`.
- Fixed `/api/jobs/workers` and `/api/jobs/queues` observability by importing the Celery app module instead of the package-exported Celery object.
- Fixed the E3 God-review Codex retry blocker by serializing audited assistant-history turns as user-context input for the Responses endpoint. The original `resolved_001` no-branch run was interrupted after repeated 400 responses; a fresh retry completed 8 ticks and produced scoreable path-mass artifacts under `raw/E3_worldfork_short_retry_a4ae2ca/worldfork_no_branch_short/resolved_001/`.
