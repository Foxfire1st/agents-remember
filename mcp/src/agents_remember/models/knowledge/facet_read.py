"""The facet-specific selection's declared contract: seeds, items, counts, page and result.

``KS-R07@v1`` owns the recorded-scope selection over paths, invariant revisions, family revisions,
memberships and realizations, and its declared set, counts, cursor contract and finite family
frontier are unchanged by this leaf. Facets are reached by this **separate** selection instead
(``KS-R07:43``'s named increment): its own seed kinds, its own item kinds, its own counts and its
own declared policy name. Nothing here is reachable from a shipped seed, and a shipped seed's page
is therefore byte-identical to what it was before this leaf -- not by a guard, but because no code
path is shared.

Two decisions are recorded here and both are the reason the module exists:

* **The selection is complete or it refuses.** One seed selects one bounded aggregate, and a
  selection that reaches ``FACET_SELECTION_ITEM_LIMIT`` refuses with the shipped
  ``selection_incomplete`` rather than serving a page that reads as the whole set. There is no
  cursor: a continuation contract is a second declared policy surface, and this selection does not
  need one to be honest about what it selected.
* **Nothing here derives currency.** Every retained facet revision is served as its own item with
  its exact revision identity, its provenance and its stored lifecycle, in an order fixed by stable
  identifiers. The only statement about which explanation revision matters is the explanation
  record's own ``current_revision_id``, which is a *recorded designation*: it is reported verbatim,
  never computed, and no field here could be read as "newest" (requirement 6.3).

Every value model is frozen and extra-forbidden under the shipped vocabulary base, so a page cannot
be extended with a field that carries a verdict, a confidence or a severity: there is nowhere for
one to go.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Any, Literal

from pydantic import Field, model_validator

from agents_remember.kernel.canonical_json import sha256_digest
from agents_remember.models.knowledge.authorship import Authorship
from agents_remember.models.knowledge.base import (
    LABEL_MAX_LENGTH,
    PROSE_MAX_LENGTH,
    SHA256_PATTERN,
    UUID_PATTERN,
    KnowledgeModel,
)
from agents_remember.models.knowledge.facet import (
    AttachmentEndpoint,
    ExplanationSubject,
)
from agents_remember.models.knowledge.read import KnowledgeReadSnapshot
from agents_remember.models.knowledge.result import KnowledgeRefusal

# The declared policy this selection's pages were produced by. It is its own name rather than the
# shipped ``recorded-family-frontier/v1``: a policy name is a claim about which selection produced a
# page, and two selections that select different things are two policies whatever module they live
# in.
FACET_SELECTION_POLICY_VERSION = "authored-judgment-facets/v1"

# The declared execution bound of one facet selection, and the reason it refuses past it. It is a
# bound on enumeration, not a page size: a selection either fits whole or is refused, so a caller is
# never handed a page it could read as the complete set.
FACET_SELECTION_ITEM_LIMIT = 5000

FACET_ITEM_LIMIT_REASON = f"the facet selection exceeded the declared execution bound of {FACET_SELECTION_ITEM_LIMIT} items"


# ---------------------------------------------------------------------------
# Stored values. Each is the exact shape one canonical row serves, with the digest the read
# operations expose so a caller carries an expectation straight from a read.


class FacetRecord(KnowledgeModel):
    """One stored facet envelope: its kind, its lifecycle and what governs it.

    ``governing_route_id`` is the **envelope's** association (requirement 4.2), not a second route
    mechanism: ``None`` is the explicit ungoverned state, never a default of the repository root,
    and a route id here is the route the record itself declares.
    """

    repository_id: str = Field(pattern=UUID_PATTERN)
    record_id: str = Field(pattern=UUID_PATTERN)
    facet_kind: str = Field(min_length=1)
    record_schema: str = Field(min_length=1)
    lifecycle: str = Field(min_length=1)
    authority_home: str = Field(min_length=1)
    governing_route_id: str | None = Field(default=None, pattern=UUID_PATTERN)
    provenance: Authorship
    row_digest: str = Field(pattern=SHA256_PATTERN)


class FacetRevision(KnowledgeModel):
    """One sealed facet revision, with the payload exactly as it was validated and stored.

    ``content_digest`` is the revision aggregate's own seal. It is not a digest column on the facet
    *record*: the record mints no identity of its own and carries none (requirement 7.2).
    """

    repository_id: str = Field(pattern=UUID_PATTERN)
    record_id: str = Field(pattern=UUID_PATTERN)
    revision_id: str = Field(pattern=UUID_PATTERN)
    facet_kind: str = Field(min_length=1)
    record_schema: str = Field(min_length=1)
    payload: Mapping[str, Any]
    predecessor_revision_id: str | None = Field(default=None, pattern=UUID_PATTERN)
    content_digest: str = Field(pattern=SHA256_PATTERN)
    provenance: Authorship


class FacetAttachment(KnowledgeModel):
    """One authored attachment: the exact facet revision and the exact typed endpoint it names."""

    repository_id: str = Field(pattern=UUID_PATTERN)
    attachment_id: str = Field(pattern=UUID_PATTERN)
    facet_revision_id: str = Field(pattern=UUID_PATTERN)
    endpoint: AttachmentEndpoint
    provenance: Authorship
    row_digest: str = Field(pattern=SHA256_PATTERN)


class DecisionSupersession(KnowledgeModel):
    """One recorded supersession edge between two exact decision revisions.

    The edge is the only thing this row asserts. It does not rewrite the superseded decision, and
    the superseded revision is served with its own payload, provenance and attachments unchanged
    (requirement 5.3).
    """

    repository_id: str = Field(pattern=UUID_PATTERN)
    superseding_revision_id: str = Field(pattern=UUID_PATTERN)
    superseded_revision_id: str = Field(pattern=UUID_PATTERN)
    provenance: Authorship
    row_digest: str = Field(pattern=SHA256_PATTERN)


class ExplanationRecord(KnowledgeModel):
    """One separable explanation record: its exact subject and its recorded designation.

    ``current_revision_id`` is the designation this record *stores*. It is ``None`` until a
    designation operation has recorded one, and that ``None`` is a fact -- an explanation whose
    recorded designation is absent has no designated revision, which is not the same statement as
    "the newest one is current".
    """

    repository_id: str = Field(pattern=UUID_PATTERN)
    explanation_id: str = Field(pattern=UUID_PATTERN)
    subject: ExplanationSubject
    current_revision_id: str | None = Field(default=None, pattern=UUID_PATTERN)
    provenance: Authorship
    row_digest: str = Field(pattern=SHA256_PATTERN)


class ExplanationRevision(KnowledgeModel):
    """One sealed explanation revision: append-only, with its exact predecessor named."""

    repository_id: str = Field(pattern=UUID_PATTERN)
    explanation_id: str = Field(pattern=UUID_PATTERN)
    revision_id: str = Field(pattern=UUID_PATTERN)
    predecessor_revision_id: str | None = Field(default=None, pattern=UUID_PATTERN)
    body: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)
    payload_digest: str = Field(pattern=SHA256_PATTERN)
    provenance: Authorship


# ---------------------------------------------------------------------------
# Seeds. Two kinds, because the selection answers two questions: what is this facet record made of,
# and which explanations explain this exact statement revision.


class FacetRecordSeed(KnowledgeModel):
    """Select one facet record's aggregate by its exact record identity."""

    kind: Literal["facet_record"] = "facet_record"
    record_id: str = Field(pattern=UUID_PATTERN)


class ExplanationSubjectSeed(KnowledgeModel):
    """Select every explanation whose subject is one exact statement revision."""

    kind: Literal["explanation_subject"] = "explanation_subject"
    subject: ExplanationSubject


FacetReadSeed = Annotated[
    FacetRecordSeed | ExplanationSubjectSeed,
    Field(discriminator="kind"),
]


def facet_seed_digest(seed: FacetReadSeed) -> str:
    """Return the digest sealing one facet seed, on the shipped rule for a read selector."""

    return sha256_digest(seed.model_dump(mode="json"))


# ---------------------------------------------------------------------------
# Items. One model per item kind, so an item carries exactly the fields its kind has and a page
# cannot grow an optional bag that means different things per kind.


class FacetRecordItem(KnowledgeModel):
    """The facet envelope itself."""

    kind: Literal["facet_record"] = "facet_record"
    facet: FacetRecord


class FacetRevisionItem(KnowledgeModel):
    """One retained facet revision."""

    kind: Literal["facet_revision"] = "facet_revision"
    revision: FacetRevision


class FacetAttachmentItem(KnowledgeModel):
    """One authored attachment, carrying its exact typed endpoint."""

    kind: Literal["facet_attachment"] = "facet_attachment"
    attachment: FacetAttachment


class DecisionSupersessionItem(KnowledgeModel):
    """One recorded supersession edge."""

    kind: Literal["decision_supersession"] = "decision_supersession"
    supersession: DecisionSupersession


class ExplanationItem(KnowledgeModel):
    """One explanation record, with its recorded designation reported verbatim."""

    kind: Literal["explanation"] = "explanation"
    explanation: ExplanationRecord


class ExplanationRevisionItem(KnowledgeModel):
    """One retained explanation revision."""

    kind: Literal["explanation_revision"] = "explanation_revision"
    revision: ExplanationRevision


FacetReadItem = Annotated[
    FacetRecordItem
    | FacetRevisionItem
    | FacetAttachmentItem
    | DecisionSupersessionItem
    | ExplanationItem
    | ExplanationRevisionItem,
    Field(discriminator="kind"),
]

FacetReadItemKind = Literal[
    "facet_record",
    "facet_revision",
    "facet_attachment",
    "decision_supersession",
    "explanation",
    "explanation_revision",
]

# The declared order of the item stream. It is a fixed order over item kinds and then over stable
# identifiers, so two runs over one snapshot produce the same page and no authored label, insertion
# order or timestamp can move an item.
_FACET_KIND_ORDER: Mapping[str, int] = {
    "facet_record": 1,
    "facet_revision": 2,
    "facet_attachment": 3,
    "decision_supersession": 4,
    "explanation": 5,
    "explanation_revision": 6,
}


def facet_item_sort_key(item: FacetReadItem) -> tuple[int, str, str]:
    """Return the declared sort key of one item: item kind, then two stable identifiers."""

    return (_FACET_KIND_ORDER[item.kind], *_facet_item_identifiers(item))


def _facet_item_identifiers(item: FacetReadItem) -> tuple[str, str]:
    """Return the two stable identifiers one item is ordered by."""

    if isinstance(item, FacetRecordItem):
        return (item.facet.record_id, item.facet.record_id)
    if isinstance(item, FacetRevisionItem):
        return (item.revision.record_id, item.revision.revision_id)
    if isinstance(item, FacetAttachmentItem):
        return (item.attachment.facet_revision_id, item.attachment.attachment_id)
    if isinstance(item, DecisionSupersessionItem):
        return (item.supersession.superseding_revision_id, item.supersession.superseded_revision_id)
    if isinstance(item, ExplanationItem):
        return (item.explanation.explanation_id, item.explanation.explanation_id)
    return (item.revision.explanation_id, item.revision.revision_id)


def facet_item_id(item: FacetReadItem) -> str:
    """Return the identity one item is addressed by inside its selection."""

    first, second = _facet_item_identifiers(item)
    return f"{item.kind}:{first}/{second}"


class FacetReadCounts(KnowledgeModel):
    """What one facet selection selected, by item kind.

    The counts are the selected set's own arithmetic: they are derived from the same item tuple the
    page is built from, so a page cannot report a total its items do not add up to. There is no
    ``has_more`` and no remaining count, because a selection that does not fit whole is refused
    rather than paged.
    """

    facet_records: int = Field(ge=0)
    facet_revisions: int = Field(ge=0)
    attachments: int = Field(ge=0)
    supersession_edges: int = Field(ge=0)
    explanations: int = Field(ge=0)
    explanation_revisions: int = Field(ge=0)

    @property
    def items_total(self) -> int:
        """Return the number of items the counts describe."""

        return (
            self.facet_records
            + self.facet_revisions
            + self.attachments
            + self.supersession_edges
            + self.explanations
            + self.explanation_revisions
        )


class FacetReadPage(KnowledgeModel):
    """One complete facet page: every item the seed selected, in the declared order."""

    items: tuple[FacetReadItem, ...]
    counts: FacetReadCounts
    # ``Literal[True]`` rather than a flag: this selection serves a page only when it holds the
    # whole selected set, so the completeness claim is not something a builder can set to False.
    enumeration_complete: Literal[True] = True

    @model_validator(mode="after")
    def _require_counts_to_describe_the_items(self) -> FacetReadPage:
        if self.counts.items_total != len(self.items):
            raise ValueError(
                "a facet page reports the counts of the items it carries: "
                f"{self.counts.items_total} counted against {len(self.items)} item(s)"
            )
        expected = tuple(sorted(self.items, key=facet_item_sort_key))
        if self.items != expected:
            raise ValueError(
                "a facet page carries its items in the declared order; an order derived from "
                "insertion time or an authored label is not this selection's"
            )
        return self


class FacetReadRequest(KnowledgeModel):
    """One facet-specific selection request: exactly one seed."""

    seed: FacetReadSeed


class FacetReadResult(KnowledgeModel):
    """The typed outcome of one facet selection: a complete page, or one typed refusal."""

    state: Literal["page", "refused"]
    operation: Literal["read_facet_scope"] = "read_facet_scope"
    repository_id: str = Field(pattern=UUID_PATTERN)
    snapshot: KnowledgeReadSnapshot | None = None
    context_digest: str | None = Field(default=None, pattern=SHA256_PATTERN)
    seed: FacetReadSeed | None = None
    seed_digest: str | None = Field(default=None, pattern=SHA256_PATTERN)
    manifest_digest: str | None = Field(default=None, pattern=SHA256_PATTERN)
    policy_version: str = Field(default=FACET_SELECTION_POLICY_VERSION, max_length=LABEL_MAX_LENGTH)
    page: FacetReadPage | None = None
    refusal: KnowledgeRefusal | None = None

    @model_validator(mode="after")
    def _require_consistent_outcome(self) -> FacetReadResult:
        if self.state == "refused":
            if self.refusal is None:
                raise ValueError("a refused facet selection must carry its refusal")
            if self.page is not None:
                raise ValueError("a refused facet selection selected no page")
            return self
        if self.refusal is not None:
            raise ValueError("a facet selection that was not refused cannot also carry a refusal")
        if self.page is None:
            raise ValueError("a served facet selection carries its page")
        return self


__all__ = [
    "FACET_ITEM_LIMIT_REASON",
    "FACET_SELECTION_ITEM_LIMIT",
    "FACET_SELECTION_POLICY_VERSION",
    "DecisionSupersession",
    "DecisionSupersessionItem",
    "ExplanationItem",
    "ExplanationRecord",
    "ExplanationRevision",
    "ExplanationRevisionItem",
    "ExplanationSubjectSeed",
    "FacetAttachment",
    "FacetAttachmentItem",
    "FacetReadCounts",
    "FacetReadItem",
    "FacetReadItemKind",
    "FacetReadPage",
    "FacetReadRequest",
    "FacetReadResult",
    "FacetReadSeed",
    "FacetRecord",
    "FacetRecordItem",
    "FacetRecordSeed",
    "FacetRevision",
    "FacetRevisionItem",
    "facet_item_id",
    "facet_item_sort_key",
    "facet_seed_digest",
]
