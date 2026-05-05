# WorldFork: Auditable Branching Socio-Institutional World Models for LLM Forecasting Agents

## Abstract

Large language model forecasting systems commonly return a probability and a rationale, but the artifacts behind that forecast are difficult to inspect: counterfactual branches, unresolved endpoint evidence, social assumptions, and failure modes are often compressed into a single explanation. We present WorldFork, a forecasting protocol that represents a scenario as checkpointed timelines with branch lineage, endpoint ledgers, path mass, report provenance, and explicit socio-institutional state. We evaluate the protocol with a 108-card initialization and audit suite, a 24-card retrospective resolved-forecast pilot, and long-horizon branching stress runs designed to test lineage, report grounding, uncertainty preservation, and social-state observability. The current artifact package has passed static card QA and leakage separation; live forecast scores and audit metrics will be inserted after frozen model outputs are produced. The intended claim is not that branching simulation guarantees better Brier score, but that it produces auditable forecast objects that expose where a forecast depends on contested mechanisms or unresolved evidence.

## 1. Introduction

Forecasting is becoming a central test of whether general-purpose AI systems can reason about the real world under uncertainty. A forecast, however, is not only a probability. For institutional and social events, useful forecasts depend on actors, authority constraints, incentives, trust and dependency relationships, procedural deadlines, information flows, and unresolved evidence. A single-shot LLM forecast can mention these factors in prose, but it usually does not provide a stable artifact showing which assumptions produced which endpoint distribution.

WorldFork addresses this gap by treating a forecast as an auditable branching world model. A Big Bang initializer converts a scenario into actors, cohorts, constraints, candidate endpoints, graph layers, and initial socio-institutional state. A tick runtime advances timelines, branch policies create alternate continuations when decisions or mechanisms diverge, endpoint ledgers track outcome evidence, and final reports cite path mass and evidence. This paper evaluates whether such structure makes forecasts more inspectable and whether it can be scored against direct forecasting baselines.

The contributions are:

1. **Branching forecast protocol.** WorldFork represents forecasts with checkpointed Big Bangs, ticks, branch lineage, endpoint ledgers, path mass, report provenance, and unresolved uncertainty.
2. **Socio-institutional world state.** The protocol exposes actors and cohorts, trust/dependency/conflict/influence/coalition/exposure graphs, sociology signals, source packets, and bounded emotion observations as audit data.
3. **Pilot benchmark and evaluation plan.** The package combines a 108-card initialization/audit suite, 24 resolved forecast cards, 18 long-horizon WorldFork stress runs, direct baselines, forecast scoring, audit metrics, and social-state consistency scoring.

## 2. Method

### Big Bang Initialization

WorldFork starts from a public scenario card. The initializer produces a Big Bang with typed actors, cohorts, scenario constraints, candidate endpoints, and initial graph and sociology state. For forecast cards, endpoints are binary yes/no outcomes. For dossier and calibration cards, endpoints include several plausible institutional resolutions plus unresolved or abstention states. Initialization is scored for schema completeness, actor recall, authority fidelity, constraint preservation, endpoint extraction, graph quality, sociology baseline quality, bounded emotion observability, prompt-injection resistance, and uncertainty honesty.

### Timeline Ticks and Branching

A multiverse is advanced in ticks. Each tick can update actor positions, social signals, events, endpoint evidence, and logs. The branch policy controls maximum branch depth, active multiverses, branches per tick, and branch-score threshold. Short resolved runs compare no-branch and branching policies. Long-horizon audit runs use a deeper policy to test whether branch lineage, branch locality, path-mass accounting, and endpoint tracking remain inspectable over many ticks.

### Endpoint Ledgers, Path Mass, and Reports

Endpoint ledgers track whether each candidate endpoint is supported, contradicted, unresolved, or not yet checkable. Final reports are scored by sampling claims and verifying support from ticks, ledgers, logs, or source packets. Path mass is reported separately from yes/no forecast scoring so abstention and unresolved evidence are visible instead of silently normalized away.

### Socio-Institutional State

WorldFork models social assumptions as audit state, not as a claim of psychological realism. Graph layers and sociology signals are useful only if they make branch divergence or report claims traceable. Emotion observations are treated as bounded, logged observability signals; the paper must not claim validated human affect modeling.

## 3. Benchmark and Metrics

The benchmark contains 108 public cards: 72 existing synthetic WorldFork stress prompts and 36 additional forecasting cards. The 36-card add-on contains 24 retrospective resolved forecast cards, 8 longform source-packet dossiers, and 4 adversarial/calibration cards. Private resolutions and gold checklists are withheld from forecast-producing systems until forecasts are frozen.

Forecast accuracy on the 24 resolved cards uses binary Brier score and clamped negative log score. For WorldFork outputs with unresolved path mass, the primary yes/no score normalizes over yes and no when possible and reports unresolved mass separately. Audit runs are scored on lineage integrity, branch locality, endpoint coverage, endpoint honesty, path-mass consistency, report grounding, failure observability, cost transparency, social-state consistency, and emotion observability.

The static QA pass for this run found 108 public cards, 36 private eval rows, matching public/private IDs, no private fields in public cards, and 24/24 resolved cards with at least one resolution source. Live source fetching and full historical source verification remain to be completed before final claims.

## 4. Results

### 4.1 Card QA

The static package QA passed. Public cards contain no private resolution fields, the 36 additional public/private/legacy records share matching IDs, and the resolved pilot is balanced across yes and no labels. This is necessary but not sufficient for final benchmark validity: the resolved-card sources still require live source verification, and the paper should describe the resolved set as a retrospective pilot with leakage risk.

### 4.2 Forecast Scoring

Table 2 is reserved for direct, structured-direct, WorldFork no-branch, and WorldFork branching scores. These rows must not be filled until model outputs are frozen and scored against the private eval file.

### 4.3 Audit Metrics

Table 3 is reserved for long-horizon lineage, branch locality, endpoint coverage, path-mass consistency, report grounding, failure observability, and cost transparency. These rows require live WorldFork runs and evidence sampling.

### 4.4 Socio-Institutional State

Table 4 is reserved for graph-layer presence, graph plausibility, social mechanism traceability, sociology signal usefulness, emotion observability, and report social grounding. The intended interpretation is auditability and traceability, not psychological validity.

## 5. Limitations

The resolved cards are retrospective and use partial masking, so leakage cannot be ruled out. The synthetic and dossier cards test auditability and stress behavior rather than direct real-world forecasting accuracy. Social-state metrics are rubric-based and require evidence sampling. Emotion observations should be interpreted only as bounded trace variables. Runtime cost and wall-clock time may dominate the practical value of branching, especially for long-horizon runs. Any accuracy claim must be conditioned on the actual model routing, dates, costs, and failures recorded in the run artifacts.

## 6. Conclusion

WorldFork is best viewed as an auditable forecasting substrate. Its value is the production of inspectable forecast artifacts: branch lineage, endpoint ledgers, path mass, report provenance, unresolved uncertainty, and social assumptions that can be checked after the fact. The final paper should claim improved auditability only where the scored artifacts support it, and should claim improved forecast accuracy only if the Brier/log-score evidence supports that conclusion.

