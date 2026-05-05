# Case resolved_019
Benchmark role: resolved_forecast

## Forecast Clock
As-of date: 2025-07-25
Forecast horizon: through 2025-08-31
Forecast deadline date: 2025-08-31
Treat the simulated clock as beginning at the as-of date.

Forecast question: Will Media Company P and Studio S complete their announced merger by August 31, 2025?

## Scenario

Media Company P and Studio S have announced an anticipated closing date in early August after a long transaction process involving shareholder, regulatory, and market uncertainty. The companies say the transaction is expected to close subject to customary conditions. Resolve yes only if the merger legally closes by August 31, 2025.

## Source Packet

### Source 1: transaction_note / 2025-07-25

The parties have announced an anticipated closing date, but closing still depends on final conditions and operational execution.

### Source 2: regulatory_note / 2025-07-25

A media merger can be delayed by regulatory approvals, litigation, or political pressure.

### Source 3: endpoint_note / 2025-07-25

A scheduled closing date is not enough; completion must be announced.

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
- merger_closing
- regulatory_dependency
- near_deadline_event

## Required Forecast Output
{'type': 'probability_distribution', 'schema': {'yes': 'float in [0,1]', 'no': 'float in [0,1]', 'unresolved_mass': 'optional float in [0,1]; if used, keep yes+no+unresolved_mass = 1'}, 'scoring': 'For resolved cards, score p_yes against the hidden private resolution using binary Brier and clamped log score. If unresolved_mass is present, report it separately and normalize yes/no only if your paper explicitly chooses conditional scoring.'}

## Leakage Mitigation
Model-facing text uses partial entity masking. Do not expose private_eval entity_map or resolution during forecasting.
