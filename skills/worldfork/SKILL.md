---
name: worldfork
description: Use when operating, setting up, onboarding, configuring, estimating cost/time for, debugging, documenting, reporting on, or validating the WorldFork branching simulation backend through its CLI/API, including Big Bang initialization, Atlas demos, ticks, jobs, ledgers, reports, runtime health, and model routing.
---

# WorldFork

WorldFork is a backend + worker + CLI product for branching social simulations. Its core framing is a Monte Carlo tree search of the real world: initialize a Big Bang, advance timeline ticks, branch when decisions matter, track endpoint ledgers/path mass, and compare outcomes through structured reports.

The primary interface is the `worldfork` CLI. Start by discovering the live surface instead of guessing:

```bash
worldfork agent discover
worldfork status
worldfork setup
```

## Standard Operating Practice

- Use the CLI first. Use `worldfork query` only when command discovery shows no first-class command for the API operation.
- Put global flags before the command: `worldfork --verbosity summary runs list`.
- Start with `--verbosity summary`; move to `normal`, `full`, or `--json` only for a specific evidence gap.
- Use `--fields a,b,c` for large rows when only a few top-level keys matter.
- Do not hardcode backend URLs. Use the CLI default, `--base-url`, `WORLD_FORK_API_BASE`, or `BACKEND_API_BASE`.
- Use bounded waits for jobs: `worldfork jobs wait <job-id> --timeout 300 --poll-interval 2`.
- Treat reports as structured database records first. Markdown/PDF are renders generated from report versions on request.
- Before live API-credit work, state the likely time/cost uncertainty and inspect estimates when possible.
- Do not clear Redis, delete data, change model routes, start Atlas, or spend live API credits without user approval.

## Model, Cost, And Time Defaults

For onboarding, smoke, and Atlas, use the configured route policy unless the user explicitly authorizes another policy:

| Route class | Supported provider/model examples | Rationale |
| --- | --- | --- |
| Cohort, hero, action, high-volume simulation | OpenRouter `deepseek/deepseek-v4-flash` | Cheap and fast for many actor calls |
| Initializer, God review, endpoint ledger, report | Strong governance/report model route, such as OpenAI Codex `gpt-5.4` or OpenRouter-hosted Kimi/Claude | Stronger reasoning for governance and summaries |
| Event summary | Strong governance/report model route unless route config says otherwise | Aggregate tick-level reasoning over executed events |

Before starting a substantial Big Bang or Atlas run:

```bash
worldfork settings llm
worldfork costs estimate
worldfork runs estimate <big-bang-id>
```

After or during a run:

```bash
worldfork runs cost <big-bang-id> --include-calls
worldfork ticks timing <tick-snapshot-id>
worldfork ticks cost <tick-snapshot-id> --include-calls
```

Explain to the user that estimates are model- and branch-policy-dependent. Runtime can grow with cohort count, max ticks, branch caps, report generation, and context growth. Cohort calls may run in parallel, but downstream God review, ledger, summary, and report stages still take serial time.

## Starting A Big Bang Well

For a startup Big Bang, help the user make the scenario operational before running it:

- Define the decision question and endpoint conditions clearly.
- Choose tick duration and horizon deliberately; for Atlas-style crisis demos, 720 simulated minutes per tick is common.
- Ask whether they want a cheap exploratory run, a stronger governance/report run, or a high-quality expensive run.
- Warn that initialization can be slow because it turns the scenario into actors, cohorts, population state, graphs, initial events, branch hypotheses, and endpoint ledgers.
- Check cost/time estimates before long runs.
- Use `worldfork init` for initialization proof; use `worldfork demo atlas` or live smoke for full tick/branch/report proof.

## Progressive Skill Modules

Read only the module that matches the current task:

| Task | Read this file |
| --- | --- |
| First-time setup, provider configuration, Atlas onboarding | `skills/setup.md` |
| Report generation, endpoint ledgers, path mass, final reports | `skills/report.md` |
| Runtime failures, stuck ticks, jobs, queues, LLM audit, observability | `skills/debug.md` |
| CLI command patterns and canonical operator workflow | `skills/worldfork-cli.md` |
| Safe update, reinstall, uninstall, data preservation | `skills/update-uninstall.md` |
| ReadTheDocs, website, docs publishing, public documentation map | `skills/documentation.md` |
| Runtime mental model and component relationships | `skills/worldfork.md` |

Use `references/project-orientation.md` when the user is new to WorldFork. Use `references/setup-troubleshooting.md` when setup fails or provider/CLI/Docker behavior is unclear.
