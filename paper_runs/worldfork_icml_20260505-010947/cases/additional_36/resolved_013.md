# Case resolved_013
Benchmark role: resolved_forecast

Forecast question: Will Candidate R, the Republican nominee, win the 2025 New Jersey governor's race?

## Scenario

Candidate R previously ran statewide and faces Candidate D, a Democratic U.S. House member. The incumbent Democratic governor is term-limited. The race is discussed as competitive because of taxes, affordability, national-party sentiment, and recent Republican overperformance relative to the state's baseline. Resolve yes only if Candidate R is elected governor.

## Source Packet

### Source 1: state_context / 2025-10-01

New Jersey leans Democratic federally but has a history of competitive gubernatorial races.

### Source 2: campaign_note / 2025-10-01

Property taxes, cost of living, corruption narratives, and presidential-party approval are plausible drivers.

### Source 3: endpoint_note / 2025-10-01

A narrow Democratic win resolves no even if polling had suggested a toss-up.

## Candidate Endpoints
- yes: The event occurs by the deadline
- no: The event does not occur by the deadline

## Expected Focus
- statewide_election_forecasting
- state_baseline_vs_issue_environment
- candidate_re_run

## Required Forecast Output
{'type': 'probability_distribution', 'schema': {'yes': 'float in [0,1]', 'no': 'float in [0,1]', 'unresolved_mass': 'optional float in [0,1]; if used, keep yes+no+unresolved_mass = 1'}, 'scoring': 'For resolved cards, score p_yes against the hidden private resolution using binary Brier and clamped log score. If unresolved_mass is present, report it separately and normalize yes/no only if your paper explicitly chooses conditional scoring.'}

## Leakage Mitigation
Model-facing text uses partial entity masking. Do not expose private_eval entity_map or resolution during forecasting.
