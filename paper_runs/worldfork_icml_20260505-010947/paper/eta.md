# ETA Snapshot

Generated: 2026-05-05 01:22 UTC

These estimates use one measured E1 smoke initialization: `resolved_003` completed in 146.20 seconds on the isolated `worldfork-icml` stack.

| Block | Scope | Current status | ETA from ready backend |
| --- | --- | --- | --- |
| P0 static setup/card QA | Case preparation, manifest, leakage/schema QA | Complete for static checks | Done |
| P0 live setup validation | Docker, migrations, seed, status, readyz, agent discover, model routing | Complete on `worldfork-icml` ports `18045/15445/16445` | Done |
| Source verification | Fetch/check resolution URLs for 24 resolved cards | Automated fetch complete: 36 ok, 4 blocked/timed out | 20-45 minutes manual follow-up |
| E1 init screen | 108 `worldfork init` runs, no ticks | Smoke complete for `resolved_003` | About 4.4 hours serial at measured speed; plan 5-7 hours with retries |
| E2 direct baselines | 24 cards x 2 direct forecast prompts | Complete | Done |
| E3 short WorldFork resolved runs | 24 cards x 2 conditions x up to 8 ticks | Not started | 8-24 hours; core-12 fallback roughly half |
| E4 long-horizon audit | 18 cases x 30-35 ticks | Not started | 24-72 hours; minimum-6 fallback roughly 8-24 hours |
| E5 social-state audit | Scoring existing E4 artifacts | Not started | 2-5 hours after E4 |
| Paper finalization | Tables, figures, anonymized final draft | Draft scaffold exists | 2-4 hours after scores |

Celery queue tuning should be tested only after a baseline live run exists. The relevant knobs are worker concurrency per queue, queue routing of `worldfork.execute_job`, and provider rate limits. The current compose defaults are p0 concurrency 4, p1 concurrency 8, p2 concurrency 4, p3 concurrency 2, with `worker_prefetch_multiplier=1` and one-hour task limits.
