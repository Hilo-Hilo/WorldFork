# Card Quality Report

Generated: 2026-05-05T01:11:13.476178+00:00

## Counts

- Existing public cards: 72
- Additional public cards: 36
- Private eval rows: 36
- Legacy-schema rows: 36
- Additional role counts: {'resolved_forecast': 24, 'longform_dossier': 8, 'adversarial_calibration': 4}
- Resolved label counts: {'yes': 12, 'no': 12}

## Leakage Separation

- Public/private IDs match: True
- Public/legacy IDs match: True
- Public cards with private fields: none

## Resolution Source Coverage

- Resolved cards with at least one source: 24/24
- Source inventory: `results/resolution_sources.csv`

## Failures

- none

## Warnings

- Resolution source URLs were not independently fetched; this is static package QA only.

## Verdict

PASS
