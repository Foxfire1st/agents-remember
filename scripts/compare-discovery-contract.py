#!/usr/bin/env python3
"""MIK-R204 rule 2: prove discovery is unchanged, not merely the same size.

A count is not a contract. This spawns the seat command against two source trees and compares the whole
`tools/list` answer -- every tool's name, order, description, complete input and output schema, and its
annotations -- byte for byte after canonical JSON encoding, and prints the first differences when they
differ.

    python scripts/compare-discovery-contract.py --before <root> --after <root> [--mode explainer]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

REPO = "agents-remember"
TASK = "260928_discovery-contract"
LEAF = "260928-MIK-L999"


def world(root: Path, *, explainer: bool) -> tuple[Path, dict[str, str]]:
    (root / "tasks" / REPO / TASK).mkdir(parents=True, exist_ok=True)
    (root / "repo").mkdir(exist_ok=True)
    (root / "memory").mkdir(exist_ok=True)
    workspace = root / "explainer" / REPO / TASK / LEAF
    workspace.mkdir(parents=True, exist_ok=True)
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
            }
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
                "agent": "contract-agent",
                "request": "contract-1",
                "repository": REPO,
                "task": TASK,
                "leaf": LEAF,
                "workspace": str(workspace),
                "generation": 1,
                "seat": "contract-agent",
            }
        )
    return settings, env


def discovery(candidate: Path, *, explainer: bool) -> dict[str, Any]:
    """The full `tools/list` result from the seat command run against one source tree."""

    root = Path(tempfile.mkdtemp(prefix="ar-contract-"))
    settings, env = world(root, explainer=explainer)
    python = candidate / "mcp" / ".venv" / "bin" / "python"
    command = [str(python), "-P", "-m", "agents_remember.mcp", "--config", str(settings)]
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        text=True,
        bufsize=1,
    )
    assert process.stdin is not None and process.stdout is not None

    def exchange(payload: dict[str, Any]) -> dict[str, Any]:
        process.stdin.write(json.dumps(payload) + "\n")  # type: ignore[union-attr]
        process.stdin.flush()  # type: ignore[union-attr]
        line = process.stdout.readline()  # type: ignore[union-attr]
        if not line:
            stderr = process.stderr.read() if process.stderr is not None else ""
            raise RuntimeError(f"{candidate}: the server closed the pipe: {stderr[-1500:]}")
        return json.loads(line)

    try:
        exchange(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "compare-discovery-contract", "version": "1"},
                },
            }
        )
        process.stdin.write(
            json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n"
        )
        process.stdin.flush()
        listed = exchange({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    finally:
        process.kill()
        process.wait(timeout=10)
    return listed["result"]


def executing_source(candidate: Path, env: dict[str, str]) -> dict[str, Any]:
    """Which package the comparison's own child actually executed, read from that child.

    A venv beside a tree does not mean the tree is what runs: an editable install can point anywhere, and
    that has already bitten this leaf once. So the running source is asked for, not assumed, and the record
    says whether it lies under the root being compared -- an unqualified attribution is worse than none.
    """

    python = candidate / "mcp" / ".venv" / "bin" / "python"
    probe = subprocess.run(
        [str(python), "-c", "import agents_remember; print(agents_remember.__file__)"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    reported = probe.stdout.strip() or f"<no answer: {probe.stderr.strip()[-300:]}>"
    return {
        "reported_package": reported,
        "under_this_root": str(candidate.resolve()) in reported,
    }


def provenance(candidate: Path) -> dict[str, Any]:
    """Where the bytes came from: which source tree, at which head, and how dirty it was.

    A digest of an answer is only meaningful beside the tree that produced it, so the comparison records
    the root, its git head and its dirty-file count rather than assuming the reader knows.
    """

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(candidate), *args], capture_output=True, text=True, check=False
        ).stdout.strip()

    dirty = [line for line in git("status", "--porcelain").splitlines() if line.strip()]
    return {
        "root": str(candidate.resolve()),
        "head": git("rev-parse", "HEAD") or "not-a-git-tree",
        "dirty_entries": len(dirty),
        "python": str(candidate / "mcp" / ".venv" / "bin" / "python"),
    }


def canonical(result: dict[str, Any]) -> str:
    return json.dumps(result, sort_keys=True, separators=(",", ":"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before", type=Path, required=True)
    parser.add_argument("--after", type=Path, required=True)
    parser.add_argument("--mode", choices=("ordinary", "explainer"), default="ordinary")
    parser.add_argument("--out", type=Path, default=None)
    arguments = parser.parse_args(argv)

    explainer = arguments.mode == "explainer"
    before = discovery(arguments.before, explainer=explainer)
    after = discovery(arguments.after, explainer=explainer)
    executed = {
        "before": executing_source(
            arguments.before,
            world(Path(tempfile.mkdtemp(prefix="ar-src-")), explainer=explainer)[1],
        ),
        "after": executing_source(
            arguments.after, world(Path(tempfile.mkdtemp(prefix="ar-src-")), explainer=explainer)[1]
        ),
    }
    before_json, after_json = canonical(before), canonical(after)
    evidence = arguments.out.parent if arguments.out is not None else Path(".")
    evidence.mkdir(parents=True, exist_ok=True)
    raw_before = evidence / f"discovery-{arguments.mode}-before.json"
    raw_after = evidence / f"discovery-{arguments.mode}-after.json"
    raw_before.write_text(json.dumps(before, indent=1, sort_keys=True), encoding="utf-8")
    raw_after.write_text(json.dumps(after, indent=1, sort_keys=True), encoding="utf-8")
    record = {
        "requirement": "MIK-R204 @ v1 rule 2",
        # What this compares and what it does not: discovery only. Runtime refusals -- dispatch refusing a
        # tool outside the surface, a missing or invalid binding refusing service -- are separate claims,
        # proven by their own tests, and are not folded in here as if a discovery digest covered them.
        "scope": "tools/list only: names, order, descriptions, complete schemas, annotations; not runtime refusals",
        "provenance": {
            "before": provenance(arguments.before),
            "after": provenance(arguments.after),
        },
        "executed_source": executed,
        "attribution_qualified": all(side["under_this_root"] for side in executed.values()),
        "raw": {
            "before": {
                "path": str(raw_before),
                "sha256": hashlib.sha256(raw_before.read_bytes()).hexdigest(),
            },
            "after": {
                "path": str(raw_after),
                "sha256": hashlib.sha256(raw_after.read_bytes()).hexdigest(),
            },
        },
        "mode": arguments.mode,
        "before_root": str(arguments.before),
        "after_root": str(arguments.after),
        "tool_count_before": len(before["tools"]),
        "tool_count_after": len(after["tools"]),
        "sha256_before": hashlib.sha256(before_json.encode()).hexdigest(),
        "sha256_after": hashlib.sha256(after_json.encode()).hexdigest(),
        "identical": before_json == after_json,
        "names_before": [tool["name"] for tool in before["tools"]],
        "names_after": [tool["name"] for tool in after["tools"]],
    }
    if before_json != after_json:
        differing = []
        for index, (one, other) in enumerate(zip(before["tools"], after["tools"], strict=False)):
            if canonical(one) != canonical(other):
                differing.append(
                    {
                        "index": index,
                        "name": one.get("name"),
                        "before": one,
                        "after": other,
                    }
                )
        record["differences"] = differing[:5]
        record["difference_count"] = len(differing)
    print(json.dumps(record, indent=1))
    if arguments.out is not None:
        arguments.out.write_text(json.dumps(record, indent=1), encoding="utf-8")
    return 0 if record["identical"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
