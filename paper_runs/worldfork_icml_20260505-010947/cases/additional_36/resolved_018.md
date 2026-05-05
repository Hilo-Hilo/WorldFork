# Case resolved_018
Benchmark role: resolved_forecast

## Forecast Clock
As-of date: 2025-04-01
Forecast horizon: through 2025-04-30
Forecast deadline date: 2025-04-30
Treat the simulated clock as beginning at the as-of date.

Forecast question: Will the European Commission fine both Gatekeeper A and Gatekeeper M under the Digital Markets Act by April 30, 2025?

## Scenario

The European Commission has been investigating whether two large digital gatekeepers comply with early Digital Markets Act obligations. Gatekeeper A is accused of restricting app-developer steering; Gatekeeper M is scrutinized over user choice around personal-data use. Resolve yes only if both firms receive DMA fines by the deadline.

## Source Packet

### Source 1: regulatory_context / 2025-04-01

The DMA is new, and early enforcement decisions carry signaling value beyond the fine amount.

### Source 2: company_context / 2025-04-01

Both companies have incentives to contest interpretations and negotiate compliance changes.

### Source 3: endpoint_note / 2025-04-01

A finding without a monetary fine for either company is not enough for yes.

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
- regulatory_enforcement
- multi_condition_endpoint
- deadline

## Required Forecast Output
{'type': 'probability_distribution', 'schema': {'yes': 'float in [0,1]', 'no': 'float in [0,1]', 'unresolved_mass': 'optional float in [0,1]; if used, keep yes+no+unresolved_mass = 1'}, 'scoring': 'For resolved cards, score p_yes against the hidden private resolution using binary Brier and clamped log score. If unresolved_mass is present, report it separately and normalize yes/no only if your paper explicitly chooses conditional scoring.'}

## Leakage Mitigation
Model-facing text uses partial entity masking. Do not expose private_eval entity_map or resolution during forecasting.
