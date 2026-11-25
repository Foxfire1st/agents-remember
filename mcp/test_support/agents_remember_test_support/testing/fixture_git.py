"""Git run hermetically in a test fixture repository: no user or system configuration is read.

``HOME`` is the fixture root and the system configuration is off, so a developer's own Git
settings (signing, hooks, templates) cannot change what a fixture commits or fail its run.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


def fixture_git(root: Path, args: list[str]) -> str:
    """Run ``git <args>`` in ``root`` and answer its trimmed output; a failure fails the fixture."""

    result = subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
        env={
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "HOME": str(root),
            "GIT_CONFIG_NOSYSTEM": "1",
        },
    )
    if result.returncode != 0:
        raise AssertionError(f"fixture git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()
