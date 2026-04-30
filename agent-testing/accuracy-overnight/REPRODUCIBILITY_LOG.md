# WorldFork Overnight Accuracy Evaluation Reproducibility Log

Study branch: `research/overnight-accuracy-2026-04-30`

Worktree: `/Users/hansonwen/WorldFork-accuracy-overnight`

Primary model constraint: `google/gemini-3.1-flash-lite-preview`

This file is the single running reference for the study. Every command, runtime check, generated artifact, notable observation, blocker, and final finding should be recorded here.

## 2026-04-30 02:34 PDT - Worktree Start

- Created isolated worktree `/Users/hansonwen/WorldFork-accuracy-overnight`.
- Created branch `research/overnight-accuracy-2026-04-30` from fresh `origin/dev`.
- Confirmed HEAD `b2070b5` with subject `Improve outcome accuracy controls (#8)`.
- Current source checkout `/Users/hansonwen/WorldFork` is dirty and behind `origin/dev`, so no work is being done there.

## 2026-04-30 02:36 PDT - User Requirement Update

- Added requirement: final deliverable must include finalized LaTeX PDF.
- Added requirement: interesting patterns or promising paths discovered during the run should be explored.
- Added requirement: maintain this single document as the trace log and reference document for all findings and commands.
- Added requirement: save every constructed file and do not delete generated artifacts, to keep the study reproducible.

## 2026-04-30 02:39 PDT - Runtime Isolation Finding

- `worldfork` is installed at `/opt/homebrew/bin/worldfork`, version `0.1.0`.
- Existing healthy WorldFork backends are running on `127.0.0.1:8003` and `127.0.0.1:18003`.
- Docker inspection showed those backends are mounted from other checkouts (`/Users/hansonwen/worldfork-hilo` and `/Users/hansonwen/WorldFork-fullagent-testing`).
- Decision: this study should use a separate backend target from the new worktree, with a compose override and distinct host ports, rather than mixing runtime artifacts with existing checkouts.

## 2026-04-30 02:43 PDT - Artifact Placement Correction

- An initial patch created `agent-testing/accuracy-overnight/*` under `/Users/hansonwen/WorldFork` because the patch tool used the original session working directory.
- The intended artifacts were then recreated under `/Users/hansonwen/WorldFork-accuracy-overnight`.
- The accidental files were not deleted; they are noted here for traceability.

## 2026-04-30 02:47 PDT - Local Environment

- Created `/Users/hansonwen/WorldFork-accuracy-overnight/.env` from `.env.example`.
- Copied the existing `OPENROUTER_API_KEY` value from `/Users/hansonwen/WorldFork/.env` without printing the secret.
- `.env` is ignored by `.gitignore`; it exists only to run the isolated local stack.

## 2026-04-30 02:50:30 PDT - Benchmark Generated

- Generated 50 anonymized event dossiers in `events/`.
- Generated matching hidden source/outcome files in `sources/`.
- Generated baseline actor, cohort, and hero payloads for reproducible manual initialization.
- Generated `benchmark-index.json` and `analysis/parameter_matrix.json`.

## 2026-04-30 02:50:37 PDT - Benchmark Validation

- Validation result: ok
- Matrix cells checked: 50.
- Error count: 0.
- Details written to `analysis/benchmark_validation.json`.

## 2026-04-30 02:52:11 PDT - Source Link Check

- Checked source URLs: 150.
- Reachable or accepted: 76.
- Failures requiring manual review: 74.
- Details written to `analysis/source_link_check.json`.

## 2026-04-30 02:57 PDT - Local CLI And PDF Tooling Check

- Reinstalled `worldfork` from `/Users/hansonwen/WorldFork-accuracy-overnight/cli` with `uv tool install --force ./cli`.
- Confirmed `worldfork --version` reports `0.1.0` from the local worktree install.
- `pdflatex`, `latexmk`, `tectonic`, `lualatex`, and `xelatex` were not initially available.
- `pandoc 3.8.3` and `WeasyPrint 68.0` are available; a true LaTeX PDF still needs a LaTeX engine such as `tectonic` or `pdflatex`.

## 2026-04-30 03:00 PDT - Isolated Backend Started

- Ran `docker compose -p worldfork-accuracy-overnight -f docker-compose.yml -f agent-testing/accuracy-overnight/docker-compose.accuracy.yml up -d --build`.
- Created isolated Docker project `worldfork-accuracy-overnight`.
- Host ports: API `18013`, Postgres `15436`, Redis `16381`.
- Ran Alembic migrations to head and seeded settings/model-routing/rate-limit/branch-policy/Zep defaults.

## 2026-04-30 02:55:02 PDT - Runtime Health Commands

- Target base URL: `http://127.0.0.1:18013`.
- Ran `agent discover`, `status`, `models defaults`, and root `/readyz` through the `worldfork` CLI.
- Failures: none.
- Raw command records written under `runs/health/`.

## 2026-04-30 02:56:36 PDT - Baseline Runs

- Attempted baseline runs: 1.
- Completed: 1.
- Failures: 0.
- Aggregate records written to `runs/baseline/baseline_records.json`.

## 2026-04-30 03:02 PDT - Pilot Harness Fix

- The first baseline pilot completed for `tech_weeks_api_pricing_blackout`.
- Big Bang ID: `ead7533c-4080-46c8-ac0f-e84be5ccdbc2`.
- Final report version ID: `152ec650-c108-41e7-9b71-b2b03640041c`.
- Initial model audit failed because `logs list --limit 1000` exceeded the API limit of 500.
- Patched the harness to use `--limit 500` and `--verbosity full` for LLM logs.
- Refreshed the pilot model audit from `worldfork --verbosity full logs list`; all 35 audited LLM rows used `google/gemini-3.1-flash-lite-preview`.
- Patched baseline and sweep loops to reuse existing completed run records so pilot artifacts are preserved instead of overwritten.

## 2026-04-30 05:03:18 PDT - Baseline Runs

- Attempted baseline runs: 50.
- Completed: 50.
- Failures: 0.
- Aggregate records written to `runs/baseline/baseline_records.json`.

## 2026-04-30 08:17:52 PDT - Parameter Sweep Runs

- Attempted sweep run count: 64.
- Completed: 59.
- Failures: 5, all in `long_high_detail_strict`.
- Sweep factors: tick count, agent count, prompt complexity, branch threshold.
- Aggregate records written to `runs/sweep/sweep_records.json`.

## 2026-04-30 08:19 PDT - Sweep Failure Pattern

- Failed sweep events: `tech_3y_filesharing_injunction`, `ai_1_3y_debt_recovery_scandal`, `ai_3y_childcare_benefit_scandal`, `labor_1_3y_coffee_union_campaign`, and `labor_3y_low_wage_campaign`.
- Each failed run was the `long_high_detail_strict` variant.
- Each failure showed an `HTTP 503 ... LLM unavailable` tick error followed by an `HTTP 409 ... final report requires terminal multiverses` error.
- This suggests the long/high-detail/strict setting is high-risk for endpoint completion because it runs many more LLM calls before terminal state and can leave active branches if one call fails late.
- The matched `long_compressed_default` variants completed for these same long-horizon events, so long horizon alone was not the observed failure trigger.

## 2026-04-30 08:20 PDT - Score Aggregation

- Aggregated run records: 114.
- Completed scored records: 109.
- Completed model audits: 109/109 Gemini-only.
- Completed-run audited LLM calls: 6,439.
- Overall token-overlap top-outcome match rate: 0.8073.
- Overall mean total variation distance: 0.3639.
- Wrote `analysis/score_summary.json`, `research-report.md`, and `accuracy-advice.md`.

## 2026-04-30 08:18:28 PDT - Score Aggregation

- Loaded run records: 114.
- Completed records: 109.
- Wrote `analysis/score_summary.json`, `research-report.md`, and `accuracy-advice.md`.

## 2026-04-30 08:22:48 PDT - LaTeX Build

- Wrote `latex/accuracy-evaluation.tex`.
- PDF build exit code: 0.
- Expected PDF path: `latex/accuracy-evaluation.pdf`.

## 2026-04-30 08:24 PDT - Final Runtime And Artifact Check

- Verified `latex/accuracy-evaluation.pdf` exists and is a PDF document, 30,099 bytes.
- Verified `agent-testing/accuracy-overnight` artifact tree is approximately 1.4 GB with 510 files at max depth 2.
- Verified isolated backend `/readyz` remains healthy at `http://127.0.0.1:18013`.
- `worldfork status` on the isolated backend reports `run_count=114` and `job_count=0`.
- Git status in `/Users/hansonwen/WorldFork-accuracy-overnight` shows only untracked `agent-testing/`.
- No commit, push, PR, or cleanup deletion was performed.

## 2026-04-30 12:44 PDT - Resume/Retry Improvement Checkpoint

- User asked whether failed runs are easily resumable/retryable and requested necessary improvements using subagents.
- Subagent read-only code inspection found endpoint-prior guidance already exists in prompt templates and reports, so the novel accuracy-improvement space is structural rather than more prompt wording: durable endpoint state, endpoint coverage ledgers, authority-weighted endpoint evidence, contradiction checks, and calibration tests.
- Subagent log analysis confirmed the five failed sweep runs are retryable only after active multiverses become terminal/reportable. A direct retry of `05_final_report.json` would repeat `HTTP 409 final report requires terminal multiverses`.
- Worker subagent added `resume-failures` to `harness/accuracy_harness.py` and documented it in `README.md`.
- Verified locally: `python3 -m py_compile agent-testing/accuracy-overnight/harness/accuracy_harness.py` and `python3 agent-testing/accuracy-overnight/harness/accuracy_harness.py resume-failures --help`.
- Isolated backend status before retry: `worldfork --base-url http://127.0.0.1:18013 --verbosity summary status` returned `status=ok`, `run_count=114`, `job_count=0`.
- Planned retry command: `python3 agent-testing/accuracy-overnight/harness/accuracy_harness.py resume-failures --base-url http://127.0.0.1:18013 --timeout 240 --max-requests-per-run 40 --retry-attempts 3 --retry-sleep-seconds 10`.

## 2026-04-30 13:02:36 PDT - Score Aggregation

- Loaded run records: 114.
- Completed records: 109.
- Wrote `analysis/score_summary.json`, `research-report.md`, and `accuracy-advice.md`.

## 2026-04-30 13:02:42 PDT - LaTeX Build

- Wrote `latex/accuracy-evaluation.tex`.
- PDF build exit code: 0.
- Expected PDF path: `latex/accuracy-evaluation.pdf`.

## 2026-04-30 13:02 PDT - Resume Verification Result

- Ran broad resume command with `--max-requests-per-run 40`; stopped it manually after repeated current-provider `HTTP 503 ... LLM unavailable` responses on the first failed run to avoid spending the full outage window. Partial command artifacts were preserved under `runs/sweep/long_high_detail_strict/ai_1_3y_debt_recovery_scandal/resume_20260430T194507Z_*.json`.
- The broad attempt wrote multiverse-list records plus multiple tick-attempt records; no original failure records were deleted.
- Added `--max-consecutive-transient-failures` to `resume-failures` so provider outages are resumable with a clear status instead of silently burning the full request cap.
- First targeted fail-fast verification surfaced a status-classification bug: the run recorded `stop_reason=transient_failure_cap_reached` but top-level `status=resume_failed`.
- Patched the harness to preserve the last successful multiverse-list status separately from tick status.
- Verified patched command with `python3 agent-testing/accuracy-overnight/harness/accuracy_harness.py resume-failures --base-url http://127.0.0.1:18013 --timeout 60 --max-requests-per-run 1 --retry-attempts 1 --retry-sleep-seconds 0 --max-consecutive-transient-failures 1 --only ai_1_3y_debt_recovery_scandal`.
- Result: `ai_1_3y_debt_recovery_scandal` now has top-level status `transient_failure_cap_reached`, `requests_used=1`, final active multiverse `M1`, and Gemini-only model audit still OK.
- Regenerated `analysis/score_summary.json`, `research-report.md`, `accuracy-advice.md`, `latex/accuracy-evaluation.tex`, and `latex/accuracy-evaluation.pdf` after the retryability patch.

## 2026-04-30 13:03:58 PDT - Score Aggregation

- Loaded run records: 114.
- Completed records: 109.
- Wrote `analysis/score_summary.json`, `research-report.md`, and `accuracy-advice.md`.

## 2026-04-30 13:04:02 PDT - LaTeX Build

- Wrote `latex/accuracy-evaluation.tex`.
- PDF build exit code: 0.
- Expected PDF path: `latex/accuracy-evaluation.pdf`.

## 2026-04-30 13:04:41 PDT - LaTeX Build

- Wrote `latex/accuracy-evaluation.tex`.
- PDF build exit code: 0.
- Expected PDF path: `latex/accuracy-evaluation.pdf`.
- Updated the LaTeX report to include a compact incomplete-run table with event ID, current status, failed tick, and active multiverses.

## 2026-04-30 13:16 PDT - Git Branch Preparation

- User requested pushing the experiment branch and opening a PR against `dev`.
- Fast-forwarded `research/overnight-accuracy-2026-04-30` from `origin/dev` (`b2070b5..27ae6bc`) before committing.
- Ran a secret-pattern scan across the committed experiment artifacts; matches were documentation/source-link false positives and the log statement that an existing local secret was copied without printing it.
- Confirmed `agent-testing/accuracy-overnight/runs/` is still saved locally at approximately 1.4 GB but is ignored by the repository-wide `runs/` rule and is not staged for the PR commit.
- Staged 509 non-ignored experiment files: benchmark dossiers, hidden source/outcome notes, local harness, analysis summaries, Markdown reports, LaTeX source, and finalized PDF.
