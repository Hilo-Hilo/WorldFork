#!/usr/bin/env bash
# Guard main against development-only Docker/container files and local run artifacts.
set -euo pipefail

failed=0

blocked_paths=(
  ".devcontainer"
  "docker-compose.dev.yml"
  "docker-compose.local.yml"
  "infra/docker/api.dev.Dockerfile"
  "infra/docker/worker.dev.Dockerfile"
  "infra/docker/dev"
)

blocked_tracked_paths=(
  "agent-testing"
  "artifacts"
  "data"
  "logs"
  "runs"
  "test-results"
)

blocked_tracked_patterns=(
  '(^|/)accuracy-overnight(/|$)'
  '(^|/)full-runtime-accuracy(/|$)'
  '(^|/)whitepaper-[0-9][^/]*(/|$)'
  '(^|/)(run-results|simulation-results|logbook|logbooks)(/|$)'
  '(^|/)docker-(stats|events|compose-ps|system-df)[^/]*\.(txt|log|jsonl|json)$'
  '(^|/)host-disk-[^/]*\.txt$'
  '\.(pdf|log|sqlite|db|duckdb|parquet|tsbuildinfo)$'
)

for path in "${blocked_paths[@]}"; do
  if [[ -e "$path" ]]; then
    echo "main-branch guard: dev-only path must not be present on main: $path" >&2
    failed=1
  fi
done

for path in "${blocked_tracked_paths[@]}"; do
  if git ls-files -- "$path" | grep -q .; then
    echo "main-branch guard: local run artifacts must not be tracked on main: $path" >&2
    failed=1
  fi
done

tracked_files="$(git ls-files)"
for pattern in "${blocked_tracked_patterns[@]}"; do
  if matches="$(printf '%s\n' "$tracked_files" | grep -E "$pattern" || true)" && [[ -n "$matches" ]]; then
    echo "main-branch guard: generated artifact paths must not be tracked on main:" >&2
    printf '%s\n' "$matches" >&2
    failed=1
  fi
done

if grep -R --include='*.Dockerfile' -nE '\.\[dev\]' infra/docker >/tmp/worldfork-dev-extra-grep.txt 2>/dev/null; then
  echo "main-branch guard: runtime Dockerfiles must not install Python dev extras:" >&2
  cat /tmp/worldfork-dev-extra-grep.txt >&2
  failed=1
fi

if grep -nE 'target:[[:space:]]*dev' docker-compose.yml >/tmp/worldfork-compose-dev-target-grep.txt 2>/dev/null; then
  echo "main-branch guard: docker-compose.yml must not target a dev image:" >&2
  cat /tmp/worldfork-compose-dev-target-grep.txt >&2
  failed=1
fi

exit "$failed"
