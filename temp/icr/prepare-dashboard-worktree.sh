#!/usr/bin/env bash
# Prepare the dashboard toolchain inside a leaf worktree:
#  - link dashboard/node_modules to the main checkout (never reinstall per leaf)
#  - run `panda codegen` so the gitignored `styled-system` exists for ts/vitest
set -euo pipefail
LEAF_ROOT="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
MAIN=/home/firefox/projects/agents-remember
cd "$LEAF_ROOT/dashboard"
if [ ! -d node_modules ]; then
  ln -s "$MAIN/dashboard/node_modules" node_modules
fi
./node_modules/.bin/panda codegen >/dev/null
echo "dashboard prepared in $LEAF_ROOT/dashboard"
