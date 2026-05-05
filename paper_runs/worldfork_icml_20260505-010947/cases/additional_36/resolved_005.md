# Case resolved_005
Benchmark role: resolved_forecast

## Forecast Clock
As-of date: 2026-01-23
Forecast horizon: until the 98th Academy Awards ceremony
Forecast deadline date: 2026-03-15
Treat the simulated clock as beginning at the as-of date.

Forecast question: Will Nominee S win Best Picture at the 98th Academy Awards?

## Scenario

Nominee S enters the race with the most nominations and unusually strong conversation about genre recognition and representational impact. Several other Best Picture nominees have stronger historical alignment with Academy preferences, including a politically literate prestige drama by an established auteur. The endpoint is yes only if Nominee S is officially announced as Best Picture winner.

## Source Packet

### Source 1: nomination_note / 2026-01-23

Nominee S has a nomination-count advantage, but nomination volume does not mechanically determine Best Picture.

### Source 2: industry_note / 2026-01-23

Preferential ballot dynamics can reward broad consensus rather than passionate first-place support.

### Source 3: endpoint_note / 2026-01-23

Only the official Best Picture category counts; wins in acting, directing, or technical categories do not resolve yes.

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
- award_forecasting
- preferential_ballot
- front_runner_uncertainty

## Required Forecast Output
{'type': 'probability_distribution', 'schema': {'yes': 'float in [0,1]', 'no': 'float in [0,1]', 'unresolved_mass': 'optional float in [0,1]; if used, keep yes+no+unresolved_mass = 1'}, 'scoring': 'For resolved cards, score p_yes against the hidden private resolution using binary Brier and clamped log score. If unresolved_mass is present, report it separately and normalize yes/no only if your paper explicitly chooses conditional scoring.'}

## Leakage Mitigation
Model-facing text uses partial entity masking. Do not expose private_eval entity_map or resolution during forecasting.
