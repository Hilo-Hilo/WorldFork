# WorldFork Update, Reinstall, And Uninstall Module

Use this module when the user asks to update WorldFork, refresh the local
checkout, reinstall the CLI/runtime, preserve local history, or uninstall
WorldFork cleanly.

## Core Rule: Refresh This Skill First

Before updating the WorldFork repo or runtime, refresh the public skill from
GitHub. Update instructions may have changed, and the skill is the operator
runbook.

```bash
npx skills add Hilo-Hilo/WorldFork/skills/worldfork --all
```

After refreshing, reread `$worldfork` and this module before continuing.

## Preservation Policy

Default to preserving local preferences and history:

- `.env`
- OpenAI Codex auth under `~/.worldfork/`
- Docker volumes and Postgres data
- Redis unless the user approves clearing local disposable queue state
- `runs/`
- `artifacts/`
- local Docker override files
- generated reports and report-version records
- local branches and uncommitted source changes

Never run destructive cleanup such as `make clean-data`, deleting Docker volumes,
or deleting the checkout unless the user explicitly asks for a purge.

## Safe Update

Use the CLI updater for normal updates. It is designed to preserve local
preferences, run history, artifacts, Docker overrides, and local data.

```bash
npx skills add Hilo-Hilo/WorldFork/skills/worldfork --all
worldfork update --dry-run
worldfork update --yes
```

If the CLI is missing or stale, reinstall it from the current checkout, then
retry the updater:

```bash
python3.11 -m pip install -e ./cli
worldfork --help
worldfork update --dry-run
worldfork update --yes
```

After code updates, apply migrations and restart only when needed:

```bash
make migrate
make up
worldfork status
worldfork query GET /readyz --no-api-prefix
```

If Docker services are already running and the update changes backend code,
restart through the project’s normal Compose/Make path rather than killing
containers by hand.

## Reinstall Without Losing Data

Use this when the CLI/package install is broken but the checkout and local data
should remain.

```bash
npx skills add Hilo-Hilo/WorldFork/skills/worldfork --all
python3.11 -m pip install -e ./cli
worldfork --help
make build
make up
make migrate
worldfork status
```

This preserves Docker volumes, `.env`, runs, artifacts, reports, and local auth.

If `worldfork` resolves to an old shim:

```bash
which -a worldfork || true
python3.11 -m pip install -e ./cli
worldfork --help
```

Use the source-checkout fallback only to keep setup moving:

```bash
cd cli
uv run --extra dev worldfork --help
cd ..
```

## Clean Uninstall, Preserve History

Use this when the user wants WorldFork stopped/removed from active runtime but
may want to keep local data.

```bash
make down
npx skills remove worldfork -y
```

Then tell the user what remains by design:

- source checkout;
- `.env`;
- Docker volumes;
- `runs/`;
- `artifacts/`;
- `~/.worldfork/` auth/preferences.

Do not remove those preserved files unless the user asks.

## Full Purge

Only do this when the user explicitly asks to delete local runtime data.

Before purging, state that this can remove local database history, run state, and
artifacts. Offer a backup first.

Suggested backup shape:

```bash
backup_dir=\"$HOME/worldfork-backup-$(date +%Y%m%d-%H%M%S)\"
mkdir -p \"$backup_dir\"
cp -a .env \"$backup_dir/.env\" 2>/dev/null || true
cp -a runs \"$backup_dir/runs\" 2>/dev/null || true
cp -a artifacts \"$backup_dir/artifacts\" 2>/dev/null || true
cp -a ~/.worldfork \"$backup_dir/worldfork-home\" 2>/dev/null || true
```

Then, only with approval:

```bash
make down
make clean-data
npx skills remove worldfork -y
```

Remove the source checkout only if the user also asks to remove the code.

## Troubleshooting Updates

If `worldfork update --dry-run` refuses to proceed, do not bypass it with
destructive Git commands. Explain the specific blocker:

- dirty tracked files;
- diverged branch;
- remote changes to protected local paths;
- missing remote/branch;
- stale CLI install;
- unavailable backend.

For source conflicts, ask whether the user wants to preserve, stash, commit, or
manually inspect local changes. For runtime readiness issues, switch to
`skills/debug.md`.
