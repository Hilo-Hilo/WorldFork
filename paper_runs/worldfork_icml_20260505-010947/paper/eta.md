# ETA Snapshot

Generated: 2026-05-06 00:59 UTC

## Live Update

The E3 deadline-aware branching core-12 run on the isolated `worldfork-icml`
stack is terminal and scored. The main E3 comparison now uses branching
path-mass aggregation across explicit yes/no candidate endpoints. The
highest-probability single path is diagnostic only.

Live queue/runtime state at this snapshot:

- E3 core-12 cases: 12/12 terminal.
- Branch paths: 68 terminal multiverses across the core-12 batch.
- Latest branch-path tick range: all paths reached tick 16.
- Branching aggregate row: mean Brier 0.224597, mean log score 0.613304,
  mean unresolved mass 0.0.
- DeepSeek structured direct core-12 row: mean Brier 0.194950, mean log score
  0.591768, mean unresolved mass 0.0.
- 50/50 DeepSeek structured direct prior plus E3 branch aggregate: mean Brier
  0.190560, mean log score 0.553376, mean unresolved mass 0.0.
- Current p1 execution layout: E3 long-limit workers are no longer needed for
  E3; remaining live work is E4.
- Postgres is not the active bottleneck at this snapshot; connection pressure
  is low relative to the raised ICML cap.

ETA from this snapshot:

| Deliverable | ETA | Basis |
| --- | ---: | --- |
| E3 deadline-aware branching core-12 terminal artifacts | Done | 12/12 jobs terminal |
| E3 branching aggregate plus direct-prior blend score refresh | Done | `results/e3_core12_comparison_scores.csv`; `results/e3_direct_prior_blend_best.csv` |
| Minimum E4 audit recovery/finalization | 8-24 hours | Still dominated by long-horizon branching unless narrowed to already-running minimum rows |
| E5 social-state scoring from E4 artifacts | 2-5 hours | Offline scoring/audit after E4 artifacts exist |
| Paper draft/table refresh after E3 plus available E4/E5 evidence | 1-3 hours | Existing draft scaffold and tables already exist; final quality depends on E4 coverage |

Shortest defensible paper path remains: use the E3 core-12 branching aggregate
as the main WorldFork resolved-card row, use fixed direct-prior blends as
sensitivity rows, keep older E3 rows as diagnostic artifacts only, and state
coverage limits plainly.

These estimates use live runs on the isolated `worldfork-icml` stack: `resolved_003` initialization completed in 146.20 seconds; a one-tick synchronous run plus report generation completed in 408.49 seconds; a queued `run-until-complete/jobs` smoke on `resolved_004` initialized in 131.22 seconds, then completed one tick plus reports in a 219.47 second job wait; a scored no-branch validation on `resolved_001` initialized in 135.12 seconds and completed 8 queued ticks in a 705.58 second job wait; a batched no-branch run on `resolved_003`, `resolved_005`, and `resolved_007` submitted three p1 run jobs concurrently and completed 8 ticks each with per-job waits of 648.90, 709.14, and 754.11 seconds; a scored branching validation on `resolved_001` initialized in 120.12 seconds and completed 27 tick snapshots across four multiverses in a 2131.68 second job wait; a four-case queued initializer batch completed within the first 148.14 second wait after submission; an eight-case queued initializer batch ran with p1 at 8 active jobs and finished all eight jobs in roughly 3.2 minutes from submission; a 22-case queued add-on batch finished in 464.64 seconds wall time; and the remaining 72 existing public cards finished as queued p1 jobs in 1796.65 seconds wall time.

Tick counts are budget caps, not mandatory endpoints. If a ledger resolves naturally, freeze the row even if the cap was 16, 32, or 35. If unresolved mass remains high, resume the existing Big Bang rather than reinitializing. The 16-to-35 no-branch resume is a completed diagnostic: it reused existing Big Bangs but did not move any frozen path-mass forecast, so more no-branch ticks are not the fastest path to a better paper.

| Block | Scope | Current status | ETA from ready backend |
| --- | --- | --- | --- |
| P0 static setup/card QA | Case preparation, manifest, leakage/schema QA | Complete for static checks | Done |
| P0 live setup validation | Docker, migrations, seed, status, readyz, agent discover, model routing | Complete on `worldfork-icml` ports `18045/15445/16445` | Done |
| Source verification | Fetch/check resolution URLs for 24 resolved cards | 39/40 URLs verified ok after browser follow-up; remaining Reuters URL is gated but the same case has a primary court-source row marked ok | Done for current evidence package |
| E1 init screen | 108 `worldfork init` runs, no ticks | Complete: 108/108 live initializations evidenced; automated coverage table generated | Done |
| E2 direct baselines | 24 cards x GPT-5.4 and DeepSeek v4 Flash direct forecast prompts | Complete | Done |
| E3 short WorldFork resolved runs | Core-12 branching aggregate plus direct-prior sensitivity | Deadline-aware branching core-12 is complete. The branching aggregate scores Brier 0.224597 / log 0.613304 / unresolved 0.0; the 50/50 DeepSeek structured direct prior blend scores Brier 0.190560 / log 0.553376 / unresolved 0.0. | Done for current evidence package; full 24-card branching remains optional and roughly 15-24 hours under current policy |
| E4 long-horizon audit | 18 cases x 30-35 ticks | Not started | 24-72 hours; minimum-6 fallback roughly 8-24 hours |
| E5 social-state audit | Scoring existing E4 artifacts | Not started | 2-5 hours after E4 |
| Paper finalization | Tables, figures, anonymized final draft | Draft scaffold exists | 2-4 hours after scores |

The shortest defensible completion path is now: keep the E3 core-12 branching aggregate and fixed direct-prior blend as the resolved forecast evidence. Running E3 branching full-24 and E4 full-18 is still the stronger evidence package, but it is a multi-day wall-clock path under the current branching policy.

Future default ICML E3/E4/E5 runs should use OpenRouter `deepseek/deepseek-v4-flash` for `cohort_agent` and `hero_agent`, with governance/report routes on a strong configured model. The existing Codex-only rows are smoke/ablation rows and should not be aggregated with default-route rows unless the table separates route policy.

Celery queue tuning should be tested through job-first endpoints, not the synchronous `run-until-complete` path used for the first tick smoke. During the synchronous smoke, `/jobs/queues` reported all queues idle. During the queued smoke and scored E3 validations, `/jobs/queues` showed active p1 tasks and `/jobs/workers` showed `worldfork.execute_job` running on the p1 worker. The three-case no-branch E3 batch confirmed the speedup path: independent p1 run jobs overlapped and finished in one shared wall-clock window rather than three serial windows. The four-case initializer batch showed four active p1 jobs, the eight-case initializer batch showed p1 at its configured concurrency of 8, the 22-case add-on batch drained in three waves, and the existing 72-card batch held p1 saturation at 8 until completion. Queueing therefore improves throughput across independent cases. For a single case, the job is still one long p1 task, so queue tuning is unlikely to speed up an individual run. The original E3 validation failure was not a queue-capacity issue; it stalled inside a God-review Codex retry loop that is now fixed for fresh validations. The branching validation shows the larger risk: the current branch policy expanded one nominal 8-tick card into 27 tick snapshots across four multiverses. The current compose defaults are p0 concurrency 4, p1 concurrency 8, p2 concurrency 4, p3 concurrency 2, with `worker_prefetch_multiplier=1` and one-hour task limits.
