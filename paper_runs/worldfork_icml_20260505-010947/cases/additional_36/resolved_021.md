# Case resolved_021
Benchmark role: resolved_forecast

## Forecast Clock
As-of date: 2026-02-20
Forecast horizon: through 2026-03-31
Forecast deadline date: 2026-03-31
Treat the simulated clock as beginning at the as-of date.

Forecast question: Will Mission A2 launch by March 31, 2026?

## Scenario

Mission A2 is the first crewed mission in a lunar exploration program since a prior uncrewed test. The vehicle has undergone rollout and rehearsal activity, but launch timing remains sensitive to hardware checks, ground systems, safety review, weather, and range availability. Resolve yes only if the mission launches by March 31, 2026, local launch-site time.

## Source Packet

### Source 1: schedule_context / 2026-02-20

The program is trying to preserve an early-2026 launch window after multiple historical delays.

### Source 2: risk_note / 2026-02-20

Crewed missions require conservative treatment of unresolved technical issues.

### Source 3: endpoint_note / 2026-02-20

A launch on April 1 or later resolves no even if the delay is less than 24 hours.

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
- space_launch_schedule
- technical_delay_risk
- deadline_precision

## Required Forecast Output
{'type': 'probability_distribution', 'schema': {'yes': 'float in [0,1]', 'no': 'float in [0,1]', 'unresolved_mass': 'optional float in [0,1]; if used, keep yes+no+unresolved_mass = 1'}, 'scoring': 'For resolved cards, score p_yes against the hidden private resolution using binary Brier and clamped log score. If unresolved_mass is present, report it separately and normalize yes/no only if your paper explicitly chooses conditional scoring.'}

## Leakage Mitigation
Model-facing text uses partial entity masking. Do not expose private_eval entity_map or resolution during forecasting.
