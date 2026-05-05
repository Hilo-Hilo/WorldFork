# Case resolved_003
Benchmark role: resolved_forecast

Forecast question: Will the Federal Open Market Committee lower the target range for the federal funds rate at its December 10, 2025 meeting?

## Scenario

The FOMC faces a late-2025 decision after prior easing expectations, mixed labor-market signals, and inflation still above target. Markets discuss a possible quarter-point cut, but policymakers emphasize data dependence. The endpoint is yes only if the target range is lower immediately after the December 10 statement than it was immediately before the meeting.

## Source Packet

### Source 1: macro_note / 2025-11-15

Recent communication points to data dependence rather than a pre-committed cut.

### Source 2: risk_note / 2025-11-15

Inflation progress and labor-market softening point in opposite directions for policy.

### Source 3: endpoint_note / 2025-11-15

Ignore changes to implementation details unless the target range itself is lowered.

## Candidate Endpoints
- yes: The event occurs by the deadline
- no: The event does not occur by the deadline

## Expected Focus
- central_bank_policy
- binary_resolution
- rate_target_definition

## Required Forecast Output
{'type': 'probability_distribution', 'schema': {'yes': 'float in [0,1]', 'no': 'float in [0,1]', 'unresolved_mass': 'optional float in [0,1]; if used, keep yes+no+unresolved_mass = 1'}, 'scoring': 'For resolved cards, score p_yes against the hidden private resolution using binary Brier and clamped log score. If unresolved_mass is present, report it separately and normalize yes/no only if your paper explicitly chooses conditional scoring.'}

## Leakage Mitigation
Model-facing text uses partial entity masking. Do not expose private_eval entity_map or resolution during forecasting.
