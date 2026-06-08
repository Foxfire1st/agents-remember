#!/usr/bin/env sh
# One-time, per-clone setup: enable this repo's Git hooks.
#
# Git does not auto-enable committed hooks on clone (by design), so every clone
# has to opt in once. Run this after cloning:
#
#     ./setup-hooks.sh
#
# It points git at .githooks/. The pre-commit hook checks generated package and
# harness copies, and the pre-push hook runs the project quality wrapper (ruff +
# Pyright + pytest + CRAP, enforcing). Bypass a single hook run intentionally
# with `git commit --no-verify` or `git push --no-verify`.
#
# Note: this is local fast feedback only. The non-bypassable backstop is the
# GitHub Actions "Quality checks" workflow, which runs on every push/PR to main
# regardless of local setup.

set -e

root="$(git rev-parse --show-toplevel)"
cd "$root"

git config core.hooksPath .githooks
echo "[setup-hooks] core.hooksPath -> .githooks (pre-commit and pre-push gates enabled for this clone)"

if [ ! -x ".venv/bin/python" ] && ! command -v python3 >/dev/null 2>&1; then
  echo "[setup-hooks] tip: install the dev env so the hook can run: pip install -e \"mcp[dev]\"" >&2
fi
