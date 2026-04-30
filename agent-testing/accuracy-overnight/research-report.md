# Overnight WorldFork Accuracy Evaluation

Generated: 2026-04-30 13:03:58 PDT

Primary log: `REPRODUCIBILITY_LOG.md`

## Scope

This local-only study uses 50 anonymized concluded-event dossiers arranged as 10 categories by 5 horizons. Real source links and expected outcome distributions are stored separately under `sources/` and are not included in prompt text.

Live model constraint: `google/gemini-3.1-flash-lite-preview`.

## Benchmark Validation

- Validation OK: `True`
- Event count: `50`
- Matrix cells: `50`

## Parameter Matrix

Baseline uses medium agent count, medium description complexity, baseline tick counts by horizon, and default branch threshold. The focused sweep uses four fractional-factorial variants across tick count, agent count, prompt complexity, and branch threshold.

## Aggregate Accuracy

```json
{
  "count": 109,
  "top_match_rate": 0.8073,
  "mean_tvd": 0.3639,
  "mean_actual_probability": 0.4417,
  "gemini_only_runs": 109,
  "llm_calls": 6439
}
```

## Variant Breakdown

| variant | count | top_match_rate | mean_tvd | mean_actual_probability | gemini_only_runs | llm_calls |
| --- | --- | --- | --- | --- | --- | --- |
| baseline | 50 | 0.78 | 0.3536 | 0.4434 | 50 | 3052 |
| long_compressed_default | 16 | 0.75 | 0.3781 | 0.4385 | 16 | 1097 |
| long_high_detail_strict | 11 | 0.9091 | 0.3739 | 0.3833 | 11 | 1209 |
| short_compressed_permissive | 16 | 0.8125 | 0.3844 | 0.4677 | 16 | 408 |
| short_high_detail_default | 16 | 0.875 | 0.3549 | 0.4539 | 16 | 673 |


## Category Breakdown

| category | count | top_match_rate | mean_tvd | mean_actual_probability | gemini_only_runs | llm_calls |
| --- | --- | --- | --- | --- | --- | --- |
| AI/public systems | 23 | 0.8261 | 0.3561 | 0.4312 | 23 | 1366 |
| campus/civil society | 5 | 0.8 | 0.3133 | 0.3617 | 5 | 305 |
| corporate PR crisis | 5 | 1.0 | 0.3589 | 0.5571 | 5 | 305 |
| elections/legitimacy | 9 | 0.4444 | 0.3744 | 0.3611 | 9 | 483 |
| environment/resource | 5 | 1.0 | 0.2747 | 0.4867 | 5 | 305 |
| finance/market confidence | 5 | 0.8 | 0.3893 | 0.5067 | 5 | 306 |
| labor/social movements | 23 | 0.8261 | 0.3817 | 0.3962 | 23 | 1285 |
| policy/regulatory backlash | 5 | 0.8 | 0.268 | 0.4614 | 5 | 303 |
| public health | 5 | 0.4 | 0.424 | 0.3 | 5 | 305 |
| tech/platform | 24 | 0.9167 | 0.3829 | 0.5208 | 24 | 1476 |


## Horizon Breakdown

| horizon | count | top_match_rate | mean_tvd | mean_actual_probability | gemini_only_runs | llm_calls |
| --- | --- | --- | --- | --- | --- | --- |
| 1-3 months | 22 | 0.8636 | 0.3283 | 0.3848 | 22 | 1080 |
| 1-3 years | 20 | 0.7 | 0.4285 | 0.4112 | 20 | 1430 |
| 3+ years | 19 | 1.0 | 0.3046 | 0.4977 | 19 | 1479 |
| 3-12 months | 22 | 0.9091 | 0.3181 | 0.5562 | 22 | 1402 |
| weeks | 26 | 0.6154 | 0.4266 | 0.3755 | 26 | 1048 |


## Failed/Retryable Runs

The five incomplete sweep records are operational failures, not hidden-outcome scoring failures. All occurred in `long_high_detail_strict` after late `HTTP 503 ... LLM unavailable` tick failures left one or more active multiverses. A direct final-report retry is not sufficient because `/api/big-bangs/<id>/reports/final` rejects nonterminal inputs with `HTTP 409 final report requires terminal multiverses`.

| event_id | variant | status | stop_reason | failed_tick | active_multiverses | tick_error | final_error |
| --- | --- | --- | --- | --- | --- | --- | --- |
| labor_3y_low_wage_campaign | long_high_detail_strict | report_failed |  | tick_008 | M1, M1.1 | Error: HTTP 503 POST api/multiverses/5f04a8c6-7a65-4bdf-9016-c855dcac1e30/simulate-next-tick: LLM unavailable | Error: HTTP 409 POST api/big-bangs/9ba7f30f-b9e4-4ec4-8de3-242c8bb2e9c6/reports/final: final report requires terminal multiverses: M1, M1.1 |
| labor_1_3y_coffee_union_campaign | long_high_detail_strict | report_failed |  | tick_008 | M1 | Error: HTTP 503 POST api/multiverses/36770887-3799-4690-b7a4-f0799682b766/simulate-next-tick: LLM unavailable | Error: HTTP 409 POST api/big-bangs/7668a803-9736-4591-acf2-522362af50ea/reports/final: final report requires terminal multiverses: M1 |
| tech_3y_filesharing_injunction | long_high_detail_strict | report_failed |  | tick_010 | M1, M1.1 | Error: HTTP 503 POST api/multiverses/7ea622b1-59bb-496e-bf54-a84a66866510/simulate-next-tick: LLM unavailable | Error: HTTP 409 POST api/big-bangs/92fdc91a-f510-4ac3-a002-63d66ced42bb/reports/final: final report requires terminal multiverses: M1, M1.1 |
| ai_1_3y_debt_recovery_scandal | long_high_detail_strict | transient_failure_cap_reached | transient_failure_cap_reached | tick_008 | M1 | Error: HTTP 503 POST api/multiverses/fb73ee92-fe94-4ba2-ad97-50cd452bd0f2/simulate-next-tick: LLM unavailable | Error: HTTP 409 POST api/big-bangs/da4c359d-2a72-4bfa-a5c4-669058256bf0/reports/final: final report requires terminal multiverses: M1 |
| ai_3y_childcare_benefit_scandal | long_high_detail_strict | report_failed |  | tick_009 | M1, M1.1 | Error: HTTP 503 POST api/multiverses/0be95f3a-bd9f-4a3b-91ee-aecca22c6a2c/simulate-next-tick: LLM unavailable | Error: HTTP 409 POST api/big-bangs/1f813c2c-311d-43cb-ab5e-232bda9d2a9d/reports/final: final report requires terminal multiverses: M1, M1.1 |


The harness now provides `resume-failures`, which lists active multiverses, advances them with fresh idempotency keys, backs off on transient `503`/timeout failures, writes new `resume_*` command records, refreshes model audits, and regenerates reports when all multiverses are terminal. During verification, the live provider path still returned `503`; a targeted retry therefore exited with `transient_failure_cap_reached`, preserving a clean retryable state.

## Representative Run Records

| event_id | category | horizon | variant | status | top_observed | actual_probability | tvd |
| --- | --- | --- | --- | --- | --- | --- | --- |
| election_3y_coup_after_disputed_vote | elections/legitimacy | 3+ years | baseline | completed | military_coup_and_durable_conflict | 0.6667 | 0.1333 |
| environment_1_3m_fuel_pipeline_shutdown | environment/resource | 1-3 months | baseline | completed | pipeline_restarts_quickly_after_regional_shortages | 0.3333 | 0.3067 |
| finance_1_3y_crypto_exchange_fraud | finance/market confidence | 1-3 years | baseline | completed | bankruptcy_founder_conviction_and_long_asset_recovery | 1.0 | 0.34 |
| finance_weeks_short_squeeze | finance/market confidence | weeks | baseline | completed | volatile_squeeze_recedes_after_restrictions_and_hearings | 0.5 | 0.34 |
| labor_1_3m_delivery_contract_threat | labor/social movements | 1-3 months | baseline | completed | strike_averted_with_tentative_contract | 0.75 | 0.18 |
| election_weeks_certification_pressure | elections/legitimacy | weeks | baseline | completed | state_level_reversal | 0.3333 | 0.5867 |
| tech_1_3y_social_platform_buyout | tech/platform | 1-3 years | baseline | completed | deal_collapse | 0.0 | 0.88 |
| health_1_3y_school_reopening_conflict | public health | 1-3 years | baseline | completed | long_term_remote_default | 0.2 | 0.52 |
| policy_3_12m_net_neutrality_repeal | policy/regulatory backlash | 3-12 months | baseline | completed | federal_repeal_takes_effect_with_state_litigation_tail | 0.75 | 0.24 |
| finance_3y_sovereign_debt_crisis | finance/market confidence | 3+ years | baseline | completed | bailouts_and_austerity_keep_country_in_currency_union | 0.3333 | 0.4267 |
| labor_3y_low_wage_campaign | labor/social movements | 3+ years | baseline | completed | large_local_wage_gains_without_full_federal_target | 0.5 | 0.14 |
| corp_weeks_airline_removal | corporate PR crisis | weeks | baseline | completed | apology_settlement_and_policy_changes | 0.5 | 0.42 |
| finance_3_12m_algorithmic_stablecoin_collapse | finance/market confidence | 3-12 months | baseline | completed | stablecoin_and_token_collapse_with_enforcement_tail | 0.5 | 0.3 |
| campus_1_3y_statue_campaign | campus/civil society | 1-3 years | baseline | completed | statue_retained_with_contextualization_process | 0.375 | 0.26 |
| labor_3_12m_hollywood_strikes | labor/social movements | 3-12 months | baseline | completed | new_contracts_with_pay_and_ai_guardrails | 0.3333 | 0.4667 |
| environment_3y_crossborder_pipeline | environment/resource | 3+ years | baseline | completed | permit_revoked_project_terminated | 0.5 | 0.34 |
| labor_1_3y_coffee_union_campaign | labor/social movements | 1-3 years | baseline | completed | many_election_wins_slow_contracting_then_framework | 0.3333 | 0.3667 |
| health_1_3m_measles_policy | public health | 1-3 months | baseline | completed | outbreak_contained_and_exemption_law_passes | 0.4 | 0.32 |
| labor_weeks_auto_strike | labor/social movements | weeks | baseline | completed | prolonged_strike_no_deal | 0.3333 | 0.5867 |
| corp_1_3m_beer_brand_boycott | corporate PR crisis | 1-3 months | baseline | completed | sustained_sales_drop_and_brand_leadership_changes | 0.5 | 0.34 |
| corp_3_12m_luxury_ad_controversy | corporate PR crisis | 3-12 months | baseline | completed | campaign_pulled_apologies_and_brand_repair | 0.2857 | 0.3343 |
| policy_1_3m_fuel_tax_revolt | policy/regulatory backlash | 1-3 months | baseline | completed | fuel_tax_scrapped_but_protests_continue | 0.5 | 0.12 |
| health_3y_water_crisis | public health | 3+ years | baseline | completed | emergency_acknowledged_settlement_and_slow_infrastructure_repair | 0.5 | 0.4 |
| health_3_12m_ebola_response | public health | 3-12 months | baseline | completed | vaccine_led_resolution | 0.2 | 0.38 |
| health_weeks_vaccine_pause | public health | weeks | baseline | completed | long_pause | 0.2 | 0.5 |


## Model Audit

Every completed run writes `model_audit.json` under its run directory. The aggregate table counts a run as Gemini-only only when all audited LLM rows list exactly `google/gemini-3.1-flash-lite-preview`.

## Scoring Caveat

Automated scoring uses token overlap between final report endpoint text and hidden outcome labels. Low-signal or surprising cases should be manually reviewed against `final_report.md`, tick records, and source files before treating the score as final.

## Accuracy Pattern Notes

- Weakest completed categories were public health and elections/legitimacy. The logs show process states such as reviews, pauses, certifications, and investigations being mistaken for endpoint movement.
- Weakest horizon was weeks. The model often under-weighted short institutional deadlines, rapid warning-lift decisions, and quick negotiated settlements.
- `short_high_detail_default` provided the best reliability-adjusted sweep signal. `long_high_detail_strict` had the best completed top-match rate but materially higher runtime failure risk and cost.
