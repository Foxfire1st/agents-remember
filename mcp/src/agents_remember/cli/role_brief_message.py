"""The first message of one role launch: its opening, its direction, its capsule and projection.

The message is read again from cache on every turn of a seat's life, so it is assembled here once
and in one order (MIK-R193 rule 1): who started the seat, the direction to split its own work, the
AR-tools paragraph, the first-read paragraph, the optional shared block, the compiled capsule, and
finally the projection of the hand-over under its heading. Nothing here says anything twice, and
nothing here writes a file: the projection's file reference is planned from the bytes the launch
will write beside the message.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agents_remember.cli.role_handover_file import planned_handover_file
from agents_remember.cli.role_handover_projection import (
    first_read_paragraph,
    project_handover,
    projection_text,
)

# The heading the optional shared block stands under, between the brief's opening paragraphs and
# the capsule; it is written only when a compilation provides the block.
SHARED_BLOCK_HEADING = "What every role acts on"
# The heading the projection follows. A role's own text names it, so it is written once.
HANDOVER_HEADING = "AR owner assignment and canonical task handover:"


@dataclass(frozen=True, slots=True)
class BriefParts:
    """The first message's text parts that come from the compilation and the role's constants."""

    # Who started the seat, as the opening sentence says it.
    started_from: str
    # The one direction every role's first message carries, once, before the AR-tools paragraph.
    direction: str
    ar_tools: str
    first_read: str
    instructions: str
    # The optional "What every role acts on" block; None until a source provides one (rule 1).
    shared_block: str | None = None


def brief_prompt(
    handover: dict[str, Any],
    *,
    report_path: str,
    bound_document: dict[str, Any] | None,
    parts: BriefParts,
) -> tuple[str, dict[str, Any], dict[str, Any], str]:
    """The compiled first message, the projection it carries, and the file it names.

    The returns are the message, the projection, the complete hand-over's reference and the text
    that reference's file will hold.
    """

    handover_file, handover_file_content = planned_handover_file(report_path, handover)
    projection = project_handover(
        handover,
        handover_file=handover_file,
        bound_document=bound_document,
    )
    prompt = (
        f"AR ROLE BRIEF: {parts.started_from} started this role with the role and "
        "operation identified below. "
        "The following effective instructions and canonical handover are the complete AR "
        "role/operation instructions and assignment for this agent. Do not reopen ambient AR role, "
        "lifecycle, or coordination-level role-routing text to infer a different assignment. "
        "Preserve system/developer instructions, approvals, sandbox policy, and "
        "repository-specific coding/tool rules.\n\n"
        f"{parts.direction}\n\n"
        # Before the capsule, which names AR tools: where this session holds them.
        f"AR tools, before your first tool call: {parts.ar_tools}\n\n"
        f"{first_read_paragraph(parts.first_read)}\n\n"
        f"{shared_block(parts.shared_block)}{parts.instructions}"
        f"\n\n{HANDOVER_HEADING}\n"
        f"{projection_text(projection)}"
    )
    return prompt, projection, handover_file, handover_file_content


def shared_block(text: str | None) -> str:
    """The optional shared block, once under its heading, or nothing at all (rule 1)."""

    if not text:
        return ""
    return f"## {SHARED_BLOCK_HEADING}\n\n{text}\n\n"
