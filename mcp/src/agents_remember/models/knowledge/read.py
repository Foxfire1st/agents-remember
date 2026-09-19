"""The selective recorded-scope read: its seeds, its declared snapshot, its page and its cursor.

This module is the whole vocabulary of one read. It holds no SQL, no Git resolution and no
authority decision; it declares what a caller may ask for and what a caller is told, and the
deciders import it rather than redefining a branch of it.

Four splits are load-bearing:

* **Seed versus selection.** A :class:`KnowledgeReadSeed` is what the caller asked for; the
  selection manifest a page declares is what the recorded graph answered. A seed never carries a
  filter, a sort, a revision preference or a display version, because none of those may select a
  record: the only thing that narrows a selection is an exact identity the caller named.
* **Selection versus page.** ``selected_*`` counts describe the complete selected set;
  ``returned_*`` and ``remaining_*`` describe the walk up to and including this page. All three
  travel, so a one-item page cannot be read as a one-item scope at any position of the walk --
  not only at its first page.
* **Provenance versus verdict.** Every statement, role, rationale and lifecycle crosses as the
  authored text it is stored as. Nothing here can carry a current-truth marker, a severity, a
  ranking or a semantic assessment, because the response has no field that could hold one.
* **Continuing versus re-binding.** :class:`KnowledgeReadCursor` names the exact snapshot,
  context, selector and policy it continues. A cursor is therefore not a permission to read
  whatever the path holds now: a caller that presents one against another dataset is refused
  rather than served a page assembled from two revisions.
"""

from __future__ import annotations

import base64
import json
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from agents_remember.kernel.canonical_json import sha256_digest
from agents_remember.models.knowledge.base import (
    LABEL_MAX_LENGTH,
    PATH_MAX_LENGTH,
    PROSE_MAX_LENGTH,
    REFERENCE_MAX_LENGTH,
    SHA256_PATTERN,
    UUID_PATTERN,
    KnowledgeModel,
    require_plain_git_path,
)
from agents_remember.models.knowledge.candidate import SnapshotIdentity
from agents_remember.models.knowledge.graph import RealizationRole
from agents_remember.models.knowledge.result import KnowledgeRefusal
from agents_remember.models.knowledge.source import SourceLocator

__all__ = [
    "ANCHOR_RESOLUTIONS",
    "EXACT_REVISION_SEED_KINDS",
    "IDENTITY_SEED_KINDS",
    "KNOWLEDGE_READ_POLICY_VERSION",
    "MAX_PAGE_ITEMS",
    "MAX_PAGE_UTF8_BYTES",
    "SELECTION_ITEM_LIMIT",
    "AdvertisedExpansion",
    "AnchorResolution",
    "AnchorResolutionState",
    "DirectlyContainingFamily",
    "FamilyIdentitySeed",
    "FamilyRevisionSeed",
    "InvariantIdentitySeed",
    "InvariantRevisionSeed",
    "ItemKind",
    "KnowledgeReadBudget",
    "KnowledgeReadContext",
    "KnowledgeReadCounts",
    "KnowledgeReadCursor",
    "KnowledgeReadPage",
    "KnowledgeReadRequest",
    "KnowledgeReadResult",
    "KnowledgeReadSeed",
    "KnowledgeReadSnapshot",
    "PathSeed",
    "ReadItem",
    "ReadRevisionGroup",
    "ReadStage",
    "SelectionReason",
    "continue_from_cursor",
]

# The selection policy this module's pages were produced by. It is part of a cursor's binding
# because a policy change can move the selected set, and a page assembled under another policy is
# not a continuation of this one.
KNOWLEDGE_READ_POLICY_VERSION = "recorded-family-frontier/v1"

# The declared initial budgets. They are presentation policy, never a completeness mechanism: a
# caller may reduce them and the response still reports the complete selected set.
MAX_PAGE_ITEMS = 32
MAX_PAGE_UTF8_BYTES = 49152

# The declared execution bound of one selection. Reaching it refuses with ``selection_incomplete``
# rather than emitting invented totals or claiming a complete scope.
SELECTION_ITEM_LIMIT = 5000

ItemKind = Literal[
    "invariant_revision",
    "family_revision",
    "family_membership",
    "realization_claim",
    "advertised_family",
]

# The item kinds whose selection names one exact revision. The other two relate revisions: a
# membership cites a family revision and an invariant revision, a realization claim cites an
# invariant revision, and an advertised family is a frontier membership.
EXACT_REVISION_SEED_KINDS: tuple[str, ...] = ("invariant", "family")
IDENTITY_SEED_KINDS: tuple[str, ...] = ("invariant", "family")

AnchorResolutionState = Literal[
    "exact_recorded_blob",
    "recorded_blob_mismatch",
    "path_absent",
    "entry_not_blob",
    "recorded_object_unavailable",
    "unsupported_locator",
    "not_requested",
]

ANCHOR_RESOLUTIONS: tuple[str, ...] = (
    "exact_recorded_blob",
    "recorded_blob_mismatch",
    "path_absent",
    "entry_not_blob",
    "recorded_object_unavailable",
    "unsupported_locator",
    "not_requested",
)

ReadStage = Literal[
    "seed_selected",
    "invariant_identity",
    "invariant_revision",
    "family_identity",
    "family_revision",
    "member_of_selected_family",
    "realization_of_selected_revision",
    "sibling_membership_in_another_family",
]


class PathSeed(KnowledgeModel):
    """Select the recorded realizations at one repository-relative path."""

    kind: Literal["path"] = "path"
    path: str = Field(min_length=1, max_length=PATH_MAX_LENGTH)

    @field_validator("path")
    @classmethod
    def _require_confined_relative_posix_path(cls, value: str) -> str:
        """Refuse anything that is not a confined repository-relative POSIX path.

        The same shape rule the stored anchor path applies, restated for the seed: a seed is not a
        filesystem address, and a path that no stored anchor could carry selects nothing by
        construction rather than by a lookup that happens to miss. Both rules are the one shared
        check, so a spelling the write path refuses cannot be presented as a seed that is answered
        with an absence.
        """

        cleaned = value.strip()
        if not cleaned:
            raise ValueError("a path seed must not be blank")
        if cleaned.startswith(("/", "\\", "~")):
            raise ValueError(f"a path seed must be repository-relative: {value!r}")
        if "\\" in cleaned or "\x00" in cleaned:
            raise ValueError(f"a path seed must be a NUL-free POSIX path: {value!r}")
        if any(part in {"", ".", ".."} for part in cleaned.split("/")):
            raise ValueError(f"a path seed must not contain empty, '.' or '..' segments: {value!r}")
        return require_plain_git_path(cleaned, what="a path seed")


class InvariantIdentitySeed(KnowledgeModel):
    """Select every retained revision of one invariant identity in the snapshot."""

    kind: Literal["invariant"] = "invariant"
    invariant_id: str = Field(pattern=UUID_PATTERN)


class InvariantRevisionSeed(KnowledgeModel):
    """Select exactly one named revision of one invariant identity."""

    kind: Literal["invariant_revision"] = "invariant_revision"
    invariant_id: str = Field(pattern=UUID_PATTERN)
    revision_id: str = Field(pattern=UUID_PATTERN)


class FamilyIdentitySeed(KnowledgeModel):
    """Select every retained revision of one family identity in the snapshot."""

    kind: Literal["family"] = "family"
    family_id: str = Field(pattern=UUID_PATTERN)


class FamilyRevisionSeed(KnowledgeModel):
    """Select exactly one named revision of one family identity."""

    kind: Literal["family_revision"] = "family_revision"
    family_id: str = Field(pattern=UUID_PATTERN)
    revision_id: str = Field(pattern=UUID_PATTERN)


# The one discriminated union of seeds. A display version, an insertion instant and a "latest"
# flag are deliberately absent: none of them can be represented, so none of them can select.
KnowledgeReadSeed = Annotated[
    PathSeed
    | InvariantIdentitySeed
    | InvariantRevisionSeed
    | FamilyIdentitySeed
    | FamilyRevisionSeed,
    Field(discriminator="kind"),
]


class KnowledgeReadContext(KnowledgeModel):
    """The explicit context one baseline or candidate read is addressed at.

    Three identities, and the read verifies all three against the file it opened:

    * ``repository_id`` -- the namespace the dataset must be bound to;
    * ``knowledge`` -- the exact logical snapshot the caller selected, not whatever the path
      holds now;
    * ``code`` -- the exact Git tree source anchors resolve against, or ``None`` when no source
      resolution was requested.

    ``task_ref`` is an opaque reference to admission facts owned elsewhere. It is ``None`` for a
    baseline read, which is a supported state and not a degraded one: a read of recorded
    knowledge during planning does not need a leaf, an enclosure or a fabricated task, and this
    operation never asks a contract owner for one.
    """

    repository_id: str = Field(pattern=UUID_PATTERN)
    knowledge: SnapshotIdentity
    repository_root: str | None = Field(default=None, max_length=PATH_MAX_LENGTH)
    code_tree_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}$|^[0-9a-f]{64}$")
    task_ref: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)

    @model_validator(mode="after")
    def _require_one_namespace(self) -> KnowledgeReadContext:
        if self.knowledge.repository_id != self.repository_id:
            raise ValueError(
                "the read context names a repository the selected knowledge snapshot does not "
                "belong to"
            )
        return self

    @model_validator(mode="after")
    def _require_usable_source_resolution(self) -> KnowledgeReadContext:
        """Refuse a half-specified source resolution rather than reporting one that cannot run.

        Resolving an anchor needs both the tree to resolve inside and the root to run Git in.
        Supplying only one of them is not a narrower request, it is an incomplete one, and
        answering it with ``not_requested`` would report a caller's mistake as a fact about the
        recorded anchor.
        """

        if (self.code_tree_id is None) != (self.repository_root is None):
            raise ValueError(
                "source resolution needs both repository_root and code_tree_id; supplying one "
                "without the other is an incomplete resolution request"
            )
        return self


class KnowledgeReadBudget(KnowledgeModel):
    """The page budget: whole items and a serialized-page byte ceiling.

    A budget changes how much of a selection one page carries and nothing else. It never narrows
    the selection, never drops an essential condition and never turns a truncated result into a
    complete one.
    """

    max_items: int = Field(default=MAX_PAGE_ITEMS, ge=1, le=SELECTION_ITEM_LIMIT)
    max_utf8_bytes: int = Field(default=MAX_PAGE_UTF8_BYTES, ge=1)


class KnowledgeReadRequest(KnowledgeModel):
    """One read: what to select, at which snapshot, and how much fits on a page.

    ``continuation`` is the opaque cursor a previous page returned. A request carries either a
    fresh seed or a continuation, and the cursor itself carries the seed that produced the page,
    so a continuation cannot change the selection it continues.
    """

    seed: KnowledgeReadSeed
    budget: KnowledgeReadBudget = Field(default_factory=KnowledgeReadBudget)
    continuation: str | None = Field(default=None, max_length=PROSE_MAX_LENGTH)


class KnowledgeReadSnapshot(KnowledgeModel):
    """The one snapshot a page declares, and the one a continuation is bound to."""

    repository_id: str = Field(pattern=UUID_PATTERN)
    schema_version: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    logical_digest: str = Field(pattern=SHA256_PATTERN)
    context_digest: str = Field(pattern=SHA256_PATTERN)


class DirectlyContainingFamily(KnowledgeModel):
    """One family revision that directly contains a selected invariant revision.

    It records why the family entered the selected set, separately from the membership rows that
    were added because of it.
    """

    family_id: str = Field(pattern=UUID_PATTERN)
    family_revision_id: str = Field(pattern=UUID_PATTERN)
    via_invariant_revision_id: str = Field(pattern=UUID_PATTERN)


class SelectionReason(KnowledgeModel):
    """Why one selected record is in the selected set, with the record that put it there."""

    stage: ReadStage
    via_id: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)


class ReadRevisionGroup(KnowledgeModel):
    """How many retained revisions of one identity this query selected.

    The group is the honest statement of what a revision page shows: a header that says how many
    revisions of this identity the query selected, so seeing one revision on a page cannot be read
    as that identity having one revision.
    """

    record_id: str = Field(pattern=UUID_PATTERN)
    selected_revision_count: int = Field(ge=0)


class AdvertisedExpansion(KnowledgeModel):
    """One recorded relationship the page advertises without traversing it.

    A sibling invariant's membership in another family is exactly this: a recorded fact the caller
    can act on, and not permission for this read to walk that family in the default result.
    """

    kind: Literal["family"] = "family"
    via_invariant_revision_id: str = Field(pattern=UUID_PATTERN)
    family_id: str = Field(pattern=UUID_PATTERN)
    family_revision_id: str = Field(pattern=UUID_PATTERN)
    member_id: str = Field(pattern=UUID_PATTERN)


class AnchorResolution(KnowledgeModel):
    """One recorded anchor observed against the requested code snapshot.

    The recorded identity stays on the observation whatever the outcome: an anchor whose bytes
    differ, whose path is gone or whose locator this increment cannot resolve is reported as that
    observation and is never promoted to a current realization, and no path is looked up in a
    working tree or at HEAD instead.
    """

    anchor_id: str = Field(pattern=UUID_PATTERN)
    path: str = Field(min_length=1, max_length=PATH_MAX_LENGTH)
    recorded_source_identity: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    observed_source_identity: str | None = Field(default=None, max_length=LABEL_MAX_LENGTH)
    locator: SourceLocator
    resolution: AnchorResolutionState
    detail: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)


class ReadItem(KnowledgeModel):
    """One indivisible primary item of the selected set.

    One item carries the statement (or joint guarantee), its essential conditions and its authored
    provenance together, because a page that returned a statement without its conditions would
    have truncated a governing record rather than a page.
    """

    kind: ItemKind
    item_id: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    selection_reasons: tuple[SelectionReason, ...] = ()

    # invariant_revision and family_revision
    invariant_id: str | None = Field(default=None, pattern=UUID_PATTERN)
    family_id: str | None = Field(default=None, pattern=UUID_PATTERN)
    record_id: str | None = Field(default=None, pattern=UUID_PATTERN)
    revision_id: str | None = Field(default=None, pattern=UUID_PATTERN)
    display_version: str | None = Field(default=None, max_length=LABEL_MAX_LENGTH)
    display_label: str | None = Field(default=None, max_length=LABEL_MAX_LENGTH)
    statement: str | None = Field(default=None, max_length=PROSE_MAX_LENGTH)
    applicability: str | None = Field(default=None, max_length=PROSE_MAX_LENGTH)
    essential_conditions: tuple[str, ...] = ()
    exclusions: tuple[str, ...] = ()
    joint_guarantee: str | None = Field(default=None, max_length=PROSE_MAX_LENGTH)
    lifecycle: str | None = Field(default=None, max_length=LABEL_MAX_LENGTH)
    acceptance_ref: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
    provenance: dict[str, object] | None = None
    payload_digest: str | None = Field(default=None, pattern=SHA256_PATTERN)

    # family_membership and advertised_family
    member_id: str | None = Field(default=None, pattern=UUID_PATTERN)
    family_revision_id: str | None = Field(default=None, pattern=UUID_PATTERN)
    invariant_revision_id: str | None = Field(default=None, pattern=UUID_PATTERN)

    # realization_claim
    claim_id: str | None = Field(default=None, pattern=UUID_PATTERN)
    role: RealizationRole | None = None
    rationale: str | None = Field(default=None, max_length=PROSE_MAX_LENGTH)
    anchor: AnchorResolution | None = None


class KnowledgeReadCounts(KnowledgeModel):
    """The selected set and one page of it, counted by kind and by distinct source location.

    Claim identities and source locations are counted separately because they are different
    facts: two claims at one location are two claims and one location, and a claim whose anchor
    is recorded once is one location however many times it is cited.

    The three ``primary_items_*`` fields describe one walk of the declared set:
    ``primary_items_total`` is the whole selected set and is the same number on every page of it,
    ``primary_items_returned`` is how much of that set the pages up to and including this one have
    emitted, and ``primary_items_remaining`` is what is still ahead. A page therefore never
    reports a total that shrinks as the walk proceeds: truncation cannot be read as a smaller
    scope, which is the packet's own non-conformance example.
    """

    invariant_revisions_total: int = Field(ge=0)
    family_revisions_total: int = Field(ge=0)
    memberships_total: int = Field(ge=0)
    realization_claims_total: int = Field(ge=0)
    advertised_expansions_total: int = Field(ge=0)
    primary_items_total: int = Field(ge=0)
    primary_items_returned: int = Field(ge=0)
    primary_items_remaining: int = Field(ge=0)
    distinct_source_locations_total: int = Field(ge=0)
    distinct_source_paths_total: int = Field(ge=0)
    unresolved_anchor_total: int = Field(ge=0)

    @model_validator(mode="after")
    def _require_consistent_page_arithmetic(self) -> KnowledgeReadCounts:
        """Refuse counts that contradict themselves before a caller reads them as facts.

        The invariant is the walk's, not one page's: what has been returned plus what remains is
        the declared selection total. ``returned`` is cumulative over the walk, so a page that
        reported only its own slice here would make this identity false at every position after
        the first -- which is exactly the shape a continuation must not be able to produce.
        """

        if self.primary_items_returned + self.primary_items_remaining != self.primary_items_total:
            raise ValueError(
                "returned plus remaining items must equal the selected total; a page that "
                "reports otherwise cannot be continued to the declared set"
            )
        if self.primary_items_returned > self.primary_items_total:
            raise ValueError("a page cannot have returned more items than the selection holds")
        return self


class KnowledgeReadPage(KnowledgeModel):
    """One bounded page of the selected set, with its continuation and its completeness flag.

    ``items`` is this page's slice; ``counts.primary_items_returned`` is the walk's cumulative
    figure including this slice. The two are equal only on the first page, and a caller that wants
    the slice size reads ``len(items)`` rather than the walk's counter.
    """

    items: tuple[ReadItem, ...] = ()
    counts: KnowledgeReadCounts
    has_more: bool
    enumeration_complete: bool
    continuation: str | None = Field(default=None, max_length=PROSE_MAX_LENGTH)
    minimum_utf8_bytes: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _require_honest_truncation(self) -> KnowledgeReadPage:
        """Refuse a page that presents a truncated selection as a complete one.

        The three facts are one statement: a continuation exists exactly when items remain, the
        enumeration is complete exactly when none do, and the two must never disagree, because a
        caller's completeness decision is made from them.
        """

        if self.has_more == self.enumeration_complete:
            raise ValueError(
                "has_more and enumeration_complete describe one state and must be opposites; a "
                "truncated page must never be presentable as a complete one"
            )
        if self.has_more != (self.continuation is not None):
            raise ValueError("a page with remaining items must carry a continuation and no other")
        return self


class KnowledgeReadResult(KnowledgeModel):
    """The typed outcome of one read: a declared page, or one typed refusal."""

    state: Literal["page", "refused"]
    operation: Literal["read_knowledge_scope"] = "read_knowledge_scope"
    repository_id: str = Field(pattern=UUID_PATTERN)
    database_ref: str | None = Field(default=None, max_length=LABEL_MAX_LENGTH)
    snapshot: KnowledgeReadSnapshot | None = None
    context_digest: str | None = Field(default=None, pattern=SHA256_PATTERN)
    seed: KnowledgeReadSeed | None = None
    seed_digest: str | None = Field(default=None, pattern=SHA256_PATTERN)
    manifest_digest: str | None = Field(default=None, pattern=SHA256_PATTERN)
    policy_version: str = Field(default=KNOWLEDGE_READ_POLICY_VERSION, max_length=LABEL_MAX_LENGTH)
    directly_containing_families: tuple[DirectlyContainingFamily, ...] = ()
    revision_groups: tuple[ReadRevisionGroup, ...] = ()
    page: KnowledgeReadPage | None = None
    refusal: KnowledgeRefusal | None = None

    @model_validator(mode="after")
    def _require_one_outcome(self) -> KnowledgeReadResult:
        """A result is a page or a refusal, never both and never neither."""

        if self.state == "page" and (self.page is None or self.refusal is not None):
            raise ValueError("a page result carries a page and no refusal")
        if self.state == "refused" and (self.refusal is None or self.page is not None):
            raise ValueError("a refused result carries its refusal and no page")
        return self


class KnowledgeReadCursor(KnowledgeModel):
    """The exact continuation of one page: the snapshot, context, selector and policy it binds.

    The binding is the point. A cursor is not a position in "the database at this path"; it is a
    position in one named snapshot of one selected set, and a caller that presents it elsewhere is
    refused instead of being served a page stitched from two revisions.
    """

    # Named ``cursor_format`` rather than ``schema``: ``schema`` is a deprecated ``BaseModel``
    # attribute, and a field that shadows it makes Pydantic warn on every construction.
    cursor_format: Literal["knowledge-read-cursor/v1"] = "knowledge-read-cursor/v1"
    policy_version: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    context_digest: str = Field(pattern=SHA256_PATTERN)
    seed_digest: str = Field(pattern=SHA256_PATTERN)
    manifest_digest: str = Field(pattern=SHA256_PATTERN)
    logical_digest: str = Field(pattern=SHA256_PATTERN)
    schema_version: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    position: int = Field(ge=0)


def seed_digest(seed: KnowledgeReadSeed) -> str:
    """Return the digest binding one seed into a selection identity."""

    return sha256_digest(seed.model_dump(mode="json"))


def snapshot_of_context(context: KnowledgeReadContext) -> KnowledgeReadSnapshot:
    """Return the declared snapshot of one read context, sealed with the context's own digest.

    ``context_digest`` here is the digest of the read context's resolved identity, which is what a
    cursor binds. It is derived, and it is not canonical database content.
    """

    return KnowledgeReadSnapshot(
        repository_id=context.repository_id,
        schema_version=context.knowledge.schema_version,
        logical_digest=context.knowledge.logical_digest,
        context_digest=read_context_digest(context),
    )


def read_context_digest(context: KnowledgeReadContext) -> str:
    """Return the digest sealing one read context's whole resolved identity."""

    return sha256_digest(context.model_dump(mode="json"))


def cursor_for(
    *,
    context: KnowledgeReadContext,
    seed: KnowledgeReadSeed,
    manifest_digest: str,
    position: int,
) -> str:
    """Encode one continuation bound to its snapshot, context, seed and policy."""

    cursor = KnowledgeReadCursor(
        policy_version=KNOWLEDGE_READ_POLICY_VERSION,
        context_digest=read_context_digest(context),
        seed_digest=seed_digest(seed),
        manifest_digest=manifest_digest,
        logical_digest=context.knowledge.logical_digest,
        schema_version=context.knowledge.schema_version,
        position=position,
    )
    return _b64(cursor.model_dump_json())


def continue_from_cursor(encoded: str) -> KnowledgeReadCursor | None:
    """Decode one continuation, or return ``None`` when it is not this format's cursor."""

    try:
        return KnowledgeReadCursor.model_validate_json(_unb64(encoded))
    except (ValueError, TypeError, json.JSONDecodeError):
        return None


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii")


def _unb64(text: str) -> str:
    return base64.urlsafe_b64decode(text.encode("ascii")).decode("utf-8")
