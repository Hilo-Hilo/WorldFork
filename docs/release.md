# Release Automation

## CI Gate

The CI workflow runs on pushes to `dev`, pull requests targeting `main`, and
manual dispatch. It validates Docker Compose config, backend lint, the full
maintained backend/CLI test sweep, and backend/CLI wheel builds. It never
publishes packages.

## Skill Package

The generic agent skill lives in `skills/worldfork/`. The supported user-facing
install source is the repository path, because `npx skills add` resolves git,
URL, and local-path sources:

```bash
npx skills add https://github.com/Hilo-Hilo/WorldFork/tree/main/skills/worldfork --all
```

The `worldfork-skill` npm package is an optional standalone distribution
artifact for release tracking. It is not the primary `skills add` source.

The dev branch validates the skill folder and npm package contents without
publishing on pushes to `dev` and pull requests targeting `main`. The publish
workflow runs only after changes land on `main` and only when files under
`skills/worldfork/` change.

Preferred setup is npm trusted publishing for `worldfork-skill` with:

```text
GitHub owner: Hilo-Hilo
Repository: WorldFork
Workflow filename: publish-skill.yml
```

As a fallback, add a repository secret named `NPM_TOKEN` containing an npm
automation or granular publish token for `worldfork-skill`, then set the
repository Actions variable `NPM_USE_TOKEN=true`. The workflow ignores
`NPM_TOKEN` by default so an older OTP-bound token cannot override trusted
publishing.

For subsequent releases, bump `skills/worldfork/package.json`; if the current
version is already on npm, the workflow skips publishing.
