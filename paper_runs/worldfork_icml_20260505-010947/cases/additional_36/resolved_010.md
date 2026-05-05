# Case resolved_010
Benchmark role: resolved_forecast

## Forecast Clock
As-of date: 2026-01-29
Forecast horizon: through the 2026 Australian Open women's singles final
Forecast deadline date: 2026-01-31
Treat the simulated clock as beginning at the as-of date.

Forecast question: Will Player S win the 2026 Australian Open women's singles title?

## Scenario

Player S, the world No. 1 and an experienced Australian Open finalist, reaches the final against Player R, a fifth seed with a powerful serve and prior major-title experience. Player S has the ranking and recent Melbourne-final experience edge, but the matchup is not one-sided. Resolve yes only if Player S wins the women's singles title.

## Source Packet

### Source 1: matchup_note / 2026-01-29

Both finalists have plausible title cases; prior head-to-head and serve quality matter.

### Source 2: variance_note / 2026-01-29

A best-of-three final can swing on a few service games and pressure points.

### Source 3: endpoint_note / 2026-01-29

Runner-up status resolves no.

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
- single_match_forecasting
- ranking_vs_matchup
- binary_resolution

## Required Forecast Output
{'type': 'probability_distribution', 'schema': {'yes': 'float in [0,1]', 'no': 'float in [0,1]', 'unresolved_mass': 'optional float in [0,1]; if used, keep yes+no+unresolved_mass = 1'}, 'scoring': 'For resolved cards, score p_yes against the hidden private resolution using binary Brier and clamped log score. If unresolved_mass is present, report it separately and normalize yes/no only if your paper explicitly chooses conditional scoring.'}

## Leakage Mitigation
Model-facing text uses partial entity masking. Do not expose private_eval entity_map or resolution during forecasting.
