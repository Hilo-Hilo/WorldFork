# Failures and Open Risks

## Current Blockers

- The local Docker stack was not running when the run directory was created.
- `worldfork status` against the default backend URL did not return promptly while the stack was down.
- `.env` has `OPENROUTER_API_KEY=replace-me`, so OpenRouter-backed live runs are not available from this checkout unless credentials are supplied elsewhere.
- Resolution source URLs have been inventoried but not independently fetched and verified in this run.

## Not Yet Complete

- E1 initialization screen on 108 public cards.
- E2 direct baselines on 24 resolved cards.
- E3 WorldFork short resolved no-branch and branching runs.
- E4 long-horizon audit runs.
- E5 social-state/emotion audit.
- Optional social ablation and branch-threshold sweep.

