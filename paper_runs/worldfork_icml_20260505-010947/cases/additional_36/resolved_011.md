# Case resolved_011
Benchmark role: resolved_forecast

Forecast question: Will Candidate M win the 2025 New York City mayoral general election?

## Scenario

Candidate M won a major-party primary on an affordability-centered platform and faces a former governor running outside the major-party line plus a Republican nominee. The city electorate is heavily tilted toward Candidate M's party, but general-election concerns include ideological attacks, experience questions, public-safety framing, and turnout uncertainty. Resolve yes only if Candidate M is elected mayor in the general election.

## Source Packet

### Source 1: election_context / 2025-10-01

Party registration and city partisanship favor Candidate M, but the opponent's name recognition creates nontrivial uncertainty.

### Source 2: campaign_note / 2025-10-01

Affordability, rent, policing, transit, and ideology dominate media framing.

### Source 3: endpoint_note / 2025-10-01

Winning the primary alone is insufficient; the general-election winner resolves the card.

## Candidate Endpoints
- yes: The event occurs by the deadline
- no: The event does not occur by the deadline

## Expected Focus
- urban_election_forecasting
- primary_to_general_transition
- turnout

## Required Forecast Output
{'type': 'probability_distribution', 'schema': {'yes': 'float in [0,1]', 'no': 'float in [0,1]', 'unresolved_mass': 'optional float in [0,1]; if used, keep yes+no+unresolved_mass = 1'}, 'scoring': 'For resolved cards, score p_yes against the hidden private resolution using binary Brier and clamped log score. If unresolved_mass is present, report it separately and normalize yes/no only if your paper explicitly chooses conditional scoring.'}

## Leakage Mitigation
Model-facing text uses partial entity masking. Do not expose private_eval entity_map or resolution during forecasting.
