# ETA Snapshot

Generated: 2026-05-05 01:44 UTC

These estimates use two measured live smoke runs on the isolated `worldfork-icml` stack: `resolved_003` initialization completed in 146.20 seconds, and a one-tick synchronous run plus report generation completed in 408.49 seconds after runtime-only Codex routing was applied.

| Block | Scope | Current status | ETA from ready backend |
| --- | --- | --- | --- |
| P0 static setup/card QA | Case preparation, manifest, leakage/schema QA | Complete for static checks | Done |
| P0 live setup validation | Docker, migrations, seed, status, readyz, agent discover, model routing | Complete on `worldfork-icml` ports `18045/15445/16445` | Done |
| Source verification | Fetch/check resolution URLs for 24 resolved cards | Automated fetch complete: 36 ok, 4 blocked/timed out | 20-45 minutes manual follow-up |
| E1 init screen | 108 `worldfork init` runs, no ticks | Smoke complete for `resolved_003` | About 4.4 hours serial at measured speed; plan 5-7 hours with retries |
| E2 direct baselines | 24 cards x 2 direct forecast prompts | Complete | Done |
| E3 short WorldFork resolved runs | 24 cards x 2 conditions x up to 8 ticks | One synchronous tick/report smoke complete for `resolved_003` | 8-24 hours; core-12 fallback roughly half |
| E4 long-horizon audit | 18 cases x 30-35 ticks | Not started | 24-72 hours; minimum-6 fallback roughly 8-24 hours |
| E5 social-state audit | Scoring existing E4 artifacts | Not started | 2-5 hours after E4 |
| Paper finalization | Tables, figures, anonymized final draft | Draft scaffold exists | 2-4 hours after scores |

Celery queue tuning should be tested through job-first endpoints, not the synchronous `run-until-complete` path used for the tick smoke. During that synchronous smoke, `/jobs/queues` reported all queues idle, so Celery settings were not on the critical path. The relevant knobs for the job-first test are worker concurrency per queue, queue routing of `worldfork.execute_job`, and provider rate limits. The current compose defaults are p0 concurrency 4, p1 concurrency 8, p2 concurrency 4, p3 concurrency 2, with `worker_prefetch_multiplier=1` and one-hour task limits.
