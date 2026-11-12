"""Render one computed projection as the Markdown a model actually reads.

This is the model-visible half of the projection. It carries the facts and the
source links; it does not carry the diagnostic half (the read plan, the selection
record, the document digest), which stays on the value for an operator or a
reviewer. That split is deliberate: identity and provenance diagnostics do not
belong in the prose unless a decision needs them, and a model that can see the
raw audit trail will treat it as instructions.

Two rules survive into the rendering:

* **Nothing is clipped.** A requirement, a preservation constraint, a forbidden
  overreach and a failure obligation are emitted exactly as the packet carries
  them. There is no length budget anywhere in this module.
* **Referenced is not the same as omitted.** Every source the projection did not
  inject is named under "Expansion references", so "not injected" is a visible
  decision with a link rather than a silent gap.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from .packets import INJECTED_PACKET_ROLES, referenced_headings, role_section
from .types import ProjectedFact, ProjectionChannel, RequirementProjection, TaskProjection

_CHANNEL_ORDER: tuple[ProjectionChannel, ...] = (
    "objective",
    "requirements",
    "acceptance",
    "scope",
    "decisions",
    "preservation",
    "evidence",
    "handoff",
)

_KIND_LABELS: Mapping[str, str] = {
    "current": "current",
    "historical": "historical",
    "proposal": "proposal",
}


def render_markdown(projection: TaskProjection) -> str:
    """The complete model-visible projection, in the operation's channel order."""

    blocks = [_header(projection), *_binding_block(projection)]
    channels = set(projection.selection.plan.channels)
    for channel in _CHANNEL_ORDER:
        if channel in channels:
            blocks.extend(_CHANNEL_RENDERERS[channel](projection))
    if projection.portfolio:
        blocks.extend(["", "## Portfolio facts", "", *_facts(projection.portfolio)])
    if projection.series:
        blocks.extend(["", "## Series context", "", *_facts(projection.series)])
    blocks.extend(_closure(projection))
    return "\n".join(blocks).rstrip() + "\n"


def _header(projection: TaskProjection) -> str:
    return f"# Task context — {projection.task.task_id} · {projection.task.title}"


def _binding_block(projection: TaskProjection) -> list[str]:
    task, scope = projection.task, projection.scope
    memory = (
        "disabled"
        if scope.worktree.memory_mode == "disabled"
        else f"{scope.worktree.memory_mode} on `{scope.worktree.memory_work_branch}` at "
        f"`{scope.worktree.memory_worktree or '<unbound>'}`"
    )
    return [
        "",
        "| binding | value |",
        "| --- | --- |",
        f"| task reference | `{task.reference}` |",
        f"| task document (JSON authority) | `{scope.task_document}` |",
        f"| altitude / kind | {task.altitude} / {task.kind} |",
        f"| seat / operation | {projection.selection.role or 'launcher'} / "
        f"{projection.selection.operation} |",
        f"| repository | {scope.worktree.repository_id} |",
        f"| work branch | `{scope.worktree.work_branch}` (from "
        f"`{scope.worktree.source_branch}` @ `{scope.worktree.base_commit}`) |",
        f"| code worktree | `{scope.worktree.code_worktree}` |",
        f"| memory | {memory} |",
        f"| enclosure contract | `{scope.worktree.contract_path}` |",
        f"| projection revision | `{projection.projection_revision}` |",
    ]


def _objective(projection: TaskProjection) -> list[str]:
    return ["", "## Objective", "", projection.objective.strip()]


def _requirements(projection: TaskProjection) -> list[str]:
    lines = ["", "## Requirement revisions", ""]
    owned = [item for item in projection.requirements if item.owns]
    adjacent = [item for item in projection.requirements if not item.owns and item.identity]
    unnamed = [item for item in projection.requirements if item.identity is None]
    if not owned:
        lines.append(
            "- (none) the admitted binding names no owned requirement revision for this seat"
        )
    for requirement in owned:
        lines.extend(_owned_requirement(requirement))
    if adjacent:
        lines.extend(
            [
                "",
                "### Other approved requirement packets declared by the task document",
                "",
                "Dependency and preservation context only: this seat cannot claim them closed, "
                "and their packets stay unread until expanded on demand.",
                "",
            ]
        )
        lines.extend(f"- {_adjacent_line(item)}" for item in adjacent)
    if unnamed:
        lines.extend(
            [
                "",
                "### Exact-text declarations with no version-addressed identity",
                "",
                "The task document declares these as prose. Prose does not opt itself into "
                "packet authority, so the projection reports the text verbatim and claims no "
                "revision identity for it.",
                "",
            ]
        )
        lines.extend(f"- {item.declaration_text}" for item in unnamed)
    return lines


def _owned_requirement(requirement: RequirementProjection) -> list[str]:
    packet = requirement.packet
    lines = ["", f"### `{requirement.identity}` — owned primary revision", ""]
    if packet is None:
        lines.append(
            f"Declared as {requirement.declaration} but no packet is admitted: "
            f"{requirement.declaration_text}"
        )
        return lines
    lines.extend(
        [
            f"Packet `{packet.path}` — content `{packet.digest}` — declaration "
            f"{requirement.declaration}.",
            "",
        ]
    )
    for role in INJECTED_PACKET_ROLES:
        section = role_section(packet, role)
        if section is None:
            continue
        lines.extend([f"**{section.heading}**", "", section.body, ""])
    referenced = referenced_headings(packet)
    if referenced:
        lines.append(
            "Referenced, not injected: "
            + ", ".join(f"`{heading}`" for heading in referenced)
            + f" — expand at `{packet.path}`."
        )
    return lines


def _adjacent_line(requirement: RequirementProjection) -> str:
    return f"`{requirement.identity}` — `{requirement.declaration_text}`"


def _acceptance(projection: TaskProjection) -> list[str]:
    return ["", "## Acceptance conditions", "", *_facts(projection.acceptance)]


def _scope(projection: TaskProjection) -> list[str]:
    scope = projection.scope
    lines = ["", "## Writable scope", ""]
    lines.append(f"- code: `{scope.worktree.code_worktree}` on `{scope.worktree.work_branch}`")
    if scope.worktree.memory_mode == "disabled":
        lines.append("- memory: disabled for this enclosure")
    else:
        lines.append(
            f"- memory: `{scope.worktree.memory_worktree or '<unbound>'}` on "
            f"`{scope.worktree.memory_work_branch}` (mode {scope.worktree.memory_mode})"
        )
    lines.append(f"- bound task document: `{scope.task_document}`")
    actions = ", ".join(f"`{action}`" for action in scope.admitted_actions) or "(none admitted)"
    lines.append(
        f"- admitted actions for this seat: {actions} — this projection reports the admitted "
        "snapshot and grants nothing"
    )
    return lines


def _decisions(projection: TaskProjection) -> list[str]:
    lines = ["", "## Relevant current decisions", ""]
    if not projection.decisions:
        lines.append("- (none recorded on this task document)")
    lines.extend(_facts(projection.decisions))
    return lines


def _preservation(projection: TaskProjection) -> list[str]:
    return [
        "",
        "## Preservation constraints and failure obligations",
        "",
        *_facts(projection.preservation),
    ]


def _evidence(projection: TaskProjection) -> list[str]:
    return [
        "",
        "## Recorded evidence (historical — referenced, not obligations)",
        "",
        *_facts(projection.evidence),
    ]


def _handoff(projection: TaskProjection) -> list[str]:
    return ["", "## Expected handoff", "", *_facts(projection.handoff)]


def _facts(facts: tuple[ProjectedFact, ...]) -> list[str]:
    lines: list[str] = []
    for fact in facts:
        label = _KIND_LABELS[fact.kind]
        lines.append(f"- ({label}) {fact.text}")
        if fact.source is not None:
            lines.append(f"  source: `{fact.source.render()}`")
    return lines


def _closure(projection: TaskProjection) -> list[str]:
    lines: list[str] = []
    if projection.knowledge:
        lines.extend(["", "## Knowledge expansion (supplied by an admitted expansion source)", ""])
        lines.extend(
            f"- ({_KIND_LABELS[item.kind]}) {item.text}"
            + (f"\n  source: `{item.source.render()}`" if item.source is not None else "")
            for item in projection.knowledge
        )
    if projection.gaps:
        lines.extend(
            [
                "",
                "## Incomplete required input (reported, never silently dropped)",
                "",
                *(f"- {gap}" for gap in projection.gaps),
            ]
        )
    lines.extend(["", "## Expansion references (optional — expand on demand)", ""])
    if not projection.expansion:
        lines.append("- (none)")
    lines.extend(
        f"- {reference.label}: `{reference.render()}`" for reference in projection.expansion
    )
    return lines


_CHANNEL_RENDERERS: Mapping[ProjectionChannel, Callable[[TaskProjection], list[str]]] = {
    "objective": _objective,
    "requirements": _requirements,
    "acceptance": _acceptance,
    "scope": _scope,
    "decisions": _decisions,
    "preservation": _preservation,
    "handoff": _handoff,
    "evidence": _evidence,
}


__all__ = ["render_markdown"]
