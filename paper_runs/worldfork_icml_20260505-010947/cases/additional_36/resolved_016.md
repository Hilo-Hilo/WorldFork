# Case resolved_016
Benchmark role: resolved_forecast

## Forecast Clock
As-of date: 2025-09-10
Forecast horizon: through 2025-09-30
Forecast deadline date: 2025-09-30
Treat the simulated clock as beginning at the as-of date.

Forecast question: Will Phone A be available to customers by September 30, 2025?

## Scenario

A major smartphone manufacturer has announced a new thin smartphone model, Phone A, alongside the next generation of its flagship phones. The company says preorders will open soon and availability will begin later in September. Resolve yes only if Phone A is available to customers by September 30, 2025.

## Source Packet

### Source 1: company_announcement / 2025-09-10

The announced schedule places availability in the second half of September.

### Source 2: launch_risk_note / 2025-09-10

A short-term release could still slip if supply, regulatory, or manufacturing issues appear.

### Source 3: endpoint_note / 2025-09-10

A preorder without general availability does not by itself resolve yes.

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
- product_launch_extraction
- short_horizon_forecasting
- preorder_vs_availability

## Required Forecast Output
{'type': 'probability_distribution', 'schema': {'yes': 'float in [0,1]', 'no': 'float in [0,1]', 'unresolved_mass': 'optional float in [0,1]; if used, keep yes+no+unresolved_mass = 1'}, 'scoring': 'For resolved cards, score p_yes against the hidden private resolution using binary Brier and clamped log score. If unresolved_mass is present, report it separately and normalize yes/no only if your paper explicitly chooses conditional scoring.'}

## Leakage Mitigation
Model-facing text uses partial entity masking. Do not expose private_eval entity_map or resolution during forecasting.
