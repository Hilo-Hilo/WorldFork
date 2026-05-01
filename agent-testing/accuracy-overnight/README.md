# Overnight Accuracy Evaluation

Local-only research workspace for the 2026-04-30 WorldFork accuracy study.

Primary reference:

- `REPRODUCIBILITY_LOG.md` is the running trace of commands, decisions, findings, generated artifacts, and verification status. Treat it as the source of truth for this study.

Directory layout:

- `events/`: anonymized scenario dossiers used as prompts.
- `sources/`: source links, real outcomes, and expected distributions kept out of prompts.
- `runs/`: command output, runtime IDs, model audits, and run records.
- `analysis/`: scoring tables, parameter comparisons, and discrepancy traces.
- `harness/`: local scripts used to generate and run the evaluation.
- `latex/`: LaTeX source and compiled PDF report.

Constraints:

- No push, PR, or deletion of generated artifacts.
- Use only `google/gemini-3.1-flash-lite-preview` for live API-credit calls.
- Keep source/outcome notes separate from anonymized scenario prompt files.

Resume failed runs:

```bash
python3 agent-testing/accuracy-overnight/harness/accuracy_harness.py resume-failures --base-url http://127.0.0.1:18013
```

The resume command scans failed saved `run_record.json` entries, lists active multiverses for each `big_bang_id`, retries transient `simulate-next-tick` failures with fresh idempotency keys, and writes new `resume_*` command records without deleting the original failure records. Use `--only EVENT_ID` to target one failed event.

If the provider is in a repeated transient outage, use `--max-consecutive-transient-failures` with a low value to leave a clean `transient_failure_cap_reached` status instead of spending the full request cap. Later retries remain safe because the original command records and all `resume_*` command records are retained.
