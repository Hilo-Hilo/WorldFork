# Case resolved_009
Benchmark role: resolved_forecast

## Forecast Clock
As-of date: 2026-01-28
Forecast horizon: through the 2026 Australian Open men's singles final
Forecast deadline date: 2026-02-01
Treat the simulated clock as beginning at the as-of date.

Forecast question: Will Player C win the 2026 Australian Open men's singles title?

## Scenario

Player C is the top seed and remains alive deep in the tournament. The draw still includes an all-time great with unmatched Melbourne success and a recent champion profile. Player C's case rests on form, all-surface dominance, and career-Grand-Slam motivation. Resolve yes only if Player C wins the men's singles title.

## Source Packet

### Source 1: draw_note / 2026-01-28

Late-round major tennis forecasts are sensitive to semifinal matchups and fatigue.

### Source 2: base_rate_note / 2026-01-28

Dominant top seeds are strong favorites against the field, but a single elite opponent can substantially lower title probability.

### Source 3: endpoint_note / 2026-01-28

Reaching the final is insufficient; the title must be won.

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
- tournament_forecasting
- draw_path
- elite_opponent_risk

## Required Forecast Output
{'type': 'probability_distribution', 'schema': {'yes': 'float in [0,1]', 'no': 'float in [0,1]', 'unresolved_mass': 'optional float in [0,1]; if used, keep yes+no+unresolved_mass = 1'}, 'scoring': 'For resolved cards, score p_yes against the hidden private resolution using binary Brier and clamped log score. If unresolved_mass is present, report it separately and normalize yes/no only if your paper explicitly chooses conditional scoring.'}

## Leakage Mitigation
Model-facing text uses partial entity masking. Do not expose private_eval entity_map or resolution during forecasting.
