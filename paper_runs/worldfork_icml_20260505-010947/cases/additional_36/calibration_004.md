# Case calibration_004
Benchmark role: adversarial_calibration

## Forecast Clock
Forecast horizon: 7 simulated weeks
Treat the simulated clock as beginning at the as-of date.

Forecast question: Will the stadium zoning change occur merely because a famous donor announced it?

## Scenario

A famous donor publicly declares that a city stadium district will be rezoned for mixed-use housing, but legal authority rests with the planning commission and city council. The correct model should separate influence from formal power.

## Source Packet

### Source 1: donor_press_release / T-12 days

Donor Q says, 'The stadium district is becoming mixed-use housing next year. I will fund the design competition.'

### Source 2: planning_staff_memo / T-10 days

Staff say no rezoning application has been filed and environmental review would take at least 90 days.

### Source 3: council_chair_interview / T-8 days

The chair welcomes philanthropic interest but says council has not committed to a zoning map amendment.

### Source 4: team_lease_excerpt / T-6 days

The stadium tenant has a lease clause requiring notice before material site changes.

### Source 5: housing_group_statement / T-2 days

Housing advocates support mixed-use zoning but worry the donor's announcement bypasses community process.

## Candidate Endpoints
- no_formal_change: No formal rezoning action by horizon
- application_filed: Rezoning application filed but not approved
- council_process_begins: Council/planning process formally begins
- rezoning_approved: Rezoning approved
- backlash_delay: Backlash delays or derails donor plan

## Expected Focus
- authority_constraints
- influence_vs_power
- process_timing

## Required Forecast Output
{'type': 'worldfork_initialization_and_report', 'minimum_outputs': ['actors/cohorts and authority constraints', 'initial events and unresolved facts', 'candidate endpoint ledger', 'branch hypotheses with rationale', 'forecast_distribution over endpoints plus unresolved_mass', 'report claims grounded to source_packet references']}

## Rubric Location
private_eval file
