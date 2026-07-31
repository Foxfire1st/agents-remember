#!/usr/bin/env sh
# Shared body for this repository's git hooks, in two tiers.
#
#     _gate.sh fast    pre-commit: certify exactly the staged content, cheaply.
#     _gate.sh full    pre-push:   certify the working tree with the full wrapper.
#
# Enable once per clone:  ./setup-hooks.sh
# Prerequisite:           pip install -e "mcp[dev]"
#
# The fast tier is cheap on purpose. A gate nobody can afford to run is a gate
# nobody runs, and `--no-verify` disables every check, not just the slow one.
#
# Works from any branch and any git worktree. When the current checkout has no
# local .venv (the common worktree case), fall back to the primary worktree's
# .venv so the dev tools are available without a per-worktree install. Both
# tiers put the current checkout's source first on PYTHONPATH, so the gate
# always measures *this* checkout's code regardless of which python runs it.

set -u

tier="${1:-}"
case "$tier" in
  fast) label="pre-commit" ;;
  full) label="pre-push" ;;
  *)
    echo "[githooks] usage: _gate.sh <fast|full>" >&2
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

# Scope is read off the index, exactly as `code_quality/check.py::derive_scope` does.
# This tier used to name "mcp/src/agents_remember" and "mcp/tests" by hand, which was
# narrower than the gate it fronts: a broken scripts/sync-skills.py passed pre-commit and
# was then rejected on push. An empty list is fatal rather than a silent pass -- certifying
# nothing is how a gate stops being one.
over_tracked_python() {
  if [ -z "$(git ls-files -- '*.py')" ]; then
    echo "[$label] git tracks no Python files; refusing to certify an empty scope." >&2
    return 1
  fi
  git ls-files -z -- '*.py' | xargs -0 "$@"
}

generated_copy_checks() {
  echo "[$label] checking generated skill copies..."
  "$py" scripts/sync-skills.py --check || return 1
  echo "[$label] checking generated runtime asset copies..."
  "$py" scripts/sync-runtime.py --check || return 1
  echo "[$label] checking generated harness configuration trees..."
  "$py" scripts/sync-harness.py --check || return 1
  return 0
}

run_fast_checks() {
  generated_copy_checks || return 1
  # No `--extend-ignore` and no `--select`: ruff lints exactly what pyproject selects,
  # C901/PLR0911/PLR0912/PLR0915 included. This step used to route those four codes away
  # for a `complexity-baseline` step to hold against a shrink-only list; that list is gone
  # along with the debt it recorded, and re-adding a flag here would silently un-enforce
  # four rules in the tier developers actually feel.
  echo "[$label] ruff (lint)..."
  over_tracked_python "$py" -m ruff check || return 1
  # Unformatted content is exactly what a staged-content check should catch before it
  # lands, and the whole-tree check runs in well under a second.
  echo "[$label] ruff format (--check)..."
  over_tracked_python "$py" -m ruff format --check || return 1
  echo "[$label] Pyright (types)..."
  over_tracked_python "$py" -m pyright --project . --pythonpath "$py" || return 1
  echo "[$label] fast tier passed; the full suite (pytest + CRAP) runs on push."
  return 0
}

# The full tier carries the changed-lines coverage floor, which needs to know what
# this branch was cut from. It resolves that itself -- AR_GATE_DIFF_BASE, then the
# pull request base, the configured upstream, then the default branch -- and prints
# the base it chose. Export AR_GATE_DIFF_BASE before pushing from a leaf branch cut
# from a series branch: git cannot infer that fork point, and without it the floor
# compares against the default branch and asks you to cover the series' lines too.
run_full_checks() {
  generated_copy_checks || return 1
  echo "[$label] running quality wrapper (ruff + format + Pyright + pytest + CRAP + diff coverage)..."
  "$py" -m agents_remember.code_quality.check || return 1
  return 0
}

# --- staged-content isolation ------------------------------------------------
#
# The fast tier must certify what is about to be committed, which is the index,
# not the working tree. `git stash push --keep-index` parks unstaged and
# untracked content so the working tree becomes byte-identical to the index for
# the duration of the checks. Everything below exists to guarantee that content
# comes back: a hook that loses uncommitted work is far worse than a slow hook.

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

if [ "$tier" = "full" ]; then
  run_full_checks
  exit $?
fi

if sequencer_in_progress; then
  echo "[$label] merge/rebase in progress; gating the working tree (stash is unsafe here)."
  run_fast_checks
  exit $?
fi

if ! isolation_needed; then
  run_fast_checks
  exit $?
fi

isolate_staged_content || exit 1
trap 'on_exit' EXIT
trap 'on_signal 130' INT
trap 'on_signal 143' TERM
trap 'on_signal 129' HUP

run_fast_checks
