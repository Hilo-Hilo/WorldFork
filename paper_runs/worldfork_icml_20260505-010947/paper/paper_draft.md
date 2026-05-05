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

### 4.1.1 Initialization Smoke Coverage

The additional 36-card set now has complete live initialization evidence. The synchronous initializer smoke for `resolved_003` completed in 146.20 seconds; the queued E3 smoke initialized `resolved_004` in 131.22 seconds; queued initializer batches completed the remaining 34 additional public cards. The eight-case batch reached p1's configured concurrency of 8, and the final 22-case add-on batch drained in 464.64 seconds wall time across three waves. This is still a partial screen for the paper's full E1 requirement: the existing 72 public stress cards remain to be initialized.

### 4.2 Forecast Scoring

The direct baseline block is complete for all 24 resolved cards under two conditions. The one-shot direct prompt achieved mean Brier 0.242492 and mean clamped log score 0.701698. The structured direct prompt achieved mean Brier 0.238363 and mean clamped log score 0.677498. Both direct baselines have zero unresolved mass because the output contract requires a normalized yes/no distribution. These results provide the comparison point for the pending WorldFork no-branch and branching runs; no claim about WorldFork forecast accuracy should be made until those rows are filled.

### 4.3 Runtime Smoke and Audit Metrics

The first WorldFork tick/report smoke completed for `resolved_003` under a synchronous `run-until-complete` call. It ran one tick, kept a single active multiverse, generated report artifacts, and completed 14/14 logged LLM calls. The command wall time was 408.49 seconds, with 174,427 reported tokens and 655.4009 aggregate LLM seconds across initializer, cohort, event-summary, governance, and report calls. A second smoke on `resolved_004` used the queued `/run-until-complete/jobs` path. It initialized in 131.22 seconds, then completed one tick and reports after a 219.47 second job wait, with 12/12 logged LLM calls, 143,700 reported tokens, and 445.3739 aggregate LLM seconds. Queue telemetry showed one active p1 `worldfork.execute_job` task while p0/p2/p3 were idle. Because the local OpenRouter key is a placeholder, both smokes used a runtime-only override that routed all job types to OpenAI Codex `gpt-5.4`. These smokes validate the synchronous and queued tick/report paths, but they are not scored E3 results. The queued result suggests Celery is useful for running independent cases concurrently; a single `run_big_bang_until_complete` case remains one long p1 job.

Table 3 is reserved for long-horizon lineage, branch locality, endpoint coverage, path-mass consistency, report grounding, failure observability, and cost transparency. These rows require full live WorldFork runs and evidence sampling.

### 4.4 Socio-Institutional State

Table 4 is reserved for graph-layer presence, graph plausibility, social mechanism traceability, sociology signal usefulness, emotion observability, and report social grounding. The intended interpretation is auditability and traceability, not psychological validity.

## 5. Limitations

The resolved cards are retrospective and use partial masking, so leakage cannot be ruled out. The synthetic and dossier cards test auditability and stress behavior rather than direct real-world forecasting accuracy. Social-state metrics are rubric-based and require evidence sampling. Emotion observations should be interpreted only as bounded trace variables. Runtime cost and wall-clock time may dominate the practical value of branching, especially for long-horizon runs. Any accuracy claim must be conditioned on the actual model routing, dates, costs, and failures recorded in the run artifacts.

## 6. Conclusion

WorldFork is best viewed as an auditable forecasting substrate. Its value is the production of inspectable forecast artifacts: branch lineage, endpoint ledgers, path mass, report provenance, unresolved uncertainty, and social assumptions that can be checked after the fact. The final paper should claim improved auditability only where the scored artifacts support it, and should claim improved forecast accuracy only if the Brier/log-score evidence supports that conclusion.
