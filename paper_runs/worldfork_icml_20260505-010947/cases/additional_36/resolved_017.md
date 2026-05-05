# Case resolved_017
Benchmark role: resolved_forecast

Forecast question: Will ordinary support for Operating System W10 end on October 14, 2025 as scheduled?

## Scenario

The vendor has repeatedly listed October 14, 2025 as the end-of-support date for Operating System W10. Millions of machines remain on the old operating system, and consumer advocates warn about e-waste and security exposure. Resolve yes if ordinary support ends on that date, even if paid or extended security programs exist for some users.

## Source Packet

### Source 1: vendor_schedule / 2025-07-01

The published lifecycle schedule says the final supported version remains supported through October 14, 2025.

### Source 2: public_pressure / 2025-07-01

Advocates and some governments may pressure the vendor to extend support, but no broad reversal has been announced.

### Source 3: endpoint_note / 2025-07-01

Paid extended security updates do not mean ordinary support continues.

## Candidate Endpoints
- yes: The event occurs by the deadline
- no: The event does not occur by the deadline

## Expected Focus
- scheduled_policy_change
- support_definition
- exception_handling

## Required Forecast Output
{'type': 'probability_distribution', 'schema': {'yes': 'float in [0,1]', 'no': 'float in [0,1]', 'unresolved_mass': 'optional float in [0,1]; if used, keep yes+no+unresolved_mass = 1'}, 'scoring': 'For resolved cards, score p_yes against the hidden private resolution using binary Brier and clamped log score. If unresolved_mass is present, report it separately and normalize yes/no only if your paper explicitly chooses conditional scoring.'}

## Leakage Mitigation
Model-facing text uses partial entity masking. Do not expose private_eval entity_map or resolution during forecasting.
