# Release Automation

## CI Gate

The CI workflow runs on pushes to `dev`, pull requests targeting `main`, and
manual dispatch. It validates Docker Compose config, backend lint, the full
maintained backend/CLI test sweep, and backend/CLI wheel builds. It never
publishes packages.

## Agent Skill

The generic agent skill lives in `skills/worldfork/SKILL.md`. The supported
user-facing install source is the repository path, because `npx skills add`
resolves GitHub shorthand, Git URLs, and local paths:

```bash
npx skills add Hilo-Hilo/WorldFork/skills/worldfork --all
```

The equivalent full URL is:

```bash
npx skills add https://github.com/Hilo-Hilo/WorldFork/tree/main/skills/worldfork --all
```

For local development:

```bash
npx skills add ./skills/worldfork --all
```

No npm scope, npm organization, or standalone npm package is required for the
skill install path. The dev/main validation workflow checks that the skill is
discoverable through the same `npx skills add` mechanism users run.
