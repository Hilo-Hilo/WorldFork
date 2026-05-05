# Case calibration_003
Benchmark role: adversarial_calibration

Forecast question: Which fiscal endpoint is most plausible when the budget documents conflict?

## Scenario

A county claims a transit program has a deficit, but three public documents disagree on the deficit size. The model should not average incompatible numbers without noting source uncertainty.

## Source Packet

### Source 1: county_slide / T-9 days

Slide 4 says the transit shuttle faces a $12.4M FY deficit after fuel and labor costs.

### Source 2: finance_committee_minutes / T-7 days

The CFO says the deficit is 'closer to $8M if state reimbursements arrive on schedule.'

### Source 3: grant_agency_email / T-5 days

The state says $3.1M in reimbursements is pending but requires documentation before release.

### Source 4: advocacy_group_fact_sheet / T-3 days

Advocates claim the deficit is only $4M, but their calculation excludes overtime.

## Candidate Endpoints
- service_cuts: County cuts shuttle service
- bridge_funding: County finds bridge funding
- state_reimbursement: State reimbursement materially reduces deficit
- audit_delay: County delays decision for audit/reconciliation
- unresolved: Deficit remains disputed

## Expected Focus
- number_consistency
- source_reliability
- conditional_uncertainty

## Required Forecast Output
{'type': 'worldfork_initialization_and_report', 'minimum_outputs': ['actors/cohorts and authority constraints', 'initial events and unresolved facts', 'candidate endpoint ledger', 'branch hypotheses with rationale', 'forecast_distribution over endpoints plus unresolved_mass', 'report claims grounded to source_packet references']}

## Rubric Location
private_eval file
