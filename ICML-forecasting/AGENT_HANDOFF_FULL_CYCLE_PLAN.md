# WorldFork ICML Forecasting Paper: Agent Handoff Full-Cycle Plan

**Purpose.** This is a handoff document for an autonomous coding/research agent. Execute the full cycle: set up WorldFork, validate the benchmark cards, run the benchmark matrix, score outputs, produce paper-ready tables/figures, and draft the ICML forecasting-workshop paper.

**Target paper framing.** Use the title/claim: **WorldFork: Auditable Branching Socio-Institutional World Models for LLM Forecasting Agents**. The paper is about forecasting agents and evaluation, not a generic social-simulation demo. The core artifact is a forecasting protocol that exposes branch lineage, endpoint ledgers, path mass, report provenance, unresolved uncertainty, and socio-institutional state. Emotion is included as an observability layer only; do not claim validated human psychology.

**Non-negotiable principle.** Do not show `worldfork_additional_36_private_eval.jsonl` to any model that is producing forecasts. Use it only after forecast artifacts are frozen. If any run or scoring script leaks private resolutions into model input, mark that condition invalid and rerun.

---

## 0. 统筹 / Overall Coordination Plan

### 0.1 Definition of done

The work is complete only when the agent has produced these artifacts under a timestamped run directory, for example `paper_runs/worldfork_icml_<YYYYMMDD-HHMMSS>/`:

```text
paper_runs/worldfork_icml_<timestamp>/
  README.md
  setup/
    environment.md
    worldfork_status.json
    model_routing.json
    docker_resource_summary.md
  cases/
    existing_72/*.md
    additional_36/*.md
  manifests/
    benchmark_case_manifest.jsonl
    run_manifest.jsonl
    direct_baseline_manifest.jsonl
    worldfork_manifest.jsonl
    scoring_manifest.jsonl
  raw/
    <condition>/<case_id>/...
  results/
    forecast_scores.csv
    audit_scores.csv
    init_scores.csv
    social_state_scores.csv
    card_quality_report.md
    aggregate_results.md
    bootstrap_intervals.json
    e4_bootstrap_intervals.json
    e4_runtime_cost_summary.csv
  paper/
    paper_outline.md
    paper_draft.md
    tables/*.csv
    figures/*.png
    appendix_artifact_index.md
  failures.md
```

The final paper draft must include: method, benchmark, forecast scoring, WorldFork audit metrics, socio-institutional state evaluation, limitations, and anonymized reproducibility notes.

### 0.2 Workstreams and agent roles

If the host agent can spawn subagents, use these roles. If not, execute them serially in this order and keep the same artifact boundaries.

| Role | Responsibility | Must produce |
|---|---|---|
| Coordinator / 统筹 | Owns run directory, timeline, priority decisions, stop/go criteria, final integration | `README.md`, final status, failure triage |
| Setup agent | Fresh checkout/worktree, environment, Docker, migrations, model routing, smoke | `setup/environment.md`, `worldfork_status.json`, `model_routing.json` |
| Card QA agent | Validates public/private card separation, source/resolution checks, schema checks | `results/card_quality_report.md` |
| Benchmark runner | Runs baselines and WorldFork benchmark conditions | `manifests/run_manifest.jsonl`, raw outputs |
| Scoring agent | Computes Brier/log scores, audit rubrics, social-state scores, bootstrap CIs | `results/*.csv`, `aggregate_results.md` |
| Paper agent | Writes the 4-page paper and appendix from scored artifacts only | `paper/paper_draft.md`, tables, figure plan |
| Reviewer agent | Checks claims against evidence, leakage, overclaiming, reproducibility | `paper/review_notes.md`, `failures.md` updates |

### 0.3 Priority order when time or API budget is tight

Run in this exact priority order. Do not skip a higher-priority block to run a lower-priority block.

1. **P0 setup and card QA**: prove environment, verify cards, confirm model routing, prevent leakage.
2. **P1 direct forecasting baselines on all 24 resolved cards**: needed for Brier/log score.
3. **P2 WorldFork init screen on all 108 public cards**: existing 72 + new 36.
4. **P3 WorldFork short forecast runs on the 24 resolved cards**: no-branch and branching.
5. **P4 long-horizon audit runs on 18 cases**: 6 existing hard cases + 8 dossiers + 4 calibration cases.
6. **P5 social-state/emotion audit and optional ablation**: score all long-horizon runs; run ablation only if feasible.
7. **P6 branch-threshold sweep**: stretch analysis only.

If a block partially completes, keep all raw data and report it honestly. Partial completion is acceptable; hidden failure is not.

---

## 1. Repository and Setup Instructions

### 1.1 Fresh environment

Use a fresh disposable clone or worktree. Do not run paper experiments in a dirty development checkout unless explicitly necessary.

```bash
run_root="$PWD/paper_runs/worldfork_icml_$(date +%Y%m%d-%H%M%S)"
export RUN_ROOT="$run_root"
mkdir -p "$run_root/setup" "$run_root/manifests" "$run_root/results" "$run_root/raw" "$run_root/paper"
cd "$run_root"

# Use the local uploaded repo if available; otherwise clone the appropriate repo.
# If starting from a zip:
unzip /path/to/WorldFork-main.zip -d .
cd WorldFork-main

python3.11 -m pip install -e ./cli
worldfork --help
```

Prepare environment:

```bash
cp .env.example .env
# Put OPENROUTER_API_KEY and other required provider settings in .env.
# Configure OpenAI Codex OAuth if using it for governance/report routes.
worldfork settings openai-codex-login || true

make build
make up
make migrate
make seed

worldfork status | tee "$run_root/setup/worldfork_status.txt"
worldfork query GET /readyz --no-api-prefix | tee "$run_root/setup/readyz.json"
worldfork agent discover | tee "$run_root/setup/agent_discover.json"
worldfork settings llm | tee "$run_root/setup/model_routing.json"
worldfork settings branch-policy | tee "$run_root/setup/branch_policy.json"
```

Live calls should use the default ICML route policy unless explicitly changed and recorded. This is the default benchmark approach, not an ablation:

- `cohort_agent`: OpenRouter `deepseek/deepseek-v4-flash`.
- `hero_agent`: OpenRouter `deepseek/deepseek-v4-flash`.
- other high-volume event/action routes: use the configured fast OpenRouter route unless a run manifest states otherwise.
- governance/report routes (`initializer_agent`, `god_agent`, `endpoint_ledger`, `report_agent`): use a strong configured route, such as OpenAI Codex `gpt-5.4` or an OpenRouter-hosted Kimi/Claude model.

Codex-only live rows are valid smoke/ablation evidence only. Do not aggregate them with the default ICML route-policy rows unless the table explicitly separates route policy.

After provider and routing edits, verify the executable provider path before launching default E3/E4/E5 rows:

```bash
worldfork settings provider-test openrouter | tee "$run_root/setup/openrouter_provider_test_before_batch.json"
```

If `worldfork settings llm` shows OpenRouter configured but the provider test says `provider 'openrouter' not registered`, refresh the provider row through `worldfork settings providers --data ...` or restart the backend, then repeat the provider test. Do not treat DeepSeek cohort/hero routing as ready until the provider test passes.

Before every benchmark batch, capture:

```bash
worldfork settings llm > "$run_root/setup/model_routing_before_batch.json"
worldfork --verbosity summary runs list > "$run_root/setup/runs_before_batch.json"
docker compose ps > "$run_root/setup/docker_ps_before_batch.txt"
docker system df -v > "$run_root/setup/docker_df_before_batch.txt"
```

### 1.2 Resource monitoring

Start Docker monitoring before live WorldFork runtime runs. Save raw telemetry. At minimum:

```bash
mkdir -p "$run_root/setup/resource_monitor"
(
  while true; do
    ts="$(date -Is)"
    ids="$(docker compose ps -q)"
    if [ -n "$ids" ]; then
      docker stats --no-stream --format '{{json .}}' $ids \
        | jq -c --arg ts "$ts" '. + {timestamp:$ts}' \
        >> "$run_root/setup/resource_monitor/docker-stats.jsonl"
      docker inspect $ids \
        | jq -c --arg ts "$ts" '.[] | {timestamp:$ts,name:.Name,id:.Id,state:.State,restart_count:.RestartCount,health:(.State.Health // null)}' \
        >> "$run_root/setup/resource_monitor/docker-inspect.jsonl"
    fi
    sleep 15
  done
) &
echo $! > "$run_root/setup/resource_monitor/monitor.pid"
```

Stop it at the end:

```bash
kill "$(cat "$run_root/setup/resource_monitor/monitor.pid")" 2>/dev/null || true
docker compose ps > "$run_root/setup/docker_ps_after.txt"
docker system df -v > "$run_root/setup/docker_df_after.txt"
```

---

## 2. Benchmark Inputs: Exactly What to Benchmark

### 2.1 Public inputs

Use these public/model-facing files:

1. Existing WorldFork benchmark: `skills/worldfork-full-agent-test/references/accuracy-benchmark-prompts.jsonl`.
2. New add-on benchmark: `worldfork_additional_36_public.jsonl` from this zip.
3. Legacy schema file only when a repo script requires the old format: `worldfork_additional_36_legacy_schema.jsonl`.

Use this private file **only after forecasts are frozen**:

- `worldfork_additional_36_private_eval.jsonl`.

### 2.2 Existing 72-case screen

Run **all 72** existing cases for the init/audit screen.

- `civic_policy` (6): `civic_001`, `civic_002`, `civic_003`, `civic_004`, `civic_005`, `civic_006`
- `labor_economic_dependency` (6): `labor_001`, `labor_002`, `labor_003`, `labor_004`, `labor_005`, `labor_006`
- `platform_algorithmic_governance` (6): `platform_001`, `platform_002`, `platform_003`, `platform_004`, `platform_005`, `platform_006`
- `public_health_resource` (6): `health_001`, `health_002`, `health_003`, `health_004`, `health_005`, `health_006`
- `campus_institutional_legitimacy` (6): `campus_001`, `campus_002`, `campus_003`, `campus_004`, `campus_005`, `campus_006`
- `media_information_dynamics` (6): `media_001`, `media_002`, `media_003`, `media_004`, `media_005`, `media_006`
- `institutional_governance` (6): `governance_001`, `governance_002`, `governance_003`, `governance_004`, `governance_005`, `governance_006`
- `adversarial_edge` (6): `edge_001`, `edge_002`, `edge_003`, `edge_004`, `edge_005`, `edge_006`
- `report_quality_probe` (6): `report_001`, `report_002`, `report_003`, `report_004`, `report_005`, `report_006`
- `longform_initializer` (6): `longform_001`, `longform_002`, `longform_003`, `longform_004`, `longform_005`, `longform_006`
- `election_legitimacy` (4): `election_001`, `election_002`, `election_003`, `election_004`
- `housing_zoning` (4): `housing_001`, `housing_002`, `housing_003`, `housing_004`
- `corporate_regulatory_endpoint` (4): `corporate_001`, `corporate_002`, `corporate_003`, `corporate_004`

### 2.3 New 36-card add-on

Run all 36 new public cards for initialization/card handling. Split them as:

- Resolved forecast cards, 24: `resolved_001`, `resolved_002`, `resolved_003`, `resolved_004`, `resolved_005`, `resolved_006`, `resolved_007`, `resolved_008`, `resolved_009`, `resolved_010`, `resolved_011`, `resolved_012`, `resolved_013`, `resolved_014`, `resolved_015`, `resolved_016`, `resolved_017`, `resolved_018`, `resolved_019`, `resolved_020`, `resolved_021`, `resolved_022`, `resolved_023`, `resolved_024`
- Longform dossier cards, 8: `dossier_001`, `dossier_002`, `dossier_003`, `dossier_004`, `dossier_005`, `dossier_006`, `dossier_007`, `dossier_008`
- Adversarial/calibration cards, 4: `calibration_001`, `calibration_002`, `calibration_003`, `calibration_004`

### 2.4 Core long-horizon cases

Run these 18 cases for long-horizon WorldFork evaluation:

`civic_002`, `labor_002`, `platform_004`, `health_004`, `election_002`, `corporate_004`, `dossier_001`, `dossier_002`, `dossier_003`, `dossier_004`, `dossier_005`, `dossier_006`, `dossier_007`, `dossier_008`, `calibration_001`, `calibration_002`, `calibration_003`, `calibration_004`

Reason: this set combines 6 hard existing synthetic institutional cases, all 8 source-packet dossier cases, and all 4 uncertainty/adversarial cards. It is the paper's best evidence for lineage, branching, endpoint ledgers, social-state claims, report grounding, and unresolved uncertainty.

### 2.5 Core social-state/emotion analysis cases

Score social-state and emotion observability on all 18 long-horizon cases. If an ablation toggle is available or can be safely added, run the ablation on exactly these 8 cases:

`civic_002`, `labor_002`, `platform_004`, `health_004`, `dossier_001`, `dossier_003`, `dossier_004`, `dossier_006`

### 2.6 Branch-threshold sweep cases, optional/stretch

Run only if P0-P5 are done. Use exactly these 6 cases:

`civic_002`, `labor_002`, `platform_004`, `health_004`, `dossier_001`, `dossier_004`

Sweep thresholds: `0.55`, `0.75`, `0.95`.

---

## 3. Experiment Matrix

### 3.1 Required experiments

| Experiment ID | Cases | Conditions | Tick cap / stop rule | Branch policy | Main metrics |
|---|---:|---|---:|---|---|
| E0 Card QA | 108 public + 36 private eval | static validation only | n/a | n/a | schema, leakage, endpoint quality, source/resolution verification |
| E1 Init screen | 108 public | WorldFork init only | no runtime | no runtime | schema completeness, actor recall, authority fidelity, graph/sociology/emotion baseline, uncertainty |
| E2 Direct baselines | 24 resolved | direct, structured-direct | n/a | n/a | Brier, log score, calibration, JSON validity |
| E3 Resolved WorldFork short | 24 resolved | no-branch, branching | 8 cap; stop on ledger/path-mass resolution | see below | Brier, log score, unresolved mass, endpoint coverage, cost |
| E4 Long-horizon audit | 18 cases | full branching | 35 cap; stop early on ledger/path-mass resolution; 30 nominal lower bound only for unresolved non-fallback runs | see below | lineage, branch locality, path mass, report grounding, social-state consistency |
| E5 Social-state/emotion audit | 18 long-horizon cases | scoring only | same as E4 | same as E4 | social mechanism grounding, graph consistency, emotion observability |

### 3.2 Stretch experiments

| Experiment ID | Cases | Conditions | Max ticks | Purpose |
|---|---:|---|---:|---|
| S1 Social-state ablation | 8 cases | full WorldFork vs no-sociology prompt influence | 20-35 | test whether socio-institutional context changes branch/report quality |
| S2 Branch-threshold sweep | 6 cases × 3 thresholds | threshold 0.55/0.75/0.95 | 20 | branch sensitivity and cost/quality tradeoff |
| S3 Deep representative runs | 3 cases | full branching, 50 ticks | 50 | only if all core tables are done |

Do not run stretch experiments until E0-E5 are complete.

---

## 4. Prepare Case Files

Create one markdown scenario file per public card. Do not include private eval fields.

```bash
mkdir -p "$run_root/cases/existing_72" "$run_root/cases/additional_36"
python - <<'PY'
import json, pathlib, os, hashlib
run_root = pathlib.Path(os.environ.get('RUN_ROOT', '.'))
existing = pathlib.Path('skills/worldfork-full-agent-test/references/accuracy-benchmark-prompts.jsonl')
additional = pathlib.Path('/path/to/worldfork_additional_36_public.jsonl')
out_existing = run_root/'cases/existing_72'
out_add = run_root/'cases/additional_36'
out_existing.mkdir(parents=True, exist_ok=True)
out_add.mkdir(parents=True, exist_ok=True)

def write_case(obj, out_dir):
    case_id = obj['case_id']
    role = obj.get('benchmark_role', obj.get('category', 'worldfork_case'))
    question = obj.get('question', '')
    scenario = obj.get('scenario_text') or obj.get('prompt') or ''
    src_packet = obj.get('source_packet') or []
    endpoints = obj.get('candidate_endpoints') or obj.get('endpoints') or []
    parts = [f'# Case {case_id}', f'Benchmark role: {role}']
    if question:
        parts.append(f'Forecast question: {question}')
    if scenario:
        parts.append('## Scenario')
        parts.append(scenario)
    if src_packet:
        parts.append('## Source Packet')
        for i,s in enumerate(src_packet,1):
            parts.append(f"### Source {i}: {s.get('source_type','source')} / {s.get('date','undated')}")
            parts.append(s.get('text',''))
    if endpoints:
        parts.append('## Candidate endpoints')
        for e in endpoints:
            if isinstance(e, dict):
                parts.append(f"- {e.get('id','endpoint')}: {e.get('label', e.get('description',''))}")
            else:
                parts.append(f'- {e}')
    for key in ['must_include_actors','must_preserve_constraints','expected_uncertainties','forbidden_errors','scoring_notes']:
        val = obj.get(key)
        if val:
            parts.append(f'## {key}')
            if isinstance(val, list):
                parts.extend(f'- {x}' for x in val)
            else:
                parts.append(str(val))
    p = out_dir/f'{case_id}.md'
    p.write_text('\n\n'.join(parts), encoding='utf-8')
    return p

manifest = []
for path,out,label in [(existing,out_existing,'existing_72'),(additional,out_add,'additional_36')]:
    with path.open() as f:
        for line in f:
            obj=json.loads(line)
            p=write_case(obj,out)
            manifest.append({
                'case_id': obj['case_id'],
                'group': label,
                'benchmark_role': obj.get('benchmark_role', obj.get('category')),
                'difficulty': obj.get('difficulty'),
                'path': str(p),
                'sha256': hashlib.sha256(p.read_bytes()).hexdigest(),
            })
(run_root/'manifests').mkdir(parents=True, exist_ok=True)
with (run_root/'manifests/benchmark_case_manifest.jsonl').open('w') as f:
    for row in manifest:
        f.write(json.dumps(row, ensure_ascii=False)+'\n')
PY
```

---

## 5. E0: Card Quality and Leakage QA

### 5.1 Required checks

Before running live simulations, validate the cards.

For all 108 public cases:

- JSON parses.
- `case_id` is unique.
- Public card contains no `resolution`, `resolution_date`, or private answer field.
- Forecast cards have mutually exclusive candidate endpoints.
- Dossier cards have source packets, not only instructions to invent a dossier.
- Calibration cards explicitly test uncertainty/no-fabrication behavior.

For the 24 resolved private eval cards:

- The private `case_id` matches one public resolved card.
- Resolution is binary `yes`/`no` unless explicitly multiclass.
- Resolution date and source are present.
- The event was truly resolved by the stated date.
- As-of date precedes resolution date.
- Public scenario does not reveal the answer.
- If an official source or high-authority source contradicts the private eval, correct the private eval, record the correction, and do not score until corrected.

Write `results/card_quality_report.md` with:

```text
- number of public cards checked
- number of private eval cards checked
- leakage failures
- source verification failures
- corrected cards, if any
- cards excluded from scoring, if any
```

Do not let card QA become an endless research project. Spend enough time to verify correctness of the 24 resolved cards, then proceed.

---

## 6. E1: WorldFork Initialization Screen on 108 Public Cards

### 6.1 Run shape

Run all 108 public cases through `worldfork init`. Do **not** run ticks for E1. The purpose is T0 initialization quality, not forecast accuracy.

Command template:

```bash
case_file="$run_root/cases/<group>/<case_id>.md"
out_dir="$run_root/raw/E1_init/<case_id>"
mkdir -p "$out_dir"

worldfork init \
  --name "E1_init_<case_id>" \
  --scenario-file "$case_file" \
  --max-ticks 1 \
  --tick-duration-minutes 720 \
  --branch-policy '{"max_branch_depth":1,"max_active_multiverses":1,"max_branches_per_tick":1,"branch_score_threshold":0.999}' \
  --wait-timeout 900 \
  | tee "$out_dir/init_stdout.json"
```

After each init, extract `big_bang_id` and root multiverse ID. Then capture:

```bash
worldfork query GET /api/big-bangs/<big_bang_id>/initialization > "$out_dir/initialization.json"
worldfork query GET /api/big-bangs/<big_bang_id>/initialization/actors > "$out_dir/actors.json"
worldfork query GET /api/big-bangs/<big_bang_id>/initialization/traits > "$out_dir/traits.json"
worldfork query GET /api/big-bangs/<big_bang_id>/initialization/graphs > "$out_dir/graphs.json"
worldfork query GET /api/big-bangs/<big_bang_id>/initialization/sociology-baseline > "$out_dir/sociology_baseline.json"
worldfork query GET /api/big-bangs/<big_bang_id>/initialization/emotion-baseline > "$out_dir/emotion_baseline.json"
worldfork query GET /api/big-bangs/<big_bang_id>/initialization/audit > "$out_dir/initialization_audit.json"
worldfork --verbosity normal --fields id,source,status,message,provider,model,big_bang_id logs list --run-id <big_bang_id> --source llm > "$out_dir/llm_logs.json"
worldfork --verbosity summary runs workspace <big_bang_id> > "$out_dir/workspace.json"
```

Append one row to `manifests/worldfork_manifest.jsonl`.

### 6.2 Init scoring rubric

Score 0-4 for each dimension:

| Dimension | Meaning |
|---|---|
| schema_completeness | required init objects exist and are non-empty |
| actor_recall_and_roles | important actors/cohorts appear with plausible roles |
| authority_fidelity | actors cannot do impossible legal/institutional actions |
| constraint_preservation | deadlines, laws, resources, contracts, and uncertainty are preserved |
| endpoint_extraction | candidate outcomes are explicit and not prematurely resolved |
| graph_quality | trust/dependency/conflict/influence/coalition/exposure edges are plausible |
| sociology_baseline_quality | silence pressure, mobilization, identity, attention, homophily, contagion are plausible if present |
| emotion_observability_quality | affective observations exist when relevant, are not saturated, and are not treated as real hidden emotions |
| prompt_injection_resistance | adversarial text is treated as data, not as system/developer instruction |
| uncertainty_honesty | missing facts are marked unknown/unresolved rather than invented |

Aggregate by category and difficulty. Main table: mean score, pass rate, critical failure rate.

---

## 7. E2: Direct Forecasting Baselines on the 24 Resolved Cards

### 7.1 Conditions

Run two retrieval-free direct baselines on all 24 resolved cards.

Cases: `resolved_001`, `resolved_002`, `resolved_003`, `resolved_004`, `resolved_005`, `resolved_006`, `resolved_007`, `resolved_008`, `resolved_009`, `resolved_010`, `resolved_011`, `resolved_012`, `resolved_013`, `resolved_014`, `resolved_015`, `resolved_016`, `resolved_017`, `resolved_018`, `resolved_019`, `resolved_020`, `resolved_021`, `resolved_022`, `resolved_023`, `resolved_024`

Conditions:

1. `direct_llm`: one forecast probability and short rationale.
2. `structured_direct_llm`: decomposition into drivers, uncertainties, base rates from card text, then probability.

Do not allow web browsing or private eval access during these forecasts.

### 7.2 Direct baseline prompt

System:

```text
You are a calibrated forecasting assistant. You are evaluating a resolved-but-hidden event, but you do not know the resolution. Use only the public card text provided by the user. Do not use web search. Do not infer from real-world memory if the card is entity-masked. Return valid JSON only.
```

User:

```text
Public forecast card:
<public card JSON or markdown here>

Return JSON only:
{
  "case_id": "...",
  "condition": "direct_llm",
  "p_yes": <number 0 to 1>,
  "p_no": <number 0 to 1>,
  "confidence": "low|medium|high",
  "main_drivers": ["..."],
  "main_uncertainties": ["..."],
  "leakage_warning": "none|possible_real_world_memory|other"
}
Rules: p_yes + p_no must equal 1. Do not mention the true resolution.
```

### 7.3 Structured-direct prompt

System is the same. User:

```text
Public forecast card:
<public card JSON or markdown here>

Think in a calibrated forecasting style, but output JSON only. Use only the card.
Return:
{
  "case_id": "...",
  "condition": "structured_direct_llm",
  "evidence_for_yes": ["..."],
  "evidence_for_no": ["..."],
  "base_rate_or_analogies_from_card_only": ["..."],
  "key_uncertainties": ["..."],
  "p_yes": <number 0 to 1>,
  "p_no": <number 0 to 1>,
  "calibration_note": "...",
  "leakage_warning": "none|possible_real_world_memory|other"
}
Rules: p_yes + p_no must equal 1. Do not use web. Do not claim to know the resolution.
```

### 7.4 Direct scoring

After outputs are frozen, load private eval and compute:

- Binary Brier: `(p_yes - y)^2` where `y = 1` for yes and `0` for no.
- Log score / negative log likelihood: `-log(clamp(p_true, 0.01, 0.99))`.
- JSON validity rate.
- Leakage-warning rate.
- Calibration by coarse bins if at least 24 outputs exist.

Save `results/forecast_scores.csv` rows for both conditions.

---

## 8. E3: WorldFork Short Forecast Runs on the 24 Resolved Cards

### 8.1 Conditions and run configs

Run all 24 resolved cards under two WorldFork conditions.

**Condition A: `worldfork_no_branch_short`**

```json
{"max_branch_depth":1,"max_active_multiverses":1,"max_branches_per_tick":1,"branch_score_threshold":0.999}
```

`max_ticks=8` was the original pilot cap, but corrected paper rows must use
deadline-aware tick duration. Compute the per-card simulated horizon from the
public `as_of_date` to the public forecast deadline, and treat `max_ticks` as
the granularity cap. Do not use fixed 720-minute ticks for new E3 paper rows.

**Condition B: `worldfork_branching_short`**

```json
{"max_branch_depth":2,"max_active_multiverses":4,"max_branches_per_tick":1,"branch_score_threshold":0.75}
```

`max_ticks=8` was the original pilot cap, but corrected paper rows must use
deadline-aware tick duration. For branching rows, prefer a small representative
slice before scaling because branching can multiply active timelines and runtime
cost.

If cost or provider limits prevent running all 24 WorldFork short cases, use this exact fallback subset and record the downgrade: `resolved_001`, `resolved_003`, `resolved_005`, `resolved_007`, `resolved_009`, `resolved_011`, `resolved_013`, `resolved_015`, `resolved_017`, `resolved_019`, `resolved_021`, `resolved_023`. Baselines must still run all 24.

Tick counts are caps, not targets. Do not spend extra ticks merely to reach 16,
32, or 35 when the explicit `yes`/`no` endpoint ledger has naturally resolved.
Auxiliary mechanism endpoints, branch hypotheses, or known-uncertainty rows are
audit evidence only; they must not keep the binary forecast unresolved. Existing
fixed-720-minute E3 Big Bangs are suspect pilot rows. Reinitialize for corrected
paper rows unless the source Big Bang already has public forecast-clock metadata
and structured `candidate_endpoints` in `scenario_input`.

Use maximum useful parallelism, not maximum possible duplicate work. Before
starting new runs, check active jobs, live wait sessions, and existing artifacts;
resume or recover those resources first. If provider logs show sustained
OpenRouter 429s, hold worker fan-out steady or drain it, then add only patched
capacity when the active pool drops.

### 8.2 WorldFork run command template

```bash
condition="worldfork_branching_short"  # or worldfork_no_branch_short
case_id="resolved_001"
case_file="$run_root/cases/additional_36/$case_id.md"
out_dir="$run_root/raw/E3_${condition}/$case_id"
mkdir -p "$out_dir"

if [ "$condition" = "worldfork_no_branch_short" ]; then
  branch_policy='{"max_branch_depth":1,"max_active_multiverses":1,"max_branches_per_tick":1,"branch_score_threshold":0.999}'
else
  branch_policy='{"max_branch_depth":2,"max_active_multiverses":4,"max_branches_per_tick":1,"branch_score_threshold":0.75}'
fi

worldfork init \
  --name "E3_${condition}_${case_id}" \
  --scenario-file "$case_file" \
  --max-ticks 16 \
  --tick-duration-minutes "<computed from public deadline horizon>" \
  --branch-policy "$branch_policy" \
  --wait-timeout 900 \
  | tee "$out_dir/init_stdout.json"

# Extract big_bang_id from init output.
worldfork query POST /api/big-bangs/<big_bang_id>/run-until-complete \
  --data '{"max_total_ticks":80}' \
  | tee "$out_dir/run_until_complete.json"

worldfork ledgers evaluate <big_bang_id> --wait --timeout 180 | tee "$out_dir/ledgers_evaluate.json"
worldfork ledgers list <big_bang_id> | tee "$out_dir/ledgers_list.json"
worldfork ledgers path-mass <big_bang_id> | tee "$out_dir/path_mass.json"
worldfork reports adjudicate <big_bang_id> | tee "$out_dir/reports_adjudicate.json"
worldfork reports adjudication <big_bang_id> | tee "$out_dir/reports_adjudication.json"
worldfork reports generate final <big_bang_id> \
  --title "Final forecast report $case_id" \
  --summary "Cross-multiverse forecast review." \
  | tee "$out_dir/final_report_generate.json"
worldfork reports list <big_bang_id> | tee "$out_dir/reports_list.json"
worldfork runs cost <big_bang_id> --include-calls | tee "$out_dir/cost.json"
worldfork --verbosity normal logs list --run-id <big_bang_id> --source llm | tee "$out_dir/llm_logs.json"
worldfork --verbosity summary runs workspace <big_bang_id> | tee "$out_dir/workspace.json"
worldfork jobs list --run-id <big_bang_id> | tee "$out_dir/jobs.json"
worldfork logs list --status failed | tee "$out_dir/failed_logs_snapshot.json"
```

For reusable batch execution, prefer the repo script over hand-written loops:

```bash
python3 ICML-forecasting/scripts/icml_pipeline.py run-worldfork-short-batch \
  --run-root "$run_root" \
  --base-url http://127.0.0.1:18045 \
  --conditions worldfork_branching_short \
  --prediction-output raw/E3_worldfork_deadline_aware_branching_core12/worldfork_predictions.jsonl \
  --output-prefix raw/E3_worldfork_deadline_aware_branching_core12 \
  --route-policy-id icml_default_deepseek_v4_flash_cohort_hero_deadline_aware_core12 \
  --core12 \
  --max-ticks 16 \
  --stop-when-endpoint-ledger-resolved \
  --wait-timeout 21600
```

To extend an existing source ledger without reinitializing:

```bash
python3 ICML-forecasting/scripts/icml_pipeline.py resume-worldfork-short-batch \
  --run-root "$run_root" \
  --base-url http://127.0.0.1:18045 \
  --source-prediction-output raw/E3_worldfork_default_route_16tick/worldfork_predictions.jsonl \
  --source-route-policy-id icml_default_deepseek_v4_flash_cohort_hero \
  --prediction-output raw/E3_worldfork_default_route_35tick_resume/worldfork_predictions.jsonl \
  --route-policy-id icml_default_deepseek_v4_flash_cohort_hero_resume35 \
  --output-prefix raw/E3_worldfork_default_route_35tick_resume \
  --conditions worldfork_no_branch_short \
  --max-ticks 35 \
  --wait-timeout 21600
```

Use resume only for corrected deadline-aware source Big Bangs. Do not resume
the older fixed-720-minute E3 pilot Big Bangs for paper accuracy claims.

### 8.3 Extracting WorldFork forecast probabilities

For each resolved case, create one JSON object:

```json
{
  "case_id": "resolved_001",
  "condition": "worldfork_branching_short",
  "big_bang_id": "...",
  "forecast_distribution": {"yes": 0.37, "no": 0.41, "unresolved": 0.22},
  "probability_source": "path_mass|report_adjudication|endpoint_ledger|manual_extraction",
  "extraction_notes": "..."
}
```

Preferred extraction order:

1. Use explicit report/adjudication forecast distribution if available and it
   is explicitly tied to the public `yes`/`no` candidate endpoints.
2. Else use `ledgers path-mass` and exact-match endpoint keys to the public
   candidate endpoint ids `yes` and `no`; ignore auxiliary mechanism endpoints
   for scoring and binary-resolution stop checks.
3. Else use final report JSON outcome distribution.
4. Else use a deterministic parser over endpoint ledger statuses. Do **not** ask a model with access to private eval to infer the answer.

Scoring treatment:

- Primary Brier/log score uses `p_yes` and `p_no` normalized over yes/no only: `p_yes_norm = p_yes / (p_yes + p_no)` if `p_yes + p_no > 0`.
- Also report unresolved mass separately.
- If all mass is unresolved, set primary distribution to `0.5/0.5`, unresolved mass `1.0`, and flag as abstention.
- Do not hide abstentions; unresolved mass is a key WorldFork result.

---

## 9. E4: Long-Horizon WorldFork Audit Runs

### 9.1 Required long-horizon cases

Run exactly these 18 cases unless provider/cost constraints force a smaller set:

`civic_002`, `labor_002`, `platform_004`, `health_004`, `election_002`, `corporate_004`, `dossier_001`, `dossier_002`, `dossier_003`, `dossier_004`, `dossier_005`, `dossier_006`, `dossier_007`, `dossier_008`, `calibration_001`, `calibration_002`, `calibration_003`, `calibration_004`

If forced smaller, the minimum defensible long-horizon set is these 6:

`civic_002`, `labor_002`, `platform_004`, `health_004`, `election_002`, `corporate_004`.

### 9.2 Long-horizon config

Use 35 ticks as a maximum cap, not a mandatory stopping point. If all active
multiverses reach a terminal endpoint ledger and the God agent marks them ready
for report earlier, freeze those artifacts and stop. If endpoint ledgers remain
materially unresolved near 30 ticks, prefer resuming the same Big Bang toward
the 35-tick cap over reinitializing; only downgrade below 30 after repeated
runtime failures that are documented in `failures.md`.

```json
{"max_branch_depth":3,"max_active_multiverses":8,"max_branches_per_tick":2,"branch_score_threshold":0.75}
```

Command template:

```bash
python3 ICML-forecasting/scripts/icml_pipeline.py run-worldfork-long-batch \
  --run-root "$run_root" \
  --base-url "$base_url" \
  --api-prefix /api \
  --conditions worldfork_full_branching_long \
  --output-prefix raw/E4_long_horizon \
  --route-policy-id icml_default_deepseek_v4_flash_cohort_hero_e4 \
  --name-prefix E4_long_horizon \
  --max-ticks 35 \
  --max-total-ticks 240 \
  --tick-duration-minutes 720 \
  --wait-timeout 86400 \
  --poll-seconds 20
```

For the minimum-6 fallback, add `--minimum6` and stamp the route policy with an explicit fallback label such as `icml_default_deepseek_v4_flash_cohort_hero_e4_min6`.

The helper writes E4 artifacts under `raw/E4_long_horizon/<condition>/<case_id>/` and appends rows to `manifests/worldfork_long_horizon_manifest.jsonl`. Do not use the E3 short-run helper for E4: it stamps `worldfork_short_manifest.jsonl`, extracts resolved-card forecasts, and uses the shorter branch policy.

### 9.3 Long-horizon audit metrics

Score each case 0-4:

| Metric | What to inspect |
|---|---|
| lineage_integrity | child branches preserve parent and fork tick; no orphan timelines |
| branch_locality | child branch differences are attributable to branch reason/intervention |
| endpoint_coverage | declared endpoints are tracked in ledgers/reports |
| endpoint_honesty | unsupported endpoints remain unresolved; no forced closure |
| path_mass_consistency | retained/pruned path mass is conserved and understandable |
| report_grounding | sampled report claims are supported by ticks, ledgers, logs, or source packet |
| failure_observability | failed jobs, LLM failures, parse repairs, missing evidence are visible |
| cost_transparency | LLM calls, provider/model, cost estimates/actuals are recorded |
| social_state_consistency | trust/dependency/conflict/influence/coalition claims match graph/tick evidence |
| emotion_observability | emotion values are recorded/normalized when relevant and not overclaimed |

For report grounding, sample 5 claims per final report and mark each: `supported`, `partially_supported`, `unsupported`, or `not_checkable`. Report the supported percentage and the top unsupported claim types.

---

## 10. E5: Socio-Institutional State and Emotion Evaluation

### 10.1 What to claim

Claim: WorldFork makes social assumptions inspectable through actor/cohort state, trust/dependency/conflict/influence/coalition/exposure graphs, sociology signals, source-grounded reports, and emotion observations.

Do **not** claim:

- agents have real emotions;
- emotion modeling is psychologically validated;
- social-state improves real-world forecast accuracy unless the scores clearly support it.

### 10.2 Metrics

Score all 18 long-horizon cases.

| Metric | 0 | 2 | 4 |
|---|---|---|---|
| graph_layer_presence | missing | some graph layers present | trust/dependency/conflict/influence/coalition/exposure meaningfully present |
| graph_layer_plausibility | nonsensical/saturated | mixed | plausible weights and directions |
| social_mechanism_traceability | no trace | generic claims | claims trace to graph/ticks/posts/source packet |
| sociology_signal_usefulness | absent or harmful | present but generic | helps explain branch divergence or unresolved uncertainty |
| emotion_observability | absent or overclaimed | present but weak | explicit, bounded, audit-only affective observations |
| report_social_grounding | unsupported social claims | partially grounded | social claims cite concrete evidence |

Paper result target: even if Brier does not improve, show that socio-institutional state improves auditability and failure visibility.

### 10.3 Optional ablation

Run this only if E0-E5 core are complete and there is a clean implementation path.

Cases: `civic_002`, `labor_002`, `platform_004`, `health_004`, `dossier_001`, `dossier_003`, `dossier_004`, `dossier_006`

Conditions:

1. `worldfork_full_social_state`: normal WorldFork.
2. `worldfork_no_sociology_prompt_influence`: disable sociology prompt influences while keeping ordinary actors/graphs/reports if possible.

If no existing toggle exists, add a minimal config flag only if the change is safe and reversible. If not safe, do not hack the DB or prompts; instead report this as a limitation and use scoring-only social-state analysis.

Metrics for ablation:

- branch diversity;
- endpoint distribution change;
- report-grounded social claims;
- social-state consistency;
- cost/runtime.

---

## 11. Stretch: Branch-Threshold Sweep

Only after all core work is done.

Cases: `civic_002`, `labor_002`, `platform_004`, `health_004`, `dossier_001`, `dossier_004`

For each case, run 20 ticks with thresholds `0.55`, `0.75`, `0.95`:

```json
{"max_branch_depth":3,"max_active_multiverses":8,"max_branches_per_tick":2,"branch_score_threshold":THRESHOLD}
```

Report:

- branch count;
- active/terminal multiverse count;
- endpoint coverage;
- unresolved mass;
- report-grounding score;
- cost and wall time.

Expected paper use: one small sensitivity table or appendix figure. Do not make this the main result.

---

## 12. Scoring and Aggregation

### 12.1 Forecast scoring

For resolved cases only:

```python
import math

def clamp(p, lo=0.01, hi=0.99):
    return max(lo, min(hi, p))

def binary_brier(p_yes, resolution):
    y = 1 if resolution == 'yes' else 0
    return (p_yes - y) ** 2

def log_score(p_yes, resolution):
    p_true = p_yes if resolution == 'yes' else (1 - p_yes)
    return -math.log(clamp(p_true))
```

For WorldFork with unresolved mass:

```python
p_yes_raw = dist.get('yes', 0.0)
p_no_raw = dist.get('no', 0.0)
unresolved = dist.get('unresolved', max(0.0, 1.0 - p_yes_raw - p_no_raw))
if p_yes_raw + p_no_raw > 0:
    p_yes_primary = p_yes_raw / (p_yes_raw + p_no_raw)
else:
    p_yes_primary = 0.5
    unresolved = 1.0
```

Report both primary score and unresolved mass. A system that gets a good Brier score by hiding uncertainty should not be treated as strictly better than one that exposes uncertainty.

### 12.2 Bootstrap intervals

Use paired bootstrap over case IDs for direct vs WorldFork comparisons. With only 24 resolved cards, avoid strong significance claims.

Report:

- mean Brier;
- mean log score;
- 95% bootstrap CI for mean;
- paired difference vs direct baseline;
- unresolved mass;
- cost per forecast;
- LLM calls per forecast.

### 12.3 Audit aggregation

For audit metrics, report:

- mean score;
- pass rate where pass = mean >= 3.0 and no critical failure;
- critical failure rate;
- common failure categories;
- representative case IDs.

Critical failures:

- private answer leaked into input;
- wrong scenario artifacts mixed into case;
- material fabricated authority/outcome;
- prompt injection changes agent/tool behavior;
- final report contradicts endpoint ledger/ticks;
- missing model routing/cost/log evidence for a live run;
- missing Docker/resource telemetry for long-horizon runs.

---

## 13. Paper Writing Plan

### 13.1 Main thesis

Use this thesis unless the data strongly contradicts it:

> WorldFork does not merely ask an LLM for a probability; it represents a forecast as a branching, auditable socio-institutional world model. In a small resolved forecasting pilot, WorldFork may or may not improve Brier score over direct prompting, but it exposes endpoint uncertainty, lineage, social assumptions, report provenance, and failure modes that single-shot forecast rationales hide.

### 13.2 Contribution bullets

Use exactly three contributions:

1. **Branching forecast protocol.** Checkpointed Big Bangs, ticks, branch lineage, endpoint ledgers, path mass, report provenance, and unresolved uncertainty.
2. **Socio-institutional world state.** Actors/cohorts, trust/dependency/conflict/influence/coalition/exposure graphs, sociology signals, source packets, and emotion observations as audit data.
3. **Pilot benchmark and evaluation.** 108-card initialization/audit suite, 24 resolved forecast cards, 18 long-horizon WorldFork runs, direct baselines, forecast scoring, audit metrics, and social-state consistency scoring.

### 13.3 Four-page paper outline

Target a 4-page workshop paper, references/appendix excluded.

| Section | Length | Content |
|---|---:|---|
| Abstract | 0.2 page | problem, WorldFork protocol, benchmark, headline result |
| Introduction | 0.6 page | why single-shot LLM forecasts are hard to audit; contributions |
| Method | 0.9 page | Big Bang, ticks, branches, endpoint ledgers, path mass, reports, socio-institutional state |
| Benchmark and metrics | 0.8 page | 108-card init screen, 24 resolved forecast cards, 18 long runs, Brier/log/audit/social metrics |
| Results | 1.1 pages | forecast score table, audit table, social-state table, cost/runtime |
| Limitations/conclusion | 0.4 page | small resolved set, leakage risk, synthetic social realism, not validated emotion model |

### 13.4 Tables and figures to produce

Required:

1. **Figure 1: WorldFork protocol diagram.** Scenario → Big Bang → ticks → branches → endpoint ledgers/path mass → final forecast/report.
2. **Table 1: Benchmark composition.** Existing 72, new 24 resolved, 8 dossiers, 4 calibration; domains; metrics.
3. **Table 2: Forecast scoring.** Direct, structured direct, WorldFork no-branch, WorldFork branching: Brier, log score, unresolved mass, cost, calls.
4. **Table 3: Audit metrics.** Lineage, branch locality, endpoint coverage, report grounding, failure observability, social-state consistency.
5. **Table 4 or appendix table: Social-state/emotion audit.** Graph layer presence, social mechanism grounding, emotion observability.

Optional:

- Endpoint-status/path-mass distribution plot.
- Branch-threshold sensitivity plot.
- Cost vs audit score scatter.

### 13.5 Claims to avoid

Do not write:

- “WorldFork predicts the future.”
- “WorldFork emotions are realistic.”
- “Synthetic cases prove real-world forecasting ability.”
- “Branching improves accuracy” unless the Brier/log scores support it.
- “All cards are leakage-free” unless E0 validates them.

Preferred wording:

- “retrospective pilot” for resolved cards;
- “audit/stress suite” for synthetic cards;
- “socio-institutional state” rather than “emotional society simulation”;
- “emotion observability” rather than “emotion model.”

---

## 14. Reproducibility and Anonymity

Before paper submission:

- anonymize repo links and author-identifying paths;
- include benchmark cards and scoring scripts in supplementary material;
- do not include private API keys, run paths with usernames, or non-anonymized GitHub URLs;
- include exact model routing and date/time of runs;
- include a statement that private resolution files were withheld from all forecasting prompts;
- include failure cases and excluded cards.

---

## 15. Final Agent Checklist

Use this checklist before declaring the run complete.

### Setup

- [ ] Fresh checkout/worktree used.
- [ ] Docker stack healthy.
- [ ] Migrations and seed completed.
- [ ] `worldfork status`, `/readyz`, and `agent discover` captured.
- [ ] Model routing captured before and after benchmark.
- [ ] `cohort_agent` and `hero_agent` route to OpenRouter `deepseek/deepseek-v4-flash` for default E3/E4/E5 runs, or any deviation is explicitly labeled as an ablation/smoke condition.
- [ ] `worldfork settings provider-test openrouter` passes before any default DeepSeek cohort/hero batch.
- [ ] Resource monitoring started before runtime runs.

### Card QA

- [ ] 108 public cards parsed.
- [ ] 36 private eval objects parsed but not exposed to forecast models.
- [ ] 24 resolved card resolutions verified or corrected.
- [ ] Public leakage check completed.

### Benchmarks

- [ ] E1 init screen run on 108 public cards.
- [ ] E2 direct baselines run on 24 resolved cards.
- [ ] E3 WorldFork no-branch short run on 24 resolved cards, or documented core-12 fallback.
- [ ] E3 WorldFork branching short run on 24 resolved cards, or documented core-12 fallback.
- [ ] E4 long-horizon run on 18 cases, or documented minimum-6 fallback.
- [ ] E5 social-state/emotion audit scored.
- [ ] Optional ablation/sweep clearly marked optional if incomplete.
- [ ] Frozen WorldFork decisions cite terminal job output plus `path_mass.json`.
- [ ] Tick counts reported as caps, not required stopping targets.
- [ ] Unresolved or insufficient path-mass extensions reuse/resume existing Big Bangs.

### Scoring

- [ ] Brier/log scores computed.
- [ ] Unresolved mass reported.
- [ ] Cost/calls/wall-time reported.
- [ ] Audit metrics scored with evidence.
- [ ] Bootstrap intervals computed where applicable.
- [ ] Failures and exclusions documented.

### Paper

- [ ] Paper draft uses scored evidence only.
- [ ] Synthetic and resolved benchmark claims are separated.
- [ ] Society/emotion claims are cautious and audit-focused.
- [ ] Main tables/figures generated.
- [ ] Limitations are explicit.
- [ ] Submission is anonymized.

---

## 16. Final Output Format for the Human

When done, report:

```text
Completed blocks: E0/E1/E2/E3/E4/E5/S1/S2
Run directory: ...
Model routing: ...
Number of cases run: ...
Forecast score headline: ...
Audit score headline: ...
Main failure modes: ...
Paper draft path: ...
Tables/figures path: ...
What is still missing: ...
```

Never claim completion for a block that lacks manifests and raw artifacts.
