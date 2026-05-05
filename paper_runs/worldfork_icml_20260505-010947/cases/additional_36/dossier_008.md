# Case dossier_008
Benchmark role: longform_dossier

Forecast question: Which endpoint dominates the university lab-contamination investigation after 9 simulated weeks?

## Scenario

A university lab faces allegations that a contamination incident was underreported before a grant renewal. The source packet contains conflicting incentives and incomplete evidence. Initialize the world without concluding misconduct at T0.

## Source Packet

### Source 1: lab_incident_log / T-28 days

A freezer alarm failed overnight; samples in two boxes exceeded temperature thresholds for an unknown duration.

### Source 2: graduate_student_email / T-22 days

A student says the affected sample IDs overlap with preliminary figures in a grant-renewal appendix.

### Source 3: principal_investigator_reply / T-21 days

The PI says the appendix used independent replicates and asks the student not to speculate until inventory reconciliation is complete.

### Source 4: sponsor_notice / T-14 days

The sponsor requests confirmation that renewal data were not affected and gives the university 30 days to respond.

### Source 5: faculty_senate_minutes / T-9 days

Faculty senators debate whether the internal review office is sufficiently independent when the grant is strategically important.

### Source 6: inventory_update / T-3 days

The lab manager confirms one affected box had mislabeled vials but cannot yet map them to experiment dates.

## Candidate Endpoints
- minor_remediation: Incident treated as limited data-quality issue
- external_investigation: Independent review or sponsor audit begins
- grant_delay: Grant renewal delayed pending clarification
- lab_suspension: Lab work paused by university or sponsor
- unresolved_evidence_gap: Evidence remains too incomplete for closure

## Expected Focus
- research_integrity
- evidence_uncertainty
- sponsor_deadline
- authority_constraints

## Required Forecast Output
{'type': 'worldfork_initialization_and_report', 'minimum_outputs': ['actors/cohorts and authority constraints', 'initial events and unresolved facts', 'candidate endpoint ledger', 'branch hypotheses with rationale', 'forecast_distribution over endpoints plus unresolved_mass', 'report claims grounded to source_packet references']}

## Rubric Location
private_eval file
