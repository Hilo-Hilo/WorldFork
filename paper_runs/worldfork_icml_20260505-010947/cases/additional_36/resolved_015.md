# Case resolved_015
Benchmark role: resolved_forecast

Forecast question: Will Console S2 be released to consumers by June 30, 2025?

## Scenario

The manufacturer has shown a first-look trailer for the successor to its widely used hybrid console and says the new system will be released in 2025. As of the forecast date, the exact global retail date and price have not been officially announced. Resolve yes only if consumers can purchase the hardware by June 30, 2025 in at least one major launch market.

## Source Packet

### Source 1: company_signal / 2025-01-17

The manufacturer has publicly committed to a 2025 release window but not a precise date.

### Source 2: supply_risk_note / 2025-01-17

Hardware launches can slip because of production, pricing, tariffs, software readiness, or launch inventory constraints.

### Source 3: endpoint_note / 2025-01-17

A reveal or preorder alone is insufficient; the system must be released to consumers.

## Candidate Endpoints
- yes: The event occurs by the deadline
- no: The event does not occur by the deadline

## Expected Focus
- product_launch_forecasting
- scheduled_release_window
- supply_chain_risk

## Required Forecast Output
{'type': 'probability_distribution', 'schema': {'yes': 'float in [0,1]', 'no': 'float in [0,1]', 'unresolved_mass': 'optional float in [0,1]; if used, keep yes+no+unresolved_mass = 1'}, 'scoring': 'For resolved cards, score p_yes against the hidden private resolution using binary Brier and clamped log score. If unresolved_mass is present, report it separately and normalize yes/no only if your paper explicitly chooses conditional scoring.'}

## Leakage Mitigation
Model-facing text uses partial entity masking. Do not expose private_eval entity_map or resolution during forecasting.
