# Case resolved_014
Benchmark role: resolved_forecast

## Forecast Clock
As-of date: 2025-04-01
Forecast horizon: through the 2025 Canadian federal election
Forecast deadline date: 2025-04-28
Treat the simulated clock as beginning at the as-of date.

Forecast question: Will Party C win the most seats in the 2025 Canadian federal election?

## Scenario

A snap federal election is underway after a leadership transition in the incumbent governing party. Party C has strong national polling history and a clear opposition message, while the incumbent party has a new leader and may recover support during the campaign. Resolve yes only if Party C wins more House of Commons seats than every other party.

## Source Packet

### Source 1: campaign_context / 2025-04-01

The election uses single-member districts, so national popular vote does not directly determine the seat winner.

### Source 2: leadership_note / 2025-04-01

A new prime minister can either reset the incumbent brand or inherit accumulated fatigue.

### Source 3: endpoint_note / 2025-04-01

Winning the popular vote but fewer seats resolves no.

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
- parliamentary_election_forecasting
- seat_vote_conversion
- leadership_reset

## Required Forecast Output
{'type': 'probability_distribution', 'schema': {'yes': 'float in [0,1]', 'no': 'float in [0,1]', 'unresolved_mass': 'optional float in [0,1]; if used, keep yes+no+unresolved_mass = 1'}, 'scoring': 'For resolved cards, score p_yes against the hidden private resolution using binary Brier and clamped log score. If unresolved_mass is present, report it separately and normalize yes/no only if your paper explicitly chooses conditional scoring.'}

## Leakage Mitigation
Model-facing text uses partial entity masking. Do not expose private_eval entity_map or resolution during forecasting.
