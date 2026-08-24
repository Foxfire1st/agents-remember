#!/usr/bin/env sh
# Shared body for this repository's git hooks, in two tiers.
#
#     _gate.sh fast      pre-commit: check staged/index content, cheaply.
#     _gate.sh targeted  pre-push:   report the pushed refs and repeat the
#                                    deterministic non-test checks.
#     _gate.sh full      manual:     refuse and point at the Dagger-only gate.
#
# Enable once per clone:  ./setup-hooks.sh
# Prerequisite:           pip install -e "mcp[dev]"
#
# The hook tiers run deterministic non-test checks only. Acceptance is owned by the
# pinned Dagger graph exactly once at leaf closeout and once at master integration.
# Push, pull-request, tag, publish, and leaf-integration paths do not rerun it. Pull
# requests still own GitHub's deterministic non-test checks. In linked worktrees,
# use the primary worktree's virtual environment when necessary and put the current
# checkout's source first on PYTHONPATH.

set -u

tier="${1:-}"
case "$tier" in
  fast) label="pre-commit" ;;
  targeted) label="pre-push" ;;
  full) label="manual-full" ;;
  *)
    echo "[githooks] usage: _gate.sh <fast|targeted|full>" >&2
    exit 2
    ;;
esac

root="$(git rev-parse --show-toplevel)" || exit 1
cd "$root" || exit 1

# Primary worktree root = parent of the shared (common) git dir. For a linked
# worktree this resolves to the main clone; for the main clone it resolves to
# itself.
common_git="$(git rev-parse --git-common-dir 2>/dev/null)"
main_root=""
if [ -n "$common_git" ]; then
  main_root="$(CDPATH= cd -- "$common_git/.." 2>/dev/null && pwd)"
fi

if [ -x ".venv/bin/python" ]; then
  py=".venv/bin/python"
elif [ -n "$main_root" ] && [ -x "$main_root/.venv/bin/python" ]; then
  py="$main_root/.venv/bin/python"
else
  py="$(command -v python3 || command -v python)"
fi
if [ -z "$py" ]; then
  echo "[$label] no python found; install the dev env: pip install -e 'mcp[dev]'" >&2
  exit 1
fi

checkout_pythonpath="$root/mcp/src"
if [ -n "${PYTHONPATH:-}" ]; then
  checkout_pythonpath="$checkout_pythonpath:$PYTHONPATH"
fi
PYTHONPATH="$checkout_pythonpath"
export PYTHONPATH

STASH_MESSAGE="agents-remember $label gate: staged-content isolation"

# --- checks -----------------------------------------------------------------

# Scope is the complete index-known Python population used by the quality wrapper. Refuse
# an empty population.
over_tracked_python() {
  if [ -z "$(git ls-files -- '*.py')" ]; then
    echo "[$label] git tracks no Python files; refusing to certify an empty scope." >&2
    return 1
  fi
  git ls-files -z -- '*.py' | xargs -0 "$@"
}

generated_copy_checks() {
  generated_projection_check || return 1
  generated_check skills scripts/sync-skills.py || return 1
  generated_check runtime scripts/sync-runtime.py || return 1
  generated_check harness scripts/sync-harness.py || return 1
  return 0
}

generated_projection_check() {
  "$py" -m agents_remember.code_quality.scope_reporting \
    --project-root "$root" generated --name projection --script scripts/sync-projection-types.py || return 1
  echo "[$label] checking generated projection copies..."
  if "$py" scripts/sync-projection-types.py --check; then
    echo "[$label] result: generated-projection PASS"
    return 0
  fi
  echo "[$label] result: generated-projection FAIL" >&2
  return 1
}

generated_check() {
  generated_name=$1
  generated_script=$2
  "$py" -m agents_remember.code_quality.scope_reporting \
    --project-root "$root" generated --name "$generated_name" --script "$generated_script" || return 1
  echo "[$label] checking generated $generated_name copies..."
  if "$py" "$generated_script" --check; then
    echo "[$label] result: generated-$generated_name PASS"
    return 0
  fi
  echo "[$label] result: generated-$generated_name FAIL" >&2
  return 1
}

report_wrapper_tier() {
  "$py" -m agents_remember.code_quality.scope_reporting \
    --project-root "$root" hook-tier --tier "$tier"
}

report_fixed_step() {
  "$py" -m agents_remember.code_quality.scope_reporting \
    --project-root "$root" fixed-step --name "$1"
}

report_untracked_scope() {
  "$py" -m agents_remember.code_quality.scope_reporting \
    --project-root "$root" untracked
}

# The host frontend rail is intentionally non-test: codegen, lint, and typecheck.
# Playwright refuses outside the pinned Dagger graph. Direct targeted Vitest is a
# diagnostic-only developer loop, not part of this hook or acceptance evidence. A fresh
# checkout without node_modules fails with the install instruction instead of skipping the gate.
dashboard_checks() {
  if [ ! -f "dashboard/package.json" ]; then
    echo "[$label] dashboard/package.json missing; skipping the frontend rail." >&2
    return 0
  fi
  if [ ! -d "dashboard/node_modules" ]; then
    echo "[$label] dashboard/node_modules missing; run 'npm ci' in dashboard/ first." >&2
    return 1
  fi
  if ! command -v npm >/dev/null 2>&1; then
    echo "[$label] npm not found; cannot run the dashboard rail." >&2
    return 1
  fi
  # Panda's generated styled-system tree is checkout-local and ignored. Linked
  # worktrees may share node_modules with the primary checkout while correctly
  # having no generated tree of their own, so typecheck must generate its declared
  # input instead of failing every fresh worktree with TS2307 import errors.
  echo "[$label] npm run codegen (dashboard)..."
  if ! npm --prefix dashboard run codegen --silent; then
    echo "[$label] result: dashboard-codegen FAIL" >&2
    return 1
  fi
  echo "[$label] result: dashboard-codegen PASS"
  for step in lint typecheck; do
    echo "[$label] npm run $step (dashboard)..."
    if ! npm --prefix dashboard run "$step" --silent; then
      echo "[$label] result: dashboard-$step FAIL" >&2
      return 1
    fi
    echo "[$label] result: dashboard-$step PASS"
  done
  return 0
}

run_fast_checks() {
  report_wrapper_tier || return 1
  if [ "${AR_QUALITY_INVOCATION:-}" = "pre-commit-sequencer" ]; then
    report_untracked_scope || return 1
  fi
  generated_copy_checks || return 1
  # Ruff uses the project selection unchanged, including C901/PLR0911/PLR0912/PLR0915.
  # Do not add command-line ignores or a baseline.
  report_fixed_step ruff || return 1
  echo "[$label] ruff (lint)..."
  if over_tracked_python "$py" -m ruff check; then
    echo "[$label] result: ruff PASS"
  else
    echo "[$label] result: ruff FAIL" >&2
    return 1
  fi
  # Reject unformatted index-known Python content.
  report_fixed_step ruff-format || return 1
  echo "[$label] ruff format (--check)..."
  if over_tracked_python "$py" -m ruff format --check; then
    echo "[$label] result: ruff-format PASS"
  else
    echo "[$label] result: ruff-format FAIL" >&2
    return 1
  fi
  report_fixed_step pyright || return 1
  echo "[$label] Pyright (types)..."
  if over_tracked_python "$py" -m pyright --project . --pythonpath "$py"; then
    echo "[$label] result: pyright PASS"
  else
    echo "[$label] result: pyright FAIL" >&2
    return 1
  fi
  dashboard_checks || return 1
  echo "[$label] result: fast-tier PASS; acceptance tests run only in Dagger."
  return 0
}

run_targeted_checks() {
  run_fast_checks || return 1
  echo "[$label] result: targeted hook PASS; leaf closeout owns targeted Dagger acceptance."
  return 0
}

run_full_checks() {
  echo "[$label] acceptance is Dagger-only; refusing host full-suite execution." >&2
  echo "[$label] run the pinned 'dagger call quality ... --mode=full' graph." >&2
  return 2
}

# --- staged-content isolation ------------------------------------------------
#
# The fast tier certifies the index. Park unstaged and untracked content with
# `git stash push --keep-index`, then restore the exact pre-gate state on every exit.

stash_commit=""

isolation_needed() {
  # Nothing to park when the working tree already equals the index.
  git diff --quiet || return 0
  [ -n "$(git ls-files --others --exclude-standard)" ]
}

# `git stash` during a merge, rebase, or cherry-pick moves the conflict
# resolution out of the tree git is about to commit from and discards MERGE_HEAD
# with it. There is no safe isolation in that state, so certify the tree as-is.
sequencer_in_progress() {
  git_dir="$(git rev-parse --git-dir)"
  [ -e "$git_dir/MERGE_HEAD" ] ||
    [ -e "$git_dir/CHERRY_PICK_HEAD" ] ||
    [ -e "$git_dir/REVERT_HEAD" ] ||
    [ -d "$git_dir/rebase-merge" ] ||
    [ -d "$git_dir/rebase-apply" ]
}

isolate_staged_content() {
  git stash push --quiet --keep-index --include-untracked --message "$STASH_MESSAGE" || {
    echo "[$label] could not park unstaged content; nothing was changed." >&2
    return 1
  }
  stash_commit="$(git rev-parse --verify --quiet refs/stash)"
  if [ -z "$stash_commit" ]; then
    echo "[$label] git stash reported success but created no stash entry." >&2
    return 1
  fi
  return 0
}

restore_worktree() {
  [ -n "$stash_commit" ] || return 0
  top="$(git rev-parse --verify --quiet refs/stash)"
  if [ "$top" != "$stash_commit" ]; then
    echo "[$label] refusing to restore: stash top moved during the gate." >&2
    echo "[$label] your work is safe in stash $stash_commit; recover with:" >&2
    echo "[$label]   git stash apply --index $stash_commit" >&2
    stash_commit=""
    return 1
  fi
  # The stash holds staged, unstaged, and untracked content; the working tree
  # holds only the staged subset. Resetting first makes the pop a conflict-free
  # restore of the exact pre-gate state, index included.
  if ! git reset --quiet --hard; then
    echo "[$label] RESTORE ABORTED before it began. Your work is intact in stash $stash_commit." >&2
    echo "[$label] recover with:  git reset --hard && git stash pop --index" >&2
    stash_commit=""
    return 1
  fi
  if ! git stash pop --index --quiet; then
    echo "[$label] RESTORE FAILED. Your work is intact in stash $stash_commit." >&2
    echo "[$label] recover with:  git stash pop --index" >&2
    stash_commit=""
    return 1
  fi
  stash_commit=""
  return 0
}

on_exit() {
  status=$?
  trap - EXIT
  restore_worktree || status=1
  exit "$status"
}

on_signal() {
  signal_status=$1
  trap - EXIT INT TERM HUP
  restore_worktree
  exit "$signal_status"
}

# --- run ---------------------------------------------------------------------

if [ "$tier" = "targeted" ]; then
  if run_targeted_checks; then
    echo "[$label] result: targeted-tier PASS"
    exit 0
  fi
  echo "[$label] result: targeted-tier FAIL" >&2
  exit 1
fi

if [ "$tier" = "full" ]; then
  if run_full_checks; then
    echo "[$label] result: full-tier PASS"
    exit 0
  fi
  echo "[$label] result: full-tier FAIL" >&2
  exit 1
fi

if sequencer_in_progress; then
  AR_QUALITY_INVOCATION="pre-commit-sequencer"
  export AR_QUALITY_INVOCATION
  echo "[$label] merge/rebase in progress; gating the working tree (stash is unsafe here)."
  if run_fast_checks; then
    exit 0
  fi
  echo "[$label] result: fast-tier FAIL" >&2
  exit 1
fi

if ! isolation_needed; then
  AR_QUALITY_INVOCATION="pre-commit-staged"
  export AR_QUALITY_INVOCATION
  if run_fast_checks; then
    exit 0
  fi
  echo "[$label] result: fast-tier FAIL" >&2
  exit 1
fi

isolate_staged_content || exit 1
AR_QUALITY_INVOCATION="pre-commit-staged"
export AR_QUALITY_INVOCATION
trap 'on_exit' EXIT
trap 'on_signal 130' INT
trap 'on_signal 143' TERM
trap 'on_signal 129' HUP

if ! run_fast_checks; then
  echo "[$label] result: fast-tier FAIL" >&2
  false
fi
