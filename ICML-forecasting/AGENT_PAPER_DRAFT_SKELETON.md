# Paper Draft Skeleton: WorldFork ICML Forecasting Workshop

## Working title

WorldFork: Auditable Branching Socio-Institutional World Models for LLM Forecasting Agents

## Abstract placeholder

Modern LLM forecasting agents often return a probability and rationale without exposing the counterfactual rollouts, endpoint evidence, social assumptions, or unresolved uncertainty behind the forecast. We introduce WorldFork, a branching forecasting protocol that converts a scenario into checkpointed timelines with branch lineage, endpoint ledgers, path mass, report provenance, and explicit socio-institutional state. We evaluate WorldFork on a 108-card initialization/audit suite, a 24-card resolved forecasting pilot, and 18 long-horizon branch-stress runs. **The standard direct-prompting baseline operates under a synoptic single-prompt regime that real-world forecasting cannot replicate; WorldFork distributes the same information across many bounded-context agents and reproduces single-prompt forecast quality without that information advantage.** The load-bearing forecast result is therefore parity with the direct baseline, not superiority. In addition, WorldFork exposes endpoint uncertainty, lineage, social assumptions, report provenance, and failure modes that single-shot forecast rationales hide — we argue auditability, not accuracy alone, is the right axis to evaluate LLM forecasting agents on.

## Introduction bullets

- Forecasting is increasingly used to evaluate general-purpose AI systems.
- The standard direct-prompting baseline aggregates the full case description, actor list, recent context, and resolution criterion into one reasoning step. That synoptic view is unrealistic: an actual forecaster — or a deployed multi-agent forecasting system — never has every relevant piece of context in a single mind at the same time.
- WorldFork distributes the same case across bounded-context agents (cohorts, heroes, institutions) that only see local actor state and recent simulation events. Compared to the direct baseline, WorldFork carries an information handicap, not an advantage.
- Reproducing single-prompt forecast quality under that tighter information regime — parity, not superiority — is the load-bearing result of this paper.
- Single-shot LLM forecasts are also hard to audit: they hide branch assumptions, social mechanisms, and failure modes. WorldFork represents a forecast as an inspectable tree of timeline rollouts with explicit endpoint evidence and report provenance.
- Contributions: protocol, socio-institutional state, parity-under-information-constraint benchmark.

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
