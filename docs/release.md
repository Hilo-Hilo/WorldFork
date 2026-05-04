# Release Automation

## CI Gate

The CI workflow runs on pushes to `dev`, pull requests targeting `main`, and manual dispatch. It validates Docker Compose config, backend lint, the full maintained backend/CLI test sweep, and backend/CLI wheel builds. It never publishes packages.

## Agent Skills

WorldFork publishes one public repository-hosted skill:

| Skill | Path | Purpose |
| --- | --- | --- |
| `worldfork` | `skills/worldfork/SKILL.md` | Public operator skill for setup, CLI operation, debugging, documentation, cost/time estimation, ledgers, and reports |

The setup, report, debug, CLI, documentation, and runtime guides are module Markdown files inside `skills/worldfork/skills/`. Agents load the module they need after the primary `worldfork` skill triggers.

The repo also keeps `worldfork-full-agent-test` as an internal validation skill for fresh-environment CLI/runtime testing. It is not part of the normal user onboarding install.

The supported user-facing install source is the repository path, because `npx skills add` resolves GitHub shorthand, Git URLs, and local paths:

```bash
npx skills add Hilo-Hilo/WorldFork/skills/worldfork --all
```

The equivalent full URL form is:

```bash
npx skills add https://github.com/Hilo-Hilo/WorldFork/tree/main/skills/worldfork --all
```

For local development inside this checkout, prefer a temporary copy when installing with `--all`. That avoids creating local agent runtime output such as `.agents/`, `skills-lock.json`, and source-tree skill symlinks in the WorldFork repository:

```bash
tmpdir="$(mktemp -d)"
cp -R ./skills/worldfork "$tmpdir/worldfork"
npx skills add "$tmpdir/worldfork" --all
```

Use direct local paths only for discovery validation:

```bash
npx skills add ./skills/worldfork --list -y
npx skills add ./skills/worldfork-full-agent-test --list -y
```

No npm scope, npm organization, or standalone npm package is required for the skill install path. The validation workflow checks that the public skill and internal validation skill are discoverable through the same `npx skills add` mechanism users run.
