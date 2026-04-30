---
name: worldfork-setup
description: Use when helping a user install, configure, verify, and onboard into WorldFork for the first time; remove this temporary setup skill after onboarding is complete.
---

# WorldFork Setup

This is a temporary bootstrap skill for getting WorldFork installed, verified, and explained to a new operator. After setup and onboarding are complete, tell the user this skill can be removed. Keep the regular `worldfork` operator skill installed if the user will keep operating the project.

## Setup Rules

- Do not hardcode backend URLs. Use `WORLD_FORK_API_BASE`, `BACKEND_API_BASE`, or the `worldfork --base-url` option when the user targets a non-default API.
- Prefer the documented CLI and Make targets. Do not bypass the CLI with Python module entrypoints unless the user explicitly asks for low-level debugging.
- Before using real API credits, confirm the model route is `google/gemini-3.1-flash-lite-preview` unless the user explicitly authorizes another model.
- If the repository is already checked out, use it. Do not clone another copy unless the current checkout is missing or unusable.

## Install WorldFork

Use these steps for a normal local setup:

```bash
git clone https://github.com/Hilo-Hilo/WorldFork.git
cd WorldFork
python3.11 -m pip install -e ./cli
worldfork --help
cp .env.example .env
```

Ask the user for their OpenRouter API key if it is not already configured, then set `OPENROUTER_API_KEY` in `.env`. Tell the user to keep the default model, `google/gemini-3.1-flash-lite-preview`, for cheap onboarding and validation runs unless they explicitly want to change providers.

Start and prepare the stack:

```bash
make build
make up
make migrate
make seed
```

If this is a reused local checkout and stale queue/cache state might affect the first run, ask before clearing local Redis. Only run this against disposable local Docker Compose Redis:

```bash
docker compose exec -T redis redis-cli FLUSHALL
```

Verify readiness:

```bash
worldfork status
worldfork query GET /readyz --no-api-prefix
worldfork agent discover
```

If setup succeeds, install the regular operator skill. Read that skill if you need the ongoing operator workflow:

```bash
npx skills add Hilo-Hilo/WorldFork/skills/worldfork --all
```

## Onboard The User

After the stack is healthy, run discovery to see what commands are available and what they do. Then give the user a short onboarding explanation of the core ideas:

- Big Bang: the initial scenario seed and workspace that defines the world.
- Run: a tracked execution of work against a Big Bang or multiverse.
- Multiverse: one timeline branch with its own state, ticks, events, and reports.
- Branching: creating alternate timelines from a decision point, agent review, or human intervention.
- Human intervention: an operator action that pauses, changes, branches, or reviews the simulation while keeping an audit trail.
- God agents: governance agents that inspect the world state, critique a timeline, and may propose or create additional branches.
- Review agent: the summarization/review layer that compares outcomes and produces structured reports across multiverses.
- Reports: versioned, structured records of what happened, with Markdown/PDF artifacts rendered from the stored report content.

Keep the onboarding practical. Show the user the commands they will actually use:

```bash
worldfork init --name "My first world" --scenario-file examples/test-big-bang.md
worldfork watch big-bang <big-bang-id>
worldfork watch multiverse <multiverse-id>
worldfork reports list <big-bang-id>
worldfork reports versions <report-id>
worldfork reports view <report-version-id>
worldfork reports render <report-version-id> --format pdf
```

## Offer The Atlas Demo

Ask the user whether they want to run the Atlas demo simulation before starting it. Briefly describe Atlas as a larger onboarding world: an Atlas Resilience Crisis scenario that creates a Big Bang, runs root and branch timelines, allows God-agent branching, generates per-multiverse reports, and finishes with a cross-multiverse outcome review.

If the user agrees, run:

```bash
worldfork demo atlas
```

Then show how to inspect the result:

```bash
worldfork runs list
worldfork watch big-bang <big-bang-id> --once
worldfork reports list <big-bang-id>
worldfork reports view <report-version-id>
```

## Remove This Setup Skill

When setup, verification, onboarding, and any requested Atlas demo are done, tell the user this bootstrap skill is no longer needed. Remove it with:

```bash
npx skills remove worldfork-setup -y
```

Do not remove the regular `worldfork` skill unless the user asks to uninstall WorldFork operator guidance too.
