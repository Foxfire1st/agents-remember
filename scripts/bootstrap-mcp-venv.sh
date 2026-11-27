#!/usr/bin/env bash
# Select uv-managed CPython and recreate the checkout MCP development environment.
#
# The uv on the PATH must be the runtime contract's version or newer; the
# script uses it as it is and installs none itself.

set -euo pipefail

script_dir="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(CDPATH= cd -- "$script_dir/.." && pwd)"
# shellcheck source=python-runtime-contract.env
. "$script_dir/python-runtime-contract.env"

venv="$repo_root/mcp/.venv"

# The uv selected by resolve_uv, before anything is moved or created.
uv_path=""
observed_uv=""

usage() {
  echo "usage: scripts/bootstrap-mcp-venv.sh [--replace | --ensure]" >&2
  exit 2
}

# version_at_least OBSERVED MINIMUM: success when the dotted OBSERVED version
# is equal to or newer than MINIMUM. Parts compare as decimal numbers, never
# as text, so 0.13.10 is newer than 0.13; missing parts are zero. Both sides
# must already match ^[0-9]+(\.[0-9]+)*$.
version_at_least() {
  local observed="$1" minimum="$2"
  local IFS=.
  # shellcheck disable=SC2206
  local -a observed_parts=($observed) minimum_parts=($minimum)
  local count=${#observed_parts[@]}
  if [ "${#minimum_parts[@]}" -gt "$count" ]; then
    count=${#minimum_parts[@]}
  fi
  local i o m
  for ((i = 0; i < count; i++)); do
    o=${observed_parts[i]:-0}
    m=${minimum_parts[i]:-0}
    [ "$((10#$o))" -gt "$((10#$m))" ] && return 0
    [ "$((10#$o))" -lt "$((10#$m))" ] && return 1
  done
  return 0
}

# resolve_uv: take the uv on the PATH when it is the contract's version or
# newer, and refuse anything older, missing or unreadable before the script
# moves or creates an environment. Sets uv_path and observed_uv, and prints
# the version and path it will build with. Returns non-zero on refusal.
resolve_uv() {
  [[ "$AR_UV_VERSION" =~ ^[0-9]+(\.[0-9]+)*$ ]] || {
    echo "[python-runtime] refusing malformed contract version AR_UV_VERSION='$AR_UV_VERSION'" >&2
    return 1
  }
  uv_path="$(command -v uv)" || {
    echo "[python-runtime] uv $AR_UV_VERSION or newer is required to create mcp/.venv; found no uv on the PATH" >&2
    return 1
  }
  uv_version_output="$("$uv_path" --version 2>/dev/null)" && uv_version_ok=true || uv_version_ok=false
  # First line only; a leading "uv " token is the version command's own name,
  # so "uv 0.13.10 (...)" and a bare "0.13.10" both read as 0.13.10. The raw
  # text is kept for the refusal below: a nonzero exit or an unreadable token
  # is refused with what was actually reported, never with <empty> for it.
  uv_version_output="${uv_version_output%%$'\n'*}"
  case "$uv_version_output" in
    "uv "*) observed_uv="${uv_version_output#uv }" ;;
    *) observed_uv="$uv_version_output" ;;
  esac
  observed_uv="${observed_uv%% *}"
  if [ "$uv_version_ok" != true ] || ! [[ "$observed_uv" =~ ^[0-9]+(\.[0-9]+)*$ ]]; then
    echo "[python-runtime] uv $AR_UV_VERSION or newer is required to create mcp/.venv; could not read the version of $uv_path (reported: '${observed_uv:-<empty>}')" >&2
    return 1
  fi
  version_at_least "$observed_uv" "$AR_UV_VERSION" || {
    echo "[python-runtime] uv $AR_UV_VERSION or newer is required to create mcp/.venv; found $observed_uv at $uv_path" >&2
    return 1
  }
  echo "[python-runtime] building with uv $observed_uv at $uv_path (requires $AR_UV_VERSION or newer)"
}

current_venv() {
  "$runtime_python" -B -P -c '
from pathlib import Path
import sys

sys.path.insert(0, sys.argv[1])
from agents_remember.python_runtime import read_venv_home

raise SystemExit(read_venv_home(Path(sys.argv[2])) != Path(sys.argv[3]))
' "$repo_root/mcp/src" "$venv" "$runtime_base/bin"
}

reserve_backup_path() {
  local folder
  mkdir -p "$backup_root" || return
  folder="$(mktemp -d "$backup_root/agents-remember-mcp-$(basename -- "$repo_root")-$(date -u +%Y%m%dT%H%M%S)-XXXXXXXX")" || return
  printf '%s/venv\n' "$folder"
}

main() {
  local mode="${1:-}"
  [ "$#" -le 1 ] || usage
  case "$mode" in
    ""|--replace|--ensure) ;;
    *) usage ;;
  esac

  resolve_uv || exit 1
  "$uv_path" python install --no-bin "$AR_PYTHON_MINOR"
  runtime_python="$("$uv_path" python find --system --managed-python --no-python-downloads "$AR_PYTHON_MINOR")"
  runtime_base="$(dirname -- "$(dirname -- "$runtime_python")")"
  "$runtime_python" "$script_dir/check-python-runtime.py" \
    --expected-version "$AR_PYTHON_MINOR" \
    --expected-base-prefix "$runtime_base" \
    --base-interpreter \
    --require-linux-pidfd \
    --uv-version "$observed_uv" >/dev/null

  # The checkout directory stays in place throughout venv replacement and rollback.
  # The shell retains the descriptor after Python acquires the lock on it.
  exec 9< "$repo_root/mcp"
  "$runtime_python" -B -P -c 'import fcntl; fcntl.flock(9, fcntl.LOCK_EX)'

  backup=""
  backup_root="${XDG_CACHE_HOME:-$HOME/.cache}/agents-remember/venv-backups"
  if [ -e "$venv" ]; then
    if [ "$mode" != --replace ] && current_venv; then
      if [ "$mode" = --ensure ] && [ -x "$venv/bin/python" ]; then
        return 0
      fi
      echo "[python-runtime] $venv already exists; pass --replace for an explicit recreation" >&2
      return 1
    fi
    backup="$(reserve_backup_path)"
    mv -T -- "$venv" "$backup"
  fi

  restore_previous() {
    status=$?
    trap - EXIT
    if [ "$status" -ne 0 ]; then
      if [ -e "$venv" ]; then
        failed="$(reserve_backup_path)"
        mv -T -- "$venv" "$failed"
        echo "[python-runtime] failed candidate retained for diagnosis at $failed" >&2
      fi
      if [ -n "$backup" ]; then
        mv -T -- "$backup" "$venv"
        rmdir -- "$(dirname -- "$backup")"
        echo "[python-runtime] restored previous venv after failed recreation" >&2
      fi
    fi
    exit "$status"
  }
  trap restore_previous EXIT

  if ! UV_PROJECT_ENVIRONMENT="$venv" "$uv_path" sync \
    --project "$repo_root/mcp" \
    --python "$AR_PYTHON_MINOR" \
    --managed-python \
    --no-python-downloads \
    --frozen \
    --all-extras; then
    echo "[python-runtime] uv sync failed with uv $observed_uv at $uv_path (requires $AR_UV_VERSION or newer)" >&2
    exit 1
  fi

  "$venv/bin/python" "$script_dir/check-python-runtime.py" \
    --expected-version "$AR_PYTHON_MINOR" \
    --expected-base-prefix "$runtime_base" \
    --require-linux-pidfd \
    --uv-version "$observed_uv"
  "$uv_path" pip check --python "$venv/bin/python"

  trap - EXIT
  if [ -n "$backup" ]; then
    rm -r -- "$backup"
    rmdir -- "$(dirname -- "$backup")"
    echo "[python-runtime] removed replaced venv at $backup after successful recreation"
  fi
  echo "[python-runtime] canonical MCP venv ready at $venv"

  # Run grammar pre-warm strictly outside the venv transaction so a missing
  # wheel, offline host, or unwritable cache never discards a valid venv.
  if [ -x "$venv/bin/agents-remember" ]; then
    if ! "$venv/bin/agents-remember" languages warm --repo "$repo_root"; then
      echo "[python-runtime] warning: language grammar pre-warm did not complete; grammars will warm lazily on demand" >&2
    fi
  fi
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  main "$@"
fi
