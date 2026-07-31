#!/usr/bin/env sh
# One-time, per-clone setup: enable this repo's Git hooks.
#
# Git does not auto-enable committed hooks on clone (by design), so every clone
# has to opt in once. Run this after cloning:
#
#     ./setup-hooks.sh
#
# It points git at .githooks/, where both hooks share .githooks/_gate.sh in two
# tiers:
#
#   pre-commit -> fast tier: certifies the STAGED content with the generated-copy
#                 checks, ruff, and Pyright. About 20 seconds.
#   pre-push   -> full tier: certifies the working tree with the whole quality
#                 wrapper (ruff + Pyright + pytest + mandatory CRAP).
#
# Bypass a single hook run intentionally with `git commit --no-verify` or
# `git push --no-verify`, remembering that the flag disables every check rather
# than the one that annoyed you.
#
# Note: this is local fast feedback only. The non-bypassable backstop is the
# GitHub Actions "Quality checks" workflow, which runs on every branch push and
# every pull request and is required by the branch ruleset on main, regardless of
# local setup.

set -e

root="$(git rev-parse --show-toplevel)"
cd "$root"

git config core.hooksPath .githooks
echo "[setup-hooks] core.hooksPath -> .githooks (fast pre-commit and full pre-push gates enabled for this clone)"

if [ ! -x ".venv/bin/python" ] && ! command -v python3 >/dev/null 2>&1; then
  echo "[setup-hooks] tip: install the dev env so the hook can run: pip install -e \"mcp[dev]\"" >&2
fi
