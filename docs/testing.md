# Testing

WorldFork uses focused local commands plus one optional live smoke.

## Maintained Test Sweep

```bash
./scripts/run_tests.sh all
```

That runs:

1. root regression tests and backend unit tests
2. CLI package tests
3. integration tests
4. e2e tests that do not require live providers

Run a focused layer:

```bash
./scripts/run_tests.sh unit
./scripts/run_tests.sh cli
./scripts/run_tests.sh integration
./scripts/run_tests.sh e2e
```

## Static Checks

```bash
make lint
docker compose config --quiet
```

## Live Smoke

```bash
worldfork smoke live
```

This command uses real API credits. It should use the configured audited model split: OpenRouter `deepseek/deepseek-v4-flash` is a supported fast/cheap route for cohort, hero, action, and event-summary work, while initializer, God-review, endpoint-ledger, and report work should use a strong governance/report model route such as OpenAI Codex `gpt-5.4` or OpenRouter-hosted Kimi/Claude.

## Redis Cleanup

If a local test run leaves queue state behind before a live smoke, clear Redis:

```bash
docker compose exec -T redis redis-cli FLUSHALL
```

Do not run this against shared or production Redis instances.
