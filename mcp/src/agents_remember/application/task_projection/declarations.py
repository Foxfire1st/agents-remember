"""The bound document's requirement declarations, read through the task-intent owner.

A requirement reaches this package in one of two admissible forms, and the
difference is load-bearing:

* an **approved packet reference** (``kind: approved-requirement-packet``) is a
  version-addressed identity the task document itself declares. The task-intent
  owner is what makes it real: it confines the path to the task root, reads the
  file, and verifies the packet's own ``Stable ID``/``Version`` table against the
  declaring reference. A missing or mismatched packet is that owner's typed
  refusal, mapped here to this package's source error rather than swallowed into
  a partial projection;
* an **exact-text declaration** is prose. Prose does not opt itself into packet
  authority, so it is projected verbatim with no invented identity and no
  obligation claim attached to it.

Nothing here searches for "the packet that looks right". A requirement the
admitted binding makes this seat accountable for, with no declared packet and no
admitted location, is refused by the caller -- never resolved by a directory scan.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from pydantic import Field, TypeAdapter

from agents_remember.errors import TaskIntentError, TaskProjectionSourceError
from agents_remember.tasks.document_refs import ResolvedTaskDocument
from agents_remember.tasks.task_intent import (
    TaskIntentRequirementPacket,
    TaskIntentRequirementText,
    task_intent_master_projection,
    task_intent_projection,
)

from . import packets
from .packets import PacketSectionRole
from .statuses import (
    STATUS_REQUIREMENT_DECLARATION_UNRESOLVED,
)
from .types import RequirementProjection

#: One requirement the bound task document declares.
RequirementDeclaration = TaskIntentRequirementText | TaskIntentRequirementPacket

_INTENT_REQUIREMENT: TypeAdapter[RequirementDeclaration] = TypeAdapter(
    Annotated[RequirementDeclaration, Field(discriminator="kind")]
)


def read_declarations(
    task_root: Path,
    document: ResolvedTaskDocument,
) -> tuple[RequirementDeclaration, ...]:
    """The bound document's requirement declarations, in declaration order.

    A leaf document projects through ``task_intent_projection``; a master
    document projects through ``task_intent_master_projection`` and is normalised
    back into the same typed union, so every caller sees one shape.
    """

    try:
        if document.document.kind == "master":
            projected = task_intent_master_projection(task_root, document)
            declared = projected["requirements"]
            assert isinstance(declared, list)
            return tuple(_INTENT_REQUIREMENT.validate_python(item) for item in declared)
        return task_intent_projection(task_root, document).requirements
    except TaskIntentError as error:
        raise TaskProjectionSourceError(
            STATUS_REQUIREMENT_DECLARATION_UNRESOLVED,
            f"the bound task document's requirement declarations did not resolve: {error}",
            next_action="repair the task document's declared requirement references",
            owner_status=getattr(error, "status", ""),
        ) from error


def declaration_identity(declaration: RequirementDeclaration) -> str | None:
    """``"<stable_id>@<version>"`` for a packet reference, ``None`` for prose."""

    if isinstance(declaration, TaskIntentRequirementPacket):
        return f"{declaration.stableId}@{declaration.version}"
    return None


def adjacent(declaration: RequirementDeclaration) -> RequirementProjection:
    """A declaration this seat does not own: dependency/preservation context only.

    Its packet is deliberately **not** read. Adjacent obligations are boundary
    context, and loading their bodies would grow the projection without giving the
    seat anything it may claim.
    """

    if isinstance(declaration, TaskIntentRequirementPacket):
        return RequirementProjection(
            identity=declaration_identity(declaration),
            owns=False,
            declaration="approved-packet",
            declaration_text=declaration.path,
            packet=None,
        )
    return RequirementProjection(
        identity=None,
        owns=False,
        declaration="exact-text",
        declaration_text=declaration.text,
        packet=None,
    )


def missing_section_gap(
    packet: packets.RequirementPacketProjection,
    role: PacketSectionRole,
) -> str:
    """The visible record of an obligation the packet does not carry."""

    accepted = ", ".join(packets.PACKET_SECTION_HEADINGS[role])
    return (
        f"{packet.identity}: packet {packet.path} carries no section for {role!r} "
        f"(accepted headings: {accepted}); the obligation is missing, not empty"
    )


__all__ = [
    "RequirementDeclaration",
    "adjacent",
    "declaration_identity",
    "missing_section_gap",
    "read_declarations",
]
