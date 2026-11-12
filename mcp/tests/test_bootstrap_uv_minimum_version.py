"""The worktree environment builds with the contract's uv or any newer one.

MIK-R139: ``scripts/bootstrap-mcp-venv.sh`` takes the ``uv`` on the PATH
when it is ``AR_UV_VERSION`` or newer, compared as numbers part by part, and
installs none itself. An older, missing or unreadable ``uv`` is refused
before any environment is moved or created, naming the lowest accepted
version, the version found and the path found. The build prints the version
and path of the ``uv`` it used, and a sync failure under a newer ``uv`` names
both versions.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
BOOTSTRAP = REPOSITORY_ROOT / "scripts" / "bootstrap-mcp-venv.sh"
CONTRACT = REPOSITORY_ROOT / "scripts" / "python-runtime-contract.env"
CHECK_RUNTIME = REPOSITORY_ROOT / "scripts" / "check-python-runtime.py"
VERSION_RE = re.compile(r"^[0-9]+(\.[0-9]+)*$")


def contract_values() -> dict[str, str]:
    """The single-authority runtime contract (rule 7) as a plain mapping."""

    values: dict[str, str] = {}
    for line in CONTRACT.read_text(encoding="utf-8").splitlines():
        if line.startswith("AR_") and "=" in line:
            key, value = line.split("=", 1)
            values[key] = value.strip().strip("'")
    return values


def contract_uv_version() -> str:
    """The single uv version the runtime contract names (rule 7)."""

    values = contract_values()
    assert list(values).count("AR_UV_VERSION") == 1
    minimum = values["AR_UV_VERSION"]
    assert VERSION_RE.match(minimum), f"contract uv version is not dotted numbers: {minimum!r}"
    return minimum


def write_fake_uv(bin_dir: Path, version_text: str, exit_code: int = 0, bare: bool = False) -> Path:
    """A PATH uv reporting ``version_text`` from `uv --version`, exiting ``exit_code``.

    ``bare`` prints the text alone (a single-token version output); otherwise it is
    printed in the real `uv X.Y.Z (...)` shape.
    """

    bin_dir.mkdir(parents=True, exist_ok=True)
    uv = bin_dir / "uv"
    if bare:
        version_line = f"echo '{version_text}'"
    elif version_text:
        version_line = f"echo 'uv {version_text} (test shim)'"
    else:
        version_line = "echo 'uv'"
    uv.write_text(
        "#!/usr/bin/env bash\n"
        'if [ "${1:-}" = "--version" ]; then\n'
        f"  {version_line};\n"
        f"  exit {exit_code}\n"
        "fi\n"
        'echo "test uv shim: unsupported args $*" >&2\n'
        "exit 3\n",
        encoding="utf-8",
    )
    uv.chmod(uv.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return uv


@pytest.fixture
def scratch_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Disposable data/cache/config folders; the script writes nowhere else."""

    env = dict(os.environ)
    for name in ("XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_CONFIG_HOME"):
        folder = tmp_path / name.lower().replace("xdg_", "")
        folder.mkdir()
        env[name] = str(folder)
    return env


def run_driver(source: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    driver = 'source "$BOOTSTRAP_PATH"\n' + source
    return subprocess.run(
        ["bash", "-c", driver],
        capture_output=True,
        check=False,
        text=True,
        env={"BOOTSTRAP_PATH": str(BOOTSTRAP), "PATH": "/usr/bin:/bin", **env},
        timeout=120,
    )


@pytest.mark.parametrize(
    ("observed", "minimum", "accepted"),
    [
        ("0.12.21", "0.12.6", True),  # the developer's newer uv beats the contract's
        ("0.12.6", "0.12.6", True),  # the contract's own version builds
        ("0.12.5", "0.12.6", False),  # one patch below is refused
        ("0.9.30", "0.12.6", False),  # text order would accept this; numbers refuse it
        ("1.0.0", "0.12.6", True),  # a newer major is accepted
        ("0.12", "0.12.0", True),  # missing parts are zero
        ("0.12.6.1", "0.12.6", True),  # a longer tail above is accepted
        ("0.12.6", "0.12.6.1", False),  # a longer tail below is refused
        ("10.0.0", "9.99.99", True),  # multi-digit parts compare as numbers
        ("0.12.06", "0.12.6", True),  # leading zeros are decimal, never octal
    ],
)
def test_version_comparison_is_numeric(
    observed: str, minimum: str, accepted: bool, scratch_env: dict[str, str]
) -> None:
    """Versions compare part by part as numbers, never as text (rule 1)."""

    proc = run_driver(
        f'if version_at_least "{observed}" "{minimum}"; then echo ACCEPT; else echo REFUSE; fi\n',
        scratch_env,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == ("ACCEPT" if accepted else "REFUSE")


@pytest.mark.parametrize("version", ["0.12.21", "1.0.0"])
def test_gate_uses_a_newer_uv_as_it_is(
    version: str, tmp_path: Path, scratch_env: dict[str, str]
) -> None:
    """A newer PATH uv is selected and its version and path are printed (rules 1, 6)."""

    minimum = contract_uv_version()
    uv = write_fake_uv(tmp_path / "bin", version)
    proc = run_driver(
        "resolve_uv\n",
        {**scratch_env, "PATH": f"{uv.parent}:/usr/bin:/bin"},
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == (
        f"[python-runtime] building with uv {version} at {uv} (requires {minimum} or newer)"
    )


def test_gate_uses_the_contract_version_itself(tmp_path: Path, scratch_env: dict[str, str]) -> None:
    """The contract's own version keeps building (rule 1, preserved)."""

    minimum = contract_uv_version()
    uv = write_fake_uv(tmp_path / "bin", minimum)
    proc = run_driver(
        "resolve_uv\n",
        {**scratch_env, "PATH": f"{uv.parent}:/usr/bin:/bin"},
    )
    assert proc.returncode == 0, proc.stderr
    assert f"building with uv {minimum} at {uv}" in proc.stdout


def run_bootstrap(env: dict[str, str], prefix: Path, path: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(BOOTSTRAP), "--prefix", str(prefix)],
        capture_output=True,
        check=False,
        text=True,
        env={**env, "PATH": path},
        timeout=300,
    )


def test_older_uv_is_refused_before_anything_is_touched(
    tmp_path: Path, scratch_env: dict[str, str]
) -> None:
    """An older uv is refused with the lowest version, the found version and path (rule 3)."""

    minimum = contract_uv_version()
    uv = write_fake_uv(tmp_path / "bin", "0.12.5")
    prefix = tmp_path / "cpython-3.14.8"
    proc = run_bootstrap(scratch_env, prefix, f"{uv.parent}:/usr/bin:/bin")
    assert proc.returncode == 1
    assert (
        f"[python-runtime] uv {minimum} or newer is required to create mcp/.venv; "
        f"found 0.12.5 at {uv}" in proc.stderr
    )
    assert "building with uv" not in proc.stdout
    assert not (Path(scratch_env["XDG_CACHE_HOME"]) / "agents-remember").exists()


def test_missing_uv_is_refused(tmp_path: Path, scratch_env: dict[str, str]) -> None:
    """No uv on the PATH is refused with the lowest accepted version (rule 3)."""

    minimum = contract_uv_version()
    prefix = tmp_path / "cpython-3.14.8"
    proc = run_bootstrap(scratch_env, prefix, "/usr/bin:/bin")
    assert proc.returncode == 1
    assert (
        f"[python-runtime] uv {minimum} or newer is required to create mcp/.venv; "
        "found no uv on the PATH" in proc.stderr
    )
    assert "building with uv" not in proc.stdout


@pytest.mark.parametrize(
    ("version_text", "reported"),
    [("banana", "banana"), ("", "uv")],
)
def test_unreadable_version_is_refused_with_its_text(
    version_text: str, reported: str, tmp_path: Path, scratch_env: dict[str, str]
) -> None:
    """A version text that cannot be read is refused with that text (rule 3)."""

    minimum = contract_uv_version()
    uv = write_fake_uv(tmp_path / "bin", version_text)
    prefix = tmp_path / "cpython-3.14.8"
    proc = run_bootstrap(scratch_env, prefix, f"{uv.parent}:/usr/bin:/bin")
    assert proc.returncode == 1
    assert (
        f"[python-runtime] uv {minimum} or newer is required to create mcp/.venv; "
        f"could not read the version of {uv} (reported: '{reported}')" in proc.stderr
    )
    assert "building with uv" not in proc.stdout


@pytest.mark.parametrize(
    "probe",
    [
        ("banana", 0, True, "banana"),  # single-token output keeps its text
        ("banana (broken)", 7, False, "banana"),  # nonzero exit keeps its text
        ("", 7, True, "<empty>"),  # nonzero exit with no output
    ],
)
def test_unreadable_version_output_is_never_reported_empty(
    probe: tuple[str, int, bool, str], tmp_path: Path, scratch_env: dict[str, str]
) -> None:
    """L139-CODE-F02: raw unreadable output is retained on refusal, with minimum/path."""

    version_text, exit_code, bare, reported = probe
    minimum = contract_uv_version()
    uv = write_fake_uv(tmp_path / "bin", version_text, exit_code=exit_code, bare=bare)
    proc = run_driver(
        'status=0; resolve_uv || status=$?\necho "status=$status"\n',
        {**scratch_env, "PATH": f"{uv.parent}:/usr/bin:/bin"},
    )
    assert "status=1" in proc.stdout, proc.stderr
    assert (
        f"[python-runtime] uv {minimum} or newer is required to create mcp/.venv; "
        f"could not read the version of {uv} (reported: '{reported}')" in proc.stderr
    )


def test_bare_version_token_is_accepted(tmp_path: Path, scratch_env: dict[str, str]) -> None:
    """A single-token dotted version is read as the version (F02 sibling)."""

    minimum = contract_uv_version()
    uv = write_fake_uv(tmp_path / "bin", "0.12.21", bare=True)
    proc = run_driver(
        "resolve_uv\n",
        {**scratch_env, "PATH": f"{uv.parent}:/usr/bin:/bin"},
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == (
        f"[python-runtime] building with uv 0.12.21 at {uv} (requires {minimum} or newer)"
    )


def test_refusal_leaves_an_existing_environment_alone(
    tmp_path: Path, scratch_env: dict[str, str]
) -> None:
    """A refusal moves no existing venv and creates no backup (rule 3, preserved)."""

    venv = REPOSITORY_ROOT / "mcp" / ".venv"
    marker = venv / ".l139-refusal-marker" if venv.is_dir() else None
    if marker is not None:
        marker.write_text("untouched", encoding="utf-8")
    uv = write_fake_uv(tmp_path / "bin", "0.11.0")
    prefix = tmp_path / "cpython-3.14.8"
    try:
        proc = run_bootstrap(scratch_env, prefix, f"{uv.parent}:/usr/bin:/bin")
        assert proc.returncode == 1
        assert "or newer is required" in proc.stderr
        if marker is not None:
            assert marker.read_text(encoding="utf-8") == "untouched"
        assert not (Path(scratch_env["XDG_CACHE_HOME"]) / "agents-remember").exists()
    finally:
        if marker is not None and marker.exists():
            marker.unlink()


def test_script_brings_no_uv_itself() -> None:
    """The script downloads and installs no uv (rule 2)."""

    text = BOOTSTRAP.read_text(encoding="utf-8")
    assert "curl" not in text
    assert "wget" not in text
    assert "uv==" not in text


def find_valid_runtime_prefix() -> Path | None:
    """A canonical runtime prefix the checker accepts, else None to skip.

    The lookup validates by running the checker's own proof (read-only) and
    never builds: the test process's base runtime first, then the default
    data-root installation.
    """

    values = contract_values()
    minor = values["AR_PYTHON_MINOR"]
    candidates = [
        Path(sys.base_prefix),
        Path.home()
        / ".local"
        / "share"
        / "agents-remember"
        / "python"
        / f"cpython-{values['AR_PYTHON_VERSION']}",
    ]
    checker_args = [
        "--expected-version",
        values["AR_PYTHON_VERSION"],
        "--require-linux-pidfd",
        "--source-url",
        values["AR_PYTHON_SOURCE_URL"],
        "--source-sha256",
        values["AR_PYTHON_SOURCE_SHA256"],
        "--builder-commit",
        values["AR_PYTHON_BUILD_COMMIT"],
    ]
    for prefix in candidates:
        python = prefix / "bin" / f"python{minor}"
        if not python.is_file() or not os.access(python, os.X_OK):
            continue
        proc = subprocess.run(
            [str(python), str(CHECK_RUNTIME), *checker_args, "--expected-base-prefix", str(prefix)],
            capture_output=True,
            check=False,
            text=True,
            timeout=120,
        )
        if proc.returncode == 0:
            return prefix
    return None


def test_sync_failure_names_both_uv_versions(tmp_path: Path, scratch_env: dict[str, str]) -> None:
    """A sync failure under a newer uv names both versions and restores (rule 5).

    The fake uv passes the gate as newer, then fails `sync`: the script must
    name the version it ran with and the contract's, and the moved-aside
    environment must be back in place afterwards.
    """

    prefix = find_valid_runtime_prefix()
    if prefix is None:
        pytest.skip("no checker-valid canonical runtime available")
    assert prefix is not None
    minimum = contract_uv_version()
    uv = write_fake_uv(tmp_path / "bin", "0.12.21")
    root = tmp_path / "repository"
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    for name in (BOOTSTRAP.name, CONTRACT.name, CHECK_RUNTIME.name, "install-python-runtime.sh"):
        shutil.copy2(REPOSITORY_ROOT / "scripts" / name, scripts / name)
    venv = root / "mcp" / ".venv"
    venv.mkdir(parents=True)
    assert venv.is_dir(), "this proof recreates an existing environment"
    marker = venv / ".l139-sync-failure-marker"
    marker.write_text("restore me", encoding="utf-8")
    backup_root = Path(scratch_env["XDG_CACHE_HOME"]) / "agents-remember" / "venv-backups"
    try:
        proc = subprocess.run(
            ["bash", str(scripts / BOOTSTRAP.name), "--replace", "--prefix", str(prefix)],
            capture_output=True,
            check=False,
            text=True,
            env={**scratch_env, "PATH": f"{uv.parent}:/usr/bin:/bin"},
            timeout=300,
        )
        assert proc.returncode == 1
        assert f"building with uv 0.12.21 at {uv}" in proc.stdout
        assert (
            f"[python-runtime] uv sync failed with uv 0.12.21 at {uv} "
            f"(requires {minimum} or newer)" in proc.stderr
        )
        assert "restored previous venv after failed recreation" in proc.stderr
        assert marker.read_text(encoding="utf-8") == "restore me"
        assert not list(backup_root.glob("agents-remember-mcp-*"))
    finally:
        if marker.exists():
            marker.unlink()
