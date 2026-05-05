# Case dossier_005
Benchmark role: longform_dossier

## Forecast Clock
Forecast horizon: 12 simulated weeks
Treat the simulated clock as beginning at the as-of date.

Forecast question: Which endpoint receives the most path mass for the Harbor Battery permitting fight?

## Scenario

A grid operator and developer seek permission for a large battery project needed for summer reliability. Residents raise fire-safety and zoning concerns. Preserve interconnection deadlines, permit authority, and seasonal reliability pressure.

## Source Packet

### Source 1: grid_operator_warning / T-20 days

Without the Harbor Battery project, the coastal zone may face emergency demand-response calls during two expected summer peak weeks.

### Source 2: developer_schedule / T-18 days

The developer says missing the May interconnection milestone would push commercial operation to the following year.

### Source 3: fire_marshal_letter / T-14 days

The fire marshal requests a revised thermal-runaway response plan and an evacuation-radius analysis before signing off.

### Source 4: neighborhood_petition / T-9 days

Residents object to siting batteries near a school and ask the zoning board to deny the variance.

### Source 5: state_energy_office_note / T-6 days

The state can preempt local denial only after a finding of regional reliability need, a process expected to take at least 45 days.

### Source 6: utility_customer_poll / T-3 days

Customers support reliability upgrades in general but are split on the specific site.

## Candidate Endpoints
- permit_approved: Local permit/variance approved before milestone
- conditional_delay: Approval delayed pending safety conditions
- permit_denied: Local board denies variance
- state_override: State initiates or grants reliability preemption
- project_slips: Milestone missed and commercial operation slips

## Expected Focus
- permit_authority
- safety_review
- seasonal_reliability
- state_preemption_timing

## Required Forecast Output
{'type': 'worldfork_initialization_and_report', 'minimum_outputs': ['actors/cohorts and authority constraints', 'initial events and unresolved facts', 'candidate endpoint ledger', 'branch hypotheses with rationale', 'forecast_distribution over endpoints plus unresolved_mass', 'report claims grounded to source_packet references']}

## Rubric Location
private_eval file
