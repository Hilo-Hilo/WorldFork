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

## Strong Report Shape

A strong report includes: executive summary, scenario context, multiverse comparison table, lineage/divergence analysis, timeline adjudication, retained/pruned timeline summaries, endpoint/path-mass distribution, evidence gaps, and an appendix of report/tick/job/log/artifact IDs.
