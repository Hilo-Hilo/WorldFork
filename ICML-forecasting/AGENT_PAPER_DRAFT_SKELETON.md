# Paper Draft Skeleton: WorldFork ICML Forecasting Workshop

## Working title

WorldFork: Auditable Branching Socio-Institutional World Models for LLM Forecasting Agents

## Abstract placeholder

Modern LLM forecasting agents often return a probability and rationale without exposing the counterfactual rollouts, endpoint evidence, social assumptions, or unresolved uncertainty behind the forecast. We introduce WorldFork, a branching forecasting protocol that converts a scenario into checkpointed timelines with branch lineage, endpoint ledgers, path mass, report provenance, and explicit socio-institutional state. We evaluate WorldFork on a 108-card initialization/audit suite, a 24-card resolved forecasting pilot, and 18 long-horizon branch-stress runs. Report the actual results here after scoring: Brier/log score, unresolved mass, report grounding, lineage integrity, social-state consistency, and cost. Our findings suggest that branching world models are most valuable not as guaranteed accuracy boosters, but as auditable forecast artifacts that reveal where forecasts depend on contested social mechanisms or unresolved endpoint evidence.

## Introduction bullets

- Forecasting is increasingly used to evaluate general-purpose AI systems.
- Single-shot LLM forecasts are hard to audit: they hide branch assumptions and failure modes.
- Social/institutional events require actors, authorities, trust/dependency/conflict, not only text rationales.
- WorldFork represents a forecast as an inspectable tree of timeline rollouts.
- Contributions: protocol, socio-institutional state, benchmark/evaluation.

## Method bullets

- Big Bang scenario initialization.
- Multiverse/tick runtime.
- Branch policy and God-agent review.
- Endpoint ledgers and path mass.
- Tick counts are caps: endpoint-ledger/path-mass resolution is the stopping criterion, and unresolved or insufficient mass resumes the existing Big Bang rather than reinitializing.
- For E3 resolved cards, the paper rows use deadline-aware tick durations and score only explicit `yes`/`no` candidate endpoints. Auxiliary mechanism endpoints are audit traces, not binary-scoring endpoints.
- Report provenance.
- Socio-institutional state: actors/cohorts, graph layers, sociology signals, social posts.
- Emotion observability: logged affective observations only, not a validated psychology model.

## Benchmark bullets

- Existing 72 synthetic stress prompts.
- New 36 add-on: 24 resolved forecast cards, 8 longform dossier cards, 4 adversarial/calibration cards.
- Direct baselines vs WorldFork no-branch vs WorldFork branching.
- Long-horizon stress set: `civic_002`, `labor_002`, `platform_004`, `health_004`, `election_002`, `corporate_004`, `dossier_001`, `dossier_002`, `dossier_003`, `dossier_004`, `dossier_005`, `dossier_006`, `dossier_007`, `dossier_008`, `calibration_001`, `calibration_002`, `calibration_003`, `calibration_004`.

## Results placeholders

### Forecast scoring

Insert Table 2.

Discuss whether WorldFork improves, matches, or worsens Brier/log. Emphasize unresolved mass and cost.

### Audit metrics

Insert Table 3.

Discuss lineage, branch locality, endpoint coverage, report grounding, failure visibility.

### Socio-institutional state

Insert Table 4.

Discuss graph/social mechanism grounding and cautious emotion-observability result.

## Limitations

- Small retrospective resolved-card set.
- Leakage risk even with masking/source controls.
- Synthetic scenarios test auditability, not real-world accuracy.
- Social-state metrics are rubric-based.
- Emotion observations are not validated human affect modeling.
- Model/cost dependence.

## Conclusion

WorldFork is best viewed as an auditable forecasting substrate. Its competitive value is exposing forecast structure and uncertainty, not claiming prophetic accuracy.
