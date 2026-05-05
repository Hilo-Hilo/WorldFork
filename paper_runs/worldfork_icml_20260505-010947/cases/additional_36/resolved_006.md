# Case resolved_006
Benchmark role: resolved_forecast

## Forecast Clock
As-of date: 2025-11-10
Forecast horizon: until the 68th Annual Grammy Awards
Forecast deadline date: 2026-02-01
Treat the simulated clock as beginning at the as-of date.

Forecast question: Will Album B, a Spanish-language album by a global Latin artist, win Album of the Year at the 2026 Grammy Awards?

## Scenario

Album B is critically prominent and commercially visible, but the Album of the Year category has historically favored English-language releases and broad Recording Academy consensus. Several major English-language pop, rap, and singer-songwriter albums are also nominated. The endpoint is yes only if Album B wins the official Album of the Year category.

## Source Packet

### Source 1: award_context / 2025-11-10

Album B has strong narrative momentum because a win would mark a major language/cultural milestone.

### Source 2: countervailing_note / 2025-11-10

Historic category voting patterns create uncertainty despite critical acclaim.

### Source 3: endpoint_note / 2025-11-10

Genre-category wins do not count; only Album of the Year resolves yes.

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
- award_forecasting
- historical_base_rates
- category_specific_resolution

## Required Forecast Output
{'type': 'probability_distribution', 'schema': {'yes': 'float in [0,1]', 'no': 'float in [0,1]', 'unresolved_mass': 'optional float in [0,1]; if used, keep yes+no+unresolved_mass = 1'}, 'scoring': 'For resolved cards, score p_yes against the hidden private resolution using binary Brier and clamped log score. If unresolved_mass is present, report it separately and normalize yes/no only if your paper explicitly chooses conditional scoring.'}

## Leakage Mitigation
Model-facing text uses partial entity masking. Do not expose private_eval entity_map or resolution during forecasting.
