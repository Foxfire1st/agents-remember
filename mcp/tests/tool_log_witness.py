"""L108 tool-log sandbox witness (MIK-R108 E4/E7, rule 3).

Reproducible repository-sandbox-tooling run: hermetic EntryPointWorlds with
the derived repository layout bridged onto the fixture repositories, real
leaf enclosures opened through the real ``worktree_start``, worker calls
with the ambient lifecycle promoted onto the contract (enclosure logs) and
controller calls without it (coordination-root logs), then the real
terminating operations through the production MCP entry.

World A proves successful public abandon: force-abandon removes the
enclosure whole, its line lands at the coordination root, and the enclosure
logs are gone and never recreated. World B proves successful public
cleanup: preview/apply/integrate/finalize/cleanup complete, the group is
gone, every line lands at the root, and a final outlived-group call logs at
the root without recreating the enclosure folder.

The driver asserts every success proof and exits nonzero when one fails, so
a frozen evidence file only ever binds a passing run. It records
timestamped before/after directory snapshots, every call's outcome and log
location, run provenance (code head, tracked diff, per-file digests,
invocation, environment), and the final repository state.

Run from the ``108_a-tool-answer-holds-what-the-caller--f57430e440`` code
root::

    PYTHONPATH=mcp/test_support:mcp/src:mcp/tests \\
        .../.venv/bin/python mcp/tests/tool_log_witness.py <evidence-out.json>
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agents_remember.application.tool_answer_log import ENCLOSURE_LOGS_DIRNAME
from agents_remember.worktrees.worktree_contract import load_contract
from tool_entry_point_world import REPO, EntryPointWorld


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _snapshot(root: Path) -> dict[str, Any]:
    """Every file under ``root`` with size and mtime; missing stays missing."""
    files: dict[str, dict[str, Any]] = {}
    if root.is_dir():
        for path in sorted(root.rglob("*")):
            if path.is_file():
                stat = path.stat()
                files[path.relative_to(root).as_posix()] = {
                    "bytes": stat.st_size,
                    "mtime": stat.st_mtime,
                }
    return {"exists": root.exists(), "is_dir": root.is_dir(), "files": files}


def _set_binding(*, role: str, agent_id: str, request_id: str, refs: dict[str, Any]) -> None:
    os.environ["AR_PASEO_AGENT_ID"] = agent_id
    os.environ["AR_ROLE"] = role
    os.environ["AR_REQUEST_ID"] = request_id
    os.environ["AR_REPORT_PATH"] = "/tmp/l108-witness-report.md"
    for name in ("AR_SPRINT_REF", "AR_MASTER_REF", "AR_TASK_REF"):
        os.environ.pop(name, None)
    for name, ref in refs.items():
        os.environ[name] = json.dumps(ref)


def _clear_binding() -> None:
    for name in (
        "AR_PASEO_AGENT_ID",
        "AR_ROLE",
        "AR_REQUEST_ID",
        "AR_REPORT_PATH",
        "AR_SPRINT_REF",
        "AR_MASTER_REF",
        "AR_TASK_REF",
    ):
        os.environ.pop(name, None)


def _git_state(repo: Path) -> dict[str, Any]:
    status = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain"],
        check=True,
        capture_output=True,
        text=True,
    )
    head = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    return {"head": head.stdout.strip(), "porcelain": status.stdout}


def _record_call(
    world: Any, calls: list[dict[str, Any]], name: str, args: dict[str, Any], plane: str
) -> None:
    """One production-entry call with its outcome and log location."""
    status, structured = world.call(name, args)
    answer = structured if isinstance(structured, dict) else {}
    calls.append(
        {
            "plane": plane,
            "tool": name,
            "status": status,
            "ok": answer.get("ok"),
            "code": answer.get("code"),
            "log": answer.get("log"),
            "log_error": answer.get("logError"),
            "raised_detail": answer.get("detail") if status != "RETURNED" else None,
        }
    )


def _bridge_derived_layout(world: EntryPointWorld) -> None:
    """Bridge the derived repository layout onto the fixture repositories.

    Settings-derived repository scopes resolve to ``workspace/<repo-id>``
    (the path key is decorative there); the fixture lays its git checkouts
    under the coordination root. The symlink makes the configured authority
    check see the fixture's physical repository, with no product change.
    """
    link = world.root / "repo-a"
    if not link.exists():
        link.symlink_to(world.code, target_is_directory=True)


def _checks_destinations(
    calls: list[dict[str, Any]], enclosure_logs: Path, coord_logs: Path
) -> dict[str, bool]:
    """Worker lines land in the enclosure; controller lines at the root."""
    worker = [
        entry for entry in calls if entry["plane"] == "worker" and entry["status"] == "RETURNED"
    ]
    controller = [
        entry for entry in calls if entry["plane"] == "controller" and entry["status"] == "RETURNED"
    ]
    return {
        "worker_calls_logged": len(worker) == 3,
        "worker_logs_in_enclosure": bool(worker)
        and all(
            isinstance(entry["log"], str) and entry["log"].startswith(enclosure_logs.as_posix())
            for entry in worker
        ),
        "controller_calls_logged": len(controller) == 3,
        "controller_logs_at_root": bool(controller)
        and all(
            isinstance(entry["log"], str) and entry["log"].startswith(coord_logs.as_posix())
            for entry in controller
        ),
    }


def _read_log_lines(coord_logs: Path) -> list[dict[str, Any]]:
    """Parse every complete daily tool line under one root log folder."""
    parsed: list[dict[str, Any]] = []
    for log_file in sorted(coord_logs.glob("ar-tools-*.jsonl")):
        for line in log_file.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                parsed.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return parsed


def _detail_for_line(coord_logs: Path, day: str, line: dict[str, Any]) -> dict[str, Any] | None:
    """Inspect the whole detail the parsed line's detail name locates."""
    name = line.get("detail")
    if not isinstance(name, str):
        return None
    detail = coord_logs / f"ar-tools-{day}" / name
    if not detail.is_file():
        return None
    try:
        return json.loads(detail.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _persisted_terminating_proof(
    coord_logs: Path, calls: list[dict[str, Any]], *tools: str
) -> dict[str, Any]:
    """Retain bounded parsed evidence of the actual root terminating lines.

    For each named terminating tool, keep the parsed daily line that names
    its successful completion plus the inspected whole detail that line
    locates. File inventories and response detail paths alone are not
    persisted-record proof; these parsed records are.
    """
    proof: dict[str, Any] = {}
    for tool in tools:
        found: dict[str, Any] | None = None
        whole: dict[str, Any] | None = None
        for log_file in sorted(coord_logs.glob("ar-tools-*.jsonl")):
            day = log_file.name.removeprefix("ar-tools-").removesuffix(".jsonl")
            for line in log_file.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if entry.get("tool") == tool and entry.get("ok") is True:
                    found = entry
                    whole = _detail_for_line(coord_logs, day, entry)
        proof[tool] = {"line": found, "detail_whole": whole}
    return proof


def _run_provenance(code_root: Path) -> dict[str, Any]:
    """Bind this run to the exact successor bytes it witnesses."""
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], check=True, capture_output=True, text=True, cwd=str(code_root)
    ).stdout.strip()
    diff = subprocess.run(
        ["git", "diff"], check=True, capture_output=True, cwd=str(code_root)
    ).stdout
    status = subprocess.run(
        ["git", "status", "--short"],
        check=True,
        capture_output=True,
        text=True,
        cwd=str(code_root),
    ).stdout
    files: dict[str, str] = {}
    for line in status.splitlines():
        path = line[3:]
        data = (code_root / path).read_bytes()
        files[path] = hashlib.sha256(data).hexdigest()
    return {
        "code_head": head,
        "tracked_diff_sha256": hashlib.sha256(diff).hexdigest(),
        "git_status": status,
        "file_sha256": files,
        "invocation": " ".join(sys.argv),
        "cwd": str(code_root),
        "python": sys.version.split()[0],
        "pythonpath": os.environ.get("PYTHONPATH"),
    }


def _witness_abandon_world() -> dict[str, Any]:
    """World A: worker calls, controller calls, successful public abandon."""
    evidence: dict[str, Any] = {}
    world = EntryPointWorld()
    try:
        _bridge_derived_layout(world)
        world.build()
        contract = load_contract(world.contract)
        assert contract.worktree_group is not None
        group = Path(contract.worktree_group)
        enclosure_logs = group / ENCLOSURE_LOGS_DIRNAME
        coord_logs = world.coord / "logs" / "mcp"
        evidence["enclosure_group"] = group.as_posix()
        evidence["enclosure_logs_made_with_enclosure"] = enclosure_logs.is_dir()

        evidence["before"] = {
            "at": _utc_now(),
            "enclosure_logs": _snapshot(enclosure_logs),
            "coord_logs": _snapshot(coord_logs),
        }

        calls: list[dict[str, Any]] = []
        worker_refs = {
            "AR_SPRINT_REF": {"repository": REPO, "path": "sprint/task.json"},
            "AR_MASTER_REF": {"repository": REPO, "path": "m/task.json"},
            "AR_TASK_REF": {"repository": REPO, "path": "m/1_leaf.json"},
        }
        _set_binding(
            role="worker",
            agent_id=str(uuid.uuid4()),
            request_id=str(uuid.uuid4()),
            refs=worker_refs,
        )
        _record_call(world, calls, "ping", {}, "worker")
        _record_call(world, calls, "server_info", {}, "worker")
        _record_call(
            world,
            calls,
            "worktree_status",
            {"repo_id": REPO, "contract_path": str(world.contract)},
            "worker",
        )
        # The controller plane has no enclosure binding: its calls log root.
        manager_refs = {
            "AR_SPRINT_REF": {"repository": REPO, "path": "sprint/task.json"},
            "AR_MASTER_REF": {"repository": REPO, "path": "m/task.json"},
        }
        _set_binding(
            role="manager",
            agent_id=str(uuid.uuid4()),
            request_id=str(uuid.uuid4()),
            refs=manager_refs,
        )
        _record_call(
            world,
            calls,
            "worktree_closeout_preview",
            {"contract_path": str(world.contract)},
            "controller",
        )
        _record_call(
            world, calls, "worktree_sync", {"contract_path": str(world.contract)}, "controller"
        )
        _record_call(
            world,
            calls,
            "task_doc",
            {"repo_id": REPO, "operation": "get", "task_name": "m", "slug": "1_leaf"},
            "controller",
        )
        _clear_binding()
        evidence["calls"] = calls
        evidence["destination_checks"] = _checks_destinations(calls, enclosure_logs, coord_logs)
        evidence["after_calls"] = {
            "at": _utc_now(),
            "enclosure_logs": _snapshot(enclosure_logs),
            "coord_logs": _snapshot(coord_logs),
        }

        abandon: list[dict[str, Any]] = []
        _record_call(
            world,
            abandon,
            "worktree_abandon",
            {"contract_path": str(world.contract), "force": True},
            "controller",
        )
        evidence["abandon"] = abandon
        (entry,) = abandon
        evidence["abandon_checks"] = {
            "returned_ok": entry["status"] == "RETURNED" and entry["ok"] is True,
            "group_removed": not group.exists(),
            "enclosure_logs_gone": not enclosure_logs.exists(),
            "log_at_coord_root": isinstance(entry["log"], str)
            and entry["log"].startswith(coord_logs.as_posix()),
        }
        # Parsed persisted proof is retained before the world is closed:
        # temporary paths are gone by the time the evidence is written.
        evidence["persisted_proof"] = _persisted_terminating_proof(
            coord_logs, abandon, "worktree_abandon"
        )
        evidence["after_abandon"] = {
            "at": _utc_now(),
            "enclosure_logs": _snapshot(enclosure_logs),
            "coord_logs": _snapshot(coord_logs),
        }
        evidence["repository_state"] = {
            "code": _git_state(world.code),
            "memory": _git_state(world.memory),
        }
    finally:
        world.close()
    return evidence


def _witness_cleanup_world() -> dict[str, Any]:
    """World B: the full terminal chain to successful public cleanup."""
    evidence: dict[str, Any] = {}
    world = EntryPointWorld()
    try:
        _bridge_derived_layout(world)
        world.build()
        contract = load_contract(world.contract)
        assert contract.worktree_group is not None
        group = Path(contract.worktree_group)
        enclosure_logs = group / ENCLOSURE_LOGS_DIRNAME
        coord_logs = world.coord / "logs" / "mcp"
        evidence["enclosure_group"] = group.as_posix()

        manager_refs = {
            "AR_SPRINT_REF": {"repository": REPO, "path": "sprint/task.json"},
            "AR_MASTER_REF": {"repository": REPO, "path": "m/task.json"},
        }
        _set_binding(
            role="manager",
            agent_id=str(uuid.uuid4()),
            request_id=str(uuid.uuid4()),
            refs=manager_refs,
        )
        evidence["before"] = {
            "at": _utc_now(),
            "enclosure_logs": _snapshot(enclosure_logs),
            "coord_logs": _snapshot(coord_logs),
        }
        chain: list[dict[str, Any]] = []
        message = {
            "contract_path": str(world.contract),
            "memory_commit_message": "witness closeout",
        }
        _record_call(world, chain, "worktree_closeout_preview", dict(message), "controller")
        _record_call(
            world,
            chain,
            "worktree_closeout_apply",
            dict(message, intent_note="witness terminal closeout"),
            "controller",
        )
        _record_call(
            world, chain, "worktree_integrate", {"contract_path": str(world.contract)}, "controller"
        )
        _record_call(
            world,
            chain,
            "lifecycle_finalize_task",
            {"contract_path": str(world.contract)},
            "controller",
        )
        _record_call(
            world, chain, "worktree_cleanup", {"contract_path": str(world.contract)}, "controller"
        )
        _clear_binding()
        evidence["chain"] = chain
        by_tool = {entry["tool"]: entry for entry in chain}
        evidence["cleanup_checks"] = {
            "preview_ok": by_tool["worktree_closeout_preview"].get("ok") is True,
            "apply_ok": by_tool["worktree_closeout_apply"].get("ok") is True,
            "integrate_ok": by_tool["worktree_integrate"].get("ok") is True,
            "finalize_ok": by_tool["lifecycle_finalize_task"].get("status") == "RETURNED",
            "cleanup_ok": by_tool["worktree_cleanup"].get("status") == "RETURNED"
            and by_tool["worktree_cleanup"].get("ok") is True,
            "group_removed": not group.exists(),
            "enclosure_logs_gone": not enclosure_logs.exists(),
            "all_logs_at_coord_root": all(
                isinstance(entry["log"], str) and entry["log"].startswith(coord_logs.as_posix())
                for entry in chain
                if entry["status"] == "RETURNED"
            ),
        }
        evidence["after_chain"] = {
            "at": _utc_now(),
            "enclosure_logs": _snapshot(enclosure_logs),
            "coord_logs": _snapshot(coord_logs),
        }
        # The server outlives the fully removed group: the next call logs
        # at the root and never makes the enclosure folder again.
        status, structured = world.call("ping", {})
        outlived = structured if isinstance(structured, dict) else {}
        evidence["outlived_group"] = {
            "status": status,
            "log": outlived.get("log"),
            "log_error": outlived.get("logError"),
            "enclosure_logs_recreated": enclosure_logs.is_dir(),
            "group_exists": group.exists(),
        }
        # Parsed persisted proof is retained before the world is closed.
        evidence["persisted_proof"] = _persisted_terminating_proof(
            coord_logs, chain, "worktree_cleanup", "worktree_abandon"
        )
        evidence["repository_state"] = {
            "code": _git_state(world.code),
            "memory": _git_state(world.memory),
        }
    finally:
        world.close()
    return evidence


def _check_abandon_world(abandon_world: dict[str, Any], failures: list[str]) -> None:
    """Collect abandon-world success-proof failures."""
    for name, value in abandon_world["destination_checks"].items():
        if not value:
            failures.append(f"abandon-world destination {name}")
    for name, value in abandon_world["abandon_checks"].items():
        if not value:
            failures.append(f"abandon {name}")


def _check_cleanup_world(cleanup_world: dict[str, Any], failures: list[str]) -> None:
    """Collect cleanup-world success-proof failures."""
    for name, value in cleanup_world["cleanup_checks"].items():
        if not value:
            failures.append(f"cleanup-chain {name}")
    outlived = cleanup_world["outlived_group"]
    if outlived["status"] != "RETURNED" or not isinstance(outlived["log"], str):
        failures.append("outlived-group call unlogged")
    if outlived["enclosure_logs_recreated"]:
        failures.append("outlived-group recreated enclosure logs")


def _check_clean_repos(
    abandon_world: dict[str, Any], cleanup_world: dict[str, Any], failures: list[str]
) -> None:
    """Collect dirty-worktree failures across both witness worlds."""
    for label, state in (
        ("abandon-world code", abandon_world["repository_state"]["code"]),
        ("abandon-world memory", abandon_world["repository_state"]["memory"]),
        ("cleanup-world code", cleanup_world["repository_state"]["code"]),
        ("cleanup-world memory", cleanup_world["repository_state"]["memory"]),
    ):
        if state["porcelain"] != "":
            failures.append(f"{label} worktree not clean")


def _check_persisted_proof(world: dict[str, Any], *tools: str, failures: list[str]) -> None:
    """Collect missing parsed persisted-record failures."""
    proof = world.get("persisted_proof") or {}
    for tool in tools:
        entry = proof.get(tool) or {}
        line = entry.get("line")
        whole = entry.get("detail_whole")
        if not isinstance(line, dict) or line.get("ok") is not True:
            failures.append(f"persisted root line for {tool}")
        if not isinstance(whole, dict):
            failures.append(f"persisted inspected detail for {tool}")


def _assert_witness(abandon_world: dict[str, Any], cleanup_world: dict[str, Any]) -> None:
    """Fail loudly unless every success proof holds; a frozen file binds a pass."""
    failures: list[str] = []
    _check_abandon_world(abandon_world, failures)
    _check_cleanup_world(cleanup_world, failures)
    _check_clean_repos(abandon_world, cleanup_world, failures)
    _check_persisted_proof(abandon_world, "worktree_abandon", failures=failures)
    _check_persisted_proof(cleanup_world, "worktree_cleanup", failures=failures)
    if failures:
        raise SystemExit(f"witness success proof failed: {sorted(failures)}")


def main(out_path: str) -> int:
    code_root = Path(__file__).resolve().parents[2]
    evidence: dict[str, Any] = {
        "witness": "l108-tool-log-sandbox",
        "started_at": _utc_now(),
    }
    abandon_world = _witness_abandon_world()
    cleanup_world = _witness_cleanup_world()
    _assert_witness(abandon_world, cleanup_world)
    evidence["abandon_world"] = abandon_world
    evidence["cleanup_world"] = cleanup_world
    evidence["provenance"] = _run_provenance(code_root)
    evidence["finished_at"] = _utc_now()
    out = Path(out_path)
    out.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    digest = hashlib.sha256(out.read_bytes()).hexdigest()
    print(f"evidence: {out} SHA-256 {digest}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
