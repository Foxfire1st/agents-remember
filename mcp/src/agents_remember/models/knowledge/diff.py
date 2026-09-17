"""The baseline-to-candidate comparison: its sides, its union items, its limits and its expansion.

This module is the whole vocabulary of one comparison, and it holds no SQL, no Git resolution and no
authority decision. It exists because a comparison is a *different claim* from a read, and the
differences are the ones that have to be unrepresentable rather than merely discouraged:

* **Two snapshots, one selector policy.** The request carries a :class:`KnowledgeDiffSide` per
  snapshot -- a baseline and a curator-updated candidate -- and each side may name a different exact
  revision. Nothing here decides which records a side selects: that is KS-R07's one selection policy,
  applied to each side by its owner. A second, diff-shaped relevance rule is not representable here
  because the request has no field that could express one.
* **No semantic verdict, by construction.** The result has no field that could hold a severity, a
  "strengthens", a "harmless" or a neutrality finding. A source-only change is reported as a changed
  *source observation* and never as a changed obligation, because the record's own field changes are
  a separate, separately typed collection. That is the packet's second non-conforming example made
  structurally impossible rather than merely avoided.
* **Origin is retained on every item.** An item carries the side payload it was selected from, so a
  relationship that only the baseline reaches is inspectable in a comparison whose candidate no longer
  traverses it. Deletion on the candidate side cannot erase the baseline half.
* **Limits are declared, not implied.** Omissions are counts with reasons, and a response that has
  them says so in :attr:`KnowledgeDiffResult.limitations`. An omission that exists without its
  declared limitation fails construction, so a partially reviewed comparison cannot be presentable as
  a whole one.

The one extension this module carries for R07's policy is :class:`KnowledgeDiffSide.selector`: an
explicit selector a side may name *in place of* the request's own seed, which is what makes "an
explicit revision selector may address different before/after revision IDs" a value rather than a
special case inside the selector.
"""

from __future__ import annotations

import base64
import json
from typing import Literal

from pydantic import Field, model_validator

from agents_remember.kernel.canonical_json import sha256_digest
from agents_remember.models.knowledge.base import (
    LABEL_MAX_LENGTH,
    PATH_MAX_LENGTH,
    PROSE_MAX_LENGTH,
    REFERENCE_MAX_LENGTH,
    SHA256_PATTERN,
    UUID_PATTERN,
    KnowledgeModel,
)
from agents_remember.models.knowledge.candidate import SnapshotIdentity
from agents_remember.models.knowledge.graph import RealizationRole
from agents_remember.models.knowledge.read import (
    KnowledgeReadContext,
    KnowledgeReadCounts,
    KnowledgeReadSeed,
    ReadItem,
    ReadRevisionGroup,
)
from agents_remember.models.knowledge.result import KnowledgeRefusal, KnowledgeRefusalCode

__all__ = [
    "DIFF_DISPLAY_MAX_ITEMS",
    "DIFF_POLICY_VERSION",
    "KNOWLEDGE_DIFF_FIELD_NAMES",
    "DiffCoverage",
    "DiffItemKind",
    "DiffLimitation",
    "DiffOmissionReason",
    "DiffRecordTransition",
    "DisplayFilter",
    "KnowledgeDiffBinding",
    "KnowledgeDiffBudget",
    "KnowledgeDiffCounts",
    "KnowledgeDiffCursor",
    "KnowledgeDiffExpansion",
    "KnowledgeDiffItem",
    "KnowledgeDiffPage",
    "KnowledgeDiffRequest",
    "KnowledgeDiffResult",
    "KnowledgeDiffSide",
    "KnowledgeDiffSourceChange",
    "KnowledgeDiffSummary",
    "OmittedChanges",
    "ReadSide",
    "SideAbsence",
    "SideRevisionGroups",
    "continue_diff_from_cursor",
    "diff_binding_digest",
    "diff_cursor_for",
    "filter_is_empty",
    "filter_policy",
    "side_revision_groups",
]

# The policy this comparison is produced by. It names the comparison contract, not a second
# selection rule: the selection each side performs is R07's, and its version travels with the
# request's own policy rather than being restated here. It is part of the binding because a
# comparison produced under another contract is not a continuation of this one.
DIFF_POLICY_VERSION = "recorded-two-snapshot-union/v1"

# The declared display budget. It bounds what one page *shows* and never what the comparison
# *selected*: the filtered and raw item totals are both carried, so a display cut cannot be read as
# a narrower comparison.
DIFF_DISPLAY_MAX_ITEMS = 32

ReadSide = Literal["before", "after"]

# The two snapshots a comparison is between. The names are the packet's own -- a baseline and a
# curator-updated candidate -- because a caller reading a response has to be able to tell which half
# of it came from the worktree candidate.
DiffItemKind = Literal[
    "invariant",
    "family",
    "membership",
    "realization",
    "advertised_family",
]

DiffCoverage = Literal[
    "selected_both",
    "selected_before_only",
    "selected_after_only",
    "present_outside_selection",
    "absent_from_snapshot",
]

# How one union item's record moved between the two snapshots, stated as a fact about the record's
# identity and the two selected sets. It is deliberately not a comparison of *meanings*: an item is
# ``unchanged`` when both sides hold the same record with the same fields, ``changed`` when both
# sides hold it and its fields differ, ``added``/``removed`` when only the candidate or only the
# baseline holds it, and ``superseding``/``superseded`` when the one record identity has a different
# exact revision on each side -- the state a schema with immutable revisions produces when an author
# revises a statement, and the one shape that must not be rendered as a deletion plus an addition.
DiffRecordTransition = Literal[
    "unchanged",
    "changed",
    "added",
    "removed",
    "superseding",
    "superseded",
]

# Every reason this response can give for omitting a change. It is *closed* on purpose: a reason
# with no producer would be dead vocabulary, and the validator below checks each member against the
# limitation that must advertise it, so a reason nothing can emit could never be checked in either
# direction. `assessment_beyond_this_increment` was declared here and removed in fix round 1 (F6)
# for exactly that reason: nothing in this package constructed it and no limitation advertised it.
DiffOmissionReason = Literal[
    "outside_the_display_filter",
    "present_outside_the_declared_selection",
    "change_not_attributed_to_a_recorded_realization",
]

# Every declared limit a comparison can carry. Each is a statement about what this response cannot
# claim, and each is checked against the item and omission data by this module's own validator so a
# gap cannot be omitted from the declaration that is supposed to advertise it.
DiffLimitation = Literal[
    "display_filtered",
    "records_present_outside_the_selection",
    "unattributed_changed_paths",
    "no_semantic_assessment_performed",
]

# The record fields the comparison compares, in one declared order. It is the *semantic* payload of
# a record: the statement or joint guarantee, the conditions a reader acts on, the exclusions, the
# lifecycle the author recorded, the acceptance it cites, the authored provenance and the stored
# payload digest. ``selection_reasons`` is deliberately absent: it says which route of the selection
# reached a record, which is a fact about a traversal and not about the record, and reporting it as a
# changed field would dress a traversal difference up as a content change.
KNOWLEDGE_DIFF_FIELD_NAMES: tuple[str, ...] = (
    "acceptance_ref",
    "applicability",
    "essential_conditions",
    "exclusions",
    "joint_guarantee",
    "lifecycle",
    "payload_digest",
    "provenance",
    "statement",
)


class KnowledgeDiffSide(KnowledgeModel):
    """One side of a comparison: the exact snapshot to read, and the selector that reads it.

    ``context`` is the whole admission this side has, exactly as the R07 read admits one: a
    namespace, an exact logical snapshot and an optional exact code tree. Nothing here selects a
    newer commit, a working tree or a Markdown document when the named one is unavailable.

    ``selector`` is optional and, when present, *replaces* the request's seed for this side only.
    That is what lets an explicit before revision and an explicit after revision be addressed
    separately, and it is the only thing this leaf adds to the selection contract: the policy that
    turns a selector into a selected set is R07's, unchanged and applied twice.
    """

    context: KnowledgeReadContext
    selector: KnowledgeReadSeed | None = None

    @model_validator(mode="after")
    def _require_one_namespace(self) -> KnowledgeDiffSide:
        if self.context.repository_id != self.context.knowledge.repository_id:
            raise ValueError(
                "a comparison side names a namespace its selected knowledge snapshot does not "
                "belong to"
            )
        return self


class KnowledgeDiffBudget(KnowledgeModel):
    """How much of the comparison one page displays.

    It is a display budget and nothing else. The selected set is computed whole and both its raw and
    filtered totals travel on every page, so a small budget can never read as a small comparison.
    """

    max_items: int = Field(default=DIFF_DISPLAY_MAX_ITEMS, ge=1)


class DisplayFilter(KnowledgeModel):
    """One declared display filter: registered relationship roles, and nothing inferred.

    A filter may select **registered relationships** -- the authored ``RealizationRole`` a
    realization claim was recorded with -- and, in a later increment, explicit authored annotations.
    It may not select by a computed relevance, a size, a recency or an inferred impact, because none
    of those is a recorded fact and a filter that invented one would be reporting a decision as data.

    Filtering reduces what is *displayed*. It does not reduce the comparison: every suppressed item
    is counted with its reason, and the response declares that it was filtered.
    """

    realization_roles: tuple[RealizationRole, ...] = ()

    @model_validator(mode="after")
    def _require_no_duplicate_role(self) -> DisplayFilter:
        if len(set(self.realization_roles)) != len(self.realization_roles):
            raise ValueError("a display filter must not name the same role twice")
        return self


def filter_is_empty(display_filter: DisplayFilter | None) -> bool:
    """Return whether one filter suppresses nothing, so no filter is reported as applied."""

    return display_filter is None or not display_filter.realization_roles


class KnowledgeDiffRequest(KnowledgeModel):
    """One comparison: the selector, the two sides, the display filter and the page position."""

    selector: KnowledgeReadSeed = Field(discriminator="kind")
    before: KnowledgeDiffSide
    after: KnowledgeDiffSide
    display_filter: DisplayFilter | None = None
    budget: KnowledgeDiffBudget = Field(default_factory=KnowledgeDiffBudget)
    continuation: str | None = Field(default=None, max_length=PROSE_MAX_LENGTH)


class KnowledgeDiffBinding(KnowledgeModel):
    """The identity a comparison and its continuation are bound to.

    Every input that can move the answer is here, and each is a *value* rather than a path or a
    name: the policy, both declared logical snapshots, both resolved contexts, both code trees and
    both side selectors. A candidate whose content changed has another ``after`` snapshot identity,
    so it cannot be continued under this binding -- that is how "candidate changes invalidate prior
    diff continuations" is enforced instead of being documented.
    """

    policy_version: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    before: SnapshotIdentity
    after: SnapshotIdentity
    before_context_digest: str = Field(pattern=SHA256_PATTERN)
    after_context_digest: str = Field(pattern=SHA256_PATTERN)
    before_selector_digest: str = Field(pattern=SHA256_PATTERN)
    after_selector_digest: str = Field(pattern=SHA256_PATTERN)
    before_code_tree_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}$|^[0-9a-f]{64}$")
    after_code_tree_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}$|^[0-9a-f]{64}$")


def diff_binding_digest(binding: KnowledgeDiffBinding) -> str:
    """Return the digest sealing one comparison binding.

    The digest is what a continuation carries. It is derived from the binding's own fields rather
    than stored beside them, so a caller cannot present a digest that disagrees with the identity
    the response published.
    """

    return sha256_digest(binding.model_dump(mode="json"))


class KnowledgeDiffSourceChange(KnowledgeModel):
    """One realization claim's source context, observed on both sides and change-typed.

    ``before_observation`` and ``after_observation`` are the two anchor observations exactly as the
    two sides reported them, or ``None`` when that side held no such claim -- a removed realization
    keeps its before observation, which is the whole point of retaining both sides. The four booleans
    are the packet's required separation: ``record_field_changed`` is a statement about the *claim's
    own authored fields*, ``source_observation_changed`` is a statement about the *source*, and the
    two ``*_change_only`` flags say which of the two moved while the other did not. A source-only
    change therefore cannot be read as a changed obligation, and no field exists that could label
    either one with a meaning: nothing in this model can carry "strengthens", "harmless" or any other
    assessment.

    ``missing_side`` is not a third change statement but the reason the other four are all false: a
    claim only one snapshot holds has no second observation to compare against, so the honest report
    is that the comparison was not made rather than that nothing moved. The absent side's own source
    context is still carried in the observation field beside it, which is what keeps a removed
    realization's earlier code inspectable.
    """

    claim_id: str = Field(pattern=UUID_PATTERN)
    before_observation: ReadItem | None = None
    after_observation: ReadItem | None = None
    record_field_changed: bool
    source_observation_changed: bool
    source_change_only: bool
    record_change_only: bool
    missing_side: ReadSide | None = None


class KnowledgeDiffItem(KnowledgeModel):
    """One union item: what each side selected, what is reachable, and how the two differ.

    The item is the union's unit, keyed by a stable identity (an identity, a revision, a membership
    or a claim) rather than by a display label, so the same record re-authored with another label
    stays one item. ``before`` and ``after`` carry the side payloads with their own selection
    reasons, which is what retains each record's originating snapshot.

    ``coverage`` is the distinction the packet requires and the design's second correction: a record
    the other side holds but did not select is ``present_outside_selection`` -- reported, with the
    path that reaches it, and explicitly not a deletion -- while a record the snapshot does not hold
    at all is ``absent_from_snapshot``.

    ``record_transition`` and ``changed_fields`` are the *record* half of the change, and
    :class:`KnowledgeDiffSourceChange` is the *source* half. The two are separate collections on
    purpose: a statement that moved is reported here and not as a source observation, a source that
    moved is reported there and not as a changed record, and no field of either can carry a verdict
    about what a change means.
    """

    item_id: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    kind: DiffItemKind
    coverage: DiffCoverage
    record_transition: DiffRecordTransition
    before: ReadItem | None = None
    after: ReadItem | None = None
    before_selected: bool
    after_selected: bool
    # The record the item is about, which is what pairs a claim with a claim and a revision with a
    # revision across two snapshots whose revision ids may differ.
    record_id: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
    revision_id: str | None = Field(default=None, pattern=UUID_PATTERN)
    changed_fields: tuple[str, ...] = ()
    # The path a reader follows to this item when it was reached through a family expansion rather
    # than by the seed, so "selected" is inspectable and not merely asserted.
    reached_via: tuple[str, ...] = ()
    source_change: KnowledgeDiffSourceChange | None = None


class OmittedChanges(KnowledgeModel):
    """One declared omission: how many items, of which kind, and why they are not displayed.

    An omission is never a judgement about the omitted change. It names the mechanism that removed
    it from the display -- a filter the caller set, a record the declared selection did not reach, a
    change no recorded realization attributes, or an assessment this increment does not perform.
    """

    reason: DiffOmissionReason
    item_kind: DiffItemKind | None = None
    omitted_count: int = Field(ge=0)
    detail: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)


class SideAbsence(KnowledgeModel):
    """One side's own typed absence, reported without stopping the comparison.

    A comparison of two snapshots can legitimately find that one side holds nothing for the selector
    while the other holds records -- a candidate that removed every realization the baseline reached
    is exactly that. Refusing the whole comparison there would hide the removal; serving the union
    silently would hide the absence. So the absence travels beside the page under R07's own two
    absence codes, and neither fact is lost.
    """

    side: ReadSide
    code: KnowledgeRefusalCode
    detail: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)


class SideRevisionGroups(KnowledgeModel):
    """The revision groups each side selected, kept apart because they can differ.

    An identity seed selects every retained revision *of that snapshot*, and two snapshots may hold
    a different number of them. Keeping one tuple per side is what makes "identity seeds retain the
    revision groups selected on each side" a value a caller reads rather than a claim it infers.
    """

    before: tuple[ReadRevisionGroup, ...] = ()
    after: tuple[ReadRevisionGroup, ...] = ()


def side_revision_groups(
    before: tuple[ReadRevisionGroup, ...], after: tuple[ReadRevisionGroup, ...]
) -> SideRevisionGroups:
    """Seal one per-side grouping of selected revisions."""

    return SideRevisionGroups(before=before, after=after)


class KnowledgeDiffExpansion(KnowledgeModel):
    """The reference to the full selected-candidate source diff this response was cut from.

    This is the packet's expansion: a response that showed a filtered or partial view must point at
    the whole comparison, and this is that pointer as a value. It names both code trees -- never a
    branch, a working tree or ``HEAD`` -- the two roots a caller runs ``command`` in, and the exact
    paths the two trees differ at. It carries no source text: this increment reports the
    *attribution* of source, and a document dump would be a different operation with a different
    limit.

    ``unattributed_changed_paths`` is the packet's other visible gap: a path that changed between the
    two trees and that **no** recorded realization claim attributes. Those paths are listed rather
    than dropped, because dropping them would hide exactly the changes a reviewer most needs to see.
    """

    reference: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    command: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)
    before_root: str | None = Field(default=None, max_length=PATH_MAX_LENGTH)
    after_root: str | None = Field(default=None, max_length=PATH_MAX_LENGTH)
    before_code_tree_id: str | None = Field(default=None, max_length=LABEL_MAX_LENGTH)
    after_code_tree_id: str | None = Field(default=None, max_length=LABEL_MAX_LENGTH)
    attributed_changed_paths: tuple[str, ...] = ()
    unattributed_changed_paths: tuple[str, ...] = ()
    detail: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)


class KnowledgeDiffCounts(KnowledgeModel):
    """What the comparison selected, what it displayed, and what it suppressed.

    The two totals are the point: ``items_total`` is the whole comparison and ``displayed_total`` is
    this page's view of it. A filter or a budget moves the second and never the first, so no caller
    can read a filtered response as a smaller comparison.
    """

    items_total: int = Field(ge=0)
    items_returned: int = Field(ge=0)
    items_remaining: int = Field(ge=0)
    displayed_total: int = Field(ge=0)
    suppressed_total: int = Field(ge=0)
    changed_field_count: int = Field(ge=0)
    changed_source_observation_count: int = Field(ge=0)

    @model_validator(mode="after")
    def _require_one_walk(self) -> KnowledgeDiffCounts:
        if self.items_returned + self.items_remaining != self.items_total:
            raise ValueError(
                "returned plus remaining comparison items must equal the comparison total; a page "
                "that reports otherwise cannot be continued to the whole comparison"
            )
        if self.displayed_total + self.suppressed_total > self.items_total:
            raise ValueError(
                "displayed plus suppressed items cannot exceed the comparison total; a suppression "
                "count larger than the comparison it was taken from is not a measurement"
            )
        return self


class KnowledgeDiffSummary(KnowledgeModel):
    """The per-snapshot counts, and the statement that they are counts and not verdicts.

    ``before`` and ``after`` are the two sides' own :class:`KnowledgeReadCounts`, carried whole so a
    caller can see that a side's zero is a measured zero of a declared selection rather than a
    silence. Nothing in this model aggregates them into a judgement about the change.
    """

    before: KnowledgeReadCounts
    after: KnowledgeReadCounts


class KnowledgeDiffPage(KnowledgeModel):
    """One displayed page of the comparison, with its continuation and its honesty flags.

    ``has_more`` and ``enumeration_complete`` are one statement and are refused when they disagree,
    exactly as the R07 page refuses it: a truncated view of a comparison must never be presentable
    as the whole of it.
    """

    items: tuple[KnowledgeDiffItem, ...] = ()
    counts: KnowledgeDiffCounts
    has_more: bool
    enumeration_complete: bool
    continuation: str | None = Field(default=None, max_length=PROSE_MAX_LENGTH)

    @model_validator(mode="after")
    def _require_honest_truncation(self) -> KnowledgeDiffPage:
        if self.has_more == self.enumeration_complete:
            raise ValueError(
                "has_more and enumeration_complete describe one state and must be opposites; a "
                "truncated comparison must never be presentable as a complete one"
            )
        if self.has_more != (self.continuation is not None):
            raise ValueError("a page with remaining items must carry a continuation and no other")
        return self


class KnowledgeDiffResult(KnowledgeModel):
    """The typed outcome of one comparison: a declared page, or one typed refusal.

    ``limitations`` is the declaration the packet requires. Two of its members are checked against
    the data by this model's own validator -- an omitted change without the limitation that
    advertises it, or a declared limitation with no omission behind it, both fail construction. The
    third is unconditional and says the one thing this response can never be read as: it is a facts
    comparison, and ``no_semantic_assessment_performed`` is always present because no field of this
    result could carry one.
    """

    state: Literal["page", "refused"]
    operation: Literal["diff_knowledge_scope"] = "diff_knowledge_scope"
    repository_id: str = Field(pattern=UUID_PATTERN)
    policy_version: str = Field(default=DIFF_POLICY_VERSION, max_length=LABEL_MAX_LENGTH)
    binding: KnowledgeDiffBinding | None = None
    binding_digest: str | None = Field(default=None, pattern=SHA256_PATTERN)
    selector_digest: str | None = Field(default=None, pattern=SHA256_PATTERN)
    policy: str | None = Field(default=None, max_length=LABEL_MAX_LENGTH)
    summary: KnowledgeDiffSummary | None = None
    revision_groups: SideRevisionGroups = Field(default_factory=SideRevisionGroups)
    limitations: tuple[DiffLimitation, ...] = ()
    omissions: tuple[OmittedChanges, ...] = ()
    side_absences: tuple[SideAbsence, ...] = ()
    expansion: KnowledgeDiffExpansion | None = None
    page: KnowledgeDiffPage | None = None
    refusal: KnowledgeRefusal | None = None

    @model_validator(mode="after")
    def _require_one_outcome(self) -> KnowledgeDiffResult:
        if self.state == "page" and (self.page is None or self.refusal is not None):
            raise ValueError("a comparison page result carries a page and no refusal")
        if self.state == "refused" and (self.refusal is None or self.page is not None):
            raise ValueError("a refused comparison carries its refusal and no page")
        return self

    @model_validator(mode="after")
    def _require_the_limitation_matches_the_omission(self) -> KnowledgeDiffResult:
        """Refuse a response whose declared limits and actual omissions disagree.

        The check runs in both directions on purpose. A response that omitted something without
        declaring the limit would be a partial view wearing a whole one's clothes, which the packet
        names directly; a response that declared a limit it did not have would teach a reviewer to
        ignore the field.
        """

        reasons = {omission.reason for omission in self.omissions}
        declared = set(self.limitations)
        if self.state != "page":
            return self
        expected = {
            "display_filtered": "outside_the_display_filter",
            "records_present_outside_the_selection": ("present_outside_the_declared_selection"),
            "unattributed_changed_paths": ("change_not_attributed_to_a_recorded_realization"),
        }
        for limitation, reason in expected.items():
            hidden = reason in reasons
            if hidden != (limitation in declared):
                raise ValueError(
                    f"a comparison that omitted changes for {reason!r} must declare the "
                    f"{limitation!r} limitation, and one that declares it must have omitted "
                    "something for that reason"
                )
        if "no_semantic_assessment_performed" not in declared:
            raise ValueError(
                "every comparison states that it performs no semantic assessment; a response that "
                "omitted the statement would read as one that performed none because there was "
                "nothing to assess"
            )
        return self


class KnowledgeDiffCursor(KnowledgeModel):
    """The exact continuation of one comparison: the binding, the position and the filter.

    The binding is the point. A cursor is a position in one comparison of two named snapshots, so a
    candidate that changed after the cursor was issued presents a binding this cursor does not name
    and is refused rather than served a page stitched from two candidate states.
    """

    cursor_format: Literal["knowledge-diff-cursor/v1"] = "knowledge-diff-cursor/v1"
    policy_version: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    binding_digest: str = Field(pattern=SHA256_PATTERN)
    selector_digest: str = Field(pattern=SHA256_PATTERN)
    policy: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    position: int = Field(ge=0)


def diff_cursor_for(
    *,
    binding: KnowledgeDiffBinding,
    selector_digest: str,
    policy: str,
    position: int,
) -> str:
    """Encode one continuation bound to its comparison, its selector and its display filter."""

    cursor = KnowledgeDiffCursor(
        policy_version=DIFF_POLICY_VERSION,
        binding_digest=diff_binding_digest(binding),
        selector_digest=selector_digest,
        policy=policy,
        position=position,
    )
    return base64.urlsafe_b64encode(cursor.model_dump_json().encode("utf-8")).decode("ascii")


def continue_diff_from_cursor(encoded: str) -> KnowledgeDiffCursor | None:
    """Decode one comparison continuation, or return ``None`` when it is not this format's cursor.

    It is a comparison's own decoder rather than the read's, because the two cursors are different
    documents that happen to share an encoding: a read cursor positions a page in one selection, and a
    comparison cursor positions one in a union of two. Presenting either to the other operation is a
    caller's mistake, and the honest answer is that the value is not this format rather than a page
    assembled from a position the other operation never issued.
    """

    try:
        return KnowledgeDiffCursor.model_validate_json(_unb64(encoded))
    except (ValueError, TypeError, json.JSONDecodeError):
        return None


def _unb64(text: str) -> str:
    return base64.urlsafe_b64decode(text.encode("ascii")).decode("utf-8")


# The filter the display is cut by, as one canonical string. The *selector* digest already binds
# which records were selected; this binds what the page chose to show of them, so a continuation
# cannot silently change the display filter it was a position in.
def filter_policy(roles: tuple[str, ...]) -> str:
    """Return the canonical spelling of one filter's effect, or ``None``'s stand-in."""

    return "roles:" + "|".join(roles) if roles else "roles:<none>"
