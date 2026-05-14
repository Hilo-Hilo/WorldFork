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

This command uses real API credits. It should use the configured audited model split: OpenRouter `deepseek/deepseek-v4-flash` for cohort, hero, action, event-summary, predicate, and single-report work, and OpenAI Codex `gpt-5.4` for initializer, God-review, endpoint-ledger, report fallback, and final multiverse report synthesis.

## Redis Cleanup

If a local test run leaves queue state behind before a live smoke, clear Redis:

```bash
docker compose exec -T redis redis-cli FLUSHALL
```

Do not run this against shared or production Redis instances.
