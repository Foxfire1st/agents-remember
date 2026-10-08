from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from agents_remember_test_support.testing.waits import HANG_GUARD_SECONDS

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _contract() -> dict[str, str]:
    values: dict[str, str] = {}
    path = REPOSITORY_ROOT / "scripts/python-runtime-contract.env"
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, value = line.split("=", 1)
        values[key] = value.strip("'\"")
    return values


def test_unsupported_version_is_named_before_importing_new_stdlib_modules() -> None:
    checker = REPOSITORY_ROOT / "scripts/check-python-runtime.py"
    expected = _contract()["AR_PYTHON_VERSION"]
    program = r"""
import builtins
import runpy
import sys

checker, expected = sys.argv[1:]
original_import = builtins.__import__

def import_without_new_stdlib(name, *args, **kwargs):
    origin = args[0] if args else kwargs.get("globals", {})
    if (
        isinstance(origin, dict)
        and origin.get("__file__") == checker
        and (name == "compression" or name.startswith("compression."))
    ):
        raise ModuleNotFoundError("compression is unavailable before Python 3.14")
    return original_import(name, *args, **kwargs)

builtins.__import__ = import_without_new_stdlib
sys.version_info = (3, 13, 15, "final", 0)
sys.argv = [checker, "--expected-version", expected]
runpy.run_path(checker, run_name="__main__")
"""
    result = subprocess.run(
        [sys.executable, "-B", "-c", program, checker.as_posix(), expected],
        capture_output=True,
        text=True,
        check=False,
        timeout=HANG_GUARD_SECONDS,
    )
    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr == (
        f"Agents Remember Python runtime refusal: expected Python {expected}, "
        f"observed 3.13.15 at {sys.executable}\n"
    )


def _write_executable(path: Path, contents: str) -> None:
    path.write_text(contents, encoding="utf-8")
    path.chmod(0o755)


@dataclass(frozen=True)
class _RuntimeFixture:
    contract: dict[str, str]
    environment: dict[str, str]
    cache_root: Path
    tooling_root: Path
    log: Path


def _runtime_fixture(tmp_path: Path) -> _RuntimeFixture:
    contract = _contract()
    cache_root = tmp_path / "cache"
    tooling_root = tmp_path / "tooling"
    shim_root = tmp_path / "bin"
    log = tmp_path / "git.log"
    (cache_root / "downloads").mkdir(parents=True)
    tooling_root.mkdir()
    shim_root.mkdir()
    archive = cache_root / "downloads" / f"Python-{contract['AR_PYTHON_VERSION']}.tar.xz"
    archive.write_text("verified fixture source\n", encoding="utf-8")
    _write_executable(
        shim_root / "sha256sum",
        '#!/usr/bin/env sh\nprintf \'%s  %s\\n\' "$AR_TEST_SOURCE_SHA" "$1"\n',
    )
    _write_executable(shim_root / "git", _GIT_SHIM)
    environment = os.environ.copy()
    environment.update(
        {
            "PATH": f"{shim_root}:{environment['PATH']}",
            "AR_TEST_LOG": log.as_posix(),
            "AR_TEST_BUILD_COMMIT": contract["AR_PYTHON_BUILD_COMMIT"],
            "AR_TEST_PYTHON_VERSION": contract["AR_PYTHON_VERSION"],
            "AR_TEST_PYTHON_MINOR": contract["AR_PYTHON_MINOR"],
            "AR_TEST_SOURCE_SHA": contract["AR_PYTHON_SOURCE_SHA256"],
            "AR_TEST_SOURCE_URL": contract["AR_PYTHON_SOURCE_URL"],
        }
    )
    return _RuntimeFixture(contract, environment, cache_root, tooling_root, log)


def _run_installer(
    tmp_path: Path,
    fixture: _RuntimeFixture,
    runtime_name: str,
) -> subprocess.CompletedProcess[str]:
    prefix = tmp_path / runtime_name / f"cpython-{fixture.contract['AR_PYTHON_VERSION']}"
    return subprocess.run(
        [
            "bash",
            (REPOSITORY_ROOT / "scripts/install-python-runtime.sh").as_posix(),
            "--prefix",
            prefix.as_posix(),
            "--cache-root",
            fixture.cache_root.as_posix(),
            "--tooling-root",
            fixture.tooling_root.as_posix(),
        ],
        check=False,
        capture_output=True,
        text=True,
        env=fixture.environment,
    )


_GIT_SHIM = r"""#!/usr/bin/env bash
set -eu
printf '%s\n' "$*" >> "$AR_TEST_LOG"
if [ "$1" = clone ]; then
  destination="$4"
  mkdir -p "$destination/.git"
  if [ "${AR_TEST_CLONE_FAIL:-0}" = 1 ]; then
    printf 'partial\n' > "$destination/partial"
    exit 23
  fi
  if [ -n "${AR_TEST_CLONE_BARRIER:-}" ]; then
    mkdir -p "$AR_TEST_CLONE_BARRIER"
    printf 'ready\n' > "$AR_TEST_CLONE_BARRIER/clone.$$"
    attempts=0
    while :; do
      set -- "$AR_TEST_CLONE_BARRIER"/clone.*
      [ "$#" -ge 2 ] && break
      attempts=$((attempts + 1))
      [ "$attempts" -lt 500 ] || exit 92
      sleep 0.01
    done
  fi
  case "${AR_TEST_RACE_FOREIGN:-}" in
    directory)
      mkdir -p "$AR_TEST_RACE_TARGET"
      printf 'foreign\n' > "$AR_TEST_RACE_TARGET/foreign-content"
      ;;
    symlink)
      mkdir -p "$AR_TEST_RACE_SYMLINK_TARGET"
      printf 'foreign\n' > "$AR_TEST_RACE_SYMLINK_TARGET/foreign-content"
      ln -s "$AR_TEST_RACE_SYMLINK_TARGET" "$AR_TEST_RACE_TARGET"
      ;;
  esac
  exit 0
fi
root="$2"
case "$3" in
  checkout)
    definition="$root/plugins/python-build/share/python-build/$AR_TEST_PYTHON_VERSION"
    executable="$root/plugins/python-build/bin/python-build"
    mkdir -p "$(dirname -- "$definition")" "$(dirname -- "$executable")"
    printf '%s#%s\n' "$AR_TEST_SOURCE_URL" "$AR_TEST_SOURCE_SHA" > "$definition"
    cat > "$executable" <<'PYTHON_BUILD'
#!/usr/bin/env sh
set -eu
printf 'python-build %s\n' "$*" >> "$AR_TEST_LOG"
prefix="$3"
mkdir -p "$prefix/bin"
printf '#!/usr/bin/env sh\nexit 0\n' > "$prefix/bin/python$AR_TEST_PYTHON_MINOR"
chmod +x "$prefix/bin/python$AR_TEST_PYTHON_MINOR"
PYTHON_BUILD
    chmod +x "$executable"
    ;;
  rev-parse)
    printf '%s\n' "$AR_TEST_BUILD_COMMIT"
    ;;
  *)
    exit 91
    ;;
esac
"""


def test_runtime_builder_is_fully_cloned_atomically_published_and_reused(tmp_path: Path) -> None:
    fixture = _runtime_fixture(tmp_path)
    first = _run_installer(tmp_path, fixture, "runtime-one")
    assert first.returncode == 0, first.stderr

    builder = fixture.tooling_root / f"pyenv-{fixture.contract['AR_PYTHON_BUILD_COMMIT'][:12]}"
    clone_lines = [
        line
        for line in fixture.log.read_text(encoding="utf-8").splitlines()
        if line.startswith("clone ")
    ]
    installer = (REPOSITORY_ROOT / "scripts/install-python-runtime.sh").read_text(encoding="utf-8")
    assert builder.is_dir()
    assert not list(fixture.tooling_root.glob(".pyenv-*.staging.*"))
    assert len(clone_lines) == 1
    assert "clone --no-checkout" in clone_lines[0]
    assert "--filter" not in clone_lines[0]
    assert installer.count('mv -T --no-clobber -- "$builder_staging" "$builder_root"') == 1
    assert installer.index('validate_builder "$builder_staging"') < installer.index("mv -T --")

    sentinel = builder / "reuse-sentinel"
    sentinel.write_text("preserve\n", encoding="utf-8")
    second = _run_installer(tmp_path, fixture, "runtime-two")
    assert second.returncode == 0, second.stderr
    clone_lines = [
        line
        for line in fixture.log.read_text(encoding="utf-8").splitlines()
        if line.startswith("clone ")
    ]
    assert len(clone_lines) == 1
    assert sentinel.read_text(encoding="utf-8") == "preserve\n"


def test_existing_foreign_builder_is_refused_and_preserved(tmp_path: Path) -> None:
    fixture = _runtime_fixture(tmp_path)
    builder = fixture.tooling_root / f"pyenv-{fixture.contract['AR_PYTHON_BUILD_COMMIT'][:12]}"
    builder.mkdir()
    marker = builder / "foreign-content"
    marker.write_text("keep\n", encoding="utf-8")

    failed = _run_installer(tmp_path, fixture, "refused-runtime")

    assert failed.returncode == 1
    assert f"refusing foreign builder path: {builder}" in failed.stderr
    assert marker.read_text(encoding="utf-8") == "keep\n"
    assert not fixture.log.exists()
    assert not list(fixture.tooling_root.glob(".pyenv-*.staging.*"))
