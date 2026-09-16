"""Read one requirement packet through the existing owners, then split it verbatim.

Two things happen here and nothing else:

* **Location and presence** belong to the existing owners.
  :func:`agents_remember.tasks.task_intent.task_intent_projection` already
  confines a task-declared packet path to the task root, reads it and verifies the
  packet's own ``Stable ID``/``Version`` header against the declaring
  ``ApprovedRequirementPacketRef``; a missing or mismatched packet is that owner's
  typed refusal. For a requirement the task document declares as exact text only,
  the consumer supplies an admitted, version-addressed location and this module
  confines it with the kernel's non-symlink artifact guard. There is no third
  route, no search for "the packet that looks right", and no default.
* **Section split** is a projection concern with no other owner. Every ``##``
  section is kept byte-for-byte in document order. Nothing is re-flowed,
  summarised or clipped: a required behaviour, a negative constraint or a failure
  obligation that shrank on the way into a capsule would be the defect the
  requirement names, so the code that could do it does not exist.

The canonical packet shape comes from
``skills/w-02-light-task-workflow/requirement-packet-template.md``; the shipped
packets spell several headings in sentence case, so a heading *role* accepts
either spelling. A heading outside the table is never dropped -- every section is
carried in :attr:`RequirementPacketProjection.sections`, and a role with no
section is reported as a gap rather than rendered as nothing.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Literal

from agents_remember.errors import TaskProjectionSourceError
from agents_remember.kernel.sidecar_pairing import confine_non_symlink_rel
from agents_remember.models.role_capsules.types import compute_content_digest

from .statuses import (
    STATUS_REQUIREMENT_PACKET_INVALID,
    STATUS_REQUIREMENT_PACKET_MISSING,
)
from .types import PacketSection, RequirementPacketProjection

#: The six packet sections that carry an obligation this projection must not lose.
#: The rest of a packet (problem statement, rationale, revision history, cold-read
#: notes) is referenced by heading instead of injected.
PacketSectionRole = Literal[
    "normative-requirement",
    "required-behavior",
    "preservation-boundaries",
    "exclusions",
    "failure-and-recovery",
    "expected-evidence",
]

INJECTED_PACKET_ROLES: tuple[PacketSectionRole, ...] = (
    "normative-requirement",
    "required-behavior",
    "preservation-boundaries",
    "exclusions",
    "failure-and-recovery",
    "expected-evidence",
)

#: Heading-role -> accepted exact spellings, template form first, corpus form second.
#: Matched exactly (never by substring), because a packet heading is a declared
#: structure, not prose to guess at.
PACKET_SECTION_HEADINGS: Mapping[PacketSectionRole, tuple[str, ...]] = {
    "normative-requirement": ("Normative Requirement", "Normative requirement"),
    "required-behavior": ("Required Behavior", "Required behavior"),
    "preservation-boundaries": (
        "Preservation Boundaries",
        "Scope and preservation boundaries",
    ),
    "exclusions": ("Forbidden Overreach", "Exclusions and forbidden overreach"),
    "failure-and-recovery": (
        "Failure And Recovery Behavior",
        "Failure and recovery behavior",
        "Failure and recovery",
    ),
    "expected-evidence": ("Expected Evidence", "Expected evidence"),
}

_HEADING_MARKER = "## "


def read_packet(
    *,
    task_root: Path,
    stable_id: str,
    revision: str,
    path: str,
) -> RequirementPacketProjection:
    """Read one confined packet and split it into verbatim sections.

    ``path`` is task-root-relative, exactly like an approved packet reference's own
    path. A missing, escaping, symlinked or non-Markdown packet is a concrete
    source-resolution refusal: the projection never substitutes another file and
    never proceeds without the obligation.
    """

    if not path.strip() or Path(path).suffix != ".md":
        raise TaskProjectionSourceError(
            STATUS_REQUIREMENT_PACKET_INVALID,
            f"requirement packet path {path!r} is not a task-relative Markdown file",
            next_action=(
                "supply the packet's declared path, e.g. "
                "'requirements/<stable-id>-<version>-<slug>.md', relative to the task root"
            ),
        )
    try:
        confined = confine_non_symlink_rel(task_root, path)
    except OSError as error:
        raise _packet_unavailable(stable_id, revision, path, str(error)) from error
    except ValueError as error:
        # ``confine_non_symlink_rel`` raises ``AuthorityError`` (a ``ValueError``) for an
        # escaping or symlinked path; that is a source-resolution problem here, not an
        # authority problem, because the caller never supplied the root.
        raise TaskProjectionSourceError(
            STATUS_REQUIREMENT_PACKET_INVALID,
            f"requirement packet path {path!r} is not a confined non-symlink artifact: {error}",
            next_action="supply the exact task-relative packet path the task document declares",
        ) from error
    source = task_root / confined
    try:
        raw = source.read_bytes()
    except OSError as error:
        raise _packet_unavailable(stable_id, revision, path, str(error)) from error
    return parse_packet(
        raw=raw,
        stable_id=stable_id,
        revision=revision,
        path=confined,
    )


def parse_packet(
    *,
    raw: bytes,
    stable_id: str,
    revision: str,
    path: str,
) -> RequirementPacketProjection:
    """Split already-read packet bytes into the typed projection."""

    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise TaskProjectionSourceError(
            STATUS_REQUIREMENT_PACKET_INVALID,
            f"requirement packet {path!r} is not UTF-8 text",
            next_action="store the packet as UTF-8 Markdown",
        ) from error
    return RequirementPacketProjection(
        stable_id=stable_id,
        revision=revision,
        path=path,
        digest=compute_content_digest(raw),
        sections=split_sections(text),
    )


def split_sections(text: str) -> tuple[PacketSection, ...]:
    """Every ``##`` section, body verbatim, in document order.

    A ``###`` sub-heading stays inside its parent section's body: the packet's own
    structure is preserved rather than flattened, and the body is joined with the
    newlines it arrived with.
    """

    sections: list[PacketSection] = []
    heading: str | None = None
    body: list[str] = []

    def flush() -> None:
        if heading is not None:
            # Surrounding blank lines are layout, not content: strip them so a section
            # body starts and ends at its own text. Nothing inside the body is touched.
            sections.append(PacketSection(heading=heading, body="\n".join(body).strip("\n")))

    for line in text.splitlines():
        if line.startswith(_HEADING_MARKER):
            flush()
            heading = line[len(_HEADING_MARKER) :].strip()
            body = []
            continue
        if heading is not None:
            body.append(line)
    flush()
    return tuple(sections)


def role_section(
    packet: RequirementPacketProjection,
    role: PacketSectionRole,
) -> PacketSection | None:
    """The section playing ``role``, or ``None`` when the packet does not carry one."""

    for spelling in PACKET_SECTION_HEADINGS[role]:
        section = packet.section(spelling)
        if section is not None:
            return section
    return None


def referenced_headings(packet: RequirementPacketProjection) -> tuple[str, ...]:
    """Headings this projection points at instead of injecting, in document order.

    Every heading outside the injected roles is listed here, so "referenced" is a
    decision with a visible record and never a silent drop.
    """

    injected = {
        section.heading
        for role in INJECTED_PACKET_ROLES
        if (section := role_section(packet, role)) is not None
    }
    return tuple(heading for heading in packet.headings if heading not in injected)


def _packet_unavailable(
    stable_id: str,
    revision: str,
    path: str,
    detail: str,
) -> TaskProjectionSourceError:
    return TaskProjectionSourceError(
        STATUS_REQUIREMENT_PACKET_MISSING,
        f"requirement packet {stable_id}@{revision} is absent or unreadable at {path!r}: {detail}",
        next_action=(
            "declare the packet on the task document as an approved-requirement-packet "
            "reference, or correct the admitted packet location; a projection is never "
            "produced without the obligation it was asked for"
        ),
    )


__all__ = [
    "INJECTED_PACKET_ROLES",
    "PACKET_SECTION_HEADINGS",
    "PacketSectionRole",
    "parse_packet",
    "read_packet",
    "referenced_headings",
    "role_section",
    "split_sections",
]
