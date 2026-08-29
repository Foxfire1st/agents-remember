from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

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


def test_runtime_contract_has_one_exact_supported_minor() -> None:
    contract = _contract()
    package = (REPOSITORY_ROOT / "mcp/pyproject.toml").read_text(encoding="utf-8")
    quality = (REPOSITORY_ROOT / ".github/workflows/quality-checks.yml").read_text(encoding="utf-8")
    release = (REPOSITORY_ROOT / ".github/workflows/publish-mcp-to-pypi.yml").read_text(
        encoding="utf-8"
    )
    dagger_python = (REPOSITORY_ROOT / ".dagger/.python-version").read_text(encoding="utf-8")

    assert contract["AR_PYTHON_VERSION"] == "3.13.15"
    assert contract["AR_PYTHON_REQUIRES"] == ">=3.13,<3.14"
    assert 'requires-python = ">=3.13,<3.14"' in package
    assert '"Programming Language :: Python :: 3.13"' in package
    assert "Programming Language :: Python :: 3.11" not in package
    assert "Programming Language :: Python :: 3.12" not in package
    assert 'python-version: "3.13.15"' in quality
    assert 'python-version: "3.13.15"' in release
    assert dagger_python.strip() == contract["AR_PYTHON_VERSION"]


@pytest.mark.skipif(sys.platform != "linux", reason="pidfd is the Linux executor contract")
def test_current_test_interpreter_passes_the_canonical_capability_probe() -> None:
    contract = _contract()
    completed = subprocess.run(
        [
            sys.executable,
            (REPOSITORY_ROOT / "scripts/check-python-runtime.py").as_posix(),
            "--expected-version",
            contract["AR_PYTHON_VERSION"],
            "--require-linux-pidfd",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    report = json.loads(completed.stdout)

    assert report["version"] == contract["AR_PYTHON_VERSION"]
    assert report["capabilities"] == {
        "os.pidfd_open": True,
        "signal.pidfd_send_signal": True,
    }
