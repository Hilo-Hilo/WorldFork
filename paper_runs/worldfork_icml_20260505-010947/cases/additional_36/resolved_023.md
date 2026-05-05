# Case resolved_023
Benchmark role: resolved_forecast

Forecast question: Will the final official COP30 negotiated text include an explicit binding commitment or roadmap for phasing out fossil fuels?

## Scenario

COP30 opens in Belém with strong pressure from many countries and civil-society groups for language on fossil-fuel phaseout. Oil- and gas-producing states resist binding language, while negotiators also debate adaptation finance, just transition, and implementation indicators. Resolve yes only if the final official negotiated text includes an explicit binding fossil-fuel phaseout commitment or roadmap.

## Source Packet

### Source 1: negotiation_context / 2025-11-10

The fossil-fuel roadmap is politically salient but highly contentious.

### Source 2: coalition_note / 2025-11-10

A large pro-phaseout coalition can still be blocked or diluted in consensus text.

### Source 3: endpoint_note / 2025-11-10

Voluntary roadmaps, side declarations, or non-binding remarks outside the official negotiated text do not count as yes.

## Candidate Endpoints
- yes: The event occurs by the deadline
- no: The event does not occur by the deadline

## Expected Focus
- multilateral_negotiation
- consensus_blocking
- binding_vs_voluntary_text

## Required Forecast Output
{'type': 'probability_distribution', 'schema': {'yes': 'float in [0,1]', 'no': 'float in [0,1]', 'unresolved_mass': 'optional float in [0,1]; if used, keep yes+no+unresolved_mass = 1'}, 'scoring': 'For resolved cards, score p_yes against the hidden private resolution using binary Brier and clamped log score. If unresolved_mass is present, report it separately and normalize yes/no only if your paper explicitly chooses conditional scoring.'}

## Leakage Mitigation
Model-facing text uses partial entity masking. Do not expose private_eval entity_map or resolution during forecasting.
