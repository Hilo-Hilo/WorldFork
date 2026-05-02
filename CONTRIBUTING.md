# Contributing to WorldFork

WorldFork is a CLI-first backend project. Most development work should be validated through the `worldfork` command, the FastAPI backend, workers, and the maintained test sweep. Do not assume a web frontend exists.

## Branch Model

WorldFork uses two long-lived branches:

| Branch | Role                                     | Rules                                                                           |
| ------ | ---------------------------------------- | ------------------------------------------------------------------------------- |
| `main` | Production and stable user-facing branch | Protected, PR-only, required checks, no dev-only artifacts                      |
| `dev`  | Flexible integration branch              | Fast iteration branch for agents and maintainers, checks enabled, no publishing |

Use short-lived topic branches for normal work:

```bash
git fetch origin
git switch dev
git pull --ff-only origin dev
git switch -c <your-branch-name>
```

Prefer this promotion path:

```text
topic branch -> dev -> main
```

`dev` is intentionally flexible right now. Maintainers and agents may push directly to `dev` for fast integration, but topic branches are still preferred when a change needs review, has risk, or spans multiple files. `main` is different: do not push directly to `main`. Promote production-ready work through a pull request with passing checks.

Do not assume every commit on `dev` belongs on `main`. `dev` can contain integration-only files such as local container overlays. When promoting to `main`, merge or cherry-pick only the production-safe changes and leave dev-only files behind.

## Required Gates

Before a change reaches `main`, these checks must be green:

- CI backend and CLI test/build workflow
- WorldFork skill validation workflow
- main-branch guard that rejects dev-only container artifacts

The `dev` branch also runs CI and skill validation, but it does not publish packages, release artifacts, or represent the stable install surface.

## Dev Compose Overlay

The default Docker path on `main` must stay production/runtime-oriented. It should build the slim runtime images from `docker-compose.yml` and must not install Python dev extras such as `.[dev]`, `pytest`, `ruff`, or `mypy` into the normal API or worker images.

Development-only Docker behavior belongs on `dev` in `docker-compose.dev.yml`. Use it explicitly:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d
```

Use the optional devtools shell only when you need an isolated container with development dependencies:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml --profile tools run --rm devtools
```

Do not copy `docker-compose.dev.yml`, `.devcontainer`, `docker-compose.local.yml`, or dev Dockerfiles into `main`. The main-branch guard rejects those paths, rejects runtime Dockerfiles that install `.[dev]`, and rejects `docker-compose.yml` entries that target dev images.

When promoting work from `dev` to `main`, avoid a blind full-branch merge if `dev` contains integration-only files. Use a pull request, cherry-pick, or a selective merge that includes production-safe commits and leaves dev-only container artifacts on `dev`.

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

Set `OPENROUTER_API_KEY` in `.env` and configure OpenAI Codex OAuth when you need live LLM-backed workflows. Live API-credit runs must use:

```text
openrouter/deepseek/deepseek-v4-flash
openai-codex/gpt-5.4
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

Before promoting changes from `dev` into `main`, confirm that:

- the `dev` workflow checks passed
- the relevant local or live smoke checks passed on `dev`
- runtime behavior was verified through `worldfork`, not through assumptions about internal APIs
- failures in jobs, logs, or reports were inspected through the CLI
- no dev-only files are included in the `main` promotion

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

## Release And Publishing

`main` is the only branch that may publish installable packages, public release artifacts, or stable user-facing installation surfaces. `dev` is for validation and integration only.

Do not add publishing credentials or release jobs to `dev` workflows. If a release workflow is introduced later, it should run from `main` or tags created from `main`, and it should use GitHub Actions secrets with the minimum required permissions.
