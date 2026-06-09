from __future__ import annotations

import subprocess
from pathlib import Path


def run_command(command: list[str], dry_run: bool, cwd: Path | None = None) -> None:
    printable = " ".join(command)
    if dry_run:
        location = f" in {cwd}" if cwd else ""
        print(f"Would run{location}: {printable}")
        return
    # Children must not inherit this process's stdio: under the stdio MCP
    # transport those descriptors are the protocol pipes, and inherited stdout
    # would write child output straight into the JSON-RPC stream (GitHub #49).
    result = subprocess.run(
        command,
        cwd=cwd,
        check=False,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        errors="replace",
    )
    if result.returncode != 0:
        tail = (result.stderr or result.stdout or "").strip()[-2000:]
        raise RuntimeError(f"command failed ({result.returncode}): {printable}\n{tail}")


def git_command(*args: str) -> list[str]:
    return ["git", "-c", "core.longpaths=true", "-c", "safe.directory=*", *args]


def repo_has_commit(repo_root: Path, commit: str) -> bool:
    result = subprocess.run(
        git_command("-C", str(repo_root), "cat-file", "-e", f"{commit}^{{commit}}"),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode == 0
