# Case resolved_022
Benchmark role: resolved_forecast

## Forecast Clock
As-of date: 2025-12-15
Forecast horizon: until NOAA/NCEI annual 2025 global climate report
Forecast deadline date: 2026-01-31
Treat the simulated clock as beginning at the as-of date.

Forecast question: Will NOAA/NCEI rank calendar year 2025 as the warmest year in the global instrumental record?

## Scenario

The year 2025 has been very warm globally, with persistent marine heat and regional extremes. However, the prior two years were also exceptionally warm, and the final ranking depends on full-year global land/ocean temperature analysis. Resolve yes only if NOAA/NCEI ranks 2025 as the warmest year since records began.

## Source Packet

### Source 1: climate_context / 2025-12-15

A very warm year can still rank second or third if recent prior years were warmer.

### Source 2: measurement_note / 2025-12-15

Use NOAA/NCEI's annual global report as the adjudication source, not individual regional records.

### Source 3: endpoint_note / 2025-12-15

If NOAA ranks 2025 third-warmest, the answer is no.

## Candidate Endpoints
- yes: The event occurs by the deadline
- no: The event does not occur by the deadline

## Binary forecast contract
The explicit candidate endpoints are the primary scoring endpoints.
Resolve yes only when the event occurs by the stated deadline.
Resolve no when the stated deadline or public settlement point passes without the event occurring.
Auxiliary mechanism endpoints must not keep the binary forecast unresolved once the yes/no endpoint is settled.
Use auxiliary mechanism endpoints only as diagnostic support for the binary forecast.

## Expected Focus
- climate_ranking
- source_specific_resolution
- base_rate_recent_extremes

## Required Forecast Output
{'type': 'probability_distribution', 'schema': {'yes': 'float in [0,1]', 'no': 'float in [0,1]', 'unresolved_mass': 'optional float in [0,1]; if used, keep yes+no+unresolved_mass = 1'}, 'scoring': 'For resolved cards, score p_yes against the hidden private resolution using binary Brier and clamped log score. If unresolved_mass is present, report it separately and normalize yes/no only if your paper explicitly chooses conditional scoring.'}

## Leakage Mitigation
Model-facing text uses partial entity masking. Do not expose private_eval entity_map or resolution during forecasting.
