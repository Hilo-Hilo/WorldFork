# Case resolved_002
Benchmark role: resolved_forecast

Forecast question: Will the 2025 Nobel Prize in Chemistry be awarded for Research Area M, the development of metal-organic frameworks or closely equivalent porous crystalline framework chemistry?

## Scenario

Research Area M has accumulated decades of work on porous crystalline architectures with applications in gas storage, catalysis, separations, water capture, and carbon capture. Competing Nobel-plausible chemistry areas include protein design, battery chemistry, chemical biology, catalysis, and microscopy-adjacent methods. The prize committee does not publish a shortlist before the announcement.

## Source Packet

### Source 1: field_note / 2025-09-15

Research Area M has a clear long-run citation and application profile, with multiple senior pioneers still living.

### Source 2: forecast_risk_note / 2025-09-15

The chemistry prize alternates unpredictably among methods, materials, biological chemistry, and applied discoveries.

### Source 3: endpoint_note / 2025-09-15

Count the endpoint as yes only if the official prize motivation centers on metal-organic frameworks or equivalent framework chemistry.

## Candidate Endpoints
- yes: The event occurs by the deadline
- no: The event does not occur by the deadline

## Expected Focus
- scientific_prize_forecasting
- category_boundary
- multi_decade_citation_signal

## Required Forecast Output
{'type': 'probability_distribution', 'schema': {'yes': 'float in [0,1]', 'no': 'float in [0,1]', 'unresolved_mass': 'optional float in [0,1]; if used, keep yes+no+unresolved_mass = 1'}, 'scoring': 'For resolved cards, score p_yes against the hidden private resolution using binary Brier and clamped log score. If unresolved_mass is present, report it separately and normalize yes/no only if your paper explicitly chooses conditional scoring.'}

## Leakage Mitigation
Model-facing text uses partial entity masking. Do not expose private_eval entity_map or resolution during forecasting.
