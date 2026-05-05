# Case dossier_001
Benchmark role: longform_dossier

## Forecast Clock
Forecast horizon: 12 simulated weeks
Treat the simulated clock as beginning at the as-of date.

Forecast question: After 12 simulated weeks, which endpoint receives the most path mass in the Riverbend housing-compliance dispute?

## Scenario

Riverbend's regional housing agency is threatening to withhold infrastructure grants from suburbs that miss new affordable-housing targets. The source packet includes legal, fiscal, and public-opinion evidence. Initialize a WorldFork Big Bang that preserves authority boundaries and uncertainty.

## Source Packet

### Source 1: agency_notice / T-14 days

The Regional Housing Agency says five suburbs are below their affordable-unit targets and gives them 45 days to submit compliance plans or risk infrastructure grant withholding.

### Source 2: suburban_mayors_letter / T-11 days

Three mayors argue the targets are based on outdated sewer-capacity assumptions and threaten litigation if grants are withheld.

### Source 3: budget_table / T-9 days

Two of the five suburbs rely on agency-linked grants for more than 30% of planned road and drainage upgrades; one relies on less than 5%.

### Source 4: renters_coalition_statement / T-7 days

Renters and housing nonprofits say delay is a tactic to protect exclusionary zoning and ask the governor to support immediate enforcement.

### Source 5: legal_memo_excerpt / T-4 days

The statute gives the agency enforcement power, but courts can stay grant penalties during expedited review.

### Source 6: local_news_excerpt / T-1 day

Homeowner groups are split: some oppose density, while others fear losing flood-mitigation funds.

## Candidate Endpoints
- agency_enforcement: Agency begins grant-withholding process
- negotiated_delay: Suburbs receive delayed compliance schedule
- court_stay: Court temporarily blocks enforcement
- partial_exemption: Some suburbs get exemptions or modified targets
- unresolved_mixed: No dominant endpoint after horizon

## Expected Focus
- authority_constraints
- fiscal_dependency
- legal_delay
- stakeholder_graph

## Required Forecast Output
{'type': 'worldfork_initialization_and_report', 'minimum_outputs': ['actors/cohorts and authority constraints', 'initial events and unresolved facts', 'candidate endpoint ledger', 'branch hypotheses with rationale', 'forecast_distribution over endpoints plus unresolved_mass', 'report claims grounded to source_packet references']}

## Rubric Location
private_eval file
