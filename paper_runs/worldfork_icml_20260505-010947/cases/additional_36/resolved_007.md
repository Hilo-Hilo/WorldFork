# Case resolved_007
Benchmark role: resolved_forecast

## Forecast Clock
As-of date: 2025-10-24
Forecast horizon: through the 2025 World Series
Forecast deadline date: 2025-11-01
Treat the simulated clock as beginning at the as-of date.

Forecast question: Will Team T, the Canadian club in the 2025 World Series, win the series?

## Scenario

Team T opens the championship series against Team L, a defending champion with deep pitching and recent postseason experience. Team T has home-field energy and a strong offensive profile, but the series format creates high variance. The endpoint is yes only if Team T wins four games before Team L does.

## Source Packet

### Source 1: series_context / 2025-10-24

The endpoint is a best-of-seven series, not a single-game result.

### Source 2: variance_note / 2025-10-24

Pitching rotations, bullpen fatigue, and travel can change probabilities after each game.

### Source 3: endpoint_note / 2025-10-24

If Team T loses in seven games, this resolves no even if the series is close.

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
- sports_series_forecasting
- multi_game_path_dependence
- variance

## Required Forecast Output
{'type': 'probability_distribution', 'schema': {'yes': 'float in [0,1]', 'no': 'float in [0,1]', 'unresolved_mass': 'optional float in [0,1]; if used, keep yes+no+unresolved_mass = 1'}, 'scoring': 'For resolved cards, score p_yes against the hidden private resolution using binary Brier and clamped log score. If unresolved_mass is present, report it separately and normalize yes/no only if your paper explicitly chooses conditional scoring.'}

## Leakage Mitigation
Model-facing text uses partial entity masking. Do not expose private_eval entity_map or resolution during forecasting.
