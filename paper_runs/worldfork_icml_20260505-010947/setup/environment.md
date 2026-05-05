# Environment

Generated: 2026-05-05 01:22 UTC

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

## Runtime Routing Override

The local OpenRouter key is a placeholder, so the smoke run used a runtime-only model-routing override that routes all configured job types to `openai-codex` / `gpt-5.4`.

- Patch file: `setup/model_routing_codex_only_patch.json`
- Apply response: `setup/model_routing_codex_only.json`
- Effective settings: `setup/model_routing_codex_only_effective.json`
