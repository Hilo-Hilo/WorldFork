# WorldFork CLI

`worldfork` is the command-line interface for a running WorldFork backend. It is designed for both humans and AI agents: compact output by default, JSON when needed, bounded job waits, watch streams, and direct access to reports.

## Install From The Monorepo

Run from the repository root:

```bash
python3.11 -m pip install -e ./cli
worldfork --help
```

## Backend Targeting

The CLI chooses a backend in this order:

1. `--base-url`
2. `WORLD_FORK_API_BASE`
3. `BACKEND_API_BASE`
4. `http://127.0.0.1:8003`

Example:

```bash
worldfork --base-url http://127.0.0.1:8003 status
```

## Core Commands

```bash
worldfork status
worldfork agent discover
worldfork setup
worldfork init --name "Atlas onboarding" --scenario-file examples/test-big-bang.md
worldfork watch big-bang <big-bang-id>
worldfork reports view <report-version-id>
worldfork smoke live
worldfork demo atlas
```

Use global options before the command:

```bash
worldfork --json status
worldfork --verbosity summary runs list
worldfork --fields id,status,created_at jobs list
```

## Documentation

See the root README and `docs/cli.md` for the full operator guide.
