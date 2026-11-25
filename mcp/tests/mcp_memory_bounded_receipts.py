"""Read completed stdio workload receipts and Linux memory limits."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

CYCLES = 3
LIMITS_KIB = {"growth": 65_536, "after_call": 1_048_576, "vmhwm": 1_572_864}
_SCOPE_EVENTS = ("max", "oom", "oom_kill", "oom_group_kill")
_PAIR_FIELDS = ("pid", "cycle", "position", "tool", "variant", "log_sequence")
_PROOF_TOOLS = ("memory_quality_check", "knowledge_write")


class CaseIdentity(Protocol):
    @property
    def sequence(self) -> int: ...

    @property
    def tool(self) -> str: ...

    @property
    def variant(self) -> str: ...


def requires_read_continuation(case: CaseIdentity) -> bool:
    # The first dependent read opens a page because the cycle has no earlier dependent read.
    return case.variant in ("source_context/continuation", "invariant/continuation")


@dataclass(frozen=True)
class WorkloadReceipt:
    calls: int
    pid: int
    serving_build: dict[str, Any]
    previewed_paths: tuple[str, ...]
    written_paths: tuple[str, ...]
    quality_trees: tuple[str, ...]
    readings_kib: dict[str, int]
    limits_kib: dict[str, int]
    scope_events: dict[str, int]
    exceeded: tuple[str, ...]


def _json_file(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_bytes())
    assert isinstance(value, dict), path
    return value


def _identity(row: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(row.get(field) for field in _PAIR_FIELDS)


def _assert_read_and_task_completion(
    case: CaseIdentity, start: dict[str, Any], end: dict[str, Any]
) -> None:
    location = f"cycle {end['cycle']} position {end['position']}"
    if case.tool == "task_doc":
        assert end.get("ok") is True and end.get("state") is None and end.get("refusal") is None, (
            f"{location}: task_doc did not succeed"
        )
    elif case.tool == "knowledge_read":
        expected_view = case.variant.split("/", 1)[0]
        assert start.get("actual_view") == end.get("actual_view") == expected_view, (
            f"{location}: expected knowledge_read view {expected_view}"
        )
        continuation = requires_read_continuation(case)
        assert (
            start.get("requested_continuation") is continuation
            and end.get("requested_continuation") is continuation
        ), f"{location}: expected requested_continuation={continuation}"
        assert (
            end.get("ok") is True
            and end.get("state") in ("view", "page")
            and end.get("refusal") is None
        ), f"{location}: knowledge_read did not return a successful view or page"


def _reply_pairs(capture: Path, cases: Sequence[CaseIdentity]) -> list[dict[str, Any]]:
    rows = [json.loads(line) for line in (capture / "calls.jsonl").read_bytes().splitlines()]
    assert [row["event"] for row in rows] == ["call-start", "call-end"] * (1 + CYCLES * len(cases))
    starts, ends = rows[::2], rows[1::2]
    assert [_identity(row) for row in starts] == [_identity(row) for row in ends]
    binding, *workload = ends
    assert binding["tool"] == "server_info" and binding["ok"] is True
    assert binding["pid"] == int((capture / "server.pid").read_text(encoding="utf-8"))
    expected = [
        (binding["pid"], cycle, position, case.tool, case.variant, case.sequence)
        for cycle in range(1, CYCLES + 1)
        for position, case in enumerate(cases, 1)
    ]
    assert [_identity(row) for row in workload] == expected
    for row in ends:
        assert not row.get("invalid_payload") and not row.get("tool_error"), row
        assert not row.get("detail_unavailable") and not row.get("log_error"), row
    for case, start, end in zip(tuple(cases) * CYCLES, starts[1:], workload, strict=True):
        _assert_read_and_task_completion(case, start, end)
    return ends


def _scope_events(replies: list[dict[str, Any]]) -> dict[str, int]:
    observed = [row["cgroup"]["events"] for row in replies if row.get("cgroup") is not None]
    if not observed:
        return {}
    assert len(observed) == len(replies), "A capped capture lost its cgroup readings"
    counts = {event: max(row[event] for row in observed) for event in _SCOPE_EVENTS}
    assert not any(counts.values()), counts
    return counts


def _readings(replies: list[dict[str, Any]], cycle_size: int) -> dict[str, int]:
    rss_swap, high_water = [], []
    for row in replies:
        memory = row["memory_kib"]
        rss, swap, hwm = (memory[key] for key in ("VmRSS", "VmSwap", "VmHWM"))
        assert all(type(value) is int and value >= 0 for value in (rss, swap, hwm))
        assert rss + swap == memory["VmRSS+VmSwap"]
        rss_swap.append(rss + swap)
        high_water.append(hwm)
    return {
        "growth": rss_swap[-1] - rss_swap[2 * cycle_size],
        "after_call": max(rss_swap),
        "vmhwm": max(high_water),
    }


def _full_replies(
    replies: list[dict[str, Any]], logs: Path, logical_root: Path
) -> list[dict[str, Any]]:
    folded = [row for row in replies if row["tool"] in _PROOF_TOOLS]
    paths = sorted(path for name in _PROOF_TOOLS for path in logs.glob(f"*/*-{name}-*.json"))
    assert len(folded) == len(paths), "The capture lost full quality or write replies"
    details = []
    for row, path in zip(folded, paths, strict=True):
        result = _json_file(path)
        assert result["operation"] == row["tool"] and result["ok"] is row["ok"]
        assert result.get("state") == row.get("state")
        if result["operation"] == "knowledge_write":
            assert result["reportPath"] == row["report_path"]
            report = logs.parent / Path(result["reportPath"]).relative_to(logical_root)
            assert report.resolve().is_relative_to(logs.resolve())
            full = _json_file(report)
            assert full["state"] == result["state"] and full["written"] == result["written"]
            result = {**result, "records": full["records"]}
        details.append(result)
    return details


def _weakness_path(reply: dict[str, Any], handoff: str, companion_paths: tuple[str, ...]) -> str:
    [record] = reply["records"]
    assert record["kind"] == "weakness" and record["action"] == "created"
    assert record["revision"] == 1 and record["handoffEntry"] == handoff
    path = record["path"]
    assert path == f"knowledge/weaknesses/{record['id']}-{handoff}.json"
    assert sorted(reply["written"]) == sorted((path, *companion_paths))
    return path


def _completed_cycles(
    details: list[dict[str, Any]], history: str
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    assert len(details) == CYCLES * 3
    previews: list[str] = []
    trees: list[str] = []
    paths: list[str] = []
    cycles = zip(details[::3], details[1::3], details[2::3], strict=True)
    for cycle, (quality, preview, write) in enumerate(cycles, 1):
        assert quality["operation"] == "memory_quality_check"
        assert quality["scopeAuthority"] == "leaf-candidate"
        assert quality["knowledgeGate"]["worklistState"] == "complete"
        assert preview["operation"] == "knowledge_write" and preview["state"] == "planned"
        assert write["operation"] == "knowledge_write" and write["state"] == "written"
        assert preview["ok"] is True and write["ok"] is True
        previews.append(_weakness_path(preview, "memory-sample-preview", (history,)))
        companions = (history, ".gitignore") if cycle == 1 else (history,)
        paths.append(_weakness_path(write, f"memory-sample-{cycle}", companions))
        trees.append(quality["finalFullCatalog"]["memoryTree"])
    assert len(set(paths)) == len(set(trees)) == CYCLES
    return tuple(previews), tuple(paths), tuple(trees)


def read_completed_workload(
    capture: Path, logs: Path, logical_root: Path, cases: Sequence[CaseIdentity], history: str
) -> WorkloadReceipt:
    replies = _reply_pairs(capture, cases)
    info = _json_file(capture / "server-info.json")
    assert info["ok"] is True and info["transport"] == "stdio"
    previews, paths, trees = _completed_cycles(_full_replies(replies, logs, logical_root), history)
    readings = _readings(replies, len(cases))
    exceeded = tuple(name for name, limit in LIMITS_KIB.items() if readings[name] > limit)
    return WorkloadReceipt(
        calls=len(replies) - 1,
        pid=replies[0]["pid"],
        serving_build=info["servingBuild"],
        previewed_paths=previews,
        written_paths=paths,
        quality_trees=trees,
        readings_kib=readings,
        limits_kib=LIMITS_KIB.copy(),
        scope_events=_scope_events(replies),
        exceeded=exceeded,
    )
