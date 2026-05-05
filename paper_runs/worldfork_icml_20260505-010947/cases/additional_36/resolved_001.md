# Case resolved_001
Benchmark role: resolved_forecast

Forecast question: Will Candidate A win the 2025 Nobel Peace Prize?

## Scenario

Candidate A is a Venezuelan opposition figure known internationally for democracy-rights advocacy. As of the forecast date, the Nobel Peace Prize shortlist is not public, and the Norwegian Nobel Committee often surprises observers. Other plausible themes include humanitarian relief, conflict mediation, press freedom, nuclear-risk reduction, climate activism, and support for political prisoners. Use only this pre-announcement information.

## Source Packet

### Source 1: analyst_note / 2025-09-15

Candidate A has unusually high moral-salience evidence because of long-running democratic-rights work under an authoritarian setting.

### Source 2: process_note / 2025-09-15

Nobel Peace nominations and shortlist details are confidential; public campaigns and news attention are not reliable evidence of committee intent.

### Source 3: countervailing_note / 2025-09-15

Recent wars and humanitarian crises give the committee many alternative symbolic targets.

## Candidate Endpoints
- yes: The event occurs by the deadline
- no: The event does not occur by the deadline

## Expected Focus
- secret_selection_process
- symbolic_institutional_choice
- geopolitical_uncertainty

## Required Forecast Output
{'type': 'probability_distribution', 'schema': {'yes': 'float in [0,1]', 'no': 'float in [0,1]', 'unresolved_mass': 'optional float in [0,1]; if used, keep yes+no+unresolved_mass = 1'}, 'scoring': 'For resolved cards, score p_yes against the hidden private resolution using binary Brier and clamped log score. If unresolved_mass is present, report it separately and normalize yes/no only if your paper explicitly chooses conditional scoring.'}

## Leakage Mitigation
Model-facing text uses partial entity masking. Do not expose private_eval entity_map or resolution during forecasting.
