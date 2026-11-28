#!/usr/bin/env sh
# Shared body for this repository's git hooks, in two tiers.
#
#     _gate.sh fast      pre-commit: check staged/index content, cheaply.
#     _gate.sh targeted  pre-push:   report the pushed refs and repeat the
#                                    deterministic non-test checks.
#     _gate.sh full      manual:     explicit full workflow; hooks do not launch it.
#
# Enable once per clone:  ./setup-hooks.sh
# Prerequisite:           create mcp/.venv and install "mcp[dev]" into it
#
# The hook tiers run deterministic non-test checks only. Closeout and integration
# publish authorized Git code/memory/ledger transactions and do not launch automatic
# full quality, test, memory-quality, curator-certification, or review operations.
# Explicit developer-requested workflows may use the repository's configured executor.
# Push, pull-request, tag, publish, and leaf-integration paths do not rerun it. Pull
# requests still own GitHub's deterministic non-test checks. In linked worktrees,
# use the primary worktree's MCP development environment when necessary and put the
# current checkout's source first on PYTHONPATH.

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
runtime_contract="$root/scripts/python-runtime-contract.env"
if [ ! -f "$runtime_contract" ]; then
  echo "[$label] canonical Python runtime contract is missing: $runtime_contract" >&2
  exit 1
fi
# shellcheck source=../scripts/python-runtime-contract.env
. "$runtime_contract"
PYTHONPATH="$root/mcp/test_support:$root/mcp/src${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONPATH

# Primary worktree root = parent of the shared (common) git dir. For a linked
# worktree this resolves to the main clone; for the main clone it resolves to
# itself.
common_git="$(git rev-parse --git-common-dir 2>/dev/null)"
main_root=""
if [ -n "$common_git" ]; then
  main_root="$(CDPATH= cd -- "$common_git/.." 2>/dev/null && pwd)"
fi

# Scope reporting imports both MCP runtime and test-support modules. A repository-root
# venv or system Python can see checkout source through PYTHONPATH while still lacking
# the package dependencies, so only a complete MCP development environment is valid.
dev_python_ready() {
  if [ "$(uname -s)" = "Linux" ]; then
    "$1" "$root/scripts/check-python-runtime.py" \
      --expected-version "$AR_PYTHON_MINOR" \
      --require-linux-pidfd >/dev/null 2>&1 || return 1
  else
    "$1" "$root/scripts/check-python-runtime.py" \
      --expected-version "$AR_PYTHON_MINOR" >/dev/null 2>&1 || return 1
  fi
  "$1" -c \
    'import pyrefly, ruff; import agents_remember_test_support.code_quality.scope_reporting' \
    >/dev/null 2>&1
}

local_py="$root/mcp/.venv/bin/python"
shared_py="$main_root/mcp/.venv/bin/python"
if [ -x "$local_py" ] && dev_python_ready "$local_py"; then
  py="$local_py"
elif [ -n "$main_root" ] && [ -x "$shared_py" ] && dev_python_ready "$shared_py"; then
  py="$shared_py"
else
  echo "[$label] complete MCP dev environment not found at mcp/.venv." >&2
  echo "[$label] Linux/WSL repair: scripts/bootstrap-mcp-venv.sh --replace" >&2
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

gate_tmp=""
scope_dir=""
bg_rail_pids=""

make_gate_tmp() {
  if command -v mktemp >/dev/null 2>&1; then
    mktemp -d "${TMPDIR:-/tmp}/ar-gate.XXXXXX"
  elif [ -x /usr/bin/mktemp ]; then
    /usr/bin/mktemp -d "${TMPDIR:-/tmp}/ar-gate.XXXXXX"
  elif [ -x /bin/mktemp ]; then
    /bin/mktemp -d "${TMPDIR:-/tmp}/ar-gate.XXXXXX"
  else
    fallback_dir="${TMPDIR:-/tmp}/ar-gate.$$"
    if [ -x /bin/mkdir ]; then
      /bin/mkdir -p "$fallback_dir" && printf '%s\n' "$fallback_dir"
    else
      mkdir -p "$fallback_dir" && printf '%s\n' "$fallback_dir"
    fi
  fi
}

remove_gate_dir() {
  target_dir=$1
  [ -n "$target_dir" ] && [ -d "$target_dir" ] || return 0
  if command -v rm >/dev/null 2>&1; then
    rm -rf -- "$target_dir"
  elif [ -x /bin/rm ]; then
    /bin/rm -rf -- "$target_dir"
  elif [ -x /usr/bin/rm ]; then
    /usr/bin/rm -rf -- "$target_dir"
  fi
}

emit_file() {
  target_file=$1
  [ -f "$target_file" ] || return 0
  if command -v cat >/dev/null 2>&1; then
    cat "$target_file"
  elif [ -x /bin/cat ]; then
    /bin/cat "$target_file"
  elif [ -x /usr/bin/cat ]; then
    /usr/bin/cat "$target_file"
  else
    while IFS= read -r line; do
      printf '%s\n' "$line"
    done < "$target_file"
    if [ -n "$line" ]; then
      printf '%s' "$line"
    fi
  fi
}

stop_pid_tree() {
  pid=$1
  if command -v pkill >/dev/null 2>&1; then
    pkill -P "$pid" 2>/dev/null || true
  elif [ -x /usr/bin/pkill ]; then
    /usr/bin/pkill -P "$pid" 2>/dev/null || true
  fi
  kill "$pid" 2>/dev/null || true
}

cleanup_gate_runtime() {
  if [ -n "$bg_rail_pids" ]; then
    for pid in $bg_rail_pids; do
      stop_pid_tree "$pid"
    done
    # shellcheck disable=SC2086
    wait $bg_rail_pids 2>/dev/null || true
    bg_rail_pids=""
  fi
  remove_gate_dir "$gate_tmp"
  gate_tmp=""
  scope_dir=""
}

# Scope is the complete index-known Python population used by the quality wrapper. Refuse
# an empty population.
# L29-R3: raise GNU xargs's 128 KiB default batch size to 256 KiB on Linux so tracked
# Python files reach Ruff and Pyrefly in one invocation while retaining normal splitting
# above 256 KiB and native defaults on BSD/macOS.
over_tracked_python() {
  if [ -z "$(git ls-files -- '*.py')" ]; then
    echo "[$label] git tracks no Python files; refusing to certify an empty scope." >&2
    return 1
  fi
  if [ "$(uname -s)" = "Linux" ]; then
    case "$(xargs --version 2>/dev/null)" in
      *GNU*)
        git ls-files -z -- '*.py' | xargs -0 -s 262144 "$@"
        return $?
        ;;
    esac
  fi
  git ls-files -z -- '*.py' | xargs -0 "$@"
}

# L29-R3: emit every hook-tier scope line in one Python invocation into a per-run
# temp directory, then print each line in its original position before its check.
prepare_scope_batch() {
  if [ -z "$gate_tmp" ]; then
    gate_tmp="$(make_gate_tmp)" || {
      echo "[$label] could not create temporary gate directory." >&2
      return 1
    }
  fi
  scope_dir="$gate_tmp/scope"
  "$py" -m agents_remember_test_support.code_quality.scope_reporting \
    --project-root "$root" hook-batch --tier "$tier" --output-dir "$scope_dir"
}

emit_scope_line() {
  emit_file "$scope_dir/$1"
}

generated_copy_checks() {
  generated_projection_check || return 1
  generated_check skills scripts/renew-instructions.py || return 1
  generated_check runtime scripts/sync-runtime.py || return 1
  return 0
}

generated_projection_check() {
  emit_scope_line generated-projection
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
  emit_scope_line "generated-$generated_name"
  echo "[$label] checking generated $generated_name copies..."
  if "$py" "$generated_script" --check; then
    echo "[$label] result: generated-$generated_name PASS"
    return 0
  fi
  echo "[$label] result: generated-$generated_name FAIL" >&2
  return 1
}

# Both tiers look for the dashboard prerequisites before running the longer checks.
# A checkout with no dashboard package skips the frontend rail.
dashboard_prerequisites() {
  [ -f "dashboard/package.json" ] || return 0
  if [ ! -d "dashboard/node_modules" ]; then
    echo "[$label] dashboard/node_modules missing; run 'npm ci' in dashboard/ first." >&2
    return 1
  fi
  if ! command -v npm >/dev/null 2>&1; then
    echo "[$label] npm not found; cannot run the dashboard rail." >&2
    return 1
  fi
}

# The host frontend rail runs codegen, lint, and typecheck. Playwright is outside
# this hook's scope. Direct targeted Vitest is a developer diagnostic.
dashboard_checks() {
  if [ ! -f "dashboard/package.json" ]; then
    echo "[$label] dashboard/package.json missing; skipping the frontend rail." >&2
    return 0
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
  # oxlint runs cold in ~0.65s across the dashboard and exposes no --cache flag.
  echo "[$label] npm run lint (dashboard)..."
  if ! npm --prefix dashboard run lint --silent; then
    echo "[$label] result: dashboard-lint FAIL" >&2
    return 1
  fi
  echo "[$label] result: dashboard-lint PASS"
  echo "[$label] npm run typecheck (dashboard)..."
  if ! npm --prefix dashboard run typecheck --silent; then
    echo "[$label] result: dashboard-typecheck FAIL" >&2
    return 1
  fi
  echo "[$label] result: dashboard-typecheck PASS"
  return 0
}

python_rail_checks() {
  # Ruff uses the project selection unchanged, including C901/PLR0911/PLR0912/PLR0915.
  # Do not add command-line ignores or a baseline.
  emit_scope_line ruff
  echo "[$label] ruff (lint)..."
  if over_tracked_python "$py" -m ruff check; then
    echo "[$label] result: ruff PASS"
  else
    echo "[$label] result: ruff FAIL" >&2
    return 1
  fi
  # Reject unformatted index-known Python content.
  emit_scope_line ruff-format
  echo "[$label] ruff format (--check)..."
  if over_tracked_python "$py" -m ruff format --check; then
    echo "[$label] result: ruff-format PASS"
  else
    echo "[$label] result: ruff-format FAIL" >&2
    return 1
  fi
  emit_scope_line pyrefly
  echo "[$label] Pyrefly (types)..."
  if over_tracked_python "$py" -m pyrefly check --min-severity error --force-exclude --python-interpreter-path "$py"; then
    echo "[$label] result: pyrefly PASS"
  else
    echo "[$label] result: pyrefly FAIL" >&2
    return 1
  fi
  emit_scope_line evidence-lifecycle
  echo "[$label] evidence lifecycle catalog..."
  if "$py" -m agents_remember_test_support.testing.evidence_lifecycle --project-root .; then
    echo "[$label] result: evidence-lifecycle PASS"
  else
    echo "[$label] result: evidence-lifecycle FAIL" >&2
    return 1
  fi
  return 0
}

# L29-R3: run Python and Dashboard rails concurrently, buffering each rail's stdout
# and stderr and replaying them in fixed order (Python first, then Dashboard).
run_concurrent_rails() {
  py_out="$gate_tmp/python.out"
  py_err="$gate_tmp/python.err"
  dash_out="$gate_tmp/dashboard.out"
  dash_err="$gate_tmp/dashboard.err"

  python_rail_checks >"$py_out" 2>"$py_err" &
  python_pid=$!
  dashboard_checks >"$dash_out" 2>"$dash_err" &
  dashboard_pid=$!
  bg_rail_pids="$python_pid $dashboard_pid"

  python_rc=0
  wait "$python_pid" || python_rc=$?
  dashboard_rc=0
  wait "$dashboard_pid" || dashboard_rc=$?
  bg_rail_pids=""

  emit_file "$py_out"
  emit_file "$py_err" >&2
  emit_file "$dash_out"
  emit_file "$dash_err" >&2

  if [ "$python_rc" -ne 0 ] || [ "$dashboard_rc" -ne 0 ]; then
    return 1
  fi
  return 0
}

run_fast_checks() {
  dashboard_prerequisites || return 1
  prepare_scope_batch || return 1
  emit_scope_line hook-tier
  if [ "${AR_QUALITY_INVOCATION:-}" = "pre-commit-sequencer" ]; then
    emit_scope_line untracked
  fi
  generated_copy_checks || return 1
  run_concurrent_rails || return 1
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
  cleanup_gate_runtime
  restore_worktree || status=1
  exit "$status"
}

on_signal() {
  signal_status=$1
  trap - EXIT INT TERM HUP
  cleanup_gate_runtime
  restore_worktree
  exit "$signal_status"
}

trap 'on_exit' EXIT
trap 'on_signal 130' INT
trap 'on_signal 143' TERM
trap 'on_signal 129' HUP

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

if ! run_fast_checks; then
  echo "[$label] result: fast-tier FAIL" >&2
  false
fi
