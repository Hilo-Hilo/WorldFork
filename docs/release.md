# Release Automation

## CI Gate

The CI workflow runs on pushes to `dev`, pull requests targeting `main`, and
manual dispatch. It validates Docker Compose config, backend lint, the full
maintained backend/CLI test sweep, and backend/CLI wheel builds. It never
publishes packages.

## Agent Skills

WorldFork publishes two repository-hosted skills:

| Skill | Path | Purpose |
| --- | --- | --- |
| `worldfork-setup` | `skills/worldfork-setup/SKILL.md` | Temporary bootstrap skill for first-time installation and onboarding |
| `worldfork` | `skills/worldfork/SKILL.md` | Ongoing operator skill for running and debugging WorldFork |

The supported user-facing install source is the repository path, because
`npx skills add` resolves GitHub shorthand, Git URLs, and local paths:

```bash
npx skills add Hilo-Hilo/WorldFork/skills/worldfork-setup --all
npx skills add Hilo-Hilo/WorldFork/skills/worldfork --all
```

The equivalent full URL form is:

```bash
npx skills add https://github.com/Hilo-Hilo/WorldFork/tree/main/skills/worldfork-setup --all
npx skills add https://github.com/Hilo-Hilo/WorldFork/tree/main/skills/worldfork --all
```

For local development inside this checkout, prefer a temporary copy when
installing with `--all`. That avoids creating local agent runtime output such
as `.agents/`, `skills-lock.json`, and source-tree skill symlinks in the
WorldFork repository:

```bash
tmpdir="$(mktemp -d)"
cp -R ./skills/worldfork "$tmpdir/worldfork"
cp -R ./skills/worldfork-setup "$tmpdir/worldfork-setup"
npx skills add "$tmpdir/worldfork-setup" --all
npx skills add "$tmpdir/worldfork" --all
```

Use direct local paths only for discovery validation:

```bash
npx skills add ./skills/worldfork --list -y
npx skills add ./skills/worldfork-setup --list -y
```

No npm scope, npm organization, or standalone npm package is required for the
skill install path. The dev/main validation workflow checks that both skills are
discoverable through the same `npx skills add` mechanism users run.
