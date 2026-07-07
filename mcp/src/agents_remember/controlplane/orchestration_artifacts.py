"""Orchestration artifact and escalation packet helpers."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

OrchestrationRole = Literal["designer", "strategist", "orchestrator", "manager", "worker", "reviewer"]
EscalationReason = Literal["blocked", "plan-delta", "quality-failure", "missing-artifact"]

_ROLE_ESCALATION: dict[OrchestrationRole, OrchestrationRole | Literal["developer"]] = {
    "worker": "manager",
    "manager": "orchestrator",
    "orchestrator": "developer",
    "designer": "orchestrator",
    "strategist": "orchestrator",
    "reviewer": "orchestrator",
}
_SAFE_STEM = re.compile(r"[^A-Za-z0-9_.-]+")


class TurnReportArtifact(BaseModel):
    """The durable worker hand-off artifact required by the L2 worker job."""

    model_config = ConfigDict(extra="forbid")

    leafId: str
    title: str
    path: str
    templatePath: str


class MasterHandoverPacket(BaseModel):
    """Manager-to-orchestrator handover packet shaped like the L2 template."""

    model_config = ConfigDict(extra="forbid")

    masterId: str
    title: str
    manager: str
    integrationBranch: str
    base: str
    verdict: str
    verdictOutcome: Literal["pass", "pass-with-notes"]
    written: str
    changeSetSummary: list[str] = Field(default_factory=list)
    leavesLanded: list[str] = Field(default_factory=list)
    requirementsAddressed: str
    carryOverState: list[str] = Field(default_factory=list)
    knownFollowUps: list[str] = Field(default_factory=list)


class EscalationPacket(BaseModel):
    """Structured escalation that preserves the worker -> manager -> orchestrator ladder."""

    model_config = ConfigDict(extra="forbid")

    fromRole: OrchestrationRole
    toRole: OrchestrationRole | Literal["developer"]
    reason: EscalationReason
    subject: str
    summary: str
    artifactPath: str | None = None


def template_path(runtime_root: Path, name: str) -> Path:
    """Return one orchestration template path under the runtime skill tree."""
    return runtime_root / "skills" / "l-01-agent-lifecycles" / "templates" / name


def turn_report_artifact(task_root: Path, leaf_id: str, title: str, runtime_root: Path) -> TurnReportArtifact:
    """Build the standard worker turn-report artifact location."""
    stem = _SAFE_STEM.sub("-", leaf_id).strip("-") or "leaf"
    return TurnReportArtifact(
        leafId=leaf_id,
        title=title,
        path=str(task_root / "notes" / "reports" / f"{stem}-worker-report.md"),
        templatePath=str(template_path(runtime_root, "turn-report.md")),
    )


def escalation_packet(
    *,
    from_role: OrchestrationRole,
    reason: EscalationReason,
    subject: str,
    summary: str,
    artifact_path: str | None = None,
) -> EscalationPacket:
    """Route an escalation exactly one rung up the ladder."""
    return EscalationPacket(
        fromRole=from_role,
        toRole=_ROLE_ESCALATION[from_role],
        reason=reason,
        subject=subject,
        summary=summary,
        artifactPath=artifact_path,
    )


def render_master_handover_packet(packet: MasterHandoverPacket) -> str:
    """Render a manager handover packet in the shape of the L2 template."""
    return "\n".join(
        [
            f"# Master Handover - {packet.masterId} - {packet.title}",
            "",
            "| Field | Value |",
            "| --- | --- |",
            f"| master | {packet.masterId} |",
            f"| manager | {packet.manager} |",
            f"| integration branch | {packet.integrationBranch} |",
            f"| base | {packet.base} |",
            f"| verdict | {packet.verdict} |",
            f"| verdict outcome | {packet.verdictOutcome} |",
            f"| written | {packet.written} |",
            "",
            "## Change-Set Summary",
            *[f"- {item}" for item in packet.changeSetSummary],
            f"- Leaves landed: {', '.join(packet.leavesLanded) if packet.leavesLanded else 'none'}",
            "",
            "## Requirements / Steps Completion",
            f"- All master requirements addressed: {packet.requirementsAddressed}",
            "",
            "## Carry-Over State (for the orchestrator's master -> super C-11)",
            *[f"- {item}" for item in packet.carryOverState],
            "",
            "## Known Follow-Ups",
            *[f"- {item}" for item in (packet.knownFollowUps or ['none'])],
            "",
            "## Reachability",
            "- Manager seat stays reachable until the series retires.",
            "",
        ]
    )
