# Contributing to WorldFork

WorldFork is a CLI-first backend project. Most development work should be validated through the `worldfork` command, the FastAPI backend, workers, and the maintained test sweep. Do not assume a web frontend exists.

## Branch Flow

Always do your work on your own branch. A topic branch keeps the shared branches reviewable and gives the `dev` branch a clean integration point.

Use this flow for every change:

1. Start from the current integration branch.

   ```bash
   git fetch origin
   git switch dev
   git pull --ff-only origin dev
   git switch -c <your-branch-name>
   ```

2. Make and commit focused changes on your branch.

3. Run the appropriate local checks before integration.

   ```bash
   ./scripts/run_tests.sh all
   make lint
   ```

4. When the branch is ready for `main`, merge it into `dev` first. The `dev` branch is the integration gate for WorldFork. It has workflow checks set up to catch breakage before anything reaches `main`.

5. Let the `dev` workflows finish. If they fail, fix the issue on your branch or on a follow-up branch and merge that fix into `dev`.

6. After `dev` is green and the runtime smoke/functionality checks pass on `dev`, merge the validated work into `main`.

Do not bypass `dev` for normal feature, bugfix, documentation, or agent-surface changes.

## Local Setup

Install the local CLI before using the runtime:

```bash
python3.11 -m pip install -e ./cli
worldfork --help
```

Configure the backend:

```bash
cp .env.example .env
```

Set `OPENROUTER_API_KEY` in `.env` when you need live LLM-backed workflows. Live API-credit runs must use:

```text
google/gemini-3.1-flash-lite-preview
```

Start the local stack:

```bash
make build
make up
make migrate
make seed
```

Verify the backend:

```bash
worldfork status
worldfork query GET /readyz --no-api-prefix
```

## Working With WorldFork

The `worldfork` command is the primary operator and agent interface. Start exploration with summary output and put global flags before the command:

```bash
worldfork --verbosity summary runs list
worldfork --fields id,status,created_at jobs list
worldfork --json status
```

Use canonical runtime commands when testing changes:

```bash
worldfork agent discover
worldfork status
worldfork init --name "Atlas onboarding" --scenario-file examples/test-big-bang.md
worldfork watch big-bang <big-bang-id>
worldfork watch multiverse <multiverse-id>
worldfork runs list
worldfork jobs list --status failed
worldfork logs list --status failed
worldfork models defaults
worldfork settings show
worldfork smoke live
worldfork demo atlas
```

Long-running mutations are job-first. Use bounded waits:

```bash
worldfork jobs wait <job-id> --timeout 300
```

Reports are structured database records first. Markdown and PDF outputs are render artifacts for a specific report version:

```bash
worldfork reports list <big-bang-id>
worldfork reports view <report-version-id>
worldfork reports render <report-version-id> --format pdf
```

## Validation

Run the full maintained sweep before merging into `dev`:

```bash
./scripts/run_tests.sh all
make lint
```

For focused work, use the narrowest matching layer while iterating:

```bash
./scripts/run_tests.sh unit
./scripts/run_tests.sh cli
./scripts/run_tests.sh integration
./scripts/run_tests.sh e2e
```

Check Docker Compose configuration when touching runtime wiring:

```bash
docker compose config --quiet
```

Run the live smoke only when the backend is configured and API-credit use is intended:

```bash
worldfork smoke live
```

Before merging `dev` into `main`, confirm that:

- the `dev` workflow checks passed
- the relevant local or live smoke checks passed on `dev`
- runtime behavior was verified through `worldfork`, not through assumptions about internal APIs
- failures in jobs, logs, or reports were inspected through the CLI

## Scope And Review Expectations

Keep contributions focused. Include tests with behavior changes, update documentation when commands or workflows change, and avoid unrelated refactors inside feature branches.

New agent work should target the canonical runtime surface:

- `backend/app/main.py`
- the `app.*` package
- `/api/agent/*`
- `/api/big-bangs`
- `/api/multiverses`
- `/api/ticks`
- `/api/jobs`
- `/api/logs`
- `/api/reports`
- the `worldfork` CLI

Compatibility routes can remain for older contracts, but new contributor and agent workflows should use the canonical surface.
