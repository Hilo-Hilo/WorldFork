# Environment

Generated: 2026-05-05 02:58 UTC

## Git

- Branch: `ICML-forecasting`
- Run directory: `paper_runs/worldfork_icml_20260505-010947`

## Runtime

- Compose project: `worldfork-icml`
- API: `http://127.0.0.1:18045`
- Postgres host port: `15445`
- Redis host port: `16445`
- Reason for alternate ports: the default host ports were occupied by a separate `main-work` WorldFork stack.

## Setup Evidence

- `setup/readyz_pre_migrate.json`
- `setup/readyz_post_migrate.json`
- `setup/worldfork_status.json`
- `setup/agent_discover.json`
- `setup/model_defaults.json`
- `setup/model_routing.json`
- `setup/branch_policy.json`
- `setup/workers_after_fix.json`
- `setup/queues_after_fix.json`
- `setup/docker_ps_after_start.txt`
- `setup/docker_stats_snapshot.jsonl`

## Smoke Result

E1 smoke case `resolved_003` completed initialization in 146.20 seconds.

- Big Bang ID: `5dc8596a-c386-428b-91bc-38ba76a2d688`
- Actors: 9
- Traits: 9
- Graphs, sociology baseline, and emotion baseline: present

E3 smoke case `resolved_003` completed one synchronous tick plus report generation in 408.49 seconds.

- Big Bang ID: `5dc8596a-c386-428b-91bc-38ba76a2d688`
- Ticks run by command: 1
- Multiverse count: 1
- Final report version ID: `56016e3d-73b6-40ca-bceb-3d9cdc66a524`
- LLM calls: 14 succeeded, 0 failed
- Reported total tokens: 174,427
- Reported aggregate LLM seconds: 655.4009

E3 queue smoke case `resolved_004` completed one queued `run_big_bang_until_complete` job through Celery p1.

- Big Bang ID: `ca19adac-8899-42d6-8a6a-503509f72bd1`
- Job ID: `38fbc8bf-e031-48e1-8282-f1c56f947048`
- Init wall time: 131.22 seconds
- Job wait wall time: 219.47 seconds
- Ticks run by job: 1
- Multiverse count: 1
- Final report version ID: `5a04a01e-52aa-45b2-94a0-cfb7de676aaa`
- LLM calls: 12 succeeded, 0 failed
- Reported total tokens: 143,700
- Reported aggregate LLM seconds: 445.3739
- Queue telemetry during run: one active p1 task; p0/p2/p3 idle

E1 queued initializer batch completed four independent p1 jobs:

| Case | Big Bang ID | Job wait seconds | Actors | Traits |
| --- | --- | ---: | ---: | ---: |
| `resolved_001` | `3c6aa085-967d-44a3-877d-cf879007eade` | 148.14 | 11 | 11 |
| `resolved_002` | `1ec92f15-26cb-4b9f-8182-82a11dbe3d7a` | 0.13 | 7 | 7 |
| `resolved_005` | `8e267d9d-5b4b-4e11-a535-99b94868929f` | 0.12 | 9 | 9 |
| `resolved_006` | `0f5f24ce-317f-4829-9835-f9e37cc546b9` | 0.11 | 8 | 8 |

The near-zero waits for the final three cases occurred because they completed while the script was waiting on the first queued job.

E1 queued initializer batch completed eight additional independent p1 jobs:

| Case | Big Bang ID | Job elapsed seconds | Actors | Traits |
| --- | --- | ---: | ---: | ---: |
| `resolved_007` | `b5b98022-cafe-4cd0-95e9-002b80eaa013` | 136.72 | 8 | 8 |
| `resolved_008` | `13e0eb20-08a5-4e1b-bb7f-53e406833619` | 120.07 | 9 | 9 |
| `resolved_009` | `60634643-0713-41b6-b741-e49c4ea93cdf` | 114.74 | 7 | 7 |
| `resolved_010` | `a8cd6ba5-3c91-4a3f-87a7-2d6a455bedcf` | 112.37 | 7 | 7 |
| `resolved_011` | `8677a186-d015-4d6b-bc12-8c67290f893d` | 188.80 | 11 | 11 |
| `resolved_012` | `6b8c4c5b-80c8-43bf-a368-c1d3c6865ae1` | 161.62 | 14 | 14 |
| `resolved_013` | `14ce9ad3-7472-4d36-b06b-d947df628004` | 163.64 | 10 | 10 |
| `resolved_014` | `a2db89ad-49c7-4af5-9b0b-581dac4f6e44` | 115.41 | 8 | 8 |

During this batch, `/jobs/queues` reported p1 at 8 active tasks, with p0/p2/p3 idle.

E1 queued initializer batch completed the remaining 22 additional public cards: `resolved_015` through `resolved_024`, `dossier_001` through `dossier_008`, and `calibration_001` through `calibration_004`.

- Batch wall time from first job create to last job finish: 464.64 seconds.
- Per-job elapsed range, including queue wait: 109.49 to 463.97 seconds.
- Result: additional 36-card live initialization coverage is complete.

E1 queued initializer batch completed the remaining 72 existing public cards.

- Batch wall time from first job create to last job finish: 1796.65 seconds.
- Queue behavior: p1 stayed saturated at its configured concurrency of 8 through most of the batch; p0/p2/p3 were idle.
- Result: full 108-card live initialization coverage is complete.
- Automated coverage summary: 108/108 succeeded, mean actor count 8.76, mean trait count 8.82, mean graph edge count 33.99, sociology baseline present for 108/108 cases, and emotion baseline present for 108/108 cases.

## Runtime Routing Override

The local OpenRouter key is a placeholder, so the smoke run used a runtime-only model-routing override that routes all configured job types to `openai-codex` / `gpt-5.4`.

- Patch file: `setup/model_routing_codex_only_patch.json`
- Apply response: `setup/model_routing_codex_only.json`
- Effective settings: `setup/model_routing_codex_only_effective.json`
