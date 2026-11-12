#!/usr/bin/env bash
# Run a command on one allowed core shared with CPU burners; always reclaim them.
set -euo pipefail

if (( $# == 0 )); then
  echo "Usage: AR_LOAD_RUNS=3 $0 command [arguments...] (runs: 1 or 3)" >&2
  exit 2
fi
load_runs=${AR_LOAD_RUNS:-3}
load_burners=10
if [[ $load_runs != 1 && $load_runs != 3 ]]; then
  echo "AR_LOAD_RUNS must be 1 (scheduled full run) or 3 (named checks)" >&2
  exit 2
fi
load_core=$(python3 -c 'import os; print(min(os.sched_getaffinity(0)))')
burner_pids=()
cleanup() {
  if (( ${#burner_pids[@]} )); then
    kill "${burner_pids[@]}" 2>/dev/null || true
    wait "${burner_pids[@]}" 2>/dev/null || true
  fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
for (( index=0; index<load_burners; index++ )); do
  taskset -c "$load_core" bash -c 'while :; do :; done' &
  burner_pids+=("$!")
done
for burner_pid in "${burner_pids[@]}"; do
  kill -0 "$burner_pid"
done
passed=0
failed=0
echo "load-independence: core=$load_core burners=$load_burners runs=$load_runs"
for (( run=1; run<=load_runs; run++ )); do
  echo "load-independence: run=$run"
  if taskset -c "$load_core" "$@"; then
    passed=$((passed + 1))
  else
    failed=$((failed + 1))
  fi
done
echo "load-independence: passed=$passed failed=$failed"
(( failed == 0 ))
