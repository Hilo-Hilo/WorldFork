#!/usr/bin/env bash
# WorldFork test runner - unit, integration, e2e in CI order.
#
# Usage:
#   ./scripts/run_tests.sh             # full sweep (unit + integration + e2e)
#   ./scripts/run_tests.sh unit        # root regression + unit tests
#   ./scripts/run_tests.sh cli         # CLI package tests
#   ./scripts/run_tests.sh integration # just integration
#   ./scripts/run_tests.sh e2e         # just e2e
#
# Exit code is non-zero on first failing layer; downstream layers are not run.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PYTEST="${PYTEST:-.venv/bin/python -m pytest}"
LAYER="${1:-all}"

run_unit() {
  echo "==> unit + root regression"
  $PYTEST -c pyproject.toml backend/tests/*.py backend/tests/unit -n auto -q
}

run_cli() {
  echo "==> CLI package"
  (cd cli && uv run --extra dev python -m pytest -q)
}

run_integration() {
  echo "==> integration"
  # Note: integration tests boot the full ASGI app per test and are slow
  # serially. -n auto parallelises across cores. (Install pytest-timeout
  # to add --timeout=60 if you want a per-test deadline.)
  $PYTEST -c pyproject.toml backend/tests/integration -q -n auto
}

run_e2e() {
  echo "==> e2e"
  # E2E tests exercise the full app + real ledger I/O; keep serial so SQLite
  # in-memory state is isolated per test.
  $PYTEST -c pyproject.toml backend/tests/e2e -q -m "not requires_broker"
}

case "$LAYER" in
  unit)        run_unit ;;
  cli)         run_cli ;;
  integration) run_integration ;;
  e2e)         run_e2e ;;
  all)         run_unit && run_cli && run_integration && run_e2e ;;
  *)
    echo "Unknown layer: $LAYER (expected: unit | cli | integration | e2e | all)" >&2
    exit 2
    ;;
esac
