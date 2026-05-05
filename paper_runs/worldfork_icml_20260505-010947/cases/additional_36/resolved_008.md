# Case resolved_008
Benchmark role: resolved_forecast

## Forecast Clock
As-of date: 2026-02-06
Forecast horizon: through Super Bowl LX
Forecast deadline date: 2026-02-08
Treat the simulated clock as beginning at the as-of date.

Forecast question: Will Team N, the AFC champion, win Super Bowl LX?

## Scenario

Team N enters the title game against Team S, the NFC champion, at a neutral site. Both teams have strong regular-season records. Team N's defense and quarterback story are central to its case, while Team S has a run-game advantage and defensive depth. Resolve yes only if Team N wins the game outright.

## Source Packet

### Source 1: matchup_note / 2026-02-06

A neutral-site final reduces home-field assumptions but does not eliminate crowd and travel effects.

### Source 2: forecast_note / 2026-02-06

Single-game football outcomes have high variance; injuries and turnovers are major branch drivers.

### Source 3: endpoint_note / 2026-02-06

Point spread performance does not matter; only the outright winner counts.

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
- single_game_forecasting
- injury_turnover_variance
- binary_resolution

## Required Forecast Output
{'type': 'probability_distribution', 'schema': {'yes': 'float in [0,1]', 'no': 'float in [0,1]', 'unresolved_mass': 'optional float in [0,1]; if used, keep yes+no+unresolved_mass = 1'}, 'scoring': 'For resolved cards, score p_yes against the hidden private resolution using binary Brier and clamped log score. If unresolved_mass is present, report it separately and normalize yes/no only if your paper explicitly chooses conditional scoring.'}

## Leakage Mitigation
Model-facing text uses partial entity masking. Do not expose private_eval entity_map or resolution during forecasting.
