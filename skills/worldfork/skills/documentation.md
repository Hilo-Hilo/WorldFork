# WorldFork Documentation Module

Use this module when updating or explaining WorldFork documentation, README content, ReadTheDocs, or the public landing site.

## Documentation Sources

- ReadTheDocs source: `docs/`
- Sphinx config: `docs/conf.py`
- Main README: `README.md`
- Public site checkout, when present on this machine: `/Users/hansonwen/worldfork-site`
- Public docs URL: `https://worldfork.readthedocs.io/en/latest/`
- Site URL: `https://worldfork.tech`

## Current Public Skill Install

Document one user-facing skill install:

```bash
npx skills add Hilo-Hilo/WorldFork/skills/worldfork --all
```

Do not publish separate `worldfork-setup` or `worldfork-report` install instructions. Those workflows are internal modules inside the `worldfork` skill.

## ReadTheDocs Pages

Update the smallest relevant page:

- `docs/setup.md` for first-run install/onboarding.
- `docs/cli.md` for commands.
- `docs/runtime.md` for initializer/tick/queue internals.
- `docs/reporting.md` for ledgers, path mass, reports, renders, and report costs.
- `docs/agent.md` for agent-safe operations.
- `docs/release.md` for skill publishing and validation.

## Style

Keep docs CLI-first and backend-first. Say clearly when something spends live API credits, may take a long time, or can mutate local runtime state.
