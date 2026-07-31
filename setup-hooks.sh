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
#                 checks, ruff, `ruff format --check`, and Pyright. About 20
#                 seconds.
#   pre-push   -> full tier: certifies the working tree with the whole quality
#                 wrapper (ruff + format + Pyright + pytest + mandatory CRAP).
#                 Its Radon steps are reports and cannot fail it.
#
# It also points local `git blame` at .git-blame-ignore-revs, so the whole-tree
# reformat does not become the blame answer for a fifth of the repository.
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

# GitHub and GitLab honour .git-blame-ignore-revs on their own; local `git blame`
# needs to be told. Without this, `git blame` answers "the 2026-07-31 reformat"
# for the 1,882 lines that commit moved.
git config blame.ignoreRevsFile .git-blame-ignore-revs
echo "[setup-hooks] blame.ignoreRevsFile -> .git-blame-ignore-revs (mechanical reformats skipped by git blame)"

if [ ! -x ".venv/bin/python" ] && ! command -v python3 >/dev/null 2>&1; then
  echo "[setup-hooks] tip: install the dev env so the hook can run: pip install -e \"mcp[dev]\"" >&2
fi
