# Case resolved_012
Benchmark role: resolved_forecast

Forecast question: Will Candidate D, a former U.S. House member, win the 2025 Virginia governor's race?

## Scenario

Candidate D faces Candidate R, the sitting lieutenant governor. The incumbent governor cannot run again because of term limits. The state often elects a governor from the party opposite the sitting U.S. president, but national polarization and candidate quality remain important. Resolve yes only if Candidate D is elected governor.

## Source Packet

### Source 1: state_context / 2025-10-01

Virginia has recent history of competitive statewide races and post-presidential-cycle swings.

### Source 2: campaign_note / 2025-10-01

Abortion, cost of living, federal workforce issues, schools, and national-party backlash are plausible drivers.

### Source 3: endpoint_note / 2025-10-01

A close loss or victory by a different Democrat would resolve no for Candidate D.

## Candidate Endpoints
- yes: The event occurs by the deadline
- no: The event does not occur by the deadline

## Expected Focus
- statewide_election_forecasting
- nationalized_state_race
- term_limited_incumbent

## Required Forecast Output
{'type': 'probability_distribution', 'schema': {'yes': 'float in [0,1]', 'no': 'float in [0,1]', 'unresolved_mass': 'optional float in [0,1]; if used, keep yes+no+unresolved_mass = 1'}, 'scoring': 'For resolved cards, score p_yes against the hidden private resolution using binary Brier and clamped log score. If unresolved_mass is present, report it separately and normalize yes/no only if your paper explicitly chooses conditional scoring.'}

## Leakage Mitigation
Model-facing text uses partial entity masking. Do not expose private_eval entity_map or resolution during forecasting.
