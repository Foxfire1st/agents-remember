#!/usr/bin/env bash
# Build/select canonical CPython and recreate the MCP development venv.
#
# The uv on the PATH must be the runtime contract's version or newer; the
# script uses it as it is and installs none itself.

set -euo pipefail

script_dir="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(CDPATH= cd -- "$script_dir/.." && pwd)"
# shellcheck source=python-runtime-contract.env
. "$script_dir/python-runtime-contract.env"

data_root="${XDG_DATA_HOME:-$HOME/.local/share}/agents-remember"
prefix="${AR_PYTHON_PREFIX:-$data_root/python/cpython-$AR_PYTHON_VERSION}"
venv="$repo_root/mcp/.venv"
replace=false

# The uv selected by resolve_uv, before anything is moved or created.
uv_path=""
observed_uv=""

usage() {
  echo "usage: scripts/bootstrap-mcp-venv.sh [--replace] [--prefix PATH]" >&2
  exit 2
}

# version_at_least OBSERVED MINIMUM: success when the dotted OBSERVED version
# is equal to or newer than MINIMUM. Parts compare as decimal numbers, never
# as text, so 0.12.21 is newer than 0.12.6; missing parts are zero. Both sides
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
  uv_version_output="$(uv --version 2>/dev/null)" && uv_version_ok=true || uv_version_ok=false
  # First line only; a leading "uv " token is the version command's own name,
  # so "uv 0.12.21 (...)" and a bare "0.12.21" both read as 0.12.21. The raw
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

main() {
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --replace) replace=true; shift ;;
      --prefix)
        [ "$#" -ge 2 ] || usage
        prefix="$2"
        shift 2
        ;;
      *) usage ;;
    esac
  done

  resolve_uv || exit 1

  "$script_dir/install-python-runtime.sh" --prefix "$prefix"
  runtime_python="$prefix/bin/python$AR_PYTHON_MINOR"
  "$runtime_python" "$script_dir/check-python-runtime.py" \
    --expected-version "$AR_PYTHON_VERSION" \
    --expected-base-prefix "$prefix" \
    --require-linux-pidfd \
    --source-url "$AR_PYTHON_SOURCE_URL" \
    --source-sha256 "$AR_PYTHON_SOURCE_SHA256" \
    --builder-commit "$AR_PYTHON_BUILD_COMMIT" >/dev/null

  backup=""
  backup_root="${XDG_CACHE_HOME:-$HOME/.cache}/agents-remember/venv-backups"
  if [ -e "$venv" ]; then
    # The backup root is created only when a previous environment is actually
    # moved aside: a fresh build writes nothing outside the worktree for this.
    mkdir -p "$backup_root"
    $replace || {
      echo "[python-runtime] $venv already exists; pass --replace for an explicit recreation" >&2
      exit 1
    }
    backup="$backup_root/agents-remember-mcp-$(basename -- "$repo_root")-$(date -u +%Y%m%dT%H%M%SZ)"
    mv -- "$venv" "$backup"
  fi

  restore_previous() {
    status=$?
    trap - EXIT
    if [ "$status" -ne 0 ]; then
      if [ -e "$venv" ]; then
        failed="$backup_root/agents-remember-mcp-failed-$(basename -- "$repo_root")-$(date -u +%Y%m%dT%H%M%SZ)"
        mkdir -p "$backup_root"
        mv -- "$venv" "$failed"
        echo "[python-runtime] failed candidate retained for diagnosis at $failed" >&2
      fi
      if [ -n "$backup" ]; then
        mv -- "$backup" "$venv"
        echo "[python-runtime] restored previous venv after failed recreation" >&2
      fi
    fi
    exit "$status"
  }
  trap restore_previous EXIT

  if ! UV_PROJECT_ENVIRONMENT="$venv" uv sync \
    --project "$repo_root/mcp" \
    --python "$runtime_python" \
    --no-managed-python \
    --frozen \
    --all-extras; then
    echo "[python-runtime] uv sync failed with uv $observed_uv at $uv_path (requires $AR_UV_VERSION or newer)" >&2
    exit 1
  fi

  "$venv/bin/python" "$script_dir/check-python-runtime.py" \
    --expected-version "$AR_PYTHON_VERSION" \
    --expected-base-prefix "$prefix" \
    --require-linux-pidfd \
    --source-url "$AR_PYTHON_SOURCE_URL" \
    --source-sha256 "$AR_PYTHON_SOURCE_SHA256" \
    --builder-commit "$AR_PYTHON_BUILD_COMMIT"
  uv pip check --python "$venv/bin/python"

  trap - EXIT
  if [ -n "$backup" ]; then
    rm -rf -- "$backup"
    echo "[python-runtime] removed replaced venv at $backup after successful recreation"
  fi
  echo "[python-runtime] canonical MCP venv ready at $venv"
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  main "$@"
fi
