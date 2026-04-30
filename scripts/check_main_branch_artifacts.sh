#!/usr/bin/env bash
# Guard main against development-only Docker/container files.
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

for path in "${blocked_paths[@]}"; do
  if [[ -e "$path" ]]; then
    echo "main-branch guard: dev-only path must not be present on main: $path" >&2
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
