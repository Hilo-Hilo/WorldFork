# Case resolved_024
Benchmark role: resolved_forecast

## Forecast Clock
As-of date: 2025-08-15
Forecast horizon: through the federal remedy decision expected in September 2025
Forecast deadline date: 2025-09-30
Treat the simulated clock as beginning at the as-of date.

Forecast question: Will the federal court order Company G to divest Browser C as a remedy in the search-antitrust case?

## Scenario

A federal court has already found Company G liable in a major search-antitrust case. The remedy phase includes proposals ranging from conduct restrictions and data-sharing duties to structural remedies. The government has argued for strong remedies, while Company G says divesting Browser C would be excessive and technically disruptive. Resolve yes only if the court orders Company G to sell or divest Browser C.

## Source Packet

### Source 1: legal_context / 2025-08-15

Liability has been found, but remedy choice is a separate judicial decision.

### Source 2: remedy_note / 2025-08-15

Structural breakup remedies are rarer and face administrability and appeal concerns.

### Source 3: endpoint_note / 2025-08-15

Data-sharing, contract limits, or conduct remedies without divestiture resolve no.

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
- antitrust_remedies
- structural_vs_conduct_relief
- legal_uncertainty

## Required Forecast Output
{'type': 'probability_distribution', 'schema': {'yes': 'float in [0,1]', 'no': 'float in [0,1]', 'unresolved_mass': 'optional float in [0,1]; if used, keep yes+no+unresolved_mass = 1'}, 'scoring': 'For resolved cards, score p_yes against the hidden private resolution using binary Brier and clamped log score. If unresolved_mass is present, report it separately and normalize yes/no only if your paper explicitly chooses conditional scoring.'}

## Leakage Mitigation
Model-facing text uses partial entity masking. Do not expose private_eval entity_map or resolution during forecasting.
