"""The projection's own revision identity, computed from its inputs.

A projection revision answers "which selection over which facts produced this
document", so it is deliberately computed from the selection and the projected
facts rather than from the rendered bytes: the rendered document can then carry
its own revision, and a consumer comparing two projections compares identities
rather than prose formatting.

It is not a second task database and it stores nothing. It moves when the admitted
branch, the accepted task-document bytes, the read set, an owned packet's content
or any projected fact moves -- which is exactly the property a later consumer needs
to decide whether its pinned projection is still current.
"""

from __future__ import annotations

from agents_remember.models.role_capsules.types import compute_content_digest

from .types import ProjectedFact, TaskProjection


def projection_revision(projection: TaskProjection) -> str:
    """One content-addressed identity over the selection plus every projected fact."""

    lines = [
        f"task\t{projection.task.reference}",
        f"document\t{projection.task.document_digest}",
        f"kind\t{projection.task.kind}",
        f"altitude\t{projection.task.altitude}",
        f"role\t{projection.selection.role or ''}",
        f"operation\t{projection.selection.operation}",
        f"branch\t{projection.worktree.work_branch}",
        f"read\t{','.join(projection.selection.read_documents)}",
        f"referenced\t{','.join(projection.selection.referenced_documents)}",
        f"knowledge\t{'admitted' if projection.knowledge else 'not-admitted'}",
    ]
    lines.extend(
        f"requirement\t{item.identity}\t{item.declaration}\t"
        f"{'-' if item.packet is None else item.packet.digest}"
        for item in projection.requirements
    )
    lines.extend(
        f"{plane}\t{fact.kind}\t{fact.text}"
        for plane, facts in _planes(projection)
        for fact in facts
    )
    return compute_content_digest("\n".join(lines).encode("utf-8"))


def _planes(
    projection: TaskProjection,
) -> tuple[tuple[str, tuple[ProjectedFact, ...]], ...]:
    """Every projected plane, so a change to any fact moves the revision."""

    return (
        ("acceptance", projection.acceptance),
        ("decisions", projection.decisions),
        ("preservation", projection.preservation),
        ("handoff", projection.handoff),
        ("portfolio", projection.portfolio),
        ("series", projection.series),
        ("evidence", projection.evidence),
    )


__all__ = ["projection_revision"]
