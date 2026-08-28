from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

from agents_remember_test_support.code_quality import check

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def ruff_lint_configuration() -> dict[str, Any]:
    """Return the repository's effective Ruff lint table."""
    with (REPOSITORY_ROOT / "pyproject.toml").open("rb") as handle:
        data = tomllib.load(handle)
    return data["tool"]["ruff"]["lint"]


def run_ruff_over_tracked_python(*arguments: str) -> subprocess.CompletedProcess[str]:
    """Run Ruff with ``arguments`` over every tracked Python file in this checkout."""
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "--no-cache",
            *arguments,
            *(path.as_posix() for path in check.git_ls_files(REPOSITORY_ROOT, "*.py")),
        ],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        check=False,
        stdin=subprocess.DEVNULL,
    )


def run_ruff_with_repository_configuration(
    source: Path,
) -> subprocess.CompletedProcess[str]:
    """Run Ruff over one file using this repository's real lint configuration."""
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "--no-cache",
            "--config",
            str(REPOSITORY_ROOT / "pyproject.toml"),
            "--output-format",
            "json",
            str(source),
        ],
        capture_output=True,
        text=True,
        check=False,
        stdin=subprocess.DEVNULL,
    )
