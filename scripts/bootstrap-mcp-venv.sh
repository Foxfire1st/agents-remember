#!/usr/bin/env bash
# Build/select canonical CPython and recreate the MCP development venv.

set -euo pipefail

script_dir="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(CDPATH= cd -- "$script_dir/.." && pwd)"
# shellcheck source=python-runtime-contract.env
. "$script_dir/python-runtime-contract.env"

data_root="${XDG_DATA_HOME:-$HOME/.local/share}/agents-remember"
prefix="${AR_PYTHON_PREFIX:-$data_root/python/cpython-$AR_PYTHON_VERSION}"
venv="$repo_root/mcp/.venv"
replace=false

usage() {
  echo "usage: scripts/bootstrap-mcp-venv.sh [--replace] [--prefix PATH]" >&2
  exit 2
}

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

"$script_dir/install-python-runtime.sh" --prefix "$prefix"
runtime_python="$prefix/bin/python3.13"
"$runtime_python" "$script_dir/check-python-runtime.py" \
  --expected-version "$AR_PYTHON_VERSION" \
  --expected-base-prefix "$prefix" \
  --require-linux-pidfd \
  --source-url "$AR_PYTHON_SOURCE_URL" \
  --source-sha256 "$AR_PYTHON_SOURCE_SHA256" \
  --builder-commit "$AR_PYTHON_BUILD_COMMIT" >/dev/null

command -v uv >/dev/null 2>&1 || {
  echo "[python-runtime] uv $AR_UV_VERSION is required to create mcp/.venv" >&2
  exit 1
}
observed_uv="$(uv --version | awk '{print $2}')"
[ "$observed_uv" = "$AR_UV_VERSION" ] || {
  echo "[python-runtime] expected uv $AR_UV_VERSION, observed $observed_uv" >&2
  exit 1
}

backup=""
backup_root="${XDG_CACHE_HOME:-$HOME/.cache}/agents-remember/venv-backups"
mkdir -p "$backup_root"
if [ -e "$venv" ]; then
  $replace || {
    echo "[python-runtime] $venv already exists; pass --replace for an explicit recreation" >&2
    exit 1
  }
  backup="$backup_root/agents-remember-mcp-$(date -u +%Y%m%dT%H%M%SZ)"
  mv -- "$venv" "$backup"
fi

restore_previous() {
  status=$?
  trap - EXIT
  if [ "$status" -ne 0 ]; then
    if [ -e "$venv" ]; then
      failed="$backup_root/agents-remember-mcp-failed-$(date -u +%Y%m%dT%H%M%SZ)"
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

UV_PROJECT_ENVIRONMENT="$venv" uv sync \
  --project "$repo_root/mcp" \
  --python "$runtime_python" \
  --no-managed-python \
  --frozen \
  --all-extras

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
  echo "[python-runtime] previous venv retained for bounded rollback at $backup"
fi
echo "[python-runtime] canonical MCP venv ready at $venv"
