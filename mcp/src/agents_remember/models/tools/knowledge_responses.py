"""Strict response models for the three mounted ``knowledge_*`` operations.

``knowledge_read``, ``knowledge_diff`` and ``knowledge_integrity_check`` read a memory tree's text
knowledge (MIK-R26 rule 5); ``knowledge_change`` and ``knowledge_project`` left the registered set
with the canonical database. Each model here is a strict ``ToolResponse``, so the field set is the
wire contract rather than a convention.

**One shape, not five.** Requirement 6.8 says a mounted tool's response payload is the same view
payload requirements 2 and 3 define, "not a second shape". A tool that re-rendered a view in its own
format would create a second renderer and therefore a second place for the classification rule to be
violated, so the payload travels as the typed view payload's own JSON and this module adds only the
envelope around it.

**A memory tree is named, never hidden (MIK-R23).** A read's ``memoryRoot`` names a converted
memory tree; the handler reads the tree's derived index and states ``memoryTree``: the tree root,
its key, the index state and every file a ``partial`` index could not read, plus ``indexComplete``
(``false`` when it was read from a partial index). A partial index is never presented as complete:
a read from one also reports ``completeWithinDeclaredScope: false``. ``repositoryId`` is the
index's constant namespace, which the server supplies. ``knowledge_diff`` reads Git trees, not an
index, and carries none of these.

**A read is a bounded page (MIK-R02).** A read of a memory tree adds ``page``: the
shared token threshold, the memory tree, the selection policy and manifest, and the walk's
``total``, ``returned`` (through this page) and ``remaining`` rows, ``enumerationComplete``, the
``headerReference`` a page that continues a family starts with, and ``flags: ["oversized_row"]``
when one row alone exceeds the threshold. ``continuation`` is the shared token, which this tool
resumes whichever surface minted it; resuming the published-intent block's token answers
``state: "page"`` with the scope page as ``payload``.

**Proofs beside the view (MIK-R28 rule 4).** An ``invariant`` or ``family`` read from a memory tree
adds ``proofs``: the ``proves`` entries of the invariant, or of every family member, each
``{id, invariant, path, anchor, facet, sidecar}``. A proof states what its test demonstrates, never
that the test passed or that the invariant holds. The field is absent for the other views.

**Currentness beside the view (MIK-R03).** A read from a memory tree adds ``currentness``: the
state of every invariant the view returns (and of every member of every family it returns) at the
code tree the caller named with ``codeTreeId`` -- ``stale``, ``unverifiable``, ``unrealized`` or
``current`` -- with each entry that is not current, the counts by state and each family's stale
members. With no ``codeTreeId`` every realized invariant is ``unverifiable``; ``HEAD`` is never
substituted. The counts cover every invariant the answer carries, whether it names the invariant
(for example as a relationship) or returns it in full, plus every member of a family it carries. A
failure of the currentness step is stated as ``unverifiableReason`` and never refuses the read.
The view payload is unchanged, so a stale invariant stays visible.

**The family-complete leaf read and family names (MIK-R01).** A
``source_context`` read of a path is the leaf read: ``state: "page"`` whose ``payload.rows`` hold the
path's invariants, each containing family's header and remaining members, their entries (each with
its state) and the advertised families, in one declared order. An ``invariant`` view adds
``families``, the families containing its invariant by ID and title.

**Route-chain families (MIK-R05).** After that content, ``payload.rows`` hold one compact
``chain_family`` row per family with a route on the path's directory or an ancestor, and
``payload.routeChain`` states the mechanical chain (``no_governing_family`` when no route covers
it). A ``registration_absent`` refusal of a path carries the same ``routeChain``. A
``source_context`` read naming ``familyRevisionId`` and no path returns that family's full content.

**A refusal is a state, not a partial success.** ``state`` is ``view``/``result`` or ``refused``, and
the refusal fields name the offending input. A handler never translates a refusal into an empty
result or a default value, so a caller can always tell "nothing was selected" from "the selection was
refused".
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from agents_remember.models.base import ToolResponse

__all__ = [
    "KnowledgeDiffResponse",
    "KnowledgeIntegrityCheckResponse",
    "KnowledgeReadResponse",
]


class KnowledgeReadResponse(ToolResponse):
    """``knowledge_read``: one named view's payload, or one typed refusal.

    ``view`` and ``snapshot`` are echoed beside the payload so a caller can tell which of the five
    views it received and which snapshot it was read at without parsing the payload's own body. The
    payload itself is the view payload verbatim -- the same object requirements 2 and 3 define.
    """

    operation: Literal["knowledge_read"] = "knowledge_read"
    state: Literal["view", "page", "refused"]
    view: str
    repositoryId: str
    snapshot: str | None = None
    completeWithinDeclaredScope: bool | None = None
    continuation: str | None = None
    page: dict[str, Any] | None = None
    threshold: dict[str, Any] | None = None
    payload: dict[str, Any] | None = None
    memoryTree: dict[str, Any] | None = None
    indexComplete: bool | None = None
    proofs: list[dict[str, Any]] | None = None
    currentness: dict[str, Any] | None = None
    families: list[dict[str, Any]] | None = None
    routeChain: dict[str, Any] | None = None
    refusalCode: str | None = None
    refusalDetail: str | None = None


class KnowledgeDiffResponse(ToolResponse):
    """``knowledge_diff``: the Git diff of the knowledge files of two memory trees, or one refusal.

    ``diff`` has the shape of the reviewer's tree diff (``ReviewKnowledgeTreeDiff``): the changed
    knowledge files (records, history, onboarding sidecars and cards) with their patches, grouped
    by record and by source path. It holds nothing an agent supplied and no semantic effect label:
    none is ever inferred from the change (requirement 6.2 quotes ``Doc13:184``'s "not inferred
    from the diff" as the whole contract).

    One answer stays within ``threshold``, the bound ``knowledge_read`` states. ``complete`` says
    whether the answer holds the whole patch of every selected file; when it does not, ``leftOut``
    names the files whose patch is absent (``files``, ``paths``, ``pathsNotNamed``), the patches
    that are cut short (``cutPatches``), and the request that reaches each (``nextAction``).
    """

    operation: Literal["knowledge_diff"] = "knowledge_diff"
    state: Literal["compared", "refused"]
    memoryRoot: str | None = None
    beforeRevision: str | None = None
    afterRevision: str | None = None
    threshold: dict[str, Any] | None = None
    complete: bool | None = None
    diff: dict[str, Any] | None = None
    leftOut: dict[str, Any] | None = None
    refusalCode: str | None = None
    refusalDetail: str | None = None


class KnowledgeIntegrityCheckResponse(ToolResponse):
    """``knowledge_integrity_check``: the validator's report of one memory tree, and a leaf's worklist.

    ``validation`` is the knowledge validator's report (MIK-R22) over the tree at ``memoryRoot``,
    against ``bases`` (memory commits) and the code checkout ``codeRoot``: ``ok``, ``refusalCount``,
    ``reportCount``, ``byRule`` (every rule that fired, with its count and whether it only reports),
    and ``violations`` -- a bounded listing, refusing violations first, with
    ``violationsTruncated`` set when the report holds more. The counts always cover the whole
    report. ``ok`` is the validator's own answer for this tree and these inputs; it is not a
    statement about the code, and a report-only finding never makes it false.

    ``worklistState`` and ``worklist`` are present when the caller named a leaf: the leaf's latest
    persisted change-to-knowledge worklist (MIK-R08), or that none is persisted yet.
    """

    operation: Literal["knowledge_integrity_check"] = "knowledge_integrity_check"
    state: Literal["reported", "refused"]
    memoryRoot: str | None = None
    codeRoot: str | None = None
    bases: list[str] = Field(default_factory=list)
    validation: dict[str, Any] | None = None
    refusalCode: str | None = None
    refusalDetail: str | None = None
    # The leaf's latest MIK-R08 worklist, when the caller named a leaf (``contractPath``).
    worklistState: Literal["present", "absent", "unreadable"] | None = None
    worklist: dict[str, Any] | None = None
