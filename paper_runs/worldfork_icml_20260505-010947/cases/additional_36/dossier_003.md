# Case dossier_003
Benchmark role: longform_dossier

## Forecast Clock
Forecast horizon: 8 simulated weeks
Treat the simulated clock as beginning at the as-of date.

Forecast question: Which endpoint dominates the hospital triage-algorithm rollout after 8 simulated weeks?

## Scenario

A hospital network plans to deploy an algorithm that prioritizes ICU-transfer review. A fairness review, capacity shortage, and union concerns complicate the rollout. Forecast endpoint mass without inventing patient outcomes.

## Source Packet

### Source 1: chief_medical_officer_memo / T-15 days

The network says the triage assistant will flag high-risk patients for earlier ICU review but will not make final admission decisions.

### Source 2: capacity_dashboard / T-13 days

ICU occupancy averaged 91% in the past month, with two campuses above 96% on weekends.

### Source 3: fairness_review_excerpt / T-10 days

Retrospective testing shows lower alert sensitivity for patients with incomplete prior records, disproportionately affecting uninsured and recently transferred patients.

### Source 4: nurses_union_statement / T-6 days

The union says staffing shortages, not alert ranking, are the bottleneck and asks for a moratorium until staffing ratios improve.

### Source 5: state_health_board_email / T-4 days

The state board requests documentation but has not ordered a halt.

### Source 6: vendor_response / T-2 days

The vendor says a patch can improve missing-record handling within three weeks.

## Candidate Endpoints
- limited_pilot: Narrow pilot at selected campuses with clinician override
- full_deployment: Network-wide deployment proceeds
- temporary_suspension: Rollout suspended pending fairness or staffing review
- external_review: State board or independent panel reviews before expansion
- unresolved_capacity_crisis: Capacity pressure dominates without clear rollout resolution

## Expected Focus
- clinical_authority
- fairness_risk
- capacity_constraints
- vendor_patch_timing

## Required Forecast Output
{'type': 'worldfork_initialization_and_report', 'minimum_outputs': ['actors/cohorts and authority constraints', 'initial events and unresolved facts', 'candidate endpoint ledger', 'branch hypotheses with rationale', 'forecast_distribution over endpoints plus unresolved_mass', 'report claims grounded to source_packet references']}

## Rubric Location
private_eval file
