# WorldFork Report Module

Use this module to inspect, generate, audit, or explain WorldFork reports, endpoint ledgers, path mass, and final outcome distributions.

## Ground Rules

- Reports are structured database records first.
- Markdown and PDF outputs are ephemeral renders from report versions.
- Do not run new ticks, continue timelines, or change model routes unless the user asks.
- Use effective path mass for final distributions; do not invent endpoint probabilities.

## Discover Evidence

```bash
worldfork agent discover
worldfork status
worldfork --verbosity summary runs list
worldfork --verbosity summary runs workspace <big-bang-id>
worldfork watch big-bang <big-bang-id> --once
```

## Inventory Reports And Ledgers

```bash
worldfork reports list <big-bang-id>
worldfork reports versions <report-id>
worldfork reports view <report-version-id> --format json
worldfork reports pack <big-bang-id> --mode summary
worldfork ledgers list <big-bang-id>
worldfork ledgers path-mass <big-bang-id>
worldfork reports adjudication <big-bang-id>
```

If no adjudication exists and timelines are terminal:

```bash
worldfork reports adjudicate <big-bang-id>
worldfork reports adjudication <big-bang-id>
```

## Generate Reports

```bash
worldfork reports generate multiverse <multiverse-id> \
  --title "M1 report" \
  --summary "Terminal timeline report."

worldfork reports generate final <big-bang-id> \
  --title "Final report" \
  --summary "Cross-multiverse outcome review."
```

If generation returns a job ID, use a bounded wait:

```bash
worldfork jobs wait <job-id> --timeout 300 --poll-interval 2
```

## Routing And Cost

Before report regeneration:

```bash
worldfork settings llm
worldfork settings model-routing
worldfork runs estimate <big-bang-id>
```

After generation:

```bash
worldfork reports view <report-version-id> --format json
worldfork query GET /api/report-versions/<report-version-id>/cost
worldfork --verbosity normal --fields id,source,status,message,provider,model,big_bang_id logs list --source llm
```

Report routing is split. Predicate extraction/resolution and single-universe
reports can use the fast lane, while final Big Bang synthesis uses
`final_report_agent_model` and the higher final-report budget. The frontend and
API expose `/api/big-bangs/<big-bang-id>/report-status` so operators can see
single-report, predicate-resolution, and final-report progress while generation
is running.

## Strong Report Shape

A strong report includes: executive summary, scenario context, multiverse comparison table, lineage/divergence analysis, timeline adjudication, retained/pruned timeline summaries, endpoint/path-mass distribution, evidence gaps, and an appendix of report/tick/job/log/artifact IDs.

## Prediction-Mode Reports

When a Big Bang's scenario_text contains a question (or the user supplied one explicitly), the report agent runs in **prediction mode** instead of pure narrative. Two `content` fields are added on top of the base report:

- `prediction_predicates` — up to 5 predicates the agent extracted from the scenario, each typed as `threshold_breach`, `binary_event`, `count`, `categorical`, or `narrative`. Threshold predicates carry `threshold`, `comparison`, `unit`, `quantity_label`. Categorical predicates carry the candidate `categories`.
- `predicate_resolutions` — one row per predicate, aggregated across timelines. Output shape depends on the predicate's `type`:
  - `threshold_breach`: `value_distribution.{p10, p50, p90, n_with_value}` plus path-mass for fired/false/null
  - `binary_event`: `hit_rate = fired_path_mass / total_path_mass`
  - `count`: `histogram_path_mass` and `histogram_count` bucketed `0/1/2/3+`
  - `categorical`: per-label `category_count` and `category_path_mass`
  - `narrative`: evidence list, no aggregation

Inside `llm_report` the agent emits a `prediction_answer` object: `{verdict ∈ {yes, no, unresolved, not_applicable}, confidence_pct, supporting_timeline_ids, counterevidence, rationale}`. The `report_markdown` opens with a Headline Answer section that summarizes each predicate in its natural shape (distribution / hit-rate / histogram / label distribution).

When auditing a prediction report:
- Cross-check `prediction_answer.verdict` against `predicate_resolutions[].fired_path_mass / total_path_mass`. The agent is told to ground verdict in path-mass weighting; mismatches deserve scrutiny.
- High `null_count` across predicates means the simulation didn't run far enough to evaluate the question — `unresolved` is the right verdict, `confidence_pct` should be low.
- Empty `predicate_resolutions` means extraction or resolution failed silently; the agent falls back to inference from `outcome_distribution` + `endpoint_ledger` and lowers confidence accordingly. Worth checking `worldfork logs list --source llm` for failed `predicate_extraction_*` or `predicate_resolution_*` rows.
