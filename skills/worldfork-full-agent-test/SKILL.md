---
name: worldfork-full-agent-test
description: "Use when an AI agent must run a from-scratch WorldFork dev-branch validation: install skills with npx, bootstrap a fresh environment, configure and audit LLM model routing, exercise every available worldfork CLI command, validate delete and manual/auto branching behavior, run the sample Atlas demo, and produce evidence-backed functionality and accuracy findings with subagents."
---

# WorldFork Full Agent Test

Use this skill for a full first-user validation of WorldFork from a clean environment. The goal is to prove the `worldfork` CLI can be installed, discovered, operated end to end, and audited by an agent that starts with no local assumptions.

## Non-Negotiables

- Use a fresh disposable clone or worktree. Do not use a dirty development checkout as the test target.
- Test the `dev` branch unless the user explicitly requests another branch.
- Install the single public WorldFork skill with `npx skills add` as part of the fresh setup.
- Use the `worldfork` CLI as the primary control surface. Use `worldfork query` only when command discovery shows no first-class CLI verb for an API operation.
- Put global flags before the command: `worldfork --verbosity summary runs list`.
- Start broad inspection with `--verbosity summary`; use `--fields` for large rows.
- Do not hardcode backend URLs. Use CLI defaults, `--base-url`, `WORLD_FORK_API_BASE`, or `BACKEND_API_BASE`.
- All live API-credit work must use the configured route policy unless the user explicitly authorizes a different model route or mixed provider policy: keep cohort, hero, action, and event-summary work on a fast/cheap route such as `openrouter/deepseek/deepseek-v4-flash`, and keep initialization, God review, endpoint-ledger evaluation, and reports on a strong governance/report model route such as `openai-codex/gpt-5.4` or OpenRouter-hosted Kimi/Claude.
- Use subagents. If the host agent cannot spawn subagents, stop and report that the required execution mode is unavailable.
- Keep every mutation scoped to disposable local data created by this test.
- Use bounded waits. Never leave unbounded polling, watchers, or servers running at the end.
- Monitor Docker/container resource health for the whole runtime portion: CPU, memory, restart count, health status, container disk growth, host disk usage, Docker volume usage, and OOM/error events.
- Two ticks is only a setup smoke. Accuracy/runtime validation must include at least one long-horizon completed run configured for 30-35 ticks, with 35 as the default target.

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
  docker-stats.jsonl
  docker-ps.jsonl
  docker-events.log
  docker-inspect.json
  docker-system-df-before.txt
  docker-system-df-after.txt
  host-disk-before.txt
  host-disk-after.txt
  resource-summary.md
  failures.md
```

The final answer must include:

- Current branch, commit, and whether the test used a fresh clone or worktree.
- Backend base URL and Docker Compose project name if customized.
- Exact effective model route used for every audited LLM route and all live calls.
- Docker Compose project name, monitored container list, peak memory/CPU, disk growth, OOM/restart/health events, and whether host disk pressure occurred.
- Pass/fail/inconclusive status for setup, CLI coverage, delete, manual branching, auto branching, reports, logs/jobs, and accuracy sweep.
- IDs for the Big Bang, root multiverse, child branches, report versions, and any delete target.
- Links or paths to the artifact directory and the highest-signal logs.

## Subagent Plan

The coordinator owns the run directory, environment decisions, final verdict, and integration of results. Spawn subagents with disjoint write scopes:

- Setup subagent: fresh clone/worktree, skill installation, CLI installation, Docker startup, migrations, seed, readiness, and setup log.
- CLI coverage subagent: enumerate every available CLI command and subcommand, build `command-matrix.csv`, and classify commands as read-only, mutation, destructive, harness, or escape hatch.
- Runtime subagent: run the sample world flow, live smoke, Atlas demo, watch commands, report commands, job/log commands, and collect IDs.
- Branch/delete subagent: validate manual branching, auto branching, lineage, intervention records, and delete behavior against disposable resources.
- Resource monitor subagent: collect Docker stats/events/disk telemetry throughout runtime and summarize peak usage, growth, restarts, health failures, and cleanup state.
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
npx skills add ./skills/worldfork --all
npx skills add ./skills/worldfork-full-agent-test --all
python3.11 -m pip install -e ./cli
worldfork --help
```

Use local `./skills/...` paths after cloning `dev`; GitHub skill installs can resolve the default branch and miss dev-only skill changes.

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

Ensure `.env` has `OPENROUTER_API_KEY`, any required auth for the selected strong governance/report route is configured, and every configured WorldFork model slot resolves to the approved live-test split unless the user explicitly authorizes a different route policy:

```text
openrouter/deepseek/deepseek-v4-flash
strong governance/report route, such as openai-codex/gpt-5.4 or OpenRouter-hosted Kimi/Claude
```

When a different policy is authorized, configure it only through `worldfork settings providers` and `worldfork settings model-routing`, then record the full `worldfork settings llm` response. It is valid to keep frequent cohort, hero, action, and event-summary calls on a cheaper OpenRouter model to control cost and latency, but route higher-impact calls to a strong provider/model when quality matters:

- strong route candidates: `initializer_chunk_extractor`, `initializer_agent`, `god_agent`, `report_agent`, `endpoint_ledger`
- cheaper route candidates: `cohort_agent`, `hero_agent`, `event_summary`

For OpenAI Codex OAuth, use `worldfork settings openai-codex-login`; do not assume the Codex CLI is installed. The standard governance/report policy is a strong model route for initialization, God review, reports, and endpoint-ledger evaluation, with supported examples including `openai-codex/gpt-5.4` and OpenRouter-hosted Kimi/Claude. Use `openrouter/deepseek/deepseek-v4-flash` for `cohort_agent`, `hero_agent`, action execution, and event summaries when cost and latency matter. Restore previous route rows after a temporary experiment unless the user explicitly wants the mixed policy to remain.

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
worldfork settings llm
```

Only clear Redis if this is a disposable local Compose stack:

```bash
docker compose exec -T redis redis-cli FLUSHALL
```

## Docker Resource Monitoring

Start resource monitoring immediately before the first live runtime command and keep it running until reports and cleanup evidence are collected. Save raw telemetry under the run directory. Do not rely on screenshots or terminal memory.

Record a before snapshot:

```bash
docker compose ps > "$run_dir/docker-compose-ps-before.txt"
docker compose ps -q > "$run_dir/docker-container-ids.txt"
docker system df -v > "$run_dir/docker-system-df-before.txt"
df -h > "$run_dir/host-disk-before.txt"
du -sh runs artifacts agent-testing 2>/dev/null > "$run_dir/worktree-disk-before.txt" || true
```

Start background monitoring:

```bash
compose_project="${COMPOSE_PROJECT_NAME:-$(basename "$PWD")}"
printf '%s\n' "$compose_project" > "$run_dir/docker-compose-project.txt"

(
  while true; do
    ts="$(date -Is)"
    ids="$(docker compose ps -q)"
    if [ -n "$ids" ]; then
      docker stats --no-stream --format '{{json .}}' $ids \
        | jq -c --arg ts "$ts" '. + {timestamp:$ts}' \
        >> "$run_dir/docker-stats.jsonl"
      docker inspect $ids \
        | jq -c --arg ts "$ts" '.[] | {timestamp:$ts,name:.Name,id:.Id,state:.State,restart_count:.RestartCount,health:(.State.Health // null),mounts:.Mounts}' \
        >> "$run_dir/docker-inspect.jsonl"
    fi
    docker compose ps --format json \
      | jq -c --arg ts "$ts" '. + {timestamp:$ts}' \
      >> "$run_dir/docker-ps.jsonl" 2>/dev/null || true
    sleep 15
  done
) &
echo "$!" > "$run_dir/docker-monitor.pid"

docker events \
  --filter "label=com.docker.compose.project=$compose_project" \
  --format '{{json .}}' \
  > "$run_dir/docker-events.log" &
echo "$!" > "$run_dir/docker-events.pid"
```

If the Compose project name is customized with `docker compose -p`, set `COMPOSE_PROJECT_NAME` or `compose_project` to the same value before starting the monitor. If `docker compose ps --format json` is unavailable, record plain `docker compose ps` snapshots instead and note the fallback.

During long runs, add timestamped resource checkpoints before and after:

- initialization
- every 5 completed ticks
- branch creation/admission
- report generation
- PDF rendering, only when explicitly requested
- cleanup

At the end, stop monitors and capture after snapshots:

```bash
kill "$(cat "$run_dir/docker-monitor.pid")" 2>/dev/null || true
kill "$(cat "$run_dir/docker-events.pid")" 2>/dev/null || true
docker compose ps > "$run_dir/docker-compose-ps-after.txt"
docker inspect $(cat "$run_dir/docker-container-ids.txt") > "$run_dir/docker-inspect.json" 2>/dev/null || true
docker system df -v > "$run_dir/docker-system-df-after.txt"
df -h > "$run_dir/host-disk-after.txt"
du -sh runs artifacts agent-testing 2>/dev/null > "$run_dir/worktree-disk-after.txt" || true
```

Write `resource-summary.md` with:

- peak memory per container
- peak CPU per container
- final container status and health
- restart count changes
- OOMKilled or error states
- Docker volume/image/cache growth from before to after
- host disk free-space change
- artifact directory size
- whether resource pressure plausibly affected runtime accuracy or failures

Resource monitoring failures do not automatically fail WorldFork, but missing resource telemetry makes the full-agent test incomplete.

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

Run a small disposable sample first. This is only a readiness smoke and must not be counted as the accuracy/runtime depth requirement:

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
worldfork reports pack <big-bang-id> --mode summary
worldfork reports adjudicate <big-bang-id>
worldfork reports adjudication <big-bang-id>
worldfork settings llm
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
- report evidence packs include compact endpoint-ledger and timeline-adjudication sections
- timeline adjudication reports retained/pruned path mass without mutating source timelines
- Markdown report views are available; PDF renders are available only when explicitly requested
- failed job/log lists are inspectable
- all audited LLM calls use the configured approved route policy

## Long-Horizon Runtime Run

After readiness, run at least one long-horizon simulation to terminal completion. Default to 35 ticks; 30 is the minimum acceptable target when runtime or provider limits force a smaller run. Two ticks is not enough for accuracy conclusions, branch-pressure assessment, resource behavior, or report quality.

The maintained continuation harness lives in this skill at `scripts/overnight_35tick_runner.py`. Use it for overnight continuation of the full-runtime case set instead of copying ad hoc scripts into `agent-testing/`. It enforces a hard 35-tick completion gate, captures Docker stats/inspect/events/disk snapshots, records config snapshots, collects endpoint ledger detail payloads, and writes the final resource/report summary after monitor shutdown. Set `WF_MODEL` only when the user explicitly authorizes a non-default model for the live run. `WF_OVERNIGHT_CONCURRENCY` is the starting concurrency and `WF_OVERNIGHT_MAX_CONCURRENCY` is the optional scale-up ceiling. The harness increases concurrency only after successful completed cases and writes changes to `concurrency-events.jsonl`. If an actual provider 429/rate-limit error appears, that evidence overrides the configured concurrency and the harness drops to one active case before cooling down.

Run it from a disposable checkout that already has `full-runtime-cases.jsonl` and the matching Docker Compose stack/database:

```bash
WF_REPO="$PWD" \
WF_ARTIFACT_ROOT="$PWD/agent-testing/full-runtime-accuracy/<timestamp>" \
WF_CASES_FILE="$PWD/agent-testing/full-runtime-accuracy/<timestamp>/full-runtime-cases.jsonl" \
WF_COMPOSE_PROJECT="<compose-project-used-for-the-stack>" \
WF_COMPOSE_OVERRIDE="$PWD/agent-testing/full-runtime-accuracy/<timestamp>/docker-compose.override.runtime.yml" \
WF_OVERNIGHT_CONCURRENCY=6 \
WF_OVERNIGHT_MAX_CONCURRENCY=8 \
WF_TARGET_MAX_TICKS=35 \
python skills/worldfork-full-agent-test/scripts/overnight_35tick_runner.py
```

By default the harness does not render PDFs or preserve local PDF outputs, because generated render files are regenerable and should not be saved. It keeps structured report JSON and Markdown views only. Set `WF_RENDER_REPORT_PDFS=1` only when PDF rendering itself is being tested. Set `WF_COPY_RENDERED_ARTIFACTS=1` only when the user explicitly needs local PDF files, and cap the copy with `WF_RENDERED_COPY_LIMIT_MIB` (default `1024`). If `WF_ARTIFACT_ROOT` is omitted, the script uses the newest `agent-testing/full-runtime-accuracy/*/full-runtime-cases.jsonl` directory. Continuation runs inherit the existing `BigBangConfig.branch_policy`; use a fresh `worldfork init --branch-policy ...` run for branch-threshold experiments.

The harness must treat transient `run-until-complete` failures as retryable, especially `LLM unavailable`, provider rate limits, HTTP 5xx, and timeouts. It uses one initial attempt plus at least ten retries by default. Raise `WF_RUN_UNTIL_COMPLETE_RETRIES` only when a run is expected to be especially noisy; do not lower it below ten. Use `WF_OVERNIGHT_SCALE_AFTER_SUCCESSES` to control how many completed cases are required before scaling up by one slot. Use `WF_RATE_LIMIT_COOLDOWN_SECONDS` to lengthen the cooldown after a real 429.

Preferred new-run command:

```bash
worldfork init \
  --name "Full agent long horizon" \
  --scenario-file examples/test-big-bang.md \
  --max-ticks 35 \
  --tick-duration-minutes 720 \
  --branch-policy '{"max_branch_depth":3,"max_active_multiverses":8,"max_branches_per_tick":2,"branch_score_threshold":0.55}' \
  --wait-timeout 900

worldfork query POST /api/big-bangs/<big-bang-id>/run-until-complete \
  --data '{"max_total_ticks":240}'
```

Use `worldfork watch big-bang <big-bang-id>` or repeated `worldfork watch big-bang <big-bang-id> --once` snapshots while the run progresses. Every 5 ticks, capture:

```bash
worldfork --verbosity summary runs workspace <big-bang-id>
worldfork --fields id,status,tick_index,summary query GET /api/multiverses/<multiverse-id>/ticks
worldfork --verbosity normal logs list --run-id <big-bang-id> --source llm
worldfork jobs list --run-id <big-bang-id>
```

The long-horizon run must collect:

- latest tick index and terminal status for every multiverse
- admitted branch count and lineage edges
- branch caps and God-agent decisions
- endpoint ledger versions and entries
- LLM model audit at normal verbosity, not summary-only
- failed jobs/logs
- multiverse reports and final Big Bang report
- Markdown report views; PDF output files only when explicitly requested
- Docker telemetry covering the whole run

### Continuing A Short Run To 35 Ticks

If a previous disposable run stopped after 2 ticks, prefer continuing it instead of discarding evidence, but only through public runtime APIs.

Continuation is not the job `resume` command. Job `resume` only resumes paused/interrupted jobs. For a terminal multiverse whose `max_ticks` was too low, use the multiverse continuation API:

```bash
worldfork query POST /api/multiverses/<multiverse-id>/continue \
  --data '{"max_ticks":35,"reason":"extend full-agent accuracy run to long horizon"}'

worldfork query POST /api/big-bangs/<big-bang-id>/run-until-complete \
  --data '{"max_total_ticks":240}'
```

Preconditions:

- the Docker stack/database still exists; `docker compose down` is OK, but `docker compose down -v` removes the DB volume and prevents continuation
- the target multiverse is terminal; if it is still active, keep running it or let it reach terminal first
- `max_ticks` must be greater than the latest tick index
- if a multiverse report already exists, preserve its report version ID as `continued_from_report_version_id` when useful for provenance

After continuation, verify:

- the multiverse `version` increments
- `state.runtime_config_version` and `state.runtime_overrides.max_ticks` show 35
- an operation log contains `multiverse_continued`
- new ticks are appended after the prior latest tick, not replacing old ticks
- reports after continuation bind to the new multiverse version

If continuation fails through public APIs, record the blocker and start a fresh 35-tick run. Do not patch the database directly for a benchmark pass.

Run the Atlas sample world demo after the small smoke passes:

```bash
worldfork demo atlas \
  --scenario-file examples/test-big-bang.md \
  --horizon-days 18 \
  --tick-duration-minutes 720 \
  --max-active-multiverses 8 \
  --max-branch-depth 3 \
  --max-branches-per-tick 2 \
  --branch-score-threshold 0.0 \
  --completion-max-requests 800
```

If the short Atlas run does not create an auto branch, rerun once with the same branch caps and `--branch-score-threshold 0.0`. If no auto branch appears after that, inspect God review outputs and record whether no branch candidates were proposed or whether branch admission failed.

## Reports And Evidence

For each Big Bang produced by the smoke and Atlas runs:

```bash
worldfork reports list <big-bang-id>
worldfork reports versions <report-id>
worldfork reports view <report-version-id>
worldfork reports view <report-version-id> --format json
```

Do not render PDFs during default accuracy or tick-only runs. Run `worldfork reports render <report-version-id> --format pdf --output report.pdf` only when the user explicitly asks for a PDF file or when PDF rendering is the behavior under test. Delete generated local render files after collecting the requested evidence unless the user asked to keep them.

Expected behavior:

- per-multiverse reports bind to the matching multiverse version
- final Big Bang report compares every terminal multiverse
- report content has an executive summary or equivalent structured summary
- outcome distribution is present for the final report
- explicit PDF renders return ephemeral render metadata, and any local `--output` file exists only because it was requested

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

Use the approved configured split unless the user explicitly authorizes a different model or mixed provider policy: `openrouter/deepseek/deepseek-v4-flash` is a supported fast/cheap route for cohort, hero, action, and event-summary work, while initialization, God review, endpoint-ledger evaluation, and reports should use a strong governance/report model route such as `openai-codex/gpt-5.4` or OpenRouter-hosted Kimi/Claude. When a single-model override is authorized, pass it through `WF_MODEL` so the harness records the configured model in `run-config.json` and injects it into every WorldFork model slot. When a mixed route policy is authorized, patch `worldfork settings model-routing`, capture `worldfork settings llm`, and verify audited LLM logs include the expected provider/model for each route. The default full benchmark is 72 initialization prompts from the bundled JSONL file. For a faster smoke, sample 12 cases while preserving the category quotas in the SOP. For a stronger study, expand to 96-100 cases by adding prompts that follow the same JSONL schema and taxonomy.

Collect initialization and audit evidence with CLI-first commands:

```bash
worldfork init --name "<case-id>" --scenario-file <case-file> --max-ticks 35 --tick-duration-minutes 720 --wait-timeout 900
worldfork query POST /api/big-bangs/<big-bang-id>/run-until-complete --data '{"max_total_ticks":240}'
worldfork query GET /api/big-bangs/<big-bang-id>/initialization
worldfork query GET /api/big-bangs/<big-bang-id>/initialization/actors
worldfork query GET /api/big-bangs/<big-bang-id>/initialization/traits
worldfork query GET /api/big-bangs/<big-bang-id>/initialization/graphs
worldfork query GET /api/big-bangs/<big-bang-id>/initialization/sociology-baseline
worldfork query GET /api/big-bangs/<big-bang-id>/initialization/emotion-baseline
worldfork query GET /api/big-bangs/<big-bang-id>/initialization/audit
worldfork --verbosity normal --fields id,source,status,message,provider,model,big_bang_id logs list --run-id <big-bang-id> --source llm
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
- resource scores: memory growth, CPU saturation, container restarts, OOM/error states, disk growth, and whether resource pressure affected the run
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
