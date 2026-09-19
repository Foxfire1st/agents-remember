"""Consuming the owner's packet resolution: the one place this record group asks the task plane.

Requirement 2.3 is a boundary about *who answers*: "Resolution stays with the owner's existing
resolver... The substrate records the reference and consumes a resolution result; it never
re-implements the confinement, re-derives the identity from the file's bytes, or substitutes its own
answer for the owner's refusal."

This module is that consumption and nothing else. It calls the owner's own per-reference resolver --
:func:`agents_remember.tasks.task_intent._approved_packet_ref`, which confines the path to the task
root, refuses a non-Markdown or absent packet, and refuses a packet whose own ``Stable ID``/
``Version`` rows disagree with the reference -- and translates its answer into this record group's
:class:`…requirement.RequirementOwnerResolution`:

* the owner resolved the reference -> ``resolved``;
* the owner refused it -> ``unresolved``, carrying ``TaskIntentError.status`` as the refusal *code*
  and ``TaskIntentError.detail`` as the *detail*, both verbatim.

No code path here constructs a refusal of its own, so a stored unresolved-owner state can only ever
carry a code the owner actually returned. Nothing here re-implements a confinement check, a Markdown
test or a metadata comparison, so there is no second answer to disagree with the owner's.

**Why this is a separate module, and why it imports a private name.** Two boundaries are being kept
apart on purpose:

* The record group's storage and read surface -- :mod:`…requirement_records`,
  :mod:`…requirement_views`, and the guards in :mod:`…requirements` -- import **no** task-plane
  module. A store can record, read and rebuild every revision with the task plane absent, which is
  what "the two planes stay independently operable" means in practice (requirement 2.5). Only this
  module crosses, and only when a caller asks it to.
* The owner exposes no *public* per-reference entry point. Its public projection,
  :func:`…task_intent.task_intent_projection`, resolves a whole task document's requirements at once
  and takes a ``ResolvedTaskDocument``, so consuming it here would make this plane hold a task-plane
  aggregate -- a coupling requirement 2.5 exists to prevent. Importing the owner's own
  per-reference resolver, private name included, is strictly better than re-deriving its answer,
  which requirement 2.3 forbids in terms. A public per-reference resolver on the task plane is the
  upstream fix; it is recorded as an observation in this leaf's report rather than worked around.

**The owner's normalized path is deliberately discarded.** ``_approved_packet_ref`` answers with the
path it resolved, relative to the task root. This module keeps only the *outcome*: the stored
reference is the one the caller submitted, byte for byte, whatever the resolution said. Adopting the
owner's normalized spelling would make the stored reference change with the resolution outcome -- so
"the reference is never rewritten" would hold for a refusal and fail for a success -- and it would
make the record's address differ from the address the caller used to reach it. One total rule is
worth more than one canonicalised spelling here, and requirement 2.3 gives the substrate no licence
to derive anything from where the packet turned out to live.
"""

from __future__ import annotations

from pathlib import Path

from agents_remember.errors import TaskIntentError
from agents_remember.models.knowledge.requirement import (
    RequirementOwnerRef,
    RequirementOwnerResolution,
)
from agents_remember.models.task_intent import ApprovedRequirementPacketRef
from agents_remember.tasks.task_intent import _approved_packet_ref


def consume_owner_resolution(
    task_root: Path, reference: RequirementOwnerRef
) -> RequirementOwnerResolution:
    """Ask the owner's resolver about one reference and carry its answer back verbatim.

    The reference is handed to the owner in the owner's own typed shape, with the same three
    components and the same admitted version spelling, so the two sides compare literally rather
    than through a translation this module could get wrong.
    """

    packet_ref = ApprovedRequirementPacketRef(
        path=reference.path, stableId=reference.stableId, version=reference.version
    )
    try:
        _approved_packet_ref(task_root, packet_ref)
    except TaskIntentError as owner_refusal:
        return RequirementOwnerResolution(
            state="unresolved",
            refusal_code=owner_refusal.status,
            refusal_detail=owner_refusal.detail,
        )
    return RequirementOwnerResolution(state="resolved")
