# Release Automation

## CI Gate

The CI workflow runs on pushes to `dev`, pull requests targeting `main`, and
manual dispatch. It validates Docker Compose config, backend lint, the full
maintained backend/CLI test sweep, and backend/CLI wheel builds. It never
publishes packages.

## Skill Package

The generic agent skill lives in `skills/worldfork/` and publishes as
`@worldfork/skill`.

The dev branch validates the skill package without publishing on pushes to
`dev` and pull requests targeting `main`. The publish workflow runs only after
changes land on `main` and only when files under `skills/worldfork/` change.

Preferred setup is npm trusted publishing for `@worldfork/skill` with:

```text
GitHub owner: Hilo-Hilo
Repository: WorldFork
Workflow filename: publish-skill.yml
```

As a fallback, add a repository secret named `NPM_TOKEN` containing an npm
automation or granular publish token for `@worldfork/skill`.

For subsequent releases, bump `skills/worldfork/package.json`; if the current
version is already on npm, the workflow skips publishing.
