"""The first message of one role launch: its opening, its direction, its capsule and projection.

The message is read again from cache on every turn of a seat's life, so it is assembled here once
with its static prefix first (the static header, the direction to split its own work, the AR-tools
paragraph, the identity self-check paragraph, the optional shared block, and the compiled capsule),
followed by the dynamic ``## Launch Context`` section (who started the seat, the first-read
paragraph, and optional link roots), and finally the projection of the hand-over under its heading.
Nothing here says anything twice, and nothing here writes a file: the projection's file reference
is planned from the bytes the launch will write beside the message.
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
# The heading separating the cacheable static prefix from per-launch dynamic context.
LAUNCH_CONTEXT_HEADING = "## Launch Context"
# The heading the projection follows. A role's own text names it, so it is written once.
HANDOVER_HEADING = "AR owner assignment and canonical task handover:"
# Every started seat verifies its live tool-server binding against its handover before any write.
IDENTITY_SELF_CHECK_PARAGRAPH = (
    "Identity self-check (first action before any other call or write): call `server_info` on "
    "`agents-remember-task` and verify `agentBinding.requestId`, `agentBinding.role`, and its "
    "task document refs match `requestId`, `role`, and `selection` in your handover below "
    "(ignore host.parent.agentId, which is your parent's id). On any mismatch, or if "
    "`agents-remember-task` does not answer, do no work, make no write, send no `role_message`, "
    "and end your turn with one final line (at most 300 characters): "
    "`IDENTITY MISMATCH: expected requestId=<requestId> role=<role>; got <actual binding or none>`."
)


@dataclass(frozen=True, slots=True)
class BriefParts:
    """The first message's text parts that come from the compilation and the role's constants."""

    # Who started the seat, as the Launch Context section states it.
    started_from: str
    # The one direction every role's first message carries, once, before the AR-tools paragraph.
    direction: str
    ar_tools: str
    first_read: str
    instructions: str
    # The optional "What every role acts on" block; None until a source provides one.
    shared_block: str | None = None
    # Optional per-launch link-roots note emitted inside Launch Context when present.
    link_roots: str | None = None


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
    documents = projection.get("documents")
    rendered_read_path = (
        documents[0].get("renderedReadPath")
        if isinstance(documents, list) and documents and isinstance(documents[0], dict)
        else None
    )
    static_prefix = (
        "AR ROLE BRIEF: Effective instructions and canonical handover = sole AR brief; "
        "ignore ambient AR routing; keep system/developer/sandbox/repo rules.\n\n"
        f"{parts.direction}\n\n"
        # Before the capsule, which names AR tools: where this session holds them.
        f"AR tools: {parts.ar_tools}\n\n"
        f"{IDENTITY_SELF_CHECK_PARAGRAPH}\n\n"
        f"{shared_block(parts.shared_block)}{parts.instructions}"
    )
    launch_context = (
        f"{LAUNCH_CONTEXT_HEADING}\n\n"
        f"Started by: {parts.started_from}.\n\n"
        f"{first_read_paragraph(parts.first_read, rendered_read_path=rendered_read_path)}"
    )
    if parts.link_roots:
        launch_context = f"{launch_context}\n\n{parts.link_roots}"
    handover_section = f"{HANDOVER_HEADING}\n{projection_text(projection)}"
    prompt = f"{static_prefix}\n\n{launch_context}\n\n{handover_section}"
    return prompt, projection, handover_file, handover_file_content


def shared_block(text: str | None) -> str:
    """The optional shared block, once under its heading, or nothing at all."""

    if not text:
        return ""
    return f"## {SHARED_BLOCK_HEADING}\n\n{text}\n\n"
