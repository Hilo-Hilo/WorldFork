# Case resolved_004
Benchmark role: resolved_forecast

## Forecast Clock
As-of date: 2026-01-10
Forecast horizon: through the FOMC decision on 2026-01-28
Forecast deadline date: 2026-01-28
Treat the simulated clock as beginning at the as-of date.

Forecast question: Will the Federal Open Market Committee lower the target range for the federal funds rate at its January 28, 2026 meeting?

## Scenario

After a December rate cut, the committee enters January with elevated uncertainty and disagreement about the right path. Some policymakers see room for further easing; others worry about inflation and financial conditions. The endpoint is yes only if the target range is lower immediately after the January statement than it was immediately before the meeting.

## Source Packet

### Source 1: policy_context / 2026-01-10

A prior cut reduces the mechanical urgency of another immediate move.

### Source 2: committee_note / 2026-01-10

The balance of risks is mixed, and dissents are plausible in either direction.

### Source 3: endpoint_note / 2026-01-10

A hold, even with dovish language or dissents favoring a cut, resolves to no.

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
- central_bank_policy
- post_cut_path_dependence
- committee_dissent

## Required Forecast Output
{'type': 'probability_distribution', 'schema': {'yes': 'float in [0,1]', 'no': 'float in [0,1]', 'unresolved_mass': 'optional float in [0,1]; if used, keep yes+no+unresolved_mass = 1'}, 'scoring': 'For resolved cards, score p_yes against the hidden private resolution using binary Brier and clamped log score. If unresolved_mass is present, report it separately and normalize yes/no only if your paper explicitly chooses conditional scoring.'}

## Leakage Mitigation
Model-facing text uses partial entity masking. Do not expose private_eval entity_map or resolution during forecasting.
