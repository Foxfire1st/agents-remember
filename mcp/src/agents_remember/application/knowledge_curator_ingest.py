"""The curator's reachable ingest: one hand-off list becomes one admitted batch and one report.

:mod:`agents_remember.application.knowledge_ingest` is the write half -- one resolved entry, one
command list, one batch. This module is the layer above it, and it is the operation the leaf's
objective names: the curator receives the orchestrator's hand-off list and commits it. It admits
the destination, completes each target path, reads the identity each path really holds, verifies
the locator against the bytes at that path, observes each anchor against the recorded tree, authors
the governing routes, commits the whole list through the closed write path, and reports every
entry's outcome so the run is auditable afterwards.

**Three cases, never conflated.** A ruling that carries no target (``target: []``) is **skipped**,
and the report carries the producer's own ``disposition`` and ``disposition_source`` so the ruling
is not lost: there is no construct to cite, so there is no claim about code to record, and
recording one would invent a claim the producer never made. A non-empty target that does not resolve
is **refused**, with the exact reason, because the producer named a place the curator cannot verify.
``CuratorEntry.citations`` may be empty -- that is the write module's documented "authored
obligation recorded with no realization" -- and this operation never reaches that path by accident:
an entry is committed only when every one of its targets resolved, verified and observed, so an
empty citation list here arrives only from an entry with no targets, which is the skipped case and
is not committed at all.

**The API allocates a new truth's identity; the label is never an input to it.** An invariant and its
first revision are the two identities this module *allocates*: fresh ``uuid4`` values, minted here
and recorded in the candidate's own allocation journal, so they carry no task, no branch, no baseline
and no label. A hand-off label is a **local hand-off label** -- two independent tasks numbering an
entry ``R-LOCAL`` are two creation operations, and they now mint two distinct truths instead of
aliasing onto one record and colliding in the merge. What makes a *repeat* of one operation resolve
to the identities it already holds is the **retry key** recorded beside them: the enclosure's own task
identity joined with the entry's id inside that list. The enclosure scopes the *key* and never the
identity, because a stored identity has to stay usable from any task, branch or later baseline --
``models/knowledge/repository.py`` says a namespace "is not a filesystem root, a branch name or a
repository display name". Repeating that operation returns the same ids; the same key carrying
different content is refused rather than silently overwritten.

A **citation's** identities stay derived: ``uuid5`` under one fixed namespace over the **repository's
own namespace** joined with the entry's own ``id`` from the hand-off list -- and, for a target, what
inside the named path the citation is about: a symbol's qualified name, or the locator kind when
there is nothing finer. Two runs of the same list therefore mint the same ids, and the ids are real
UUIDs because the write path stores UUID-shaped identities. The stable half is the repository and
never the code base commit, because a baseline is exactly the kind of value
``models/knowledge/repository.py`` says a namespace must not move with: keyed on the base commit, one
repository's obligation became a different record at each baseline while two different repositories
that shared a base commit were handed the same record identity. Note that the *resolution* tree and
the *identity* anchor are two different commits on purpose: see the next paragraph.

**A producer may cite the code its own leaf is producing.** The objective commits into the leaf's
draft-candidate, and a draft is unlanded work by definition, so the tree a target is resolved against
is the leaf's **own line** -- the code work branch tip the worktree stands on -- rather than the
``code_base_commit`` the contract recorded when the enclosure was created. Binding the resolver to the
branch base made the leaf's own deliverable a non-member of the tree it was resolved against: a file
the leaf added was refused as *gone*, and a file the leaf modified made the resolver raise before any
report existed. The two trees are kept apart where they must be: the resolution tree is the leaf's
line (so the leaf's new and changed files resolve, and the recorded ``source_identity`` is the blob id
in **that** tree), while a derived identity is anchored to neither tree -- it is keyed on the
repository's own namespace, so identity does not move when the line advances by one commit *or* when
the next task runs at a different baseline. Every report names both trees it used.

**No failure is an exception.** Every unreadable identity, unresolvable path or unverifiable locator
arrives in :class:`IngestReport` as a typed refusal whose reason names the actual failure. The
citation machinery signals some of its own boundary conditions by raising, and this operation catches
exactly those at the point of use and reports them: a failure inside the operation would violate the
first promise in this docstring -- that every entry's outcome is reported so the run is auditable --
which is why ``ingest_curator_list`` is written to return a report for every input it can read.

**The route leg does not travel through the batch, and the report says so.** The closed command
union's only route member is ``SetFamilyRevisionRoute``, which names a *family revision*. An entry
here is an invariant beside source anchors; authoring a family per entry would fabricate grouping
structure the producer never declared, which is the one thing the curator must not do. The route is
therefore recorded through the primitives that reach a governed anchor -- ``author_route``
(idempotent by path: an existing route's id is returned and no second row is written) and
``set_governing_route`` (at most one governing route per governed row; re-stating it is idempotent
and a different route is refused rather than overwritten) -- and the two legs are reported
separately instead of folded into one success. A route that cannot be recorded refuses the list by
name, before anything is committed.

**What this module does not do.** It adds no symbol extractor: a symbol locator is resolved
through the one the tree already ships (``memory_quality.style.citations``), by the same helper the
knowledge read rail asks when it observes one, so a construct accepted here is a construct the rail
can re-resolve and there is exactly one definition implementation in the tree. It does not touch
onboarding, build an export, or widen the citation machinery to a third root -- a path outside the
two admitted roots is refused as out of scope by name.

**Publication is not the curator's later act any more.** A run that selects an
:class:`IngestPublication` publishes the candidate it just committed, through the shipped publication
owner, into the destination the caller admitted. It runs HERE and not in the caller because this is
the only place that holds both facts publication needs: the admitted candidate destination the
admission already built, and the candidate's LIVE identity, which changes with every row the batch
writes and therefore cannot be supplied before the run. A run that selects none behaves exactly as
before and reports no publication, and a batch that did not commit is never published.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, cast, get_args
from uuid import UUID, uuid4, uuid5

import apsw

from agents_remember.application.knowledge import (
    open_admitted_knowledge_store,
    write_authorship,
)
from agents_remember.application.knowledge_ingest import (
    CuratorCitation,
    CuratorEntry,
    commit_curator_entries,
    curator_entry_commands,
)
from agents_remember.application.knowledge_snapshot import (
    admitted_candidate_destination,
    candidate_write_destination,
    clone_knowledge_candidate,
    create_knowledge_candidate,
    open_knowledge_candidate,
    publish_knowledge_snapshot,
)
from agents_remember.kernel.atomic_write import atomic_write_bytes
from agents_remember.kernel.canonical_json import canonical_json_bytes, decoded_json, sha256_digest
from agents_remember.kernel.git_command import run_git
from agents_remember.memory.knowledge import routes
from agents_remember.memory.knowledge.anchors import read_anchor
from agents_remember.memory.knowledge.connection import open_read_only_database
from agents_remember.memory.knowledge.logical import dataset_identity
from agents_remember.memory.knowledge.read_anchors import observe_anchor
from agents_remember.memory.knowledge.records import decode_repository_row
from agents_remember.memory.knowledge.store import OpenedKnowledgeStore
from agents_remember.memory_quality.style.citations import grammars
from agents_remember.memory_quality.style.citations.extents import bound_definitions
from agents_remember.memory_quality.style.citations.resolution import Trees
from agents_remember.memory_quality.style.citations.source_index_state import SourceIndexError
from agents_remember.models.knowledge.candidate import (
    CandidateResolution,
    MutationResult,
    SnapshotIdentity,
)
from agents_remember.models.knowledge.context import AdmittedKnowledgeDestination
from agents_remember.models.knowledge.graph import UNCLASSIFIED_ROLE, RealizationRole
from agents_remember.models.knowledge.repository import RepositoryIdentity
from agents_remember.models.knowledge.result import KnowledgeRefusal
from agents_remember.models.knowledge.snapshot import (
    CANDIDATE_RECEIPT_NAME,
    AdmittedCandidateDestination,
    CandidateBaseline,
    CandidateResult,
    PublishSnapshotRequest,
    SnapshotDestinationRequest,
    SnapshotPublicationResult,
)
from agents_remember.models.knowledge.source import (
    FileLocator,
    GitBlobIdentity,
    LineRangeLocator,
    SourceAnchor,
    SourceAnchorDraft,
    SourceLocator,
    SymbolLocator,
)
from agents_remember.worktrees.worktree_contract import WorktreeContract, load_contract

__all__ = [
    "COMMITTED",
    "REFUSED",
    "SKIPPED",
    "EntryOutcome",
    "IngestCounts",
    "IngestPublication",
    "IngestReport",
    "RouteOutcome",
    "TargetOutcome",
    "ingest_curator_list",
]

# The three per-entry outcomes, spelled once so a reader of the report and a caller branching on
# it name the same three words, and so "skipped" can never be read as a soft refusal.
COMMITTED = "committed"
REFUSED = "refused"
SKIPPED = "skipped"

# The lane this increment may write. ``task-candidate`` is refused by the write path until the
# resolved task binding the packet requires exists, so the ingest admits the draft lane only rather
# than accepting a lane parameter a caller could use to ask for the other one.
_CANDIDATE_LANE = "draft-candidate"

# The one place a name for a handed-over list is minted. Fixed forever: it is the namespace every
# derived identity is stable within, so changing it would silently re-mint each entry's identity.
_INGEST_NAMESPACE = UUID("6f2a1c94-6b0d-5e77-9a41-2d1f8c3b7e50")

# The candidate-local record of which creation operations already hold allocated identities. It sits
# beside ``candidate-receipt.json`` in the candidate directory because that is the same kind of fact:
# a local, operation-scoped record of what this candidate is, not knowledge the repository holds.
_ALLOCATION_JOURNAL_NAME = "curator-allocation-journal.json"

# Two refusal codes for the allocation seam, and neither is a weaker duplicate check. The first is
# "this idempotency key already names a different truth": a key identifies one creation operation, so
# a second content wearing it is a second operation, and silently minting a fresh identity for it (or
# silently revising the stored one) are the two things the ruling forbids. The second is "the record
# that would answer that question cannot be read": minting without knowing whether this operation
# already has identities is exactly how a retry becomes a duplicate truth, so the entry is refused
# rather than guessed at.
_CODE_ALLOCATION_CONFLICT = "allocation_content_conflict"
_CODE_ALLOCATION_UNREADABLE = "allocation_journal_unreadable"

# The completion steps, in the order the resolver answers them. Each is a name the report prints,
# so "which root answered" is never inferred from a path's spelling.
_CODE_TREE = "code-tree"
_MEMORY_ONBOARDING = "memory-onboarding"
_MEMORY_ROOT = "memory-root"

# The exact refusal reasons of step 2. The three are distinct facts, not three spellings of
# "unresolved": a task-tree path exists outside the two admitted roots, a dependency's source is
# absent by construction, and a real top-level entry whose file is gone is the damage a move did.
# A fourth is the spelling that never named a place inside the roots at all, and it is separate
# because calling an absolute path "gone" or "a dependency's source" would both be false statements
# about it. Every one of the four is decided from the recorded trees and from the path's own
# spelling -- never from ``is_file()`` on a live directory -- so the same citation earns the same
# reason at every moment, whatever the enclosure's cleanup has since deleted.
_REASON_THIRD_ROOT = "third_root_out_of_scope"
_REASON_DEPENDENCY = "dependency_source_not_ours"
_REASON_GONE = "top_level_entry_file_gone"
_REASON_OUTSIDE_BY_SPELLING = "path_outside_admitted_roots"
_REASON_NOT_CONFINED = "path_not_confined_by_spelling"
_REASON_INSIDE_BY_SPELLING = "path_inside_admitted_root_spelled_absolutely"

# The locator kinds. The rail observes all three: a symbol is resolved through the shipped
# extractor, so the distinction between a file, a range and a symbol is a distinction in what the
# locator names rather than in whether the rail can answer it.
_FILE_KIND = "file"
_RANGE_KIND = "line_range"
_SYMBOL_KIND = "symbol"

# The refusal codes of steps 3 and 4, one per distinct truth rather than one per step. A malformed
# range, a range that runs backwards, a zero-based range, a range past the end of the file, a missing
# locator, a name that is nowhere in its file, a name that is in the file but is not a definition,
# and a symbol aimed at a file that cannot define anything are eight different facts; reporting them
# under one code whose words are true of only one of them is the defect these names exist to end.
_CODE_LINE_RANGE_MALFORMED = "line_range_not_integer_bounds"
_CODE_LINE_RANGE_ORDER = "line_range_not_ordered"
_CODE_LINE_RANGE_ZERO_BASED = "line_range_not_one_based"
_CODE_LINE_RANGE_PAST_END = "line_range_past_last_line"
_CODE_TARGET_SHAPE = "target_not_a_mapping"
_CODE_LOCATOR_SHAPE = "locator_not_a_mapping"
_CODE_LOCATOR_MISSING = "target_locator_missing"
_CODE_LOCATOR_KIND = "unsupported_locator_kind"
_CODE_CONSTRUCT_ABSENT = "construct_not_in_named_file"
_CODE_NOT_A_DEFINITION = "symbol_not_a_definition"
_CODE_NO_DEFINITIONS_IN_PROSE = "symbol_target_is_prose"
_CODE_SYMBOL_LANGUAGE = "symbol_language_underdetermined"
_CODE_SYMBOL_NAME_MISSING = "symbol_name_missing"
_CODE_ANCHOR_ID_SHAPE = "anchor_id_not_a_uuid"
# The two ways an explicitly reused anchor identity fails to bind. They are separate codes because
# they need separate remedies: ``anchor_id_not_stored`` means the caller must cite a place the
# dataset holds or omit the identity to author a new one, while ``anchor_reuse_mismatch`` means the
# identity is real and the facts supplied beside it describe a different place -- so the caller
# either corrects those facts or drops them and lets the stored row speak.
_CODE_ANCHOR_ID_NOT_STORED = "anchor_id_not_stored"
_CODE_ANCHOR_REUSE_MISMATCH = "anchor_reuse_mismatch"

# The one refusal code for a target whose identity the tree and the working bytes disagree about, and
# the two observation answers that mean the recorded identity could not be confirmed. The first is
# spelled exactly as the rail's own ``recorded_blob_mismatch`` because that is the fact the report is
# naming; the others are prefixed so an entry-level code can never be mistaken for a rail answer.
_CODE_BLOB_MISMATCH = "recorded_blob_mismatch"
_CODE_OBSERVATION = "observation_"
_CODE_RESOLUTION_FAILED = "target_resolution_failed"
_CODE_TREE_UNAVAILABLE = "recorded_tree_unavailable"

# The observation rail's own answer, as the report prints it. ``exact_recorded_blob`` is the only
# passing answer: every other answer the rail can give -- ``unsupported_locator``,
# ``recorded_blob_mismatch``, ``path_absent``, ``recorded_object_unavailable`` -- is a refusal, and
# its name travels in the refused entry's code rather than in a count.
_OBSERVED_EXACT = "exact_recorded_blob"

# The one shape an empty target is reported under. Revision 1 describes the honest encoding as
# ``kind: "decision" | "measurement"``, and the fixture's eight rulings measure that the producer's
# hand is wider than that description: two are ``decision`` and six are ``finding`` or
# ``carried-defect``, each with a verdict that applies nowhere ("nothing to do", "not-code; repaired
# on the frozen tree after the barrier"). An empty target is therefore read as the ruling it is,
# whatever its kind, and the kind travels into the report unchanged rather than gating the read --
# refusing a ruling for wearing the wrong kind would lose the verdict the producer authored.
_RULING_SHAPE = "empty_target_ruling"

# The route-leg states. ``ungoverned`` is a fact and not a default: revision 1 permits an absent
# ``governing_route``, and a route invented to fill the field would be scope the producer never
# declared.
_ROUTE_AUTHORED = "authored"
_ROUTE_REUSED = "reused"
# The state a DRY run's ledger carries. It is not a fourth outcome of a real run: nothing was
# authored and nothing was attached, and saying "authored" about a run that wrote nothing is a claim
# about work the run did not do. The projected rows are still counted, because a dry report's row
# arithmetic is what the commit would carry -- but they are counted under a name that says so.
_ROUTE_PROJECTED = "projected"
_ROUTE_UNATTACHED = "authored-not-attached"
_ROUTE_UNGOVERNED = "ungoverned"
_ROUTE_REFUSED = "refused"

# The words the construct check uses to answer "is this name defined in the recorded bytes". The
# check confirms the named file defines the construct; it is never a hint that a range relocated, and
# it never produces an extent the producer did not write.
#
# The languages a symbol locator may name at all. A prose file cannot define a construct, and a
# structured data file's keys are not definitions, so neither may carry a symbol: the refusal says
# which of the two it is instead of resolving a mention into a citation.
_PROSE_LANGUAGES = frozenset({"markdown"})
_STRUCTURED_LANGUAGES = frozenset({"json", "toml"})
_LANGUAGES = {
    ".py": "python",
    ".pyi": "python",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
    ".md": "markdown",
    ".toml": "toml",
    ".json": "json",
    ".sh": "shell",
    ".sql": "sql",
}

# The revision and the one authored field every ingested entry is filed under. The hand-off list
# carries neither, so the ingest states what it authored rather than leaving a reader to infer it.
_INGESTED_VERSION = "v1"
_APPLICABILITY = "Every curator entry ingested from the orchestrator's hand-off list."

# How a blank per-target refusal reads in the report. A target that committed carries no refusal,
# and the empty string would look like one.
_NO_REFUSAL = ""

# The batch state a dry run reports. It is deliberately not "not_attempted", which reads as an
# operation that failed to reach its batch: a dry run reached every step and wrote nothing, and the
# state says exactly that. The entry outcomes and the counts beside it are what the batch *would*
# carry, so the dry report is the run's report with the commit withheld rather than a different one.
_DRY_BATCH_STATE = "dry_run_not_committed"

# The batch state a repeat of an already-admitted creation operation reports. It is deliberately not
# "refused", which is what re-issuing the same inserts earns and is the wrong answer to "repeat the
# operation that already wrote them", and not "changed", because nothing moved. The report's entries
# still carry COMMITTED with the identities they already hold, so a caller sees the repeat succeed
# with the same ids and no second truth.
_REPLAYED_BATCH_STATE = "replayed"


@dataclass(frozen=True)
class TargetOutcome:
    """What happened to one handed-over target: its path, identity, locator, observation, route."""

    path: str
    step: str | None
    completed_path: str | None
    source_identity: str | None
    locator_kind: str | None
    locator: str | None
    observation: str | None
    route_path: str | None
    route_state: str | None
    refusal: str = ""


@dataclass(frozen=True)
class RouteOutcome:
    """What happened to one governing route: the path, the route's id, and which leg answered."""

    route_path: str | None
    route_id: str | None
    state: str
    refusal: str = ""


@dataclass(frozen=True)
class EntryOutcome:
    """One entry's whole outcome: its ruling, or its targets and the route that governs them."""

    entry_id: str
    kind: str
    disposition: str
    disposition_source: str | None
    state: str
    targets: tuple[TargetOutcome, ...] = ()
    routes: tuple[RouteOutcome, ...] = ()
    refusal: str = ""


@dataclass(frozen=True)
class IngestCounts:
    """The counts that make one run auditable, each a fact about this module's own work.

    There is exactly ONE observation counter, because exactly one observation answer can reach a
    committed target: ``anchors_observed_exact``. Every other answer the rail can give --
    ``unsupported_locator``, ``recorded_blob_mismatch``, ``path_absent``,
    ``recorded_object_unavailable`` -- is a refusal, and it travels in the refused entry's own code
    (``observation_<answer>``) where a reader can act on it. Four counters pinned at zero are not an
    audit trail; they are four claims that something was measured when nothing was.
    """

    entries_read: int
    rulings: int
    targets_completed: int
    locators_resolved: int
    anchors_observed_exact: int
    route_paths: int
    routes_authored: int
    routes_reused: int
    commands_sent: int
    records_written: int


@dataclass(frozen=True)
class IngestReport:
    """The ingest's whole result: the destination it used, the batch's state, every outcome.

    ``committed``, ``rulings`` and ``refused`` are the three cases that must never merge: an entry
    appears in exactly one of them. ``batch_refusal`` is the only refusal here that is not per
    entry; it is the batch's own typed refusal when the batch itself refused, and every planned
    entry then appears in ``refused`` with that code, because the operation is all-or-nothing.

    ``code_tree_id`` is the tree the citations were actually read from -- the leaf's own code line --
    and every target's ``source_identity`` is that tree's blob id at the target's path.
    ``code_base_commit`` and ``code_tree_source`` name where that tree came from, so a reader can
    tell a run that resolved the leaf's line from one that fell back to the recorded base.

    ``publication`` is the publication this run performed, when it selected one and its batch
    committed; ``None`` means nothing was published, and ``batch_state`` says why: either the caller
    selected no destination, or the batch did not commit and a candidate that did not change is not
    published.
    """

    contract_path: str
    candidate_directory: str
    candidate_receipt: str | None
    lane: str
    code_tree_id: str
    memory_tree_id: str
    code_base_commit: str
    code_tree_source: str
    repository_id: str
    derived_identities: str
    dry_run: bool
    entries_read: tuple[str, ...]
    committed: tuple[EntryOutcome, ...]
    rulings: tuple[EntryOutcome, ...]
    refused: tuple[EntryOutcome, ...]
    counts: IngestCounts
    batch_state: str
    batch_digest_before: str | None
    batch_digest_after: str | None
    batch_refusal: KnowledgeRefusal | None
    publication: SnapshotPublicationResult | None = None


@dataclass(frozen=True)
class _Plan:
    """One entry as the ingest read it, before anything was written.

    ``invariant_id`` and ``revision_id`` are the two identities this run will write, and for a newly
    declared truth they are the ones :func:`_creation` allocated for this creation operation rather
    than anything derived from the entry's label. ``allocation`` is that allocation, carried so the
    run can record it before the batch and so a repeat of the same operation is recognisable at all;
    an entry naming an existing invariant still allocates (its revision is new), while a ruling
    allocates nothing because it writes nothing.

    ``replayed`` says the candidate this run was admitted against **already holds** this plan's
    revision, so an earlier run of the same operation wrote it and this run writes nothing for it:
    the batch's own insert-absence precondition would refuse that row, and a refusal is not what
    repeating a creation operation that already succeeded means.
    """

    entry_id: str
    kind: str
    disposition: str
    disposition_source: str | None
    statement: str
    evidence: str
    invariant_id: str
    revision_id: str
    targets: tuple[_TargetPlan, ...]
    ruling: bool
    declares_invariant: bool = True
    predecessors: tuple[str, ...] = ()
    allocation: _Allocation | None = None
    replayed: bool = False


@dataclass(frozen=True)
class _TargetPlan:
    """One resolved target: the step that answered, the blob it holds, the anchor to write.

    ``declares_anchor`` is the explicit-reuse half: a target that named a stored anchor identity is
    citing a row the repository already holds, so the batch writes the claim and not the anchor. It
    is the target-level counterpart of ``_Plan.declares_invariant``, and it exists for the same
    reason -- re-declaring a row that is already stored is refused outright, so an entry that means
    to *reuse* one has to say so.
    """

    entry_id: str
    path: str
    step: str
    completed_path: str
    blob: str
    locator: SourceLocator
    observation: str
    role: RealizationRole
    rationale: str
    route_path: str | None
    route_id: str
    anchor_id: UUID
    claim_id: str
    declares_anchor: bool = True

    @property
    def target_key(self) -> tuple[str, str, str]:
        """What makes two targets the SAME place, for the duplicate guard.

        A path alone is not it: two symbols in one file are two realizations, and refusing the second
        as a duplicate of the first is what kept a file with more than one anchor out of the ingest.
        A symbol therefore contributes its qualified name, and a range or whole-file citation
        contributes only the kind it already carried.
        """

        within = self.locator.qualified_name if isinstance(self.locator, SymbolLocator) else ""
        return (self.completed_path, self.locator.kind, within)

    @property
    def anchor(self) -> SourceAnchorDraft:
        """The exact draft the write path stores: path, tree-read identity and the locator."""

        return SourceAnchorDraft(
            anchor_id=self.anchor_id,
            path=self.completed_path,
            source_identity=GitBlobIdentity(object_id=self.blob),
            locator=self.locator,
        )

    def citation(self) -> CuratorCitation:
        """The anchor plus the claim citing it, as the write module's command list expects."""

        return CuratorCitation(
            anchor=self.anchor,
            claim_id=self.claim_id,
            role=self.role,
            rationale=self.rationale or f"The statement is realized at {self.completed_path}.",
            declares_anchor=self.declares_anchor,
        )


@dataclass(frozen=True)
class _TargetIdentities:
    """The three identities one target's own place mints, derived together rather than apart.

    They are one value because they are one decision: which construct inside the file this citation
    is about, applied to each identity the write path needs for it. Splitting them across the dataclass
    that carries them and the caller that derives them is how the anchor and the claim came to key on
    the path alone, so a file with two constructs handed the batch the same ``source_anchor`` twice.
    """

    route_id: str
    anchor_id: str
    claim_id: str


@dataclass(frozen=True)
class _Allocation:
    """The canonical identity one creation operation holds, and the content it was minted for.

    The two identities are **allocated**, not derived: fresh ``uuid4`` values that carry no task, no
    enclosure, no branch, no baseline and no label, which is what makes them usable from any of those
    later. What makes a repeat of one operation resolve to them is the retry key recorded beside them
    -- the enclosure's own task identity joined with the entry's id inside that list -- so two
    independent tasks numbering an entry alike are two operations with two keys and two identities,
    while a rerun of one task's operation finds its own key and is handed back what it already holds.

    ``content_digest`` is the third fact and the guard: the key names ONE creation operation, so the
    same key arriving with different content is refused rather than silently re-allocated or silently
    folded into the stored truth. It is ``None`` between the moment the pair is minted (which must
    happen before the targets are planned, because a claim's identity is the edge it records and that
    edge names the revision) and the moment the places it covers are resolved.
    """

    retry_key: str
    invariant_id: str
    revision_id: str
    content_digest: str | None = None

    def as_record(self) -> dict[str, str]:
        """The exact JSON object the journal stores for this allocation."""

        if self.content_digest is None:  # pragma: no cover - a planned entry settles its content
            raise ValueError(
                "an allocation whose content was never settled cannot be journalled: the journal is "
                "what a retry is refused against, and a record with no content guards nothing"
            )
        return {
            "retryKey": self.retry_key,
            "invariantId": self.invariant_id,
            "revisionId": self.revision_id,
            "contentDigest": self.content_digest,
        }


@dataclass(frozen=True)
class _Allocations:
    """What one candidate's allocation journal holds, and whether it could be read at all.

    An absent journal is the ordinary first-run case and reads as no records. A journal that is there
    and cannot be read is **not** the same fact and is not treated as empty: every entry that would
    have to mint an identity is refused with ``allocation_journal_unreadable``, because minting
    without answering "does this operation already hold one?" is how a retry becomes a duplicate.
    """

    path: Path
    records: Mapping[str, _Allocation]
    unreadable: str | None = None


@dataclass(frozen=True)
class _Authoring:
    """Which entry and which creation author one target, and which stored anchor it reuses.

    Grouped rather than passed as three arguments because they are one decision -- whose creation this
    citation belongs to, and whether it is writing an anchor or reusing one -- and because the entry
    that names the route is a different fact from the creation that keys the anchor and the claim.
    """

    entry_id: str
    revision_id: str
    named_anchor_id: str | None = None


def _target_identities(
    repository: RepositoryIdentity,
    written: str,
    locator: SourceLocator,
    authoring: _Authoring,
) -> _TargetIdentities:
    """The route, anchor and claim identities for one target, each keyed on what it actually is.

    The route is a **scope**, not a place inside one: it stays keyed on the path it governs, so N
    anchors in one file are N associations with the one route row rather than N rows.

    The anchor is the **place** the citation names, and it is keyed on the creation that authored it
    -- the allocated revision id -- rather than on the entry's local hand-off label, with the
    symbol's own qualified name as the disambiguator *inside* that creation (the same discriminator
    the observation identity uses, and the one that survives the file being edited above the
    definition). Two constructs in one file are still two anchors, and two independent tasks that
    both numbered an entry ``R-LOCAL`` and cited one construct now mint two anchors instead of
    aliasing onto one row. What makes a retry recompute the identical anchor id is the revision id:
    it is allocated once and recorded in the candidate's allocation journal, so repeating the
    operation derives the same anchor from the same input, and nothing else has to be persisted.

    The claim is neither a scope nor a place: it is the authored **edge** from one exact revision to
    one exact anchor, and it is keyed on exactly those two endpoints rather than on the entry's local
    hand-off label. That distinction is the ruling, one layer down. Keyed on the label, two
    independent tasks that both numbered an entry ``R-LOCAL`` and cited one construct minted ONE
    claim identity for two genuinely different realizations -- same claim, same role, same rationale,
    different ``invariant_revision_id`` -- and production sync then refused ``duplicate_identity`` on
    that row. A label says nothing about whether two entries represent the same truth, and a claim
    whose endpoints are distinct is a distinct claim. Keyed on the edge, the same revision citing the
    same anchor is the same claim (so a repeat is an idempotent insert of one row), two revisions at
    one place are two claims, and one revision citing two places is two claims.

    ``named_anchor_id`` is the explicit-reuse half: a producer citing a place the repository already
    records names that stored identity outright, and then the anchor is that row verbatim rather than
    a new one. The claim is still keyed on the pair, so citing a stored anchor from a new revision is
    a new edge to a known place.
    """

    if authoring.named_anchor_id is not None:
        return _TargetIdentities(
            route_id=_identity(repository, f"route:{written}", authoring.entry_id),
            anchor_id=authoring.named_anchor_id,
            claim_id=_identity(
                repository, "claim", authoring.revision_id, authoring.named_anchor_id
            ),
        )
    within = locator.qualified_name if isinstance(locator, SymbolLocator) else ""
    anchor_id = _identity(repository, f"anchor:{written}", authoring.revision_id, within)
    return _TargetIdentities(
        route_id=_identity(repository, f"route:{written}", authoring.entry_id),
        anchor_id=anchor_id,
        claim_id=_identity(repository, "claim", authoring.revision_id, anchor_id),
    )


@dataclass(frozen=True)
class _TreeIds:
    """The tree object ids this run resolves against, and the base its identities are anchored to.

    ``code`` and ``memory`` are the **resolution** trees: the code one is the leaf's own line (the
    work branch tip its worktree stands on), because a leaf must be able to cite the code it is
    producing. ``base`` is the enclosure's recorded code base commit, which is what the derived
    identities are anchored to -- identity is a fact about which leaf this is, not about how far the
    leaf has committed since its enclosure was cut.
    """

    code: str
    memory: str
    base: str
    code_source: str


@dataclass(frozen=True)
class _Paths:
    """The two local inputs the operation names: the enclosure contract and the candidate."""

    contract_path: Path
    candidate: Path


@dataclass(frozen=True)
class _Resolved:
    """One completed target: which root answered, the confined path, and the blob held there."""

    step: str
    path: str
    blob: str
    root: Path
    tree_id: str


@dataclass(frozen=True)
class _BlobIdentity:
    """What the working bytes at one resolved path are, and whether the recorded tree agrees.

    ``blob`` is the blob id the **working bytes** hash to, read by ``git hash-object``; ``recorded``
    is the blob id the resolution tree holds at that path. The two agreeing is the exact-recorded-blob
    case; disagreeing is a mismatch the report names with both ids rather than an exception, which is
    what keeps the recorded identity a measurement instead of a restatement of the tree's own answer.
    """

    blob: str
    recorded: str

    @property
    def exact(self) -> bool:
        return self.blob == self.recorded


@dataclass(frozen=True)
class _EntryFields:
    """The producer-side fields one hand-off entry carries, read once and never re-parsed."""

    entry_id: str
    kind: str
    disposition: str
    disposition_source: str | None
    statement: str
    evidence: str
    declares_invariant: bool = True
    predecessors: tuple[str, ...] = ()
    named_invariant_id: str | None = None
    role: RealizationRole | None = None
    role_rationale: str = ""

    @classmethod
    def read(cls, raw: Mapping[str, Any]) -> _EntryFields:
        source = raw.get("disposition_source")
        # A producer that names the invariant it is revising is declaring a successor, not a new
        # invariant. That distinction is what the mapper used to be unable to express: it always
        # emitted AddInvariant followed by AddInvariantRevision, so re-running a changed entry was
        # refused with `batch_stale_precondition` ("expecting the existing invariant to be absent")
        # and the repository could never evolve an obligation it already held. An entry that names no
        # invariant keeps the original behaviour, which is the correct one for a first authoring.
        predecessors = tuple(
            str(one) for one in (raw.get("predecessor_revision_ids") or ()) if str(one).strip()
        )
        # The other half of naming a successor: WHICH invariant is being revised. A producer revising
        # an obligation the repository already holds must be able to name that invariant outright,
        # because the entry's local id is a hand-off label and not an identity: it is the key a retry
        # is found by, never the name of a stored truth.
        declared = raw.get("invariant_id")
        named = str(declared).strip() if declared is not None and str(declared).strip() else None
        # The role a producer authors for this realization, validated against the SHIPPED vocabulary
        # rather than a second copy of it. An unrecognised spelling becomes no role at all: a word
        # this code does not know must not be stored as a semantic claim about the knowledge.
        stated = str(raw.get("realization_role") or "").strip()
        return cls(
            entry_id=str(raw["id"]),
            kind=str(raw.get("kind", "")),
            disposition=str(raw.get("disposition", "")),
            disposition_source=None if source is None else str(source),
            statement=str(raw.get("statement", "")),
            evidence=str(raw.get("evidence", "")),
            declares_invariant=not predecessors,
            predecessors=predecessors,
            named_invariant_id=named,
            role=cast("RealizationRole", stated) if stated in get_args(RealizationRole) else None,
            role_rationale=str(raw.get("realization_rationale") or ""),
        )


@dataclass(frozen=True)
class _Refusal:
    """One refusal before the batch: its exact code, the reason, and what was read before it.

    ``targets`` carries the places this entry's targets did resolve, so the report's entry count and
    its target count describe the same run: an entry refused at its second target still shows the
    first one, rather than the refusal erasing what the read had already established. ``planned``
    carries the same places as plans, which is what the counts read: an outcome records what was
    measured, and the counts need the object rather than a rendering of it.
    """

    code: str
    reason: str
    targets: tuple[TargetOutcome, ...] = ()
    planned: tuple[_TargetPlan, ...] = ()

    def at(self, path: str) -> _Refusal:
        return _Refusal(self.code, f"{path}: {self.reason}", self.targets, self.planned)

    def entry(self, fields: _EntryFields) -> EntryOutcome:
        outcome = _refused_entry(fields, self.code, self.reason)
        return EntryOutcome(
            entry_id=outcome.entry_id,
            kind=outcome.kind,
            disposition=outcome.disposition,
            disposition_source=outcome.disposition_source,
            state=outcome.state,
            targets=self.targets,
            refusal=outcome.refusal,
        )


def _refused_target(target: _TargetPlan) -> TargetOutcome:
    """One target that resolved and observed cleanly before its entry was refused elsewhere."""

    return TargetOutcome(
        path=target.path,
        step=target.step,
        completed_path=target.completed_path,
        source_identity=target.blob,
        locator_kind=target.locator.kind,
        locator=_locator_text(target.locator),
        observation=target.observation,
        route_path=target.route_path,
        route_state=None,
    )


@dataclass
class _RouteLedger:
    """Which route paths one run touched: what it asked for, and what the candidate answered.

    ``expected`` maps a route path to the id this run derived for it; ``answered`` maps it to the id
    the candidate actually holds. The two differ exactly when the path already had a row, which is
    how "authored" and "reused" stay distinguishable rather than being assumed. ``authored`` is the
    record of the rows this run itself wrote, which is what the report counts; an answered path this
    run did not author already had its row before the run started.
    """

    expected: dict[str, str]
    answered: dict[str, str]
    attached: dict[str, tuple[RouteOutcome, ...]]
    authored: set[str]
    projected: bool = False
    projected_scopes: int = 0

    @property
    def reused_paths(self) -> frozenset[str]:
        """The scopes whose row already existed before this run.

        ``author_route`` answers a path that already has a row by returning *that* row's id, so a
        reused scope is one whose answered id is not the id this run derived for it -- or one this
        run already knows it did not author. Both readings are kept because a run can hand a
        candidate a derived id it wrote itself in an earlier run of the same enclosure, in which
        case the two ids are equal and only the ledger's own record separates the two facts.
        """

        if self.projected:
            # A dry run never read the candidate, so it cannot know that any scope's row already
            # existed. Reporting the projection as "reused" would be a measured-looking number the
            # run never measured; the projected scopes are counted by ``projected_scopes`` instead,
            # under a name that says what they are.
            return frozenset()
        return frozenset(
            path
            for path, route_id in self.answered.items()
            if path not in self.authored or self.expected.get(path) != route_id
        )

    @property
    def route_rows(self) -> int:
        """The rows the route leg itself wrote: one per authored scope, one per accepted association.

        The three success states all mean the candidate **accepted** the association and therefore
        holds a row for it: ``authored`` when the route was written by this run, ``reused`` when the
        route already existed but this anchor's association did not, and ``attached`` when a caller
        projected the association as taken. An ungoverned target has no association to write, an
        unattached one was refused and left none, and a refused association left none either -- so
        the count is the associations the candidate accepted, not the number of targets that named a
        route. A scope this run did *not* author contributes no ``route`` row, which is why the
        scope count is ``authored`` rather than ``answered``.
        """

        accepted = sum(
            1
            for ones in self.attached.values()
            for one in ones
            if one.state in (_ROUTE_REUSED, _ROUTE_AUTHORED, _ROUTE_PROJECTED)
        )
        return len(self.authored) + self.projected_scopes + accepted


@dataclass(frozen=True)
class _Read:
    """Everything reading the list produced, so a later step names one value instead of six.

    ``rulings``, ``refused`` and ``planned`` are the three cases kept apart all the way through the
    operation: an entry is in exactly one of them, so no later step is handed a list that already
    merged two of the three. ``refused_targets`` is the other half of that accounting: a place inside
    a refused entry that had *already* completed and observed before the refusal landed elsewhere in
    its entry. It belongs to no plan -- the entry is not committed -- and it is still work this run
    really did, so the report counts it under ``targets_completed`` and not under the committed
    counts.
    """

    ids: tuple[str, ...]
    rulings: tuple[EntryOutcome, ...]
    refused: tuple[EntryOutcome, ...]
    planned: tuple[_Plan, ...]
    resolved_before_refusal: tuple[_TargetPlan, ...] = ()

    @property
    def targets(self) -> tuple[_TargetPlan, ...]:
        return tuple(one for plan in self.planned for one in plan.targets)


@dataclass(frozen=True)
class _ReportTarget:
    """What one report names: the two local paths, the resolution, the trees, and the read."""

    paths: _Paths
    resolution: CandidateResolution
    repository: RepositoryIdentity
    read: _Read
    trees: _TreeIds
    coordination_top_level: frozenset[str]

    def with_read(self, read: _Read) -> _ReportTarget:
        """The same report inputs with a later read, keeping the trees it resolved against."""

        return _ReportTarget(
            self.paths,
            self.resolution,
            self.repository,
            read,
            self.trees,
            self.coordination_top_level,
        )


@dataclass(frozen=True)
class _Source:
    """The enclosure's two roots and its recorded trees, bound once so planning names one value.

    ``anchors`` names the datasets a reused-anchor identity may resolve against, in the order they
    are consulted. Planning needs it because an entry may cite a place the dataset **already
    records** by naming that anchor's stored identity, and the stored row is the only authority on
    what that identity means: the supplied path, blob and locator are redundant restatements of facts
    the dataset already holds, and this is where they are checked against it rather than trusted
    beside it.

    The order is the candidate first, then the baseline. A candidate already on disk is what the next
    run writes into, so it is the dataset whose rows the reuse has to agree with; a run that will
    **create** the candidate clones the baseline, so until that moment the baseline is the dataset
    those rows will come from. Neither is a fallback for the other -- both are consulted, in that
    order, and an identity in neither is refused.
    """

    contract: WorktreeContract
    code_root: Path
    memory_root: Path
    tree_ids: _TreeIds
    coordination_top_level: frozenset[str]
    repository: RepositoryIdentity
    anchors: tuple[Path, ...] = ()


# --------------------------------------------------------------------------------------------
# The operation
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class IngestPublication:
    """Where a committed run publishes its candidate, and what the caller admitted is there.

    Selecting one is what makes publication reachable from the curation workflow: the run holds the
    admitted candidate destination and reads the candidate's live identity itself, so the curator
    never has to re-derive a namespace, a resolution or a digest by hand to reach the publication
    owner. ``expected_destination`` is the exact logical identity the caller observed at the
    destination, or ``None`` for "the destination is expected to be absent" -- the publication
    owner's own two modes, carried rather than reinterpreted here, so a publication can never
    overwrite a file the caller never admitted.
    """

    destination_path: Path
    expected_destination: SnapshotIdentity | None = None


@dataclass(frozen=True)
class IngestSelection:
    """Everything one run selects: where it writes, who authorized it, what it forks, and whether.

    Grouped rather than passed as four trailing arguments for a substantive reason, not to satisfy a
    linter: ``baseline`` and ``candidate_directory`` are the two halves of one decision -- the dataset
    this task forks FROM and the candidate it writes TO -- and a run that names one without being
    able to name the other is how continuity was lost in the first place. Keeping them in one value
    makes that pairing visible at every call site. ``publication`` joins them because it is the third
    half of the same decision: a candidate the caller cannot publish is knowledge the repository
    never receives.
    """

    candidate_directory: Path
    authorization_ref: str
    dry_run: bool
    baseline: Path | None = None
    publication: IngestPublication | None = None


def ingest_curator_list(
    contract_path: str | Path,
    entries: Sequence[Mapping[str, Any]] | str | Path,
    selection: IngestSelection,
) -> IngestReport:
    """Commit one hand-off list into the enclosure's candidate, or report why it was not.

    ``entries`` is the hand-off list in revision 1's shape -- the parsed list, or the path of the
    file carrying it. ``candidate_directory`` is named by the caller and echoed in the report: a
    durable convention for it belongs in the memory layer's settings, not in this operation.
    ``authorization_ref`` is required because an admitted write needs one: ``Authorship`` refuses a
    blank reference, so a default here would build an envelope that cannot validate. It is also the
    actor the envelope names -- one required reference rather than two optional ones, which is how
    "who authorized this" and "who authored it" stay the same admitted fact instead of two strings
    that can disagree.
    ``dry_run=True`` returns the identical report having written nothing, and is the only mode that
    may run without the developer's commit word.

    No input this operation can *read* makes it raise. A target the citation machinery cannot resolve
    -- an unreadable identity, an unaddressable path, a construct the file does not define -- is
    refused with the reason that names the failure and the run continues to the next entry, because
    the caller receives a report rather than a traceback for exactly those inputs.
    """

    authorization_ref = selection.authorization_ref
    candidate_directory = selection.candidate_directory
    baseline = selection.baseline
    dry_run = selection.dry_run
    _require_authorization(authorization_ref)
    paths = _Paths(contract_path=Path(contract_path), candidate=Path(candidate_directory))
    contract = load_contract(paths.contract_path)
    code_root, memory_root = _roots(contract)
    repository = _repository_identity(contract, baseline)
    source = _Source(
        contract=contract,
        code_root=code_root,
        memory_root=memory_root,
        tree_ids=_tree_ids(contract, code_root, memory_root),
        coordination_top_level=_coordination_top_level(contract),
        repository=repository,
        anchors=tuple(
            one for one in (paths.candidate, baseline) if one is not None and Path(one).is_file()
        ),
    )
    resolution = _resolution(contract, source.tree_ids)
    raw = _read_entries(entries)
    allocations = _read_allocations(paths.candidate)
    plans, refused, resolved_before_refusal = _plan_entries(raw, source, allocations)
    read = _Read(
        ids=tuple(str(one["id"]) for one in raw),
        rulings=tuple(_ruling_outcome(plan) for plan in plans if plan.ruling),
        refused=refused,
        planned=tuple(plan for plan in plans if not plan.ruling),
        resolved_before_refusal=resolved_before_refusal,
    )
    if dry_run:
        return _report(
            _ReportTarget(
                paths,
                resolution,
                repository,
                read,
                source.tree_ids,
                source.coordination_top_level,
            ),
            _projected(read.planned),
            committed=_projected_outcomes(read.planned),
            dry_run=True,
        )
    authorship = write_authorship(
        actor_ref=authorization_ref,
        authorization_ref=authorization_ref,
        origin_refs=("curator-handoff:revision-1",),
    )
    admission = _admitted_candidate(paths.candidate, repository, resolution, baseline=baseline)
    if admission.state == "refused" or admission.result.identity is None:
        # The destination itself refused, so nothing was planned and nothing was written -- and the
        # planned entries are folded into ``refused`` rather than silently dropped. The report says
        # an entry appears in EXACTLY ONE of committed/rulings/refused, and that promise is what a
        # caller counts on to know every entry it handed over was accounted for; the admission leg
        # was the one refusal path that returned the read unchanged and left its entries in no list.
        return _report(
            _ReportTarget(
                paths,
                resolution,
                repository,
                _with_refused(read, _admission_refused(read.planned, admission.result)),
                source.tree_ids,
                source.coordination_top_level,
            ),
            _Run(batch_state="not_attempted", refusal=admission.refusal),
        )
    admitted = admitted_candidate_destination(paths.candidate, repository, resolution)
    destination = candidate_write_destination(admitted, authorship)
    # The allocation is recorded BEFORE the batch, and it is recorded only now: the candidate that
    # holds the journal is the one admission just produced, so this never creates the destination it
    # writes into. A batch that then refuses has still made this creation operation's allocation, and
    # the retry of that operation resolves to it instead of minting a second identity for one truth.
    _record_allocations(allocations, read.planned)
    # ... and only a candidate that already HOLDS a plan's revision makes that plan a replay, which
    # is a question the dataset answers and the journal cannot: the journal cannot know whether the
    # batch that ran after it was written committed.
    read = _with_replays(read, admitted.database_path)
    report = _run(
        _ReportTarget(
            paths,
            resolution,
            repository,
            read,
            source.tree_ids,
            source.coordination_top_level,
        ),
        destination,
    )
    # Publication runs last and only over a candidate this run actually committed into: an entry in
    # ``committed`` is the run's own statement that the batch landed, so a refused or un-attempted
    # batch is never published and the caller is never handed a dataset change it did not make.
    if selection.publication is None or not report.committed:
        return report
    return replace(report, publication=_publish_candidate(admitted, selection.publication))


def _run(target: _ReportTarget, destination: AdmittedKnowledgeDestination) -> IngestReport:
    """Author the routes, commit the one batch, attach the routes, and report every outcome.

    The batch carries only the plans this run must **write**. A replayed plan's revision is already
    in the candidate, so re-issuing its commands would be refused by the batch's own insert-absence
    precondition -- which is the right answer to "write this again" and the wrong answer to "repeat
    the operation that already wrote it". A replay is therefore reported committed with the identities
    it already holds and contributes no command, no row and no route attachment.
    """

    resolution, repository, read = target.resolution, target.repository, target.read
    fresh = tuple(plan for plan in read.planned if not plan.replayed)
    ledger = _RouteLedger(
        expected=_distinct_routes(read.planned),
        answered=_replayed_routes(read.planned),
        attached={},
        authored=set(),
    )
    if read.planned and not fresh:
        # Every plan this run was handed is already in the candidate. There is no batch to run: the
        # routes are the ones the admitting runs wrote, so the ledger answers them as reused and
        # accepts nothing, which is exactly the rows this run wrote -- none.
        return _report(
            target,
            _Run(batch_state=_REPLAYED_BATCH_STATE, ledger=ledger),
            committed=tuple(_replayed_outcome(plan) for plan in read.planned),
        )
    route_refusals = _author_routes(destination, repository, ledger, fresh)
    if route_refusals:
        return _report(
            target.with_read(_with_refused(read, route_refusals)),
            _Run(batch_state="not_attempted"),
        )
    result = _commit(destination, resolution, fresh)
    if result is None or result.state == "refused":
        return _report(
            target.with_read(_with_refused(read, _batch_refused(fresh, result))),
            _Run(result=result, ledger=ledger),
        )
    _attach_routes(destination, repository, fresh, ledger)
    committed = tuple(
        _replayed_outcome(plan)
        if plan.replayed
        else _committed_outcome(plan, ledger.attached.get(plan.entry_id, ()))
        for plan in read.planned
    )
    commands = sum(len(curator_entry_commands(destination, _curator_entry(plan))) for plan in fresh)
    # The same projection the dry mode reports, so the two modes agree on what the batch carries
    # before the receipt is read: four rows per citation, one per command.
    projected = _projected(fresh).batch_rows
    return _report(
        target,
        _Run(result=result, commands=commands, records=projected, ledger=ledger),
        committed=committed,
    )


def _publish_candidate(
    admitted: AdmittedCandidateDestination, publication: IngestPublication
) -> SnapshotPublicationResult:
    """Publish the candidate this run just committed, into the destination the caller admitted.

    The candidate's identity is READ here rather than taken from the run's own admission, and that is
    the whole reason publication belongs to this operation: a ``SnapshotIdentity`` carries the
    dataset's logical digest, so the batch that just committed changed it, and a caller cannot name a
    value that only exists after the write. ``open_knowledge_candidate`` is the product's own
    non-raising read of exactly that value, so a candidate that cannot be read arrives as a refused
    publication carrying that refusal rather than as a traceback.

    A candidate that was admitted without an identity and without a refusal is not representable --
    ``CandidateResult``'s own validator refuses that shape -- so it is raised as the defect it is
    rather than dressed as a refusal.
    """

    opened = open_knowledge_candidate(admitted)
    if opened.refusal is not None:
        return SnapshotPublicationResult(state="refused", refusal=opened.refusal)
    if opened.identity is None:  # pragma: no cover - CandidateResult admits no such shape
        raise ValueError(
            "the committed candidate was admitted with neither an identity nor a refusal, which "
            "CandidateResult does not admit: the admission read is defective, and this is not a "
            "refusal this operation can report"
        )
    return publish_knowledge_snapshot(
        admitted,
        PublishSnapshotRequest(
            expected_candidate=opened.identity,
            destination=SnapshotDestinationRequest(
                destination_path=publication.destination_path,
                expected_destination=publication.expected_destination,
            ),
        ),
    )


def _with_refused(read: _Read, extra: tuple[EntryOutcome, ...]) -> _Read:
    """The same read with more refusals, so a report always sees one value.

    A refusal that arrives after the read -- a route leg that could not record, a batch that refused
    -- has no completed target of its own to add: those refusals are whole-entry facts, so the
    places the read had already completed stay exactly as the read recorded them.
    """

    return _Read(
        ids=read.ids,
        rulings=read.rulings,
        refused=(*read.refused, *extra),
        planned=read.planned,
        resolved_before_refusal=read.resolved_before_refusal,
    )


def _require_authorization(authorization_ref: str) -> None:
    """Refuse a blank authorization by name, rather than letting the envelope fail to build."""

    if not authorization_ref.strip():
        raise ValueError(
            "an admitted write needs the authorization it is made under, so authorization_ref "
            "must not be blank"
        )


# --------------------------------------------------------------------------------------------
# Admission: the candidate directory, its receipt, and the identity the enclosure derives
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Admission:
    """One candidate directory's admission: created, resumed, or refused with the product's answer."""

    state: str
    result: CandidateResult

    @property
    def refusal(self) -> KnowledgeRefusal | None:
        return self.result.refusal


def _admitted_candidate(
    candidate: Path,
    repository: RepositoryIdentity,
    resolution: CandidateResolution,
    *,
    baseline: Path | None = None,
) -> _Admission:
    """Resume the candidate this admission names, or create it -- forking the selected baseline.

    An existing destination is a resume attempt, never permission to initialize over it, so the
    product's own open answers first and the operation acts on that answer. A directory the open
    refused and that does exist is this operation's refusal too: the bytes there are unpublished
    authored work, and only an explicitly authorized reconciliation may touch them.

    When the directory is absent the candidate is **forked from the selected baseline** rather than
    initialized empty. That distinction is the whole of the continuity defect: an empty candidate
    holds only this task's new entry, so the repository's existing invariants are absent from it and
    the next task starts from nothing. ``clone_knowledge_candidate`` already shipped and was never
    called from here -- the baseline is what it needs, and a run that selected none still creates an
    empty candidate, which is the correct behaviour for a repository's first task.

    ``CandidateBaseline.expected_identity`` is the baseline's own identity re-read and compared
    before a byte is copied, so a baseline that moved since it was selected is caught rather than
    silently cloned from.
    """

    destination = admitted_candidate_destination(candidate, repository, resolution)
    if candidate.exists():
        opened = open_knowledge_candidate(destination)
        return _Admission(opened.state, opened)
    if baseline is not None:
        selected = Path(baseline)
        forked = clone_knowledge_candidate(
            destination,
            CandidateBaseline(database_path=selected, expected_identity=dataset_identity(selected)),
        )
        if forked.state == "created":
            return _Admission("created", forked)
        return _Admission("refused", forked)
    created = create_knowledge_candidate(destination)
    if created.state == "created":
        return _Admission("created", created)
    return _Admission("refused", created)


def _repository_namespace(database_path: Path | None) -> RepositoryIdentity | None:
    """The namespace one knowledge database already records, or ``None`` when it records none.

    This is the durable anchor, and it is read rather than derived. ``models/knowledge/repository.py``
    requires a namespace to be a *stored* identifier, and the contract a run is handed carries no
    stable repository key to derive one from -- ``code_repo_path`` differs per worktree,
    ``code_source_branch`` is a branch name, and ``repo_name`` is the display name that module names
    as explicitly not an identity. So the value that survives a baseline change cannot come from the
    contract; it comes from the repository's own dataset.

    An absent, unreadable or repository-less database is not an error here: it means this repository
    has no stored namespace yet, and the caller derives one. ``apsw`` is caught narrowly because a
    path that is not a database at all is an ordinary input in an operation that is allowed to
    create one.
    """

    if database_path is None or not database_path.is_file():
        return None
    try:
        connection = open_read_only_database(Path(database_path))
    except (apsw.Error, OSError):
        return None
    try:
        rows = list(connection.execute("SELECT repository_id, authority_home FROM repository"))
    except apsw.Error:
        return None
    finally:
        connection.close()
    if not rows:
        return None
    return decode_repository_row(rows[0])


def _repository_identity(
    contract: WorktreeContract, baseline: Path | None = None
) -> RepositoryIdentity:
    """The namespace this repository's candidates are created under, read then derived.

    ``RepositoryIdentity`` is supplied on creation and read from the database afterwards, so the
    order here is the product's own: **read the stored value first, derive only when there is none
    to read.** A repository that already holds knowledge keeps the namespace that knowledge lives
    under, whatever baseline the next task starts from.

    This used to derive from ``_enclosure(contract)``, which is the enclosure's recorded code base
    commit. That made the namespace a function of the baseline, so one repository ingested at a
    later baseline was handed a *different* namespace, its candidate held only the new entry, and
    every revision recorded at the earlier baseline was stranded under a namespace nothing would
    look in again -- while two different repositories that shared a base commit were handed the
    *same* namespace. ``models/knowledge/repository.py`` states the rule this violated: a namespace
    "is not a filesystem root, a branch name or a repository display name: those all change while
    the knowledge they scope does not".

    The derivation that remains is the cold-start fallback, and it is keyed on ``authority_home``
    rather than on the baseline. It is stable for one repository across baselines, which is what
    the finding needs; it deliberately claims nothing about telling two repositories apart, because
    the contract cannot supply that distinction -- the stored namespace is what does, and the first
    run that publishes one makes it authoritative from then on.
    """

    stored = _repository_namespace(baseline)
    if stored is not None:
        return stored
    return RepositoryIdentity(
        repository_id=str(uuid5(_INGEST_NAMESPACE, f"repository:{contract.repo_name}")),
        authority_home=contract.repo_name,
    )


def _resolution(contract: WorktreeContract, tree_ids: _TreeIds) -> CandidateResolution:
    """The candidate inputs, read from the enclosure's recorded pair rather than asserted."""

    leaf = _enclosure(contract)
    return CandidateResolution(
        lane=_CANDIDATE_LANE,
        code_tree_id=tree_ids.code,
        memory_tree_id=tree_ids.memory,
        snapshot_ref=f"curator-ingest:{leaf}",
        candidate_ref=f"curator-ingest:{leaf}",
        code_commit_id=contract.code_base_commit,
        memory_commit_id=contract.memory_base_commit,
        task_ref=_retry_scope(contract),
    )


def _enclosure(contract: WorktreeContract) -> str:
    """The one string that identifies this enclosure for identity derivation.

    Its recorded code base commit, because that is the fact distinguishing one enclosure's base
    from another's and it is already recorded in the contract the operation was handed.
    """

    return contract.code_base_commit


def _roots(contract: WorktreeContract) -> tuple[Path, Path]:
    """The code worktree and the memory worktree the contract records, both required."""

    if contract.memory_worktree is None:
        raise ValueError(
            f"contract {contract.contract_path} records no memory worktree, so the two roots the "
            "citation machinery admits cannot both be named"
        )
    return contract.code_worktree, contract.memory_worktree


def _tree_ids(contract: WorktreeContract, code_root: Path, memory_root: Path) -> _TreeIds:
    """The tree each side is read through: the leaf's own code line, and the memory base.

    The code side is the **work branch tip the worktree stands on**, not the recorded base commit,
    because the operation runs inside a leaf's enclosure and the leaf is citing the code it is
    producing: its own new module and its own edits exist in the line and not in the tree the
    enclosure was cut from. Reading the base tree made both of those unanswerable -- the added file
    was reported *gone* and the modified file raised before a report existed.

    The memory side stays the recorded memory base commit: the memory worktree is read at the exact
    tree the enclosure recorded, and a memory citation is a claim about the memory line's content
    rather than about unlanded local edits.

    A worktree with no readable code line falls back to the recorded base commit rather than
    refusing the run: the base is the one tree the contract itself guarantees, and a run that has to
    fall back says so in the report's ``code_tree_source`` instead of silently resolving against a
    tree the caller did not expect.
    """

    code_line, code_source = _code_line(code_root, contract)
    return _TreeIds(
        code=_tree_of(code_root, code_line, contract.contract_path),
        memory=_tree_of(memory_root, contract.memory_base_commit, contract.contract_path),
        base=contract.code_base_commit,
        code_source=code_source,
    )


def _code_line(code_root: Path, contract: WorktreeContract) -> tuple[str, str]:
    """The leaf's own line as a commit id, with the fact of which answer it is.

    The work branch is asked for by name first, and the worktree's own ``HEAD`` second, because the
    branch is the enclosure's declared line and ``HEAD`` is what the checkout happens to stand on.
    Both are read from the worktree rather than from the shared checkout.
    """

    for reference in (contract.code_work_branch, "HEAD"):
        if not reference:
            continue
        result = run_git(code_root, ["rev-parse", "--verify", f"{reference}^{{commit}}"])
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip(), f"work-line:{reference}"
    return contract.code_base_commit, "recorded-base-fallback"


def _tree_of(root: Path, commit: str, contract_path: Path) -> str:
    """One recorded commit's tree object id, or a named error for an input that cannot answer."""

    if not commit:
        raise ValueError(f"contract {contract_path} records no base commit for {root}")
    result = run_git(root, ["rev-parse", f"{commit}^{{tree}}"])
    if result.returncode != 0:
        raise ValueError(
            f"the recorded base commit {commit} is not available in {root}, so the tree the "
            "citation machinery resolves against cannot be named"
        )
    return result.stdout.strip()


# --------------------------------------------------------------------------------------------
# Step 0: the identity this creation operation is allocated, and the key a retry finds it by
# --------------------------------------------------------------------------------------------


def _retry_scope(contract: WorktreeContract) -> str:
    """The enclosure's own name for this operation's task, as the retry key's scoping half.

    One declaration, used by the candidate resolution and by the retry key, because the two are the
    same fact about the enclosure and a second spelling of it could drift from the one the rest of
    the operation already reports.
    """

    return contract.leaf_id or contract.task_name


def _retry_key(contract: WorktreeContract, entry_id: str) -> str:
    """The idempotency key of one entry's creation operation.

    It is **not** an identity input and never becomes one. It is the question a retry asks -- "has
    this operation already been allocated?" -- and it is scoped by the enclosure's own task identity,
    which is exactly what the ruling permits enclosure identity to scope. Two independent tasks
    numbering an entry ``R-LOCAL`` therefore ask two different questions and receive two different
    identities, while a rerun of one task's operation asks its own question and receives what it
    already holds.
    """

    return f"{_retry_scope(contract)}|{entry_id}"


def _content_digest(fields: _EntryFields, targets: Sequence[_TargetPlan]) -> str:
    """A digest over the semantic write intent one creation operation is minting an identity for.

    It covers every input of this operation whose change alters the stored truth, its lineage, or its
    realization and attribution: the kind, the statement, the evidence, the producer's disposition
    **and the source it rules from**, which invariant an entry revises when it names one, the
    predecessor edges it declares, the authored realization role and its rationale, and each resolved
    place with the locator that names the construct inside it.

    Two things are deliberately out. ``entry_id`` is the key's own scoping half rather than content:
    it is what :func:`_retry_key` normalizes into the question a retry asks, so digesting it would
    fold the key into the value the key is supposed to guard. ``declares_invariant`` is derived, not
    authored -- it is ``not predecessors`` by construction -- so it carries no fact its own source
    field does not already carry, and digesting a derivation beside its source is how one intent
    comes to have two spellings that can disagree.

    It is deliberately *not* an identity: two entries with different content are two operations even
    under one key, and the digest is what lets that be refused instead of absorbed.
    """

    return sha256_digest(
        {
            "kind": fields.kind,
            "statement": fields.statement,
            "evidence": fields.evidence,
            "disposition": fields.disposition,
            "dispositionSource": fields.disposition_source,
            "namedInvariantId": fields.named_invariant_id,
            "predecessors": list(fields.predecessors),
            "role": fields.role,
            "roleRationale": fields.role_rationale,
            "targets": [
                {"path": one.completed_path, "locator": _locator_text(one.locator)}
                for one in targets
            ],
        }
    )


def _allocation_journal(directory: Path) -> Path:
    """The candidate-local journal one run's allocations are recorded in."""

    return Path(directory) / _ALLOCATION_JOURNAL_NAME


def _read_allocations(directory: Path) -> _Allocations:
    """Read the journal this candidate holds, and say plainly when it cannot be read.

    An absent journal is the ordinary case for a candidate nothing has ingested into yet, and it
    reads as no records. A journal that is present and unreadable -- truncated, edited, written by
    something else -- is reported as unreadable rather than as empty, because those two answers lead
    to opposite actions and only one of them is safe.
    """

    path = _allocation_journal(directory)
    if not path.is_file():
        return _Allocations(path, {})
    try:
        loaded = decoded_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        return _Allocations(path, {}, f"the journal is not readable JSON: {error}")
    if not isinstance(loaded, list):
        return _Allocations(path, {}, "the journal is not a list of allocations")
    records: dict[str, _Allocation] = {}
    for one in loaded:
        record = _allocation_record(one)
        if record is None:
            return _Allocations(path, {}, "the journal carries a record this code cannot read")
        records[record.retry_key] = record
    return _Allocations(path, records)


def _allocation_record(raw: object) -> _Allocation | None:
    """One journal entry as an allocation, or ``None`` when it is not one this code wrote."""

    if not isinstance(raw, Mapping):
        return None
    names = ("retryKey", "invariantId", "revisionId", "contentDigest")
    values = [raw.get(name) for name in names]
    if not all(isinstance(value, str) and value for value in values):
        return None
    return _Allocation(*(cast("str", value) for value in values))


def _record_allocations(allocations: _Allocations, plans: Sequence[_Plan]) -> None:
    """Record the identities this run allocated, **before** the batch that could still refuse.

    The order is the whole point. A run whose batch refuses has still *made* this creation
    operation's allocation, and the retry of that operation has to resolve to it rather than mint a
    second identity for one truth -- so the record is written ahead of the commit, and it is written
    only into a candidate that admission already produced, so this never creates the destination it
    names. A run that allocated nothing writes nothing, which is why a replay leaves the file alone.
    """

    minted = {one.retry_key: one for one in (plan.allocation for plan in plans) if one is not None}
    merged = {**allocations.records, **minted}
    if merged.keys() == allocations.records.keys():
        return
    payload = canonical_json_bytes([merged[key].as_record() for key in sorted(merged)])
    atomic_write_bytes(allocations.path, payload)
    if allocations.path.read_bytes() != payload:
        raise ValueError(
            f"the allocation journal at {allocations.path} did not read back as it was written, so "
            "the identities this run allocated are not durably recorded and a retry could not find "
            "them"
        )


def _creation(
    source: _Source,
    fields: _EntryFields,
    allocations: _Allocations,
) -> tuple[_Allocation | None, _Refusal | None]:
    """The identity pair one entry's creation operation holds: recorded if it has one, else minted.

    A key the journal already carries is this operation repeating itself, so the identities it was
    allocated are handed back. A key the journal does not carry is a **new** truth: it gets fresh
    identities of its own, allocated here and never derived from the label, the enclosure, the branch
    or the baseline.

    An entry that names an existing invariant keeps the producer's identity and still allocates a
    revision: the invariant is the producer's to name, and each new revision is a new immutable
    record with an identity of its own that references its predecessors.

    This runs **before** the targets are planned, because a claim's identity is the edge it records
    and that edge names the revision. The content the key was minted for is settled afterwards, by
    :func:`_require_minted_content`, once the places it covers are resolved.
    """

    retry_key = _retry_key(source.contract, fields.entry_id)
    held = allocations.records.get(retry_key)
    if held is None and allocations.unreadable is not None:
        return None, _Refusal(
            _CODE_ALLOCATION_UNREADABLE,
            f"{allocations.unreadable}, so this entry cannot be told whether its creation operation "
            f"already holds an identity and no new one is minted over an answer that is not known "
            f"({allocations.path})",
        )
    if held is not None:
        return held, None
    return (
        _Allocation(
            retry_key=retry_key,
            invariant_id=fields.named_invariant_id or str(uuid4()),
            revision_id=str(uuid4()),
        ),
        None,
    )


def _require_minted_content(
    allocation: _Allocation,
    fields: _EntryFields,
    targets: Sequence[_TargetPlan],
) -> tuple[_Allocation, _Refusal | None]:
    """Settle the content one allocation was minted for, and refuse a key whose content moved.

    A newly minted allocation has no recorded content yet, so the digest is stamped here. An
    allocation the journal handed back already carries one, and a different digest under the same key
    is refused: one idempotency key names one creation operation, and a different truth wearing it is
    a different operation -- neither the stored truth nor this entry is overwritten by the other.
    """

    digest = _content_digest(fields, targets)
    if allocation.content_digest is None:
        return replace(allocation, content_digest=digest), None
    if allocation.content_digest != digest:
        return allocation, _Refusal(
            _CODE_ALLOCATION_CONFLICT,
            f"the creation operation {allocation.retry_key!r} was allocated "
            f"{allocation.invariant_id}/{allocation.revision_id} for content whose digest is "
            f"{allocation.content_digest}, and this entry arrives under the same key with the "
            f"different digest {digest}; one idempotency key names one creation operation, and "
            "neither the stored truth nor this entry is overwritten by the other",
        )
    return allocation, None


def _stored_revisions(database: Path) -> dict[str, str]:
    """Every revision the candidate already holds, by revision id, with the statement it records.

    This is what makes a repeat of an operation a **replay** rather than a second write. The identity
    a repeat resolves to is the same one, so the candidate that already holds that revision is
    answering the question "was this creation admitted?" from the dataset itself -- not from the
    journal, which cannot know whether a batch that ran after the record was written committed.
    """

    connection = open_read_only_database(database)
    try:
        return {
            str(row[0]): str(row[1])
            for row in connection.execute("SELECT revision_id, statement FROM invariant_revision")
        }
    finally:
        connection.close()


def _with_replays(read: _Read, database: Path) -> _Read:
    """Mark every plan an earlier run of this same operation already stored as a replay."""

    held = _stored_revisions(database)
    return replace(
        read,
        planned=tuple(replace(plan, replayed=plan.revision_id in held) for plan in read.planned),
    )


# --------------------------------------------------------------------------------------------
# Step 1: the list as data
# --------------------------------------------------------------------------------------------


def _read_entries(
    entries: Sequence[Mapping[str, Any]] | str | Path,
) -> tuple[Mapping[str, Any], ...]:
    """The hand-off list as data: the parsed list itself, or the file carrying it.

    No prose is parsed. A caller that hands over a path hands over the same JSON a caller that hands
    over the list already holds, so the two spellings cannot disagree about what was read. The
    ``id`` is the curator's handle for an entry, so two entries claiming one id is an input error
    the operation refuses before it writes anything.
    """

    if isinstance(entries, (str, Path)):
        loaded = json.loads(Path(entries).read_text(encoding="utf-8"))
    else:
        loaded = list(entries)
    if not isinstance(loaded, list):
        raise ValueError("the hand-off list is a JSON list of entries")
    seen: set[str] = set()
    for one in loaded:
        if not isinstance(one, Mapping):
            raise ValueError("every hand-off entry is a JSON object")
        entry_id = str(one.get("id", ""))
        if not entry_id:
            raise ValueError("every hand-off entry carries its own id")
        if entry_id in seen:
            raise ValueError(f"two hand-off entries carry the id {entry_id!r}")
        seen.add(entry_id)
    return tuple(loaded)


def _plan_entries(
    entries: Sequence[Mapping[str, Any]], source: _Source, allocations: _Allocations
) -> tuple[tuple[_Plan, ...], tuple[EntryOutcome, ...], tuple[_TargetPlan, ...]]:
    """Read every entry into a plan, keeping each entry's own refusals beside the plans.

    The third value is the places inside *refused* entries that had already completed before the
    refusal landed elsewhere in the same entry. They belong to no plan and they are still work the
    run did, so the report counts them where it says what was read, rather than letting a refusal at
    one target erase the target beside it from the accounting.
    """

    plans: list[_Plan] = []
    refused: list[EntryOutcome] = []
    resolved_before_refusal: list[_TargetPlan] = []
    for raw in entries:
        plan, refusal = _plan_entry(raw, source, allocations)
        if refusal is not None:
            refused.append(refusal.entry(_EntryFields.read(raw)))
            resolved_before_refusal.extend(refusal.planned)
        elif plan is not None:
            plans.append(plan)
    return tuple(plans), tuple(refused), tuple(resolved_before_refusal)


def _plan_entry(
    raw: Mapping[str, Any],
    source: _Source,
    allocations: _Allocations,
) -> tuple[_Plan | None, _Refusal | None]:
    """Read one entry: its ruling, or its targets completed, each anchor observed, and its identity.

    The identity pair is decided **first**, before any target is planned, because a claim's identity
    is the edge it records and that edge names the revision: a claim keyed on the entry's local
    hand-off label is exactly the defect this seam repairs one layer down. The *content* the key was
    minted for is settled last, once the places are resolved, so the guard against a changed entry
    arriving under one idempotency key is unchanged.

    A ruling decides nothing: it writes nothing, so it is allocated nothing.
    """

    fields = _EntryFields.read(raw)
    targets = list(raw.get("target") or [])
    if not targets:
        return _ruling_plan(fields), None
    allocation, refusal = _creation(source, fields, allocations)
    if refusal is not None:
        return None, _Refusal(refusal.code, refusal.reason)
    if allocation is None:  # pragma: no cover - a creation and its refusal are exclusive
        raise ValueError("an entry with targets resolved without a creation or a refusal")
    planned: list[_TargetPlan] = []
    seen: set[tuple[str, str, str]] = set()
    for target in targets:
        plan, refusal = _plan_target(
            fields,
            target,
            source,
            allocation.revision_id,
        )
        if refusal is not None:
            # The refusal carries the places this entry's earlier targets already resolved, so an
            # entry refused at its second target still shows the first one: the report's entry
            # count and its target count then describe the same run.
            return None, _Refusal(
                refusal.code,
                refusal.reason,
                _refused_targets(planned),
                tuple(planned),
            )
        if plan is None:  # pragma: no cover - a plan and its refusal are exclusive
            continue
        if plan.target_key in seen:
            return None, _Refusal(
                "duplicate_target_path",
                f"the entry names {plan.completed_path!r} twice, so one of the two places the "
                "producer named would be filed under the other's identity",
                _refused_targets(planned),
                tuple(planned),
            )
        seen.add(plan.target_key)
        planned.append(plan)
    allocation, refusal = _require_minted_content(allocation, fields, planned)
    if refusal is not None:
        return None, _Refusal(
            refusal.code,
            refusal.reason,
            _refused_targets(planned),
            tuple(planned),
        )
    return (
        _Plan(
            entry_id=fields.entry_id,
            kind=fields.kind,
            disposition=fields.disposition,
            disposition_source=fields.disposition_source,
            statement=fields.statement,
            evidence=fields.evidence,
            invariant_id=allocation.invariant_id,
            revision_id=allocation.revision_id,
            targets=tuple(planned),
            ruling=False,
            declares_invariant=fields.declares_invariant,
            predecessors=fields.predecessors,
            allocation=allocation,
        ),
        None,
    )


def _refused_targets(planned: Sequence[_TargetPlan]) -> tuple[TargetOutcome, ...]:
    """The rendered outcomes of the places one entry's refusal recorded as already completed."""

    return tuple(_refused_target(one) for one in planned)


def _ruling_plan(fields: _EntryFields) -> _Plan:
    """The empty-target case: a ruling that applies nowhere, carrying its own verdict.

    The entry is reported **skipped**, not refused and not committed: nothing failed, and there is
    no construct to cite, so there is no claim about code to record. ``CuratorEntry.citations`` may
    be empty, but reaching that path here would file a claim about code the producer never made --
    which is why the ruling is reported instead of committed as an unrealized obligation.
    """

    return _Plan(
        entry_id=fields.entry_id,
        kind=fields.kind,
        disposition=fields.disposition,
        disposition_source=fields.disposition_source,
        statement=fields.statement,
        evidence=fields.evidence,
        invariant_id="",
        revision_id="",
        targets=(),
        ruling=True,
    )


def _plan_target(
    fields: _EntryFields,
    target: Mapping[str, Any],
    source: _Source,
    revision_id: str,
) -> tuple[_TargetPlan | None, _Refusal | None]:
    """Complete one target's path, read its identity, verify its locator, and observe the anchor.

    This is the boundary at which the citation machinery's own exceptions stop being exceptions. The
    resolver raises for a tree it cannot read, a member that is not a regular file, and bytes that
    changed under it while it was hashing them; all three are conditions a report has a place for,
    and the promise this operation makes is that every entry's outcome is reported. Anything that is
    not one of those named conditions is left to propagate: a defect in this module is not a refusal,
    and dressing one as a refusal would be the failure mode this boundary exists to avoid, pointed
    the other way.
    """

    # A target the reader can *see* is malformed is refused with the reason, not raised on: the
    # producers write JSON by hand, and ``[null]``, a bare string and a bare mapping all reach here
    # from a list a human typed. The promise this operation makes is a report for every entry, and
    # an ``AttributeError`` from ``target.get`` is the one outcome that promise forbids -- it is
    # neither a refusal nor a defect in this module.
    if not isinstance(target, Mapping):
        return None, _Refusal(
            _CODE_TARGET_SHAPE,
            f"a target must be a mapping carrying 'path' and 'locator', and this one is "
            f"{type(target).__name__}: {target!r}",
        )
    written = str(target.get("path", ""))
    spelling = _spelling_refusal(written, source)
    if spelling is not None:
        return None, spelling
    try:
        return _plan_target_inner(fields, target, source, _confined(written), revision_id)
    except (SourceIndexError, OSError) as error:
        return None, _Refusal(
            _CODE_RESOLUTION_FAILED,
            f"{written!r} could not be resolved against the tree this run is reading "
            f"({error.__class__.__name__}: {error})",
        )


def _spelling_refusal(written: str, source: _Source) -> _Refusal | None:
    """The refusal a path earns for its own spelling, before any tree is asked about it.

    Three spellings can never name a place inside the two admitted roots, and each is a fact about
    the string. An **absolute** path is not repository-relative, so joining a root to it discards the
    root -- and the useful answer names the file it does point at, which may well be inside the code
    root: calling that "out of scope" was a false statement about a file the producer could see. A
    **traversal** is not confined to a root either, and when its normalised form lands inside one the
    refusal says the spelling is what is wrong rather than that the place does not exist. A leading
    ``./`` is neither: it is repository-relative and simply unnormalised, so it is normalised and
    allowed through to the trees.
    """

    if Path(written).is_absolute():
        return _absolute_refusal(written, source)
    if ".." in Path(written).parts:
        return _traversal_refusal(written, source)
    return None


def _absolute_refusal(written: str, source: _Source) -> _Refusal:
    """An absolute path: named as the file it points at, never as a place out of scope."""

    path = Path(written)
    relative = next(
        (
            path.relative_to(root)
            for root in (source.code_root, source.memory_root)
            if path.is_relative_to(root)
        ),
        None,
    )
    if relative is not None:
        return _Refusal(
            _REASON_INSIDE_BY_SPELLING,
            f"{written!r} is an absolute path that names {str(relative)!r} inside an admitted root, "
            "and a citation is written repository-relative; re-spell it as the relative path it "
            "already names",
        )
    return _Refusal(
        _REASON_OUTSIDE_BY_SPELLING,
        f"{written!r} is an absolute path and the two admitted roots are named by "
        "repository-relative paths, so joining a root to it would discard the root; the roots this "
        "enclosure admits are "
        + ", ".join(str(root) for root in (source.code_root, source.memory_root)),
    )


def _traversal_refusal(written: str, source: _Source) -> _Refusal:
    """A traversal: refused for the spelling, and named as the place it climbs to when there is one."""

    return _Refusal(
        _REASON_NOT_CONFINED,
        f"{written!r} contains a '..' segment, so it is not confined to either admitted root "
        f"however it is joined to one -- the roots this enclosure admits are {source.code_root} and "
        f"{source.memory_root} -- and a citation is a confined repository-relative path",
    )


def _confined(written: str) -> str:
    """One path as the tree spells it, with a leading ``./`` removed and nothing else changed."""

    relative = written
    while relative.startswith("./"):
        relative = relative[2:]
    return relative or written


def _require_stored_anchor(
    named: UUID,
    resolved: _Resolved,
    locator: SourceLocator,
    source: _Source,
) -> _Refusal | None:
    """Bind an explicitly reused anchor to the stored row it names, or refuse the disagreement.

    Naming a stored anchor identity is the half of the citation contract where the caller supplies
    **redundant** facts: the path, the blob it resolved to and the locator are all already recorded
    on the row being reused. Redundancy that is never checked is how a receipt comes to describe a
    place the dataset does not link -- the named identity can be read for its shape and then used as
    the claim's endpoint while the supplied path and locator are resolved, observed and reported
    beside it, so the success line and the stored claim name two different constructs. That is a
    false statement about the delivered result rather than a formatting slip, which is why the check
    is here, before the plan exists and therefore before anything can be written.

    The stored row is the authority on all three facts, and the supplied ones must agree with it:
    the same resolved path, the same recorded blob, and the same locator. A blob comparison is what
    makes "the same place" a measurement rather than a restatement -- two spellings of one path can
    resolve to one file, and only the object id says whether the bytes the reuse cites are the bytes
    the anchor was recorded against.

    An identity the dataset does not hold is refused too, and separately: a caller that meant to
    author a new place omits ``anchor_id`` entirely, so arriving with one that resolves to nothing is
    a caller asking to cite a row that is not there, and inventing one instead would answer a
    different question than the one asked.
    """

    if not source.anchors:  # pragma: no cover - a run always names a candidate or a baseline
        return _Refusal(
            _CODE_ANCHOR_ID_NOT_STORED,
            f"the target names the stored anchor {named}, and this run has no candidate or baseline "
            "dataset to resolve that identity against, so the place it names cannot be read",
        )
    stored = _stored_anchor(source, named)
    if stored is None:
        return _Refusal(
            _CODE_ANCHOR_ID_NOT_STORED,
            f"the target names the stored anchor {named} and neither the candidate nor the baseline "
            "dataset holds it; cite a place the dataset records, or omit anchor_id to author a new "
            "one",
        )
    disagreements: list[str] = []
    if stored.path != resolved.path:
        disagreements.append(f"path: stored {stored.path!r}, supplied {resolved.path!r}")
    if str(stored.source_identity.object_id) != resolved.blob:
        disagreements.append(
            f"source identity: stored blob {stored.source_identity.object_id}, supplied blob "
            f"{resolved.blob}"
        )
    if stored.locator != locator:
        disagreements.append(
            f"locator: stored {stored.locator.model_dump(mode='json')}, supplied "
            f"{locator.model_dump(mode='json')}"
        )
    if not disagreements:
        return None
    return _Refusal(
        _CODE_ANCHOR_REUSE_MISMATCH,
        f"the target reuses the stored anchor {named}, and the facts supplied beside that identity "
        f"describe a different place ({'; '.join(disagreements)}); a reused anchor is the stored "
        "row's own location, so either correct the supplied facts to agree with it or omit "
        "anchor_id to author a new anchor",
    )


def _stored_anchor(source: _Source, named: UUID) -> SourceAnchor | None:
    """One stored anchor with the named identity, or ``None`` when no named dataset holds it."""

    for database in source.anchors:
        connection = open_read_only_database(database)
        try:
            found = read_anchor(connection, source.repository.repository_id, str(named))
        finally:
            connection.close()
        if found is not None:
            return found
    return None


def _named_anchor_id(target: Mapping[str, Any]) -> str | UUID | _Refusal:
    """The stored anchor identity one target names, or the refusal its own spelling earns.

    A producer that means to cite a place the dataset already records names that identity here, the
    way ``invariant_id`` names an invariant an entry is revising. It must be a UUID, and it is
    checked here rather than at the schema for the same reason every other locator is: a target the
    reader can *see* is malformed is refused with the reason, and the run continues to the next entry.
    """

    declared = target.get("anchor_id")
    if declared is None:
        return ""
    text = str(declared).strip()
    if not text:
        return ""
    try:
        return UUID(text)
    except ValueError:
        return _Refusal(
            _CODE_ANCHOR_ID_SHAPE,
            f"a target's anchor_id must be the stored identity of a source_anchor, and this one is "
            f"{declared!r}; cite a place the dataset holds, or omit anchor_id to author a new one",
        )


def _plan_target_inner(
    fields: _EntryFields,
    target: Mapping[str, Any],
    source: _Source,
    written: str,
    revision_id: str,
) -> tuple[_TargetPlan | None, _Refusal | None]:
    """One target's whole read, with the failure boundary of :func:`_plan_target` around it."""

    resolved = _complete_target(written, source)
    if isinstance(resolved, _Refusal):
        return None, resolved
    locator = _locator(target.get("locator"), resolved)
    if isinstance(locator, _Refusal):
        return None, locator.at(written)
    observation = _observe(resolved, locator)
    if isinstance(observation, _Refusal):
        return None, observation.at(written)
    named = _named_anchor_id(target)
    if isinstance(named, _Refusal):
        return None, named.at(written)
    if isinstance(named, UUID):
        # A reused anchor is the stored row's own place, so the facts supplied beside its identity
        # have to agree with that row before the plan that would cite it exists.
        mismatch = _require_stored_anchor(named, resolved, locator, source)
        if mismatch is not None:
            return None, mismatch.at(written)
    route_path = target.get("governing_route")
    identities = _target_identities(
        source.repository,
        written,
        locator,
        _Authoring(
            entry_id=fields.entry_id,
            revision_id=revision_id,
            named_anchor_id=None if named == "" else str(named),
        ),
    )
    return (
        _TargetPlan(
            entry_id=fields.entry_id,
            path=written,
            step=resolved.step,
            completed_path=resolved.path,
            blob=resolved.blob,
            locator=locator,
            observation=observation,
            role=_authored_role(fields.role),
            rationale=fields.role_rationale,
            route_path=None if route_path is None else str(route_path),
            route_id=identities.route_id,
            anchor_id=UUID(identities.anchor_id),
            claim_id=identities.claim_id,
            declares_anchor=named == "",
        ),
        None,
    )


# --------------------------------------------------------------------------------------------
# Steps 2 to 5: path completion, identity, locator in the same act, and observation
# --------------------------------------------------------------------------------------------


def _complete_target(written: str, source: _Source) -> _Resolved | _Refusal:
    """Complete one path through the product's own resolver, in the three recorded steps.

    The steps are exactly three and nothing else: ``<code_root>/<path>`` through the branch tip the
    code worktree stands on, then ``<memory_root>/onboarding/<path>``, then ``<memory_root>/<path>``.
    Membership is the tree's answer and the working bytes are verified against it, and the answering
    root travels out with the path so the identity is read from that same root's tree -- a memory
    path's blob lives in the memory tree, and reading it out of the code tree is how a resolved path
    becomes a crash.

    A path that lands in none of the three is refused with the reason distinguishing the four
    measured cases: it names a place outside the admitted roots by its own spelling, it exists in a
    **third** root the citation machinery cannot reach, its first segment is no top-level entry of
    either admitted tree (a dependency's source, absent by construction), or it is a real top-level
    entry whose file is gone. Every one of the four is decided from the **recorded trees** and from
    the path's spelling, never from what happens to be on the filesystem at the moment of the run:
    the enclosure's cleanup deletes task-tree files, and a reason that changed with them would be a
    reason a reader could not reproduce from the record the citation was written against.
    """

    code_root, memory_root = source.code_root, source.memory_root
    code_tree = Trees(
        code_root=code_root, memory_root=memory_root, candidate_tree=source.tree_ids.code
    )
    if written in _members(code_tree):
        return _member(code_tree, written, _CODE_TREE, code_root, source.tree_ids.code)
    memory_tree = Trees(
        code_root=memory_root, memory_root=memory_root, candidate_tree=source.tree_ids.memory
    )
    for step, joined in ((_MEMORY_ONBOARDING, f"onboarding/{written}"), (_MEMORY_ROOT, written)):
        if joined in _members(memory_tree):
            return _member(memory_tree, joined, step, memory_root, source.tree_ids.memory)
    return _Refusal(
        "target_path_unresolved",
        _unresolved_reason(
            written, code_tree, memory_tree, source.contract, source.coordination_top_level
        ),
    )


def _member(
    tree: Trees, relative: str, step: str, root: Path, tree_id: str
) -> _Resolved | _Refusal:
    """One completed target, with the identity read from the bytes the tree verified.

    Membership is the tree's own answer, so a path that resolved but is no member of the tree that
    was asked about is refused here rather than read out of the wrong tree: an identity that does
    not belong to the tree the resolution names is exactly the disagreement this step exists to
    catch.

    The identity itself is then read from the **working bytes** and compared with the blob the tree
    records at that path. The two agreeing is the case a real run is always in -- a clean checkout
    on a committed line -- and the two disagreeing is a fact the report must carry rather than a
    condition the code assumes away: it means the bytes that will be cited are not the bytes the
    resolution tree holds, which is what an uncommitted edit to a cited file looks like. Reporting it
    as a mismatch keeps the recorded identity a measurement of the file rather than a restatement of
    the tree's own answer.
    """

    members = _members(tree)
    if relative not in members:
        return _Refusal(
            "target_path_unresolved",
            f"{relative!r} resolved in the {step} step and the recorded tree holds no member at "
            "that path, so the resolved path and the readable identity disagree",
        )
    identity = _working_identity(root / relative, members[relative][1])
    if isinstance(identity, _Refusal):
        return identity
    if not identity.exact:
        return _Refusal(
            _CODE_BLOB_MISMATCH,
            f"the working bytes at {relative!r} hash to {identity.blob}, and the tree this run "
            f"resolved against records {identity.recorded} there, so the file holds an identity the "
            "resolution tree does not; commit the change so the citation names the bytes it was "
            "resolved against",
        )
    return _Resolved(step=step, path=relative, blob=identity.recorded, root=root, tree_id=tree_id)


def _working_identity(path: Path, recorded: str) -> _BlobIdentity | _Refusal:
    """The blob id the working bytes hash to, beside the blob id the recorded tree holds.

    ``git hash-object --no-filters`` is the same ruler the citation machinery's own candidate uses,
    applied here to the one file the resolver admitted rather than to a population. It is read for
    exactly that reason: the tree's membership answer is known already, so hashing the file is the
    only step in the resolution that can *disagree* with it, and a resolution whose every step is the
    tree agreeing with itself verifies nothing.
    """

    try:
        if not path.is_file():
            return _Refusal(
                _CODE_RESOLUTION_FAILED,
                f"the recorded tree holds a member at this path and the working file is not a "
                f"readable regular file there ({path}), so its identity cannot be read",
            )
        result = run_git(path.parent, ["hash-object", "--no-filters", "--", path.name])
    except OSError as error:  # pragma: no cover - a path that cannot be stat'ed names the error
        return _Refusal(
            _CODE_RESOLUTION_FAILED,
            f"the working file at this path could not be read ({error.__class__.__name__}: {error})",
        )
    if result.returncode != 0:
        detail = result.stderr.strip() or f"git hash-object exited {result.returncode}"
        return _Refusal(
            _CODE_RESOLUTION_FAILED,
            f"the working file at this path could not be hashed, so no identity can be recorded "
            f"for it ({detail})",
        )
    return _BlobIdentity(blob=result.stdout.strip(), recorded=recorded)


def _members(tree: Trees) -> Mapping[str, tuple[str, str]]:
    """One resolver's own membership answer, or nothing when no recorded tree is bound."""

    candidate = tree.source_candidate
    return {} if candidate is None else candidate.members


def _unresolved_reason(
    written: str,
    code_tree: Trees,
    memory_tree: Trees,
    contract: WorktreeContract,
    top_level: frozenset[str],
) -> str:
    """Which of the three ways a *confined* path resolves to nothing this path took.

    A path that is not confined at all -- an absolute one, one with a ``..`` segment -- never reaches
    here: it is refused for its spelling in :func:`_spelling_refusal`, before any tree is asked about
    it, and with a reason that names the place it does point at.

    The order below is fixed by what each answer is a statement *about*, and each is read from the
    **recorded trees** -- never from ``is_file()`` on a live directory, because the enclosure's
    cleanup deletes task-tree files and a reason that changed with them could not be reproduced from
    the record the citation was written against.

    The first segment decides ownership: a path whose first segment is a real top-level entry of one
    of the two admitted trees belongs to that root, so its file is genuinely gone; one whose first
    segment is a top-level entry of the coordination tree and of neither admitted tree belongs to the
    third root, so it is out of scope by design. What is left names neither, which is a dependency's
    source, absent by construction.
    """

    if _first_segment_admitted(written, code_tree, memory_tree, top_level):
        return (
            f"{_REASON_GONE}: {written!r} names a real top-level entry of one of the two admitted "
            "roots and the resolved tree holds no file at that path, which is the damage a move or "
            "a deletion leaves behind"
        )
    if outside := _outside_the_roots(written, contract, top_level):
        return (
            f"{_REASON_THIRD_ROOT}: {written!r} names {outside}, a place under the coordination "
            "root which the two admitted roots do not hold, so it is out of scope by design rather "
            "than unresolved"
        )
    return (
        f"{_REASON_DEPENDENCY}: the first segment of {written!r} is not a top-level entry of either "
        "admitted root, so it names a dependency's source, which is absent by construction"
    )


def _first_segment_admitted(
    written: str, code_tree: Trees, memory_tree: Trees, top_level: frozenset[str]
) -> bool:
    """Whether a path's first segment belongs to one of the two admitted **trees**.

    Read from the trees rather than from the directories: ``Trees.ours`` asks ``is_dir()`` on the
    working checkout, which answers differently before and after a cleanup and differently again in
    a fresh clone. The recorded tree is the one fact about the repository this run is already
    resolving against, so the question is asked of it -- and a new top-level directory is covered
    the day it is created, because the tree that holds it is the tree being read.

    A name the COORDINATION root owns at its top level settles the question the other way, even when
    one of the admitted trees happens to own an entry of the same name. The memory layer's
    ``notes/`` and the coordination root's ``notes/`` are two different directories that share a
    spelling, and a curator report at ``notes/reports/terminal-report.md`` resolved nowhere in
    either admitted tree is not "the damage a move or a deletion leaves behind" -- the coordination
    root is holding the file. Without this the shadowing name made the classifier state a reason
    that was false of the tree it was reading, which is the one thing a refusal reason may not be.
    """

    first = written.split("/", maxsplit=1)[0]
    if not first or first in {".", ".."}:
        return False
    if first in top_level:
        return False
    return any(
        any(relative.split("/", maxsplit=1)[0] == first for relative in _members(tree))
        for tree in (code_tree, memory_tree)
    )


def _outside_the_roots(
    written: str, contract: WorktreeContract, top_level: frozenset[str]
) -> Path | None:
    """The coordination-tree place a repository-relative path names, when it names one at all.

    The third root is not admitted, and denying that a place under it exists would be a false
    refusal: the honest answer is that it names something the citation machinery cannot reach. The
    test is the path's **first segment** being a real top-level entry of the coordination root, and
    the answer does not depend on the rest of the file: a report citation stays out of scope after
    the enclosure's cleanup has deleted the report, because the directory that owned it is still
    what the spelling names. A spelling whose first segment owns nothing under the coordination root
    -- ``vendor/third_party.py`` -- answers nothing, which is what keeps a dependency's path from
    being called a third-root path just because a coordination root exists.
    """

    first = written.split("/", maxsplit=1)[0]
    if not first or first in {".", ".."}:
        return None
    if first not in top_level:
        return None
    return _within(contract.coordination_root, written) or Path(contract.coordination_root, first)


def _coordination_top_level(contract: WorktreeContract) -> frozenset[str]:
    """The names of the coordination root's own top-level directories, minus the two admitted roots.

    Read **once per run**, when the enclosure's trees are bound, and carried with them. That is the
    whole point: this is the only question in the classification that touches the filesystem, and
    asking it per entry would make the reason for one citation depend on what still existed when
    that entry happened to be read -- within a single run, let alone between runs. The two admitted
    roots' own directories are removed because a path under them is settled by the recorded trees
    before this question is ever asked.
    """

    admitted = {
        root.resolve() for root in (contract.code_repo_path, contract.memory_repo_path) if root
    }
    try:
        entries = list(Path(contract.coordination_root).iterdir())
    except OSError:  # pragma: no cover - an unreadable coordination root owns no directory
        return frozenset()
    # ENTRIES, not directories. A file directly at the coordination root is a coordination-root
    # place too, and a first segment that names one has to be classifiable as the third root rather
    # than fall through to "a dependency's source": the directory-only test made
    # ``<coordination-root>/<file>`` report ``dependency_source_not_ours`` about a file the
    # coordination root was holding.
    return frozenset(one.name for one in entries if one.resolve() not in admitted)


def _within(base: Path, relative: str) -> Path | None:
    """``base / relative`` when that is genuinely inside ``base``, and nothing when it is not.

    The comparison is lexical, never ``Path.resolve``: resolving would follow symlinks and
    re-introduce exactly the filesystem dependence this classification exists to remove.
    """

    try:
        joined = Path(base, relative)
    except (OSError, ValueError, TypeError):  # pragma: no cover - an unjoinable pair names nothing
        return None
    base_parts = Path(base).parts
    if joined == Path(base) or joined.parts[: len(base_parts)] != base_parts:
        return None
    return joined


def _locator(locator: Mapping[str, Any] | None, resolved: _Resolved) -> SourceLocator | _Refusal:
    """The producer's locator, verified against the bytes at the path it names.

    A locator is resolved in the same act as the path, so no second resolution can land somewhere
    else: the file consulted here is the file the resolver admitted. A line range is checked for
    containment and a symbol's written parts are checked to occur as a **definition** in the recorded
    bytes. Neither check invents an extent and neither searches for the construct anywhere else -- a
    construct that is not in the named file is a refusal with that reason, never a citation to
    wherever a search would have landed.

    A **null** locator is refused, not promoted. Rule 1 draws the line the producer is held to: a
    path and the construct inside it are one act, and a citation with no construct asserts that the
    whole file is the place. That is exactly the claim a producer must not make by omission, so a
    target that carries no locator gets ``target_locator_missing`` -- an omission is reported as an
    omission. (``found_at`` is the field where a null locator *is* the honest encoding, and that
    field is evidence rather than a citation.)
    """

    if locator is not None and not isinstance(locator, Mapping):
        return _Refusal(
            _CODE_LOCATOR_SHAPE,
            f"a locator must be a mapping carrying 'kind', and this one is "
            f"{type(locator).__name__}: {locator!r}",
        )
    if locator is None:
        return _Refusal(
            _CODE_LOCATOR_MISSING,
            "the target carries no locator, so it names a path without naming the construct inside "
            "it; a whole-file citation is a claim the producer must make explicitly rather than by "
            "omission, so this is reported instead of being promoted to one",
        )
    kind = str(locator.get("kind", ""))
    if kind == _FILE_KIND:
        return FileLocator()
    if kind == _RANGE_KIND:
        return _range_locator(locator, resolved)
    if kind == _SYMBOL_KIND:
        return _symbol_locator(locator, resolved)
    return _Refusal(
        _CODE_LOCATOR_KIND,
        f"the locator kind {kind!r} is not one of file, line_range or symbol",
    )


def _range_locator(locator: Mapping[str, Any], resolved: _Resolved) -> SourceLocator | _Refusal:
    """A line range, refused with the reason that is true of *this* range.

    Four different things can be wrong with a range and none of them is "the construct is not in the
    named file": non-integer bounds are a spelling the model cannot read, a reversed or zero-based
    range is a different numbering than the one the model stores, and a range past the end of the
    file is a range the file cannot hold. Each is reported with its own code and its own sentence, so
    a producer that wrote ``start: 0`` is told its range is zero-based rather than that its construct
    is missing -- which for a one-based model is what ``0`` means, and the template's own shape block
    is where that spelling is explained.
    """

    start = locator.get("start")
    end = locator.get("end")
    if (
        isinstance(start, bool)
        or isinstance(end, bool)
        or not isinstance(start, int)
        or not isinstance(end, int)
    ):
        return _Refusal(
            _CODE_LINE_RANGE_MALFORMED,
            f"a line_range locator carries integer start and end lines, and this one carries "
            f"start={start!r} and end={end!r}",
        )
    if start < 1 or end < 1:
        return _Refusal(
            _CODE_LINE_RANGE_ZERO_BASED,
            f"the recorded range {start}-{end} is not one-based, and the model's line ranges are "
            "one-based and inclusive, so line 0 names nothing",
        )
    if end < start:
        return _Refusal(
            _CODE_LINE_RANGE_ORDER,
            f"the recorded range {start}-{end} runs backwards, so it names no ordered extent in the "
            "file it was written for",
        )
    lines = len(_recorded_text(resolved).splitlines())
    if end > lines:
        return _Refusal(
            _CODE_LINE_RANGE_PAST_END,
            f"the recorded range {start}-{end} reaches past the last line of the file at this path, "
            f"which holds {lines} lines",
        )
    return LineRangeLocator(start_line=start, end_line=end)


def _symbol_locator(locator: Mapping[str, Any], resolved: _Resolved) -> SourceLocator | _Refusal:
    """A symbol the producer wrote as a bare name, resolved into the model's two-part locator.

    The producers spell a symbol as ``{"kind": "symbol", "value": "<bare name>"}``, and the model
    wants a language and a qualified name. Determining those is curator work of the same kind as
    normalising the producer's ``line_range`` spelling: the language is *derived* from the recorded
    path's own extension, and the qualified name is the name the producer wrote.

    Nothing here is a parser and nothing here searches: the one file the path already resolved to is
    handed to the **shipped** tree-sitter extractor, and the name is accepted only when the parser
    binds it. A mention -- in a docstring, in a comment, in a markdown sentence, or as a key in a
    structured data file -- is not a definition, and each of those is refused with the reason that
    is true of it rather than with one shared code.

    The definition rule is :func:`agents_remember.memory_quality.style.citations.extents.
    bound_definitions` and there is no second one: the knowledge read rail asks the same function
    when it observes a stored symbol, so a construct accepted here is a construct the rail can
    resolve, and an accepted symbol citation is re-verifiable rather than merely stored.
    """

    written = str(locator.get("value") or locator.get("qualified_name") or "").strip()
    if not written:
        return _Refusal(
            _CODE_SYMBOL_NAME_MISSING,
            "a symbol locator carries the name it names, and this one carries none",
        )
    language = _symbol_language(written, resolved)
    if isinstance(language, _Refusal):
        return language
    text = _recorded_text(resolved)
    if not bound_definitions(written, resolved.path, text.split("\n")):
        if _occurs(written, text):
            return _Refusal(
                _CODE_NOT_A_DEFINITION,
                f"the symbol {written!r} occurs in the recorded bytes at this path and the parsed "
                f"{language} source does not bind it, so it is a mention rather than the definition "
                "a citation must name",
            )
        return _Refusal(
            _CODE_CONSTRUCT_ABSENT,
            f"the symbol {written!r} does not occur in the recorded bytes at this path, so the "
            "construct the producer named is not in the file the producer named",
        )
    return SymbolLocator(language=language, qualified_name=written)


def _symbol_language(written: str, resolved: _Resolved) -> str | _Refusal:
    """The language a symbol locator may be recorded under, or the reason it may not be any.

    Three answers, and none of them is "the file does not contain the name": an extension the table
    does not know leaves the spelling under-determined, a prose document defines no construct at all,
    and a structured data file's keys are value names rather than definitions. Each is a fact about
    the file's own form, which is why each is refused here rather than reported as a missing
    construct inside a file that could not have held one.
    """

    language = _language_of(resolved.path)
    if language is None:
        return _Refusal(
            _CODE_SYMBOL_LANGUAGE,
            f"the recorded path {resolved.path!r} has no extension this ingest can derive a "
            "language from, so the producer's spelling under-determines the locator",
        )
    if language in _PROSE_LANGUAGES:
        return _Refusal(
            _CODE_NO_DEFINITIONS_IN_PROSE,
            f"{resolved.path!r} is a {language} document, and prose defines no construct, so no "
            f"symbol locator can be resolved in it; cite the passage with a line_range or the whole "
            "document with a file locator instead",
        )
    if language in _STRUCTURED_LANGUAGES:
        return _Refusal(
            _CODE_NO_DEFINITIONS_IN_PROSE,
            f"{resolved.path!r} is a {language} data file, where a key is a value's name and not a "
            f"definition of anything, so {written!r} cannot be resolved as a symbol there",
        )
    if not grammars.parsed(resolved.path):
        # The path's own extension names a language, and the SHIPPED extractor has no grammar for
        # it: ``shell``, ``sql`` and ``.pyi`` reach here. Accepting one would store a citation the
        # read rail can never resolve -- it can only report that a definition cannot be told from a
        # mention -- so the two surfaces agree by refusing it at the write boundary instead.
        return _Refusal(
            _CODE_SYMBOL_LANGUAGE,
            f"the recorded path {resolved.path!r} is written in {language}, which the shipped "
            "extractor has no grammar for, so a definition of the named construct cannot be told "
            "apart from a mention of it and no symbol locator can be verified there",
        )
    return language


def _language_of(path: str) -> str | None:
    """The language one recorded path's own extension states, or ``None`` when it states none.

    A derivation from a fact already in the target rather than a guess: every extension below is
    one the recorded repository ships, and an extension outside the table yields no language rather
    than a fabricated one.
    """

    suffix = Path(path).suffix.lower()
    return _LANGUAGES.get(suffix)


def _occurs(name: str, text: str) -> bool:
    """Whether one name appears anywhere in the recorded bytes, defined or merely mentioned.

    The test is anchored at the name's **start** and not at its end, on purpose: a file that holds
    ``resolve_budget_v`` and is cited for ``resolve_budget`` does contain the name the producer
    wrote, as part of a longer identifier, and saying it "does not occur in the file" would be a
    false statement about bytes a reader can see. What failed is that it is not a definition of that
    name, so that is the reason the report gives.
    """

    return bool(re.search(rf"\b{re.escape(name.rsplit('.', maxsplit=1)[-1])}", text))


def _recorded_text(resolved: _Resolved) -> str:
    """The recorded member's own bytes, read from the working file the tree verified."""

    return (resolved.root / resolved.path).read_text(encoding="utf-8", errors="replace")


def _observe(resolved: _Resolved, locator: SourceLocator) -> str | _Refusal:
    """Observe one anchor against the tree its path resolved in, or report the failing answer.

    The rail is handed exactly the draft the write path will store, so what the report says was
    observed is what the record will hold. ``exact_recorded_blob`` is what every locator this
    increment can consult has to earn, the symbol kind included: the rail observes a symbol through
    the shipped extractor and answers ``exact_recorded_blob`` only when those exact recorded bytes
    bind the name, so an accepted symbol citation is one the read path re-verifies rather than one
    it can only refuse. A symbol the rail reports as ``unsupported_locator`` is refused here, since
    a citation nothing can ever observe is not a citation this operation may commit.

    The rail signals some of its own boundary conditions by raising, and this is where they stop
    being exceptions: a tree that cannot be read, a path that cannot be addressed and an object store
    that cannot answer are all refused with the failure's own name and message, because the promise
    this operation makes is a report for every entry and not a traceback for the hard ones.
    """

    draft = SourceAnchorDraft(
        anchor_id=UUID(_observation_id(resolved, locator)),
        path=resolved.path,
        source_identity=GitBlobIdentity(object_id=resolved.blob),
        locator=locator,
    )
    try:
        observation = observe_anchor(
            {
                "anchor_id": str(draft.anchor_id),
                "path": draft.path,
                "source_identity": draft.source_identity.model_dump(mode="json"),
                "locator": locator.model_dump(mode="json"),
            },
            repository_root=resolved.root,
            tree_id=resolved.tree_id,
        )
    except (SourceIndexError, OSError) as error:
        return _Refusal(
            f"{_CODE_OBSERVATION}{error.__class__.__name__}",
            f"the observation of {resolved.path!r} against the tree {resolved.tree_id} could not "
            f"answer ({error.__class__.__name__}: {error}), so the citation is reported as "
            "unobservable rather than as observed",
        )
    answer = observation.resolution
    if answer == _OBSERVED_EXACT:
        return answer
    return _Refusal(f"{_CODE_OBSERVATION}{answer}", observation.detail)


def _observation_id(resolved: _Resolved, locator: SourceLocator) -> str:
    """A well-formed anchor id for the observation, so the rail sees a real UUID.

    The discriminator must name **what within the file** the locator points at, not only the kind of
    thing it is. Keying on the path and the kind alone gave two symbols in one source file the same
    anchor id -- and two different constructs in one file are two different realizations, which this
    substrate exists to record separately. That collision is why the entry mapper refused the second
    symbol of a file as ``duplicate_target_path``: the guard was telling the truth about the identity
    it was handed.

    A symbol's qualified name is the semantic part of it, so it is the discriminator: it survives the
    file being edited above the definition, where a line number would not. A range and a whole-file
    citation keep the coarser key, which is their existing behaviour and is deliberate -- a range
    citation is meant to stay stable as lines move, and widening that derivation is a separate
    question from this one.
    """

    within = f"|{locator.qualified_name}" if isinstance(locator, SymbolLocator) else ""
    return str(uuid5(_INGEST_NAMESPACE, f"observation:{resolved.path}|{locator.kind}{within}"))


# --------------------------------------------------------------------------------------------
# Step 6: the governing route, through the primitives that reach a governed anchor
# --------------------------------------------------------------------------------------------


def _author_routes(
    destination: AdmittedKnowledgeDestination,
    repository: RepositoryIdentity,
    ledger: _RouteLedger,
    plans: tuple[_Plan, ...],
) -> tuple[EntryOutcome, ...]:
    """Author every distinct route the list names, before anything is committed.

    The route leg runs first because an entry whose route cannot be recorded is a refusal, and a
    refusal must not arrive after the batch has already committed that entry's citation. The leg is
    one transaction over the candidate -- ``author_route`` is idempotent by path, so the distinct
    scopes cost one row each however many places name them -- and a partial author is rolled back.
    An entry that names no route is not refused: absent is the explicit ungoverned state.
    """

    if not ledger.expected:
        return ()
    store = open_admitted_knowledge_store(destination)
    try:
        with store.exclusive_candidate_lock("author_route") as lock_refusal:
            if lock_refusal is not None:
                return _route_refusals(plans, lock_refusal)
            try:
                _author_route_rows(store, repository, ledger, destination)
            except _RouteRefused as refused:
                return _route_refusals(plans, refused.refusal)
    finally:
        store.close()
    return ()


def _distinct_routes(plans: tuple[_Plan, ...]) -> dict[str, str]:
    """Every distinct route path the list names, with the id this run derived for it."""

    wanted: dict[str, str] = {}
    for plan in plans:
        for target in plan.targets:
            if target.route_path is not None:
                wanted.setdefault(target.route_path, target.route_id)
    return wanted


def _replayed_routes(plans: Sequence[_Plan]) -> dict[str, str]:
    """The scopes the *replayed* entries name, whose rows the runs that admitted them already wrote.

    Seeding the ledger with them is what makes ``routes_reused`` tell the truth about a repeat: the
    scopes' rows did exist before this run, and this run authored none of them. They are seeded in
    ``answered`` only, never in ``attached``, so the row count stays a count of rows this run wrote.
    """

    return _distinct_routes(tuple(plan for plan in plans if plan.replayed))


def _author_route_rows(
    store: OpenedKnowledgeStore,
    repository: RepositoryIdentity,
    ledger: _RouteLedger,
    destination: AdmittedKnowledgeDestination,
) -> None:
    """Author the distinct routes in one transaction, refusing the whole leg if one refuses.

    Each scope is asked about before it is authored, because ``author_route`` answers an existing
    path by returning its row's id without writing anything: the two answers are the same type, so
    the only way to know whether *this* run wrote the row is to look first. That fact is what the
    report's ``routes_authored`` and its row count are made of.
    """

    with store.immediate_transaction():
        for route_path, route_id in ledger.expected.items():
            stored = routes.route_for_path(store.connection, repository.repository_id, route_path)
            answer = routes.author_route(
                store.connection,
                repository.repository_id,
                routes.RouteDraft(route_id=route_id, path=route_path),
                destination.authorship,
            )
            if isinstance(answer, KnowledgeRefusal):
                raise _RouteRefused(answer)
            if stored is None:
                ledger.authored.add(route_path)
            ledger.answered[route_path] = answer


class _RouteRefused(Exception):
    """One route primitive's refusal, carried out of the transaction it refused in."""

    def __init__(self, refusal: KnowledgeRefusal) -> None:
        super().__init__(refusal.detail)
        self.refusal = refusal


def _route_refusals(
    plans: tuple[_Plan, ...], refusal: KnowledgeRefusal
) -> tuple[EntryOutcome, ...]:
    """One route leg's refusal, attributed to every entry that named a route."""

    return tuple(
        _refused_entry(
            _fields_of(plan),
            "route_not_recorded",
            f"the governing route could not be recorded ({refusal.code}): {refusal.detail}",
        )
        for plan in plans
        if any(target.route_path is not None for target in plan.targets)
    )


def _attach_routes(
    destination: AdmittedKnowledgeDestination,
    repository: RepositoryIdentity,
    plans: tuple[_Plan, ...],
    ledger: _RouteLedger,
) -> None:
    """Attach each committed anchor to its route, in one transaction over the candidate.

    The association is what makes a citation's third element recorded rather than inferred. It runs
    after the batch because the governed row is the anchor the batch wrote: the union carries no
    command that sets a route on an anchor, so the two legs cannot share one transaction, and the
    report names that rather than pretending one call did both.
    """

    if not ledger.answered:
        ledger.attached = {plan.entry_id: _ungoverned(plan) for plan in plans}
        return
    store = open_admitted_knowledge_store(destination)
    try:
        with store.exclusive_candidate_lock("set_governing_route") as lock_refusal:
            if lock_refusal is not None:
                ledger.attached = _unattached(plans, lock_refusal.detail)
                return
            _attach_rows(store, repository, plans, destination, ledger)
    finally:
        store.close()


def _attach_rows(
    store: OpenedKnowledgeStore,
    repository: RepositoryIdentity,
    plans: tuple[_Plan, ...],
    destination: AdmittedKnowledgeDestination,
    ledger: _RouteLedger,
) -> None:
    """Attach every governed anchor inside one transaction, reporting each target's own outcome."""

    with store.immediate_transaction():
        for plan in plans:
            outcomes: list[RouteOutcome] = []
            for target in plan.targets:
                outcomes.append(_attached_outcome(store, repository, target, destination, ledger))
            ledger.attached[plan.entry_id] = tuple(outcomes)


def _attached_outcome(
    store: OpenedKnowledgeStore,
    repository: RepositoryIdentity,
    target: _TargetPlan,
    destination: AdmittedKnowledgeDestination,
    ledger: _RouteLedger,
) -> RouteOutcome:
    """One target's route outcome: ungoverned, attached, or refused with the primitive's reason."""

    if target.route_path is None:
        return RouteOutcome(None, None, _ROUTE_UNGOVERNED)
    route_id = ledger.answered[target.route_path]
    answer = routes.set_governing_route(
        store.connection,
        repository.repository_id,
        routes.GoverningRouteDraft(
            governed_table="source_anchor",
            governed_id=str(target.anchor_id),
            route_id=route_id,
        ),
        destination.authorship,
    )
    if answer is None:
        state = _ROUTE_REUSED if target.route_path in ledger.reused_paths else _ROUTE_AUTHORED
        return RouteOutcome(target.route_path, route_id, state)
    return RouteOutcome(target.route_path, None, _ROUTE_REFUSED, f"{answer.code}: {answer.detail}")


def _ungoverned(plan: _Plan) -> tuple[RouteOutcome, ...]:
    """One entry with no route at all: the explicit ungoverned state, per target."""

    return tuple(
        RouteOutcome(None, None, _ROUTE_UNGOVERNED)
        if target.route_path is None
        else RouteOutcome(target.route_path, target.route_id, _ROUTE_UNATTACHED, "not attached")
        for target in plan.targets
    )


def _unattached(plans: tuple[_Plan, ...], detail: str) -> dict[str, tuple[RouteOutcome, ...]]:
    """The routes that were authored but whose association the candidate refused to take."""

    return {
        plan.entry_id: tuple(
            RouteOutcome(target.route_path, target.route_id, _ROUTE_UNATTACHED, detail)
            if target.route_path is not None
            else RouteOutcome(None, None, _ROUTE_UNGOVERNED)
            for target in plan.targets
        )
        for plan in plans
    }


# --------------------------------------------------------------------------------------------
# Steps 7 and 8: the whole list in one admitted batch
# --------------------------------------------------------------------------------------------


def _curator_entry(plan: _Plan) -> CuratorEntry:
    """One plan as the write module's entry: the invariant, its revision, and its citations."""

    return CuratorEntry(
        invariant_id=plan.invariant_id,
        display_label=plan.entry_id,
        revision_id=plan.revision_id,
        display_version=_INGESTED_VERSION,
        statement=plan.statement,
        applicability=_APPLICABILITY,
        conditions=_conditions(plan),
        predecessors=plan.predecessors,
        declares_invariant=plan.declares_invariant,
        citations=tuple(target.citation() for target in plan.targets),
    )


def _conditions(plan: _Plan) -> tuple[str, ...]:
    """The entry's own provenance, carried into the revision rather than left in the report."""

    conditions = [f"Hand-off kind: {plan.kind}.", f"Producer's disposition: {plan.disposition}."]
    if plan.disposition_source is not None:
        conditions.append(f"Disposition source: {plan.disposition_source}")
    if plan.evidence:
        conditions.append(f"Evidence: {plan.evidence}")
    return tuple(conditions)


def _commit(
    destination: AdmittedKnowledgeDestination,
    resolution: CandidateResolution,
    plans: tuple[_Plan, ...],
) -> MutationResult | None:
    """Commit every planned entry as one batch, which is the operation's own boundary."""

    if not plans:
        return None
    return commit_curator_entries(
        destination, resolution, tuple(_curator_entry(one) for one in plans)
    )


def _admission_refused(
    plans: tuple[_Plan, ...], outcome: CandidateResult
) -> tuple[EntryOutcome, ...]:
    """The destination admission's refusal, attributed per entry rather than reported only once.

    Nothing was committed and nothing was written: a candidate that was never admitted is not a
    destination, so every entry the run had already planned is reported refused with the admission's
    own code. Reporting them nowhere was the third way an entry could vanish from a report that
    promises an entry appears in exactly one of committed, rulings and refused -- a reader counting
    those three against ``entries_read`` is how a lost entry is noticed at all.
    """

    refusal = outcome.refusal
    code = "candidate_not_admitted" if refusal is None else f"candidate_{refusal.code}"
    detail = (
        "the candidate directory could not be admitted"
        if refusal is None
        else f"{refusal.code}: {refusal.detail}"
    )
    return tuple(
        _refused_entry(
            _fields_of(plan),
            code,
            "the destination refused admission, so there was no admitted candidate for this "
            f"entry to be committed into ({detail})",
        )
        for plan in plans
    )


def _batch_refused(
    plans: tuple[_Plan, ...], result: MutationResult | None
) -> tuple[EntryOutcome, ...]:
    """The batch's refusal, attributed per entry rather than reported only once.

    The operation is all-or-nothing, so a refused batch means nothing was committed: every planned
    entry is reported refused with the batch's own code, and the batch's typed refusal travels
    beside the report so the reason is readable in full.
    """

    refusal = None if result is None else result.refusal
    code = "batch_refused" if refusal is None else f"batch_{refusal.code}"
    detail = (
        "the batch was not attempted" if refusal is None else f"{refusal.code}: {refusal.detail}"
    )
    return tuple(
        _refused_entry(
            _fields_of(plan),
            code,
            "the one batch that carries the whole list did not commit, so this entry was not "
            f"committed ({detail})",
        )
        for plan in plans
    )


# --------------------------------------------------------------------------------------------
# Identity, outcomes and the report
# --------------------------------------------------------------------------------------------


def _identity(
    repository: RepositoryIdentity, kind: str, discriminator: str, within: str = ""
) -> str:
    """One **derived citation** identity: its repository, which identity it is, and its discriminator.

    This is the derivation the route, the anchor and the claim are minted by, and it is deliberately
    not the derivation an invariant or a revision uses any more: those two are **allocated** by
    :func:`_creation`, because a stored truth's identity must not be a function of the local hand-off
    label two independent tasks may both have numbered.

    ``discriminator`` is what the identity is *about*, and it differs by kind on purpose: a route and
    an anchor are keyed on the entry that names the place, while a claim is keyed on the exact
    revision whose edge it records, so two independent tasks that reuse a label and cite one construct
    mint two claims rather than one. The label is never an input to a claim.

    The stable half is the **repository's own namespace**, never the code base commit. Deriving from
    the base commit made one repository's knowledge a function of the baseline it happened to be read
    at: the same obligation under the same local label was a different record at each baseline, while
    two different repositories that shared a base commit were handed the SAME record identity. The
    contract carries no stable repository key to derive from
    (``models/knowledge/repository.py``), so the namespace is the one the selected baseline stores --
    the dataset is the durable home of that value, and a repository that has published one keeps it
    whatever baseline the next task runs at. Only a run that selects no baseline derives, through
    :func:`_repository_identity`'s cold-start fallback, which is keyed on the authority home rather
    than on any commit.

    ``within`` is the disambiguator *inside* one entry's place, and it is what makes two constructs in
    one file two records. A path and a locator kind are not enough: ``pkg/module.py`` holding
    ``resolve_budget`` and ``other`` produced one anchor identity for both, and the batch refused the
    second with ``duplicate_identity`` on ``source_anchor``. A symbol therefore contributes its
    qualified name, exactly as the observation identity already does; a range or whole-file citation
    contributes only the kind it already carried, which is its existing behaviour.
    """

    return str(
        uuid5(_INGEST_NAMESPACE, f"{repository.repository_id}|{kind}|{discriminator}|{within}")
    )


def _authored_role(authored: RealizationRole | None) -> RealizationRole:
    """The role the producer authored for this realization, or an explicit non-answer.

    This used to be inferred from the locator's kind -- ``primary-authority`` for a whole file or a
    range, ``enforcement`` for a symbol -- which locator syntax cannot establish. A symbol can be
    presentation, propagation, support or enforcement, and so can a range; the spelling of a locator
    says where to look, never what the thing found there means. Persisting an inferred role inside a
    valid provenance envelope did not make the attribution sound, and later family review and
    relevance filtering inherited the false premise from it.

    So a role is now a fact the producer states or it is absent. ``UNCLASSIFIED_ROLE`` is the shipped
    vocabulary's own answer for an unassessed edge, which is why nothing had to be widened to say so:
    an unclassified realization is a claim about what is known, not a default standing in for one.
    """

    return authored if authored is not None else UNCLASSIFIED_ROLE


def _fields_of(plan: _Plan) -> _EntryFields:
    """One plan's producer-side fields, for a refusal that must still name them."""

    return _EntryFields(
        entry_id=plan.entry_id,
        kind=plan.kind,
        disposition=plan.disposition,
        disposition_source=plan.disposition_source,
        statement=plan.statement,
        evidence=plan.evidence,
    )


def _ruling_outcome(plan: _Plan) -> EntryOutcome:
    """One ruling, reported skipped with the producer's own verdict carried into the report."""

    return EntryOutcome(
        entry_id=plan.entry_id,
        kind=plan.kind,
        disposition=plan.disposition,
        disposition_source=plan.disposition_source,
        state=SKIPPED,
    )


def _committed_outcome(plan: _Plan, routes: tuple[RouteOutcome, ...]) -> EntryOutcome:
    """One committed entry: its targets as they were measured, and the route each anchor carries."""

    return EntryOutcome(
        entry_id=plan.entry_id,
        kind=plan.kind,
        disposition=plan.disposition,
        disposition_source=plan.disposition_source,
        state=COMMITTED,
        targets=tuple(
            _target_outcome(target, routes, index) for index, target in enumerate(plan.targets)
        ),
        routes=routes,
    )


def _replayed_outcome(plan: _Plan) -> EntryOutcome:
    """One entry an earlier run of this operation already stored: the same ids, and no second write.

    It is reported **committed** because that is what the operation's outcome is -- the truth is
    committed and its identities are the ones it was allocated -- and the route each anchor carries is
    rendered ``reused`` because that is the fact: the association was recorded when the creation was
    admitted, and this run neither authored nor re-attached it.
    """

    return _committed_outcome(
        plan,
        tuple(
            RouteOutcome(target.route_path, target.route_id, _ROUTE_REUSED)
            if target.route_path is not None
            else RouteOutcome(None, None, _ROUTE_UNGOVERNED)
            for target in plan.targets
        ),
    )


def _target_outcome(
    target: _TargetPlan, routes: tuple[RouteOutcome, ...], index: int
) -> TargetOutcome:
    """One committed target, with the route outcome its own position earned."""

    route = routes[index] if index < len(routes) else RouteOutcome(None, None, _ROUTE_UNGOVERNED)
    return TargetOutcome(
        path=target.path,
        step=target.step,
        completed_path=target.completed_path,
        source_identity=target.blob,
        locator_kind=target.locator.kind,
        locator=_locator_text(target.locator),
        observation=target.observation,
        route_path=route.route_path,
        route_state=route.state,
        refusal=route.refusal,
    )


def _locator_text(locator: SourceLocator) -> str | None:
    """One locator's own written form, for the report's locator column."""

    if isinstance(locator, SymbolLocator):
        return locator.qualified_name
    if isinstance(locator, LineRangeLocator):
        return f"{locator.start_line}-{locator.end_line}"
    return None


def _refused_entry(fields: _EntryFields, code: str, reason: str) -> EntryOutcome:
    """One refused entry, with the exact reason and no fabricated success beside it."""

    return EntryOutcome(
        entry_id=fields.entry_id,
        kind=fields.kind,
        disposition=fields.disposition,
        disposition_source=fields.disposition_source,
        state=REFUSED,
        refusal=f"{code}: {reason}",
    )


def _projected(planned: tuple[_Plan, ...]) -> _Run:
    """What the batch would carry, for the dry report that committed nothing.

    One entry contributes ``AddInvariant`` and ``AddInvariantRevision``; each citation contributes
    its ``AddSourceAnchor`` and its ``AddRealizationClaim``. That is four *commands* per citation and
    four *rows* -- and the row count is where a reader has to be careful, because the write path
    reports the cited anchor a second time when the same batch wrote it (D-42). The projection counts
    the rows as distinct ``(table, record)`` pairs, which is how the real run's own receipt is
    counted, so this number is the number the real run reports rather than two rows per citation more
    than it wrote. The route leg writes one ``route`` row per distinct scope outside the batch and one
    association per governed anchor, and both are added through the same ledger property the real run
    uses.

    Counting them here is arithmetic over the plans the run already built, not a second construction
    of the batch, so a dry report's command and row counts are the counts the real run reports.
    """

    batch_rows = sum(2 + 2 * len(plan.targets) for plan in planned)
    route_paths = {one.route_path for plan in planned for one in plan.targets if one.route_path}
    return _Run(
        batch_state=_DRY_BATCH_STATE,
        commands=batch_rows,
        records=batch_rows,
        ledger=_projected_ledger(planned, route_paths),
        dry_run=True,
    )


def _projected_ledger(planned: tuple[_Plan, ...], route_paths: set[str]) -> _RouteLedger:
    """The ledger a dry run would have had, so its row count comes from the same property.

    Every named scope and every governed association is projected as ``projected``, which is why
    the dry run's ``routes_authored`` is zero: the dry run did not read the candidate, so it cannot
    know which scopes already had a row, and naming one *authored* would be a claim about work the
    run did not do. The row arithmetic is still the run's own -- ``route_rows`` counts a projected
    row exactly as it counts an authored one -- so a dry report's ``records_written`` is what the
    commit would write. Where it can differ from the real run is the case INC-8 names: a scope whose
    row already exists contributes a row to the projection and none to the commit, so a dry run
    over-reports by exactly the number of pre-existing scopes. That delta is now visible in the
    states rather than hidden behind an ``authored`` label, and ``dry_run`` on the report says which
    mode produced the number.
    """

    return _RouteLedger(
        expected={path: "" for path in route_paths},
        answered={path: "" for path in route_paths},
        attached={
            plan.entry_id: tuple(
                RouteOutcome(target.route_path, target.route_id, _ROUTE_PROJECTED)
                if target.route_path is not None
                else RouteOutcome(None, None, _ROUTE_UNGOVERNED)
                for target in plan.targets
            )
            for plan in planned
        },
        authored=set(),
        projected=True,
        projected_scopes=len(route_paths),
    )


def _projected_outcomes(planned: tuple[_Plan, ...]) -> tuple[EntryOutcome, ...]:
    """Every entry a dry run would commit, with the route each anchor would carry.

    Each named route is projected as *authored*: the dry run did not read the candidate, so it
    cannot know which scopes already have a row, and claiming "reused" would be a fact the run never
    measured. The projection is labelled by ``dry_run`` on the report for exactly that reason.
    """

    return tuple(
        _committed_outcome(
            plan,
            tuple(
                RouteOutcome(target.route_path, target.route_id, _ROUTE_PROJECTED)
                if target.route_path is not None
                else RouteOutcome(None, None, _ROUTE_UNGOVERNED)
                for target in plan.targets
            ),
        )
        for plan in planned
    )


@dataclass(frozen=True)
class _Run:
    """Everything the report needs from the run itself: the batch, its commands, its routes.

    ``dry_run`` says the batch was never opened, and the two projections then carry what the batch
    *would* have carried. That is what makes a dry report the run's own report with the commit
    withheld: the same entry outcomes, the same counts, and a batch state that names the withholding
    instead of looking like an operation that failed to reach its batch.
    """

    result: MutationResult | None = None
    commands: int = 0
    records: int = 0
    ledger: _RouteLedger | None = None
    batch_state: str | None = None
    refusal: KnowledgeRefusal | None = None
    dry_run: bool = False

    @property
    def state(self) -> str:
        if self.batch_state is not None:
            return self.batch_state
        return "no_change" if self.result is None else self.result.state

    @property
    def written(self) -> int:
        """Every row this run wrote: the batch's own distinct rows, plus the route leg's two kinds.

        Three corrections are folded in here, and each is a defect this count had before it.

        The batch's receipt is a list of *touched records*, and a record can be reported twice: the
        write path reports the anchor a claim cites a second time when the same batch wrote it
        (D-42), so the length of ``changed`` overstates the rows by one per citation. Counting
        distinct ``(table, record_id)`` pairs is the count of rows the batch actually wrote.

        The route leg's rows are not candidate commands, so they are absent from ``result.changed``
        and have to be added: one ``route`` row per scope **this run authored** -- not per scope it
        answered, because a scope that already had a row wrote nothing here -- and one association
        per governed anchor whose association the candidate accepted. A refused association left no
        row, and an ungoverned target has none to leave.

        The count is over rows the run WROTE, not over citations it rewrote. Those are two
        different claims, and conflating them is what made this number false: a route leg that ran
        and committed its rows before a batch that then refused had written real rows, and
        ``records_written`` reported zero while the candidate held them. The citation-rewrite claim
        is carried by ``committed`` and by the batch state, which say exactly that; a header that
        claims "records_written" and then denies rows the file holds is not a count of anything.
        """

        if not self.dry_run and self.result is not None and self.result.state != "changed":
            # A refused batch is all-or-nothing: it wrote no citation row of its own, whatever the
            # receipt carries. The route leg's rows are still counted below, because they exist.
            return self.route_rows
        return self.batch_rows + self.route_rows

    @property
    def batch_rows(self) -> int:
        """The distinct rows the batch wrote, deduplicated by the record each receipt entry names.

        A dry run has no receipt, so the count is the one :func:`_projected` computed from the same
        plans -- the run's own arithmetic rather than a second estimate of it. A real run's count
        comes from its receipt, where the same record can be reported twice and is counted once.
        """

        if self.result is not None:
            return len({(one.table, one.record_id) for one in self.result.changed})
        return self.records

    @property
    def route_rows(self) -> int:
        """The rows the route leg wrote: one per authored scope, one per accepted association."""

        return 0 if self.ledger is None else self.ledger.route_rows


def _report(
    target: _ReportTarget,
    run: _Run,
    *,
    committed: tuple[EntryOutcome, ...] = (),
    dry_run: bool = False,
) -> IngestReport:
    """Assemble the one report every mode returns, from what the run already measured."""

    paths, resolution, repository, read = (
        target.paths,
        target.resolution,
        target.repository,
        target.read,
    )
    return IngestReport(
        contract_path=str(paths.contract_path),
        candidate_directory=str(paths.candidate),
        candidate_receipt=str(paths.candidate / CANDIDATE_RECEIPT_NAME),
        lane=resolution.lane,
        code_tree_id=resolution.code_tree_id,
        memory_tree_id=resolution.memory_tree_id,
        code_base_commit=target.trees.base,
        code_tree_source=target.trees.code_source,
        repository_id=repository.repository_id,
        derived_identities=(
            "an invariant and its first revision are ALLOCATED by this API -- a fresh uuid4 each, "
            "recorded in the candidate's allocation journal under an idempotency key scoped by the "
            "enclosure's own task identity, so an independent task reusing a local hand-off label "
            "mints a truth of its own while a repeat of one operation resolves to the identities it "
            "already holds; a citation's route, anchor and claim are derived as uuid5 over one fixed "
            "curator-ingest namespace, the repository's own namespace identity, which identity it is, "
            "and the entry's own id -- plus, for a target, what inside the written path the citation "
            "is about: a symbol's qualified name, or the locator kind when there is nothing finer. No "
            "allocated identity contains the enclosure, the branch, the baseline or the label, because "
            "a stored identity must stay usable from any task; the derived half's stable component is "
            "the repository namespace and never the enclosure's recorded code base commit, because "
            "identity must not move when the baseline or the line advances"
        ),
        dry_run=dry_run,
        entries_read=read.ids,
        committed=committed,
        rulings=read.rulings,
        refused=read.refused,
        counts=_counts(read, run),
        batch_state=run.state,
        batch_digest_before=None if run.result is None else run.result.before.logical_digest,
        batch_digest_after=None if run.result is None else run.result.after.logical_digest,
        batch_refusal=run.refusal if run.refusal is not None else _batch_refusal(run),
    )


def _batch_refusal(run: _Run) -> KnowledgeRefusal | None:
    """The batch's own typed refusal, when there was a batch to refuse."""

    return None if run.result is None else run.result.refusal


def _counts(read: _Read, run: _Run) -> IngestCounts:
    """The auditable counts, each read from the outcome it counts rather than recomputed.

    ``targets_completed`` and ``locators_resolved`` are two different measurements and no longer one
    expression. The first counts every place whose path completed against the resolution tree, in a
    committed entry **and** in a refused one -- a place that resolved before its entry was refused
    elsewhere is work the run really did. The second counts the places that reached the batch with a
    locator resolved and verified into the model's own union, which is the set the anchor rows are
    written from. On a list where an entry is refused at its second target the two differ by exactly
    that first place, which is the whole reason for keeping them apart.

    In a dry run the two route counts are zero, because no route leg ran and no route exists to call
    reused: ``route_paths`` is the distinct scope count the list names, which is the fact a reader
    wants before a commit. The batch's two counts are the commands and rows the batch would carry,
    which is the whole point of a dry report being the run's report with the commit withheld.
    """

    ledger = run.ledger
    reused = 0 if ledger is None else len(ledger.reused_paths)
    authored = 0 if ledger is None else len(ledger.authored)
    # ``locators_resolved`` is the set the anchor rows are written from, so a replayed plan is not in
    # it: its anchor rows were written by the run that admitted the creation, and counting them here
    # would claim this run handed the batch a construct it did not hand it. ``targets_completed`` and
    # ``anchors_observed_exact`` are still counted over every planned target, because resolving and
    # observing a place is work this run really did whatever became of the entry afterwards.
    fresh = tuple(one for plan in read.planned if not plan.replayed for one in plan.targets)
    every_target = (*read.targets, *read.resolved_before_refusal)
    return IngestCounts(
        entries_read=len(read.ids),
        rulings=len(read.rulings),
        targets_completed=len(every_target),
        locators_resolved=len(fresh),
        anchors_observed_exact=sum(1 for one in read.targets if one.observation == _OBSERVED_EXACT),
        route_paths=len({one.route_path for one in read.targets if one.route_path is not None}),
        routes_authored=authored,
        routes_reused=reused,
        commands_sent=run.commands,
        records_written=run.written,
    )
