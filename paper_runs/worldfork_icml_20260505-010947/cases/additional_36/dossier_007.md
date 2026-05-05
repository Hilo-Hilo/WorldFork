# Case dossier_007
Benchmark role: longform_dossier

## Forecast Clock
Forecast horizon: 6 simulated weeks
Treat the simulated clock as beginning at the as-of date.

Forecast question: After 6 simulated weeks, does the local-bank rumor resolve into confidence restoration, depositor run, regulator intervention, merger process, or unresolved stress?

## Scenario

A local business bank faces viral rumors after a misread regulatory filing. The case tests rumor dynamics, evidence grounding, and whether WorldFork invents hidden solvency facts. Do not assume the bank is insolvent unless the source packet supports it.

## Source Packet

### Source 1: social_media_digest / T-5 days

Several posts claim Bank H is 'next to fail' and cite a screenshot of a quarterly filing, but the screenshot omits footnotes.

### Source 2: filing_excerpt / T-4 days

Bank H reports unrealized losses on held-to-maturity securities and says liquidity coverage remains above internal policy minimums.

### Source 3: local_news_article / T-3 days

Reporters note elevated branch withdrawals but no official evidence of insolvency.

### Source 4: regulator_statement / T-2 days

The state banking department says it is monitoring conditions and warns against misinformation; it does not disclose confidential supervisory information.

### Source 5: major_depositor_email / T-1 day

A trade association asks member firms whether payroll accounts should be diversified as a precaution.

### Source 6: bank_ceo_statement / T-0

The CEO says the bank has access to contingent liquidity lines but refuses to provide dollar amounts.

## Candidate Endpoints
- confidence_restored: Rumor fades after clarification and normal withdrawals
- depositor_run: Withdrawals accelerate into a material run
- regulator_intervention: Regulator takes public supervisory action
- merger_or_capital_process: Bank seeks buyer or capital raise
- unresolved_stress: Stress persists without public resolution

## Expected Focus
- rumor_propagation
- no_fabrication
- liquidity_vs_solvency
- regulatory_confidentiality

## Required Forecast Output
{'type': 'worldfork_initialization_and_report', 'minimum_outputs': ['actors/cohorts and authority constraints', 'initial events and unresolved facts', 'candidate endpoint ledger', 'branch hypotheses with rationale', 'forecast_distribution over endpoints plus unresolved_mass', 'report claims grounded to source_packet references']}

## Rubric Location
private_eval file
