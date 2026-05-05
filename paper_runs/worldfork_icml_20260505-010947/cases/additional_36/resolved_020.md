# Case resolved_020
Benchmark role: resolved_forecast

## Forecast Clock
As-of date: 2024-12-01
Forecast horizon: through 2024-12-31
Forecast deadline date: 2024-12-31
Treat the simulated clock as beginning at the as-of date.

Forecast question: Will Grocery Chain K and Grocery Chain A complete their proposed merger by December 31, 2024?

## Scenario

Two large U.S. grocery chains are pursuing a merger after a long antitrust review. Regulators and several states argue the merger would harm competition and workers; the companies argue divestitures can fix the concerns. Court decisions are pending in early December. Resolve yes only if the transaction legally closes by the end of 2024.

## Source Packet

### Source 1: regulatory_context / 2024-12-01

The FTC and state partners are actively challenging the deal.

### Source 2: company_context / 2024-12-01

The companies have proposed divestitures and claim the merger would improve competition against larger retailers.

### Source 3: endpoint_note / 2024-12-01

An appeal, settlement, or continued litigation without closing resolves no by the deadline.

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
- antitrust_litigation
- court_injunction_risk
- merger_deadline

## Required Forecast Output
{'type': 'probability_distribution', 'schema': {'yes': 'float in [0,1]', 'no': 'float in [0,1]', 'unresolved_mass': 'optional float in [0,1]; if used, keep yes+no+unresolved_mass = 1'}, 'scoring': 'For resolved cards, score p_yes against the hidden private resolution using binary Brier and clamped log score. If unresolved_mass is present, report it separately and normalize yes/no only if your paper explicitly chooses conditional scoring.'}

## Leakage Mitigation
Model-facing text uses partial entity masking. Do not expose private_eval entity_map or resolution during forecasting.
