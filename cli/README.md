# WorldFork CLI

Standalone command-line client for a running WorldFork backend.

## Local Development

From the repo root:

```bash
python3.11 -m pip install -e ./cli
worldfork --help
```

Core operator commands:

```bash
worldfork status
worldfork agent discover
worldfork smoke live
worldfork demo atlas
```

The CLI selects the backend from `WORLD_FORK_API_BASE`, `BACKEND_API_BASE`, or
`--base-url`.
