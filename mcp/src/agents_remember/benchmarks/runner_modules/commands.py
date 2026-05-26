from __future__ import annotations

import subprocess
from pathlib import Path


def run_command(command: list[str], dry_run: bool, cwd: Path | None = None) -> None:
    printable = " ".join(command)
    if dry_run:
        location = f" in {cwd}" if cwd else ""
        print(f"Would run{location}: {printable}")
        return
    subprocess.run(command, cwd=cwd, check=True)


def git_command(*args: str) -> list[str]:
    return ["git", "-c", "core.longpaths=true", "-c", "safe.directory=*", *args]


def repo_has_commit(repo_root: Path, commit: str) -> bool:
    result = subprocess.run(
        git_command("-C", str(repo_root), "cat-file", "-e", f"{commit}^{{commit}}"),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode == 0
