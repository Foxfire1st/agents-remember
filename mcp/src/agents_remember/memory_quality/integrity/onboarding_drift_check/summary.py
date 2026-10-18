"""Bounded drift summary helpers for context packets."""

from __future__ import annotations

import hashlib
import json
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agents_remember.kernel.atomic_write import atomic_write_text
from agents_remember.kernel.primitives.drift_snapshot import drift_snapshot_path
from agents_remember.kernel.primitives.observer_paths import DRIFT_SNAPSHOT_SCHEMA
from agents_remember.memory_quality.converted_check import (
    KnowledgeBasePort,
    converted_knowledge_check,
)
from agents_remember.memory_quality.integrity.onboarding_drift_check.git_ops import (
    current_branch_name,
)
from agents_remember.memory_quality.integrity.onboarding_drift_check.models import (
    DriftSummaryPacket,
)
from agents_remember.memory_quality.integrity.onboarding_drift_check.report import (
    resolve_report_path,
)
from agents_remember.memory_quality.knowledge_validator.trees import KnowledgeTreeReadError
from agents_remember.memory_quality.reference_state import (
    STALE_REFERENCE_CHECK,
    is_converted_memory,
)
from agents_remember.worktrees.cutover_lock import legacy_format_refusal

#: The converted-tree result names what it measured (MIK-R44 rule 2).
CONVERTED_BASIS = "converted-references"


@dataclass(frozen=True)
class DriftSummaryOutput:
    """Optional report/row materialization requested by an internal caller."""

    report_path: Path | None = None
    include_rows: bool = False
    write_report: bool = True


def not_checked() -> DriftSummaryPacket:
    return {"status": "notChecked"}


def run_drift_summary(
    *,
    code_repository_root: Path,
    context: Any,
    detail_limit: int = 10,
    output: DriftSummaryOutput | None = None,
    base: KnowledgeBasePort | None = None,
) -> DriftSummaryPacket:
    if not context.onboarding_root.exists():
        return {
            "status": "error",
            "error": f"onboarding root does not exist: {context.onboarding_root}",
        }
    memory_root = Path(getattr(context, "memory_root", None) or context.onboarding_root.parent)
    if is_converted_memory(memory_root):
        onboarding = Path(context.onboarding_root)
        if not onboarding.is_dir():
            return {
                "status": "error",
                "error": f"onboarding root is not a readable directory: {context.onboarding_root}",
            }
        return _run_converted_summary(
            code_repository_root=code_repository_root,
            context=context,
            detail_limit=detail_limit,
            base=base,
            output=output,
        )

    return {
        "status": "error",
        "error": legacy_format_refusal(
            memory_root, operation="the drift check", subject=f"memory root {memory_root}"
        ),
    }


def _violation_sample(finding: dict[str, Any]) -> dict[str, Any]:
    """The refusing validator finding as the converted result samples it (MIK-R44 rule 2)."""

    return {
        "path": finding.get("path"),
        "field": finding.get("field"),
        "rule": finding.get("rule"),
        "message": finding.get("message"),
    }


def _run_converted_summary(
    *,
    code_repository_root: Path,
    context: Any,
    detail_limit: int,
    base: KnowledgeBasePort | None,
    output: DriftSummaryOutput | None = None,
) -> DriftSummaryPacket:
    """Build the drift summary from `converted_knowledge_check` and nothing else.

    The legacy classification is never called for a converted tree (MIK-R44 rule 1):
    a converted card has no metadata table, so legacy discovery would answer an
    unread "checked, 0" instead of measuring the tree's reference anchors. The
    landing report is likewise not computed here: this summary surfaces stale
    anchors and validator findings only, and the landing census belongs to the
    quality check that carries the leaf's admitted base.
    """

    memory_root = Path(getattr(context, "memory_root", None) or context.onboarding_root.parent)
    output = output or DriftSummaryOutput()
    try:
        result = converted_knowledge_check(
            memory_root, code_repository_root, base=base, include_landing=False
        )
    except (KnowledgeTreeReadError, ValueError, OSError) as error:
        return {
            "status": "error",
            "error": f"converted drift measurement failed for {memory_root}: {error}",
        }
    states = result["referenceStates"]
    current = int(states.get("current", 0))
    stale = int(states.get("stale", 0))
    refusals = result["findings"]
    report_only = result["reportOnlyFindings"]
    stale_findings = [
        finding for finding in report_only if finding.get("check") == STALE_REFERENCE_CHECK
    ]
    packet: DriftSummaryPacket = {
        "status": "checked",
        "basis": CONVERTED_BASIS,
        "memoryRoot": memory_root.resolve().as_posix(),
        "codeRoot": code_repository_root.resolve().as_posix(),
        "sidecarCount": int(result["sidecarCount"]),
        "count": current + stale,
        "states": {"current": current, "stale": stale},
        "actionableCount": stale,
        "actionableSample": stale_findings[:detail_limit],
        "violationCount": len(refusals),
        "violationSample": [_violation_sample(finding) for finding in refusals[:detail_limit]],
        "reportOnlyCount": len(report_only),
        "unreadableSidecars": list(result["unreadableSidecars"]),
    }
    report_path = output.report_path or resolve_report_path(
        None,
        context.coordination_root,
        context.temp_root,
        code_repository_root,
        context.memory_root,
    )
    if output.write_report:
        _write_converted_report(packet, report_path)
        _write_converted_snapshot(
            code_repository_root,
            context,
            packet,
            actionable_fingerprint=(
                _actionable_fingerprint(stale_findings)
                if not result["unreadableSidecars"]
                else None
            ),
            report_path=report_path,
        )
        packet["reportPath"] = report_path.as_posix()
    if output.include_rows:
        packet["rows"] = [*stale_findings, *refusals]
    return packet


def _write_converted_report(packet: DriftSummaryPacket, report_path: Path) -> None:
    """Render the converted Markdown drift report from the same measured result."""

    states = packet.get("states", {})
    lines = [
        "# Onboarding drift (converted references)",
        "",
        f"basis: {packet.get('basis')}",
        f"memory root: {packet.get('memoryRoot')}",
        f"code root: {packet.get('codeRoot')}",
        f"sidecars read: {packet.get('sidecarCount')}",
        f"anchors observed: {packet.get('count')}",
        f"current: {states.get('current', 0)}, stale: {states.get('stale', 0)}",
        f"validator violations (refusing): {packet.get('violationCount')}",
        f"validator findings (report-only): {packet.get('reportOnlyCount')}",
        "",
    ]
    actionable = packet.get("actionableSample", [])
    lines.append(f"## Stale anchors ({packet.get('actionableCount')})")
    if not actionable and packet.get("actionableCount"):
        lines.append(f"_showing 0 of {packet.get('actionableCount')} (detail limit)_")
    for entry in actionable:
        lines.append(
            f"- {entry.get('path')}: {entry.get('field')}: "
            f"target {entry.get('target')} is {entry.get('state')}"
        )
    if packet.get("actionableCount", 0) > len(actionable):
        lines.append(f"_showing {len(actionable)} of {packet.get('actionableCount')}_")
    lines.append("")
    violations = packet.get("violationSample", [])
    lines.append(f"## Validator violations ({packet.get('violationCount')})")
    for entry in violations:
        lines.append(f"- {entry.get('path')}: {entry.get('field')}: {entry.get('rule')}")
        lines.append(f"  {entry.get('message')}")
    if packet.get("violationCount", 0) > len(violations):
        lines.append(f"_showing {len(violations)} of {packet.get('violationCount')}_")
    lines.append("")
    unreadable = packet.get("unreadableSidecars", [])
    if unreadable:
        lines.append("## Unreadable sidecars")
        lines.extend(f"- {path}" for path in unreadable)
        lines.append("")
    atomic_write_text(report_path, "\n".join(lines))


def _write_converted_snapshot(
    code_repository_root: Path,
    context: Any,
    packet: DriftSummaryPacket,
    *,
    actionable_fingerprint: str | None,
    report_path: Path | None = None,
) -> None:
    """Persist the converted drift result as the observer snapshot, from the same result.

    The snapshot's `counts` are `current`, `stale` and `violation`, and its
    `actionableCount` is the stale-anchor count (MIK-R44 rule 6).
    """

    states = packet.get("states", {})
    branch = current_branch_name(code_repository_root)
    payload = {
        "schema": DRIFT_SNAPSHOT_SCHEMA,
        "repository": code_repository_root.name,
        "branch": branch,
        "checkedAt": datetime.now(UTC).isoformat(),
        "sourceRoot": code_repository_root.resolve().as_posix(),
        "memoryRoot": Path(context.memory_root).resolve().as_posix(),
        "basis": CONVERTED_BASIS,
        **({"reportPath": report_path.as_posix()} if report_path is not None else {}),
        "counts": {
            "current": states.get("current", 0),
            "stale": states.get("stale", 0),
            "violation": packet.get("violationCount", 0),
        },
        "actionableCount": packet.get("actionableCount", 0),
        "actionableFingerprint": actionable_fingerprint,
        "rows": [
            *packet.get("actionableSample", []),
            *packet.get("violationSample", []),
        ],
    }
    snapshot_path = drift_snapshot_path(
        context.coordination_root,
        repository=code_repository_root.name,
        branch=branch,
    )
    # The snapshot is a dashboard convenience: a write error must never fail the drift run
    # that produced it.
    with suppress(OSError):
        atomic_write_text(snapshot_path, json.dumps(payload, indent=2, default=str) + "\n")


def _actionable_fingerprint(findings: list[dict[str, Any]]) -> str:
    """One order-independent identity of complete findings, never a sampled view or clock."""
    canonical_rows = sorted(
        json.dumps(finding, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        for finding in findings
    )
    return hashlib.sha256(("[" + ",".join(canonical_rows) + "]").encode()).hexdigest()
