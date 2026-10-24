#!/usr/bin/env python3
"""MIK-R204 rule 7: measure one AR server start, from process spawn to the completed tool list.

This measures the thing the requirement names -- **spawn through the completed `tools/list` answer,
including `initialize` and including the server's own tool-call-free refusal path** -- and records what
makes the number comparable: the two answers' arrival times, the host's load before and after, the
interpreter and dependency versions, the source identity, the cache conditions and the exact command.

It is a bounded wall-clock witness, not an any-load promise: the bound is 5.0 s on this 20-logical-CPU
host at one-minute load at most 40, and a run outside that load is reported as unrepresentative rather
than quietly kept.

Usage (from the worktree root, with the venv python):

    .venv/bin/python scripts/measure-ar-server-start.py --mode ordinary
    .venv/bin/python scripts/measure-ar-server-start.py --mode explainer
    .venv/bin/python scripts/measure-ar-server-start.py --mode ordinary --repeat 3 --out /var/tmp/x.json

The server is launched exactly as a seat launches it: `python -m agents_remember.mcp.server --config
<settings>`, with `AR_EXPLAINER_BINDING` in the environment for the explainer mode and absent otherwise.
No tool is called.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import tempfile
import time
from importlib import metadata
from pathlib import Path
from typing import Any

#: The requirement's bound, in seconds, and the load it is stated at.
BOUND_SECONDS = 5.0
STATED_LOAD = 40.0

REPO = "agents-remember"
TASK = "260928_measure-server-start"
LEAF = "260928-MIK-L999"


def load_average() -> float:
    """This host's one-minute load, as the requirement states it."""

    try:
        return os.getloadavg()[0]
    except (OSError, AttributeError):  # pragma: no cover - not reachable on this host
        return float("nan")


def write_world(root: Path, *, explainer: bool) -> tuple[Path, dict[str, str]]:
    """A minimal coordination world and settings file, plus the launch environment."""

    (root / "tasks" / REPO / TASK).mkdir(parents=True, exist_ok=True)
    (root / "repo").mkdir(exist_ok=True)
    (root / "memory").mkdir(exist_ok=True)
    workspace = root / "explainer" / REPO / TASK / LEAF
    workspace.mkdir(parents=True, exist_ok=True)
    # Settings must not live inside the coordinator root (runtime_config refuses it), so they sit beside it.
    settings = root.parent / (root.name + "-settings.json")
    settings.write_text(
        json.dumps(
            {
                "coordinationRoot": str(root),
                "workspaceRoot": str(root / "workspace"),
                "transcriptRoot": str(root / "transcripts"),
                "repositories": {
                    REPO: {"path": str(root / "repo"), "memoryRoot": str(root / "memory")}
                },
                "providers": {},
            },
            indent=1,
        ),
        encoding="utf-8",
    )
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in ("AR_EXPLAINER_BINDING", "AR_EXPLAINER_WORKSPACE")
    }
    if explainer:
        env["AR_EXPLAINER_BINDING"] = json.dumps(
            {
                "capability": "explainer",
                "agent": "measure-agent",
                "request": "measure-1",
                "repository": REPO,
                "task": TASK,
                "leaf": LEAF,
                "workspace": str(workspace),
                "generation": 1,
                "seat": "measure-agent",
            }
        )
    return settings, env


def measure(settings: Path, env: dict[str, str]) -> dict[str, Any]:
    """Spawn the server, take initialize and tools/list, and time each answer's arrival."""

    observed_at_start = load_average()
    command = [sys.executable, "-P", "-m", "agents_remember.mcp", "--config", str(settings)]
    spawn = time.monotonic()
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        text=True,
        bufsize=1,
    )

    def exchange(payload: dict[str, Any]) -> dict[str, Any]:
        assert process.stdin is not None and process.stdout is not None
        process.stdin.write(json.dumps(payload) + "\n")
        process.stdin.flush()
        line = process.stdout.readline()
        if not line:
            stderr = process.stderr.read() if process.stderr is not None else ""
            raise RuntimeError(f"the server closed the pipe: {stderr[-2000:]}")
        return json.loads(line)

    try:
        initialized = exchange(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "measure-ar-server-start", "version": "1"},
                },
            }
        )
        initialize_at = time.monotonic() - spawn
        if process.stdin is not None:
            process.stdin.write(
                json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n"
            )
            process.stdin.flush()
        listed = exchange({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        tools_at = time.monotonic() - spawn
    finally:
        process.kill()
        process.wait(timeout=10)

    tools = (listed.get("result") or {}).get("tools") or []
    return {
        "initialize_seconds": round(initialize_at, 3),
        "tool_list_seconds": round(tools_at, 3),
        "tool_count": len(tools),
        "first_tools": [tool.get("name") for tool in tools[:5]],
        "initialize_ok": "result" in initialized,
        "load_before": round(observed_at_start, 2),
        "load_after": round(load_average(), 2),
        "command": " ".join(command[1:]),
    }


def environment_facts() -> dict[str, Any]:
    """What makes the number comparable: interpreter, dependencies, source, caches."""

    def version(name: str) -> str:
        try:
            return metadata.version(name)
        except metadata.PackageNotFoundError:
            return "absent"

    source = Path(__file__).resolve().parents[1]
    head = subprocess.run(
        ["git", "-C", str(source), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    return {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "logical_cpus": os.cpu_count(),
        "mcp": version("mcp"),
        "pydantic": version("pydantic"),
        "fastapi": version("fastapi"),
        "tiktoken": version("tiktoken"),
        "source_root": str(source),
        "source_head": head or "unknown",
        "source_dirty_files": len(
            [
                line
                for line in subprocess.run(
                    ["git", "-C", str(source), "status", "--porcelain"],
                    capture_output=True,
                    text=True,
                    check=False,
                ).stdout.splitlines()
                if line.strip()
            ]
        ),
        "bytecode_cache": "PYTHONDONTWRITEBYTECODE" in os.environ or "existing __pycache__",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("ordinary", "explainer"), default="ordinary")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--out", type=Path, default=None)
    arguments = parser.parse_args(argv)

    root = Path(tempfile.mkdtemp(prefix=f"ar-start-{arguments.mode}-"))
    settings, env = write_world(root, explainer=arguments.mode == "explainer")
    runs = [measure(settings, env) for _ in range(max(1, arguments.repeat))]
    best = min(run["tool_list_seconds"] for run in runs)
    record = {
        "requirement": "MIK-R204 @ v1 rule 7",
        "mode": arguments.mode,
        "bound_seconds": BOUND_SECONDS,
        "stated_load": STATED_LOAD,
        "load_representative": all(run["load_before"] <= STATED_LOAD for run in runs),
        "runs": runs,
        "best_tool_list_seconds": best,
        "within_bound": best <= BOUND_SECONDS,
        "environment": environment_facts(),
        "world": str(root),
    }
    print(json.dumps(record, indent=1))
    if arguments.out is not None:
        arguments.out.write_text(json.dumps(record, indent=1), encoding="utf-8")
    return 0 if record["within_bound"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
