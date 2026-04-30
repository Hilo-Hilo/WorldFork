# Reporting

WorldFork reports are structured database records with optional rendered
artifacts.

## Report Layers

- `reports` are logical report slots, scoped to either one multiverse or the
  final Big Bang.
- `report_versions` are generated revisions. Each version stores parsable
  content, source metadata, report-agent model metadata, source multiverse IDs,
  source multiverse version, source config version, and latest tick binding.
- Artifacts are rendered files, such as Markdown or PDF outputs. They are
  cached from `report_versions.content` and can be regenerated.

Artifacts are not the canonical report. If a rendered Markdown/PDF artifact is
removed or regenerated, the report version remains reconstructable from the
database content.

## Viewing Outcomes

```bash
worldfork reports list <big-bang-id>
worldfork reports versions <report-id>
worldfork reports view <report-version-id>
worldfork reports view <report-version-id> --format json
worldfork reports render <report-version-id> --format pdf
```

The Markdown view includes the outcome summary, outcome distribution,
multiverse comparison, report inventory, and evidence appendix when those
sections are present in the structured report content.

## Continuing A Multiverse

When a multiverse reaches `max_ticks`, it becomes terminal and ready for a
report. A continuation increments that multiverse's version and stores a
per-multiverse runtime override with the new `max_ticks`. Sibling timelines keep
their original runtime config.

Reports generated before continuation stay bound to the old multiverse version.
Reports generated after continuation point to the newer multiverse version and
latest tick snapshot.

## Storage And Deletion

Report versions point at the source multiverse IDs, source multiverse version,
source config version, and source tick snapshot available when the report was
created. Storage cleanup should preserve those references. If deletion support
is added for Big Bangs, multiverses, or artifacts, it should either block while
referenced reports exist or tombstone the source rows instead of silently
orphaning historical report versions.
