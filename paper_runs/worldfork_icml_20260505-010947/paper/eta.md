# ETA Snapshot

Generated: 2026-05-05 02:20 UTC

These estimates use live runs on the isolated `worldfork-icml` stack: `resolved_003` initialization completed in 146.20 seconds; a one-tick synchronous run plus report generation completed in 408.49 seconds; a queued `run-until-complete/jobs` smoke on `resolved_004` initialized in 131.22 seconds, then completed one tick plus reports in a 219.47 second job wait; a four-case queued initializer batch completed within the first 148.14 second wait after submission; an eight-case queued initializer batch ran with p1 at 8 active jobs and finished all eight jobs in roughly 3.2 minutes from submission; and a 22-case queued add-on batch finished in 464.64 seconds wall time.

| Block | Scope | Current status | ETA from ready backend |
| --- | --- | --- | --- |
| P0 static setup/card QA | Case preparation, manifest, leakage/schema QA | Complete for static checks | Done |
| P0 live setup validation | Docker, migrations, seed, status, readyz, agent discover, model routing | Complete on `worldfork-icml` ports `18045/15445/16445` | Done |
| Source verification | Fetch/check resolution URLs for 24 resolved cards | Automated fetch complete: 36 ok, 4 blocked/timed out | 20-45 minutes manual follow-up |
| E1 init screen | 108 `worldfork init` runs, no ticks | Additional 36-card set complete; 36/108 live initializations evidenced | Remaining 72 existing cases: about 25-45 minutes if p1=8 throughput holds; plan 1 hour with retries/provider throttling |
| E2 direct baselines | 24 cards x 2 direct forecast prompts | Complete | Done |
| E3 short WorldFork resolved runs | 24 cards x 2 conditions x up to 8 ticks | Synchronous and queued one-tick/report smokes complete | 8-24 hours; core-12 fallback roughly half |
| E4 long-horizon audit | 18 cases x 30-35 ticks | Not started | 24-72 hours; minimum-6 fallback roughly 8-24 hours |
| E5 social-state audit | Scoring existing E4 artifacts | Not started | 2-5 hours after E4 |
| Paper finalization | Tables, figures, anonymized final draft | Draft scaffold exists | 2-4 hours after scores |

Celery queue tuning should be tested through job-first endpoints, not the synchronous `run-until-complete` path used for the first tick smoke. During the synchronous smoke, `/jobs/queues` reported all queues idle. During the queued smoke, `/jobs/queues` showed one active p1 task and `/jobs/workers` showed `worldfork.execute_job` running on the p1 worker. The four-case initializer batch showed four active p1 jobs, the eight-case initializer batch showed p1 at its configured concurrency of 8, and the 22-case add-on batch drained in three waves. Queueing therefore improves throughput across independent cases. For a single case, the job is still one long p1 task, so queue tuning is unlikely to speed up an individual run. The current compose defaults are p0 concurrency 4, p1 concurrency 8, p2 concurrency 4, p3 concurrency 2, with `worker_prefetch_multiplier=1` and one-hour task limits.
