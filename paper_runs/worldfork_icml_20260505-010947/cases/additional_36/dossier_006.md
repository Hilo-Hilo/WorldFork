# Case dossier_006
Benchmark role: longform_dossier

## Forecast Clock
Forecast horizon: 14 simulated weeks
Treat the simulated clock as beginning at the as-of date.

Forecast question: Which endpoint dominates the interstate water-rationing dispute after 14 simulated weeks?

## Scenario

A multi-state water authority must allocate drought cuts among cities, farms, and tribal governments. The legal compact is ambiguous about emergency shortages. Model authority, treaty/compact constraints, hydrological uncertainty, and distributive conflict.

## Source Packet

### Source 1: hydrology_bulletin / T-30 days

Reservoir storage is 28% below the 20-year median; spring inflows are forecast with high uncertainty.

### Source 2: compact_clause_excerpt / T-25 days

The compact allows emergency reductions but does not specify how tribal senior rights interact with municipal health-and-safety minimums.

### Source 3: farm_bureau_statement / T-20 days

Irrigation districts say a uniform 20% cut would bankrupt late-season growers and demand crop-specific exemptions.

### Source 4: tribal_council_letter / T-18 days

Three tribal governments say any allocation that ignores senior rights will trigger litigation and federal consultation demands.

### Source 5: city_mayors_call_notes / T-12 days

Metro mayors support conservation but say hospitals and basic sanitation require protected municipal minimums.

### Source 6: federal_bureau_email / T-6 days

Federal staff offer technical mediation but say formal intervention requires either a state request or litigation posture.

## Candidate Endpoints
- emergency_rationing: Authority imposes emergency cuts
- negotiated_compact: Parties agree temporary allocation formula
- tribal_or_state_litigation: Litigation blocks or reshapes allocation
- federal_mediation: Federal bureau enters formal mediation/intervention
- unresolved_shortage: No stable allocation after horizon

## Expected Focus
- compact_authority
- senior_rights
- hydrological_uncertainty
- distributional_conflict

## Required Forecast Output
{'type': 'worldfork_initialization_and_report', 'minimum_outputs': ['actors/cohorts and authority constraints', 'initial events and unresolved facts', 'candidate endpoint ledger', 'branch hypotheses with rationale', 'forecast_distribution over endpoints plus unresolved_mass', 'report claims grounded to source_packet references']}

## Rubric Location
private_eval file
