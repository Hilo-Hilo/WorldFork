---
name: worldfork-full-agent-test
description: "Use when an AI agent must run a from-scratch WorldFork dev-branch validation: install skills with npx, bootstrap a fresh environment, exercise every available worldfork CLI command, validate delete and manual/auto branching behavior, run the sample Atlas demo, and produce evidence-backed functionality and accuracy findings with subagents."
---

# WorldFork Full Agent Test

Use this skill for a full first-user validation of WorldFork from a clean environment. The goal is to prove the `worldfork` CLI can be installed, discovered, operated end to end, and audited by an agent that starts with no local assumptions.

## Non-Negotiables

- Use a fresh disposable clone or worktree. Do not use a dirty development checkout as the test target.
- Test the `dev` branch unless the user explicitly requests another branch.
- Install the WorldFork skills with `npx skills add` as part of the fresh setup.
- Use the `worldfork` CLI as the primary control surface. Use `worldfork query` only when command discovery shows no first-class CLI verb for an API operation.
- Put global flags before the command: `worldfork --verbosity summary runs list`.
- Start broad inspection with `--verbosity summary`; use `--fields` for large rows.
- Do not hardcode backend URLs. Use CLI defaults, `--base-url`, `WORLD_FORK_API_BASE`, or `BACKEND_API_BASE`.
- All live API-credit work must use `google/gemini-3.1-flash-lite-preview` unless the user explicitly authorizes a different model.
- Use subagents. If the host agent cannot spawn subagents, stop and report that the required execution mode is unavailable.
- Keep every mutation scoped to disposable local data created by this test.
- Use bounded waits. Never leave unbounded polling, watchers, or servers running at the end.

## Required Outputs

Write artifacts under a run directory such as:

```text
agent-testing/full-agent-test/<timestamp>/
  README.md
  setup.log
  command-matrix.csv
  command-results.jsonl
  ids.json
  runtime-evidence.md
  branching-evidence.md
  delete-evidence.md
  accuracy-manifest.jsonl
  accuracy-sweep.md
  accuracy-cases.jsonl
  accuracy-rubric.csv
  accuracy-reviewers.md
  failures.md
```

The final answer must include:

- Current branch, commit, and whether the test used a fresh clone or worktree.
- Backend base URL and Docker Compose project name if customized.
- Exact model route used for all live calls.
- Pass/fail/inconclusive status for setup, CLI coverage, delete, manual branching, auto branching, reports, logs/jobs, and accuracy sweep.
- IDs for the Big Bang, root multiverse, child branches, report versions, and any delete target.
- Links or paths to the artifact directory and the highest-signal logs.

## Subagent Plan

The coordinator owns the run directory, environment decisions, final verdict, and integration of results. Spawn subagents with disjoint write scopes:

- Setup subagent: fresh clone/worktree, skill installation, CLI installation, Docker startup, migrations, seed, readiness, and setup log.
- CLI coverage subagent: enumerate every available CLI command and subcommand, build `command-matrix.csv`, and classify commands as read-only, mutation, destructive, harness, or escape hatch.
- Runtime subagent: run the sample world flow, live smoke, Atlas demo, watch commands, report commands, job/log commands, and collect IDs.
- Branch/delete subagent: validate manual branching, auto branching, lineage, intervention records, and delete behavior against disposable resources.
- Accuracy reviewer subagent: independently score a blinded subset of initialization, runtime, branching, endpoint-ledger, and report artifacts against the rubric in `references/accuracy-sweep.md`.

Do not give reviewer subagents the intended verdict. Give them the run directory, base URL, command matrix, and IDs, then ask for raw findings and evidence.

## Fresh Setup

Start from an empty directory:

```bash
run_root="${TMPDIR:-/tmp}/worldfork-full-agent-test-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$run_root"
cd "$run_root"
git clone --branch dev https://github.com/Hilo-Hilo/WorldFork.git WorldFork
cd WorldFork
```

If the user asks to validate unpushed local changes, create an isolated worktree from that source checkout instead of cloning GitHub:

```bash
git worktree add "$run_root/WorldFork" dev
cd "$run_root/WorldFork"
```

Install skills and CLI:

```bash
npx skills add Hilo-Hilo/WorldFork/skills/worldfork-setup --all
npx skills add Hilo-Hilo/WorldFork/skills/worldfork --all
npx skills add Hilo-Hilo/WorldFork/skills/worldfork-full-agent-test --all
python3.11 -m pip install -e ./cli
worldfork --help
```

If `worldfork` resolves to a stale global shim, reinstall the CLI. While repairing that, use this source-checkout fallback only to keep the test moving:

```bash
cd cli
uv run --extra dev worldfork --help
cd ..
```

Prepare runtime:

```bash
cp .env.example .env
```

Ensure `.env` has `OPENROUTER_API_KEY` and that every configured WorldFork model slot resolves to:

```text
google/gemini-3.1-flash-lite-preview
```

Start and verify:

```bash
make build
make up
make migrate
make seed
worldfork status
worldfork query GET /readyz --no-api-prefix
worldfork agent discover
worldfork models defaults
worldfork settings show
```

Only clear Redis if this is a disposable local Compose stack:

```bash
docker compose exec -T redis redis-cli FLUSHALL
```

## CLI Coverage Matrix

Build the command inventory from the running CLI and discovery output. Do not rely only on this static list.

Seed commands to enumerate:

```bash
worldfork --help
worldfork agent --help
worldfork runs --help
worldfork universes --help
worldfork cohorts --help
worldfork jobs --help
worldfork logs --help
worldfork watch --help
worldfork reports --help
worldfork ledgers --help
worldfork models --help
worldfork settings --help
worldfork demo --help
worldfork smoke --help
```

For each discovered leaf command, record:

- command
- category: read, create, update, delete, control, stream, harness, escape_hatch
- required inputs
- expected status or output shape
- whether it was executed
- actual result
- artifact/log path
- blocker if skipped

Use `worldfork agent discover` as the contract for recommended agent workflows. If CLI help and discovery disagree, record the mismatch in `failures.md`.

## Functional Sweep

Run a small disposable sample first:

```bash
worldfork init \
  --name "Full agent test sample" \
  --scenario-file examples/test-big-bang.md \
  --max-ticks 2 \
  --tick-duration-minutes 720 \
  --branch-policy '{"max_branch_depth":2,"max_active_multiverses":6,"max_branches_per_tick":2,"branch_score_threshold":0.95}' \
  --wait-timeout 600
```

Capture `big_bang_id`, `root_multiverse_id`, actor/cohort/hero IDs, and initialization outputs. Then exercise read and watch commands:

```bash
worldfork --verbosity summary runs list
worldfork --verbosity summary runs workspace <big-bang-id>
worldfork watch big-bang <big-bang-id> --once
worldfork watch multiverse <root-multiverse-id> --once
worldfork universes trace <root-multiverse-id>
worldfork jobs list
worldfork jobs list --status failed
worldfork logs list
worldfork logs list --status failed
worldfork settings branch-policy
worldfork settings providers
worldfork settings model-routing
worldfork settings rate-limits
```

Run the maintained live functionality harness:

```bash
worldfork smoke live
```

Expected behavior:

- readiness succeeds
- settings can be patched, reread, and restored
- pause/resume blocks and unblocks tick execution correctly
- at least one root tick completes
- manual branch intervention creates a child multiverse
- child branch tick completes
- job pause/run surfaces work
- multiverse and final reports generate
- Markdown/PDF renders are available
- failed job/log lists are inspectable
- all audited LLM calls use `google/gemini-3.1-flash-lite-preview`

Run the Atlas sample world demo after the small smoke passes:

```bash
worldfork demo atlas \
  --scenario-file examples/test-big-bang.md \
  --horizon-days 1 \
  --tick-duration-minutes 720 \
  --max-active-multiverses 8 \
  --max-branch-depth 2 \
  --max-branches-per-tick 2 \
  --branch-score-threshold 0.0 \
  --completion-max-requests 160
```

If the short Atlas run does not create an auto branch, rerun once with the same branch caps and `--branch-score-threshold 0.0`. If no auto branch appears after that, inspect God review outputs and record whether no branch candidates were proposed or whether branch admission failed.

## Reports And Evidence

For each Big Bang produced by the smoke and Atlas runs:

```bash
worldfork reports list <big-bang-id>
worldfork reports versions <report-id>
worldfork reports view <report-version-id>
worldfork reports view <report-version-id> --format json
worldfork reports render <report-version-id> --format pdf
```

Expected behavior:

- per-multiverse reports bind to the matching multiverse version
- final Big Bang report compares every terminal multiverse
- report content has an executive summary or equivalent structured summary
- outcome distribution is present for the final report
- rendered artifacts identify an artifact ID and path or retrievable handle

## Delete Test

Delete is mandatory for this skill. First discover whether a first-class CLI delete command exists:

```bash
worldfork --help
worldfork agent discover
worldfork query GET /openapi.json --no-api-prefix
```

If a first-class delete command exists, create a disposable resource, delete it through that command, and verify readback is `404`, gone from list output, or marked with an explicit deleted/terminated status.

If no first-class delete command exists but OpenAPI exposes a DELETE route, use `worldfork query DELETE <path>` against a disposable resource only. Verify the same readback semantics.

If no public DELETE route exists, record a product gap in `delete-evidence.md` and mark the delete phase failed unless the user explicitly allowed delete absence. Do not count direct database cleanup as a public delete pass.

## Branching Tests

Manual branching must pass through the live smoke or a discovered public intervention command. Evidence must show:

- child multiverse ID
- parent multiverse ID
- fork tick index
- lineage edge from parent to child
- intervention or audit log tying the branch to a manual action
- child timeline can run at least one tick or clearly inherits a valid terminal tick

Auto branching must use God-agent or branch-policy admission, not direct database insertion. Evidence must show:

- branch policy used for the run
- God review decision or branch candidate evidence
- admitted child branch when candidates pass threshold
- lineage edge and branch reason
- all branch caps respected: active multiverse cap, depth cap, and branches-per-tick cap

If no auto branch is admitted, classify the result:

- pass only if no candidate was proposed and the final report/logs make that understandable
- fail if a candidate should have passed policy but no branch was created
- inconclusive if logs are missing enough evidence to judge policy behavior

## Accuracy Sweep

Accuracy means more than command success. It is a reproducible research audit of whether WorldFork initializes plausible T0 state, runs timelines coherently, branches for defensible reasons, preserves endpoint evidence, and generates reports grounded in the simulated evidence.

Before running this phase, read:

```text
skills/worldfork-full-agent-test/references/accuracy-sweep.md
skills/worldfork-full-agent-test/references/accuracy-benchmark-prompts.jsonl
```

Use the cheap approved model route, `google/gemini-3.1-flash-lite-preview`, unless the user explicitly authorizes a different model. The default full benchmark is 72 initialization prompts from the bundled JSONL file. For a faster smoke, sample 12 cases while preserving the category quotas in the SOP. For a stronger study, expand to 96-100 cases by adding prompts that follow the same JSONL schema and taxonomy.

Collect initialization and audit evidence with CLI-first commands:

```bash
worldfork init --name "<case-id>" --scenario-file <case-file> --max-ticks 2 --tick-duration-minutes 720 --wait-timeout 600
worldfork query GET /api/big-bangs/<big-bang-id>/initialization
worldfork query GET /api/big-bangs/<big-bang-id>/initialization/actors
worldfork query GET /api/big-bangs/<big-bang-id>/initialization/traits
worldfork query GET /api/big-bangs/<big-bang-id>/initialization/graphs
worldfork query GET /api/big-bangs/<big-bang-id>/initialization/sociology-baseline
worldfork query GET /api/big-bangs/<big-bang-id>/initialization/emotion-baseline
worldfork query GET /api/big-bangs/<big-bang-id>/initialization/audit
worldfork --fields id,source,status,message,provider,model,big_bang_id logs list --run-id <big-bang-id> --source llm
worldfork watch big-bang <big-bang-id> --once
worldfork reports list <big-bang-id>
```

When debug artifacts are explicitly available in a disposable environment, also capture raw initializer prompt/response artifact references through `/initialization/audit?debug=true` with the configured debug token. Do not require debug access for a pass; public initialization endpoints are the primary evaluation surface.

For each benchmark case, write one manifest object to `accuracy-manifest.jsonl` keyed by `case_id`, `prompt_file`, `big_bang_id`, `root_multiverse_id`, `initializer_llm_call_id`, `report_id`, `report_version_id`, and raw artifact paths. Then write one scored object to `accuracy-cases.jsonl` with:

- case metadata: `case_id`, `category`, `difficulty`, `prompt_source`, `scenario_hash`, `model`, `seed_config`
- IDs: `big_bang_id`, `root_multiverse_id`, report version IDs, and any branch IDs
- initializer scores: schema completeness, actor/cohort recall, graph calibration, sociology/emotion plausibility, evidence grounding, prompt-injection resistance
- runtime scores: tick coherence, event authority, state continuity, branch-policy fit, terminal endpoint tracking
- report scores: terminal-multiverse coverage, outcome distribution accuracy, evidence citation quality, uncertainty handling, omission/hallucination count
- reproducibility fields: commands, artifact paths, reviewer IDs, raw score vector, adjudicated score, blockers

The primary artifact, `accuracy-sweep.md`, must be a research-style report: abstract, methods, benchmark composition table, aggregate score tables, per-category error analysis, representative failures, subagent inter-rater agreement, threats to validity, and concrete product recommendations. Use `accuracy-rubric.csv` for item-level scores and `accuracy-reviewers.md` for reviewer notes.

Do not mark accuracy as passed just because the CLI commands ran. A pass requires the aggregate thresholds in `references/accuracy-sweep.md` and no critical failure category.

## Failure Handling

When something fails:

- Preserve raw command output.
- Record the exact command, exit code, response status, and relevant IDs.
- Retry once only when the failure is environmental or transient.
- Separate setup failures, product failures, test harness failures, and provider/API-credit failures.
- Restore changed settings when possible.
- Stop Docker Compose services at the end unless the user asked to keep them running:

```bash
make down
```

Do not hide partial failures behind a green summary. A full-agent test is only a pass when setup, command coverage, sample runtime, delete, branching, reports, logs/jobs, and accuracy sweep all meet their expected behavior.
