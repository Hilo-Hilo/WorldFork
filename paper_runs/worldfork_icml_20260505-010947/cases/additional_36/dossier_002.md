# Case dossier_002
Benchmark role: longform_dossier

## Forecast Clock
Forecast horizon: 10 simulated weeks
Treat the simulated clock as beginning at the as-of date.

Forecast question: What endpoint dominates the school district AI-tutor contract dispute after 10 simulated weeks?

## Scenario

A school district is about to launch an AI tutoring vendor contract, but privacy, accessibility, and procurement questions surface. Initialize a grounded world state and track whether the rollout proceeds, narrows, stalls, or collapses.

## Source Packet

### Source 1: superintendent_memo / T-21 days

The district plans to launch TutorPilot for grades 7-9 in six middle schools, citing learning loss and teacher workload.

### Source 2: vendor_security_addendum / T-18 days

The vendor says student data are encrypted and not sold, but model-training use is described as 'de-identified service improvement' without a plain-language opt-out.

### Source 3: parent_coalition_email / T-12 days

Parents demand an opt-in system, independent privacy review, and a translated notice for non-English-speaking families.

### Source 4: teacher_union_minutes / T-10 days

Teachers support extra tutoring but object to dashboards being used for performance evaluation.

### Source 5: procurement_note / T-8 days

The contract was approved under an emergency instructional-services exception; two board members now question whether that exception applies.

### Source 6: accessibility_audit / T-3 days

The pilot interface passes basic screen-reader checks but fails two multilingual support tests.

## Candidate Endpoints
- full_launch: District launches pilot substantially as planned
- guardrailed_launch: Launch proceeds with opt-out/opt-in and privacy safeguards
- postponement: Board postpones pending review
- contract_cancellation: District cancels or rebids contract
- legal_or_regulatory_hold: External complaint or regulator pauses deployment

## Expected Focus
- privacy_uncertainty
- procurement_authority
- vendor_constraints
- equity_accessibility

## Required Forecast Output
{'type': 'worldfork_initialization_and_report', 'minimum_outputs': ['actors/cohorts and authority constraints', 'initial events and unresolved facts', 'candidate endpoint ledger', 'branch hypotheses with rationale', 'forecast_distribution over endpoints plus unresolved_mass', 'report claims grounded to source_packet references']}

## Rubric Location
private_eval file
