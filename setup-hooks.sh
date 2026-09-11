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
#   pre-push   -> targeted tier: records Git's ref-update stream, then repeats the
#                 deterministic non-test checks over current-checkout bytes. It does
#                 not stage, run acceptance, or claim to certify the pushed tree.
#
# It also points local `git blame` at .git-blame-ignore-revs, so the whole-tree
# reformat does not become the blame answer for a fifth of the repository.
#
# Bypass a single hook run intentionally with `git commit --no-verify` or
# `git push --no-verify`, remembering that the flag disables every check rather
# than the one that annoyed you.
#
# Note: this is local fast feedback only. GitHub's non-bypassable deterministic
# backstop runs on pull requests, not ordinary pushes. Acceptance is separate and
# lifecycle-owned: targeted once at leaf closeout, full once at master integration.

set -e

root="$(git rev-parse --show-toplevel)"
cd "$root"

git config core.hooksPath .githooks
echo "[setup-hooks] core.hooksPath -> .githooks (fast pre-commit and targeted non-test pre-push checks enabled)"

# GitHub and GitLab honour .git-blame-ignore-revs on their own; local `git blame`
# needs to be told. Without this, `git blame` answers "the 2026-07-31 reformat"
# for the 1,882 lines that commit moved.
git config blame.ignoreRevsFile .git-blame-ignore-revs
echo "[setup-hooks] blame.ignoreRevsFile -> .git-blame-ignore-revs (mechanical reformats skipped by git blame)"

if [ ! -x "mcp/.venv/bin/python" ]; then
  echo "[setup-hooks] tip: create the canonical Linux/WSL dev env with scripts/bootstrap-mcp-venv.sh" >&2
fi
