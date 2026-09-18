"""The ``CitationBinding`` vocabulary: what is bound, and every state a binding can be observed in.

A prose document cites a knowledge record, and nothing records which one. This module declares the
record that does -- the **prose owner revision**, the **local citation key** as written in that
revision, the **typed target reference**, and the **locator** inside the target's recorded scope --
together with the closed vocabulary of states one binding observation resolves to.

Five decisions are load-bearing here and are stated once:

* **The owner revision is referenced, never minted.** :class:`ProseOwnerRevision` names the memory
  repository, the document's repository-relative confined POSIX path, and the recorded identity of
  that document *as the memory side already supplies it*: a Git object identity of the document
  blob. That is the shipped ``GitBlobIdentity`` convention the whole tree already uses for "these
  exact recorded bytes" (``models/knowledge/source.py``), not a digest scheme invented here. The
  requirement is that the identity is *recorded* and is **not** derived at read time from whatever
  the file now contains, and a blob identity read from the object store satisfies exactly that: the
  recorded object is addressed by the identity the binding stored, and the document's current bytes
  never enter the observation.

* **The local key is stored as written, in two halves, plus the written source.** ``local key`` is a
  *local* fact: its meaning is scoped to its owner revision, so a rewritten key is a different key
  and a later rewrite never silently re-points the binding. Because the key is stored as its parts
  rather than as one flattened string, the failure states stay distinguishable: whether the anchor
  text, the file, or the extent moved is a fact a curator acts on. ``written_source`` keeps the
  ``path:start-end`` the prose itself wrote, which is what makes the second reading of "target
  reference and locator" carried rather than lost.

* **The locator is the shipped union.** :data:`SourceLocator` from ``models/knowledge/source.py`` is
  the location *inside the target's recorded scope* -- a knowledge record is a claim, while the
  ``path:start-end`` the prose writes points at the code artifact. One locator vocabulary serves
  every consumer; a binding-local spelling would drift from the anchors it sits beside. A recorded
  locator stays readable when no resolver supports it, which is why ``unsupported_locator`` is a
  reported state rather than a write refusal.

* **The state set is closed, and a shared fact reuses the shipped literal.** Requirement 4.1a fixes
  that where an observation reports a fact the shipped ``AnchorResolutionState`` already names, the
  reported value **is** that shipped literal and not a binding-local spelling. Four of this
  vocabulary's members are therefore *literally* members of ``ANCHOR_RESOLUTIONS``:
  ``exact_recorded_blob`` (the key is present and the binding resolves), ``recorded_blob_mismatch``
  (the key is not recorded in its owner revision), ``recorded_object_unavailable`` (the owner
  revision's bytes cannot be obtained) and ``unsupported_locator``. The remaining members --
  ``uncovered_key_form``, ``ambiguous_key``, ``target_record_absent``, ``target_revision_absent`` and
  ``target_kind_mismatch`` -- name facts the shipped vocabulary does not have, and naming them here
  rather than folding them into a shipped literal is what keeps the mapping exact in both
  directions. ``models.knowledge.read.AnchorResolutionState`` deliberately gains **no** member: the
  extension is one-directional so an anchor resolution can never acquire a citation fact.

* **An uncovered key form is a state, not a gap.** This increment declares which written key forms it
  covers (:data:`COVERED_KEY_FORMS`). A key whose form it does not cover is *present and recognized*
  and resolves to its own explicit state, counted in the denominator exactly like every other
  unresolved state -- never omitted because the reader of that form was not built yet, and never
  folded into "the key is absent from its owner revision". That is what makes partial coverage
  visible: the view carries a ``partial_key_form_coverage`` limitation naming the uncovered form, so
  a partial coverage can never render like a complete one.

Nothing in this module is a content address, a logical digest or a fingerprint: ``payload_digest``
stays on the revision aggregate that owns it, and a binding is not an identity of the prose it cites.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Literal

from pydantic import Field, model_validator

from agents_remember.models.knowledge.base import (
    GIT_OBJECT_PATTERN,
    LABEL_MAX_LENGTH,
    PATH_MAX_LENGTH,
    PROSE_MAX_LENGTH,
    KnowledgeModel,
    require_plain_git_path,
)
from agents_remember.models.knowledge.source import SourceLocator

__all__ = [
    "BINDING_RECORD_KIND",
    "BINDING_RECORD_SCHEMA",
    "BINDING_STATES",
    "CITATION_BINDING_POLICY_VERSION",
    "CITATION_SELECTION_ITEM_LIMIT",
    "CIT_MARK",
    "COVERED_KEY_FORMS",
    "KEY_FORMS",
    "NO_SEMANTIC_COMPLETENESS_LIMITATION",
    "SHIPPED_BINDING_STATES",
    "SHIPPED_STATE_FACTS",
    "AmbiguousKey",
    "CitationBindingClosureRequest",
    "CitationBindingClosureResult",
    "CitationBindingCounts",
    "CitationBindingItem",
    "CitationBindingLimitation",
    "CitationBindingObservation",
    "CitationBindingPayload",
    "CitationBindingRequest",
    "CitationBindingResult",
    "CitationBindingState",
    "CitationBindingWriteIdentity",
    "CitationKeyForm",
    "CitationTargetReference",
    "KeyFormCoverage",
    "LocalCitationKey",
    "ProseCitationKey",
    "ProseOwnerRevision",
    "TableRowKeyForm",
    "TargetReferenceState",
    "WrittenSource",
    "covered_key_form_names",
    "render_local_key",
    "shipped_literal_for",
]

# The prose citation mark, as the shipped citation parser declares it
# (``memory_quality/style/citations/prose.py``: ``CIT_MARK = "cit:("``). It is re-declared here as a
# *quotation* rather than imported, because the memory layer may not depend on the style-checker
# package and because the binding stores the body rather than the whole construct: a recorded
# ``cit:`` body re-rendered with this mark is how the recorded key -- not the document's current
# text -- becomes the string an observation looks for. A case asserts the two spellings agree, so the
# quotation cannot drift from the grammar it quotes.
CIT_MARK = "cit:("

# The record envelope's registry keys for this record group. The pair is the key, exactly as the
# facet vocabulary and the detection record group declare their own: a second spelling elsewhere
# could drift from the registry.
BINDING_RECORD_KIND = "citation_binding"
BINDING_RECORD_SCHEMA = "citation-binding/v1"

# The selection policy this module's closure was produced by. It travels with the closure for the
# same reason the shipped read's policy version travels with a cursor: a policy change can move the
# selected set, and a closure assembled under another policy is not this closure.
CITATION_BINDING_POLICY_VERSION = "citation-binding-closure/v1"

# The declared execution bound of one closure assembly. Reaching it refuses with
# ``selection_incomplete`` rather than emitting invented totals or claiming a complete scope.
CITATION_SELECTION_ITEM_LIMIT = 5000


# ---------------------------------------------------------------------------
# The owner revision.


class ProseOwnerRevision(KnowledgeModel):
    """The exact revision of the prose document a binding was authored against.

    ``blob_object_id`` is the recorded identity of the document's bytes **as the memory owner
    supplies it**: a Git object identity, the shipped convention for "these exact bytes"
    (``SourceIdentity`` in ``models/knowledge/source.py``). It is required, not optional: a binding
    whose owner revision is unknown is a binding that silently attaches to a rewrite, which is the
    one failure a citation record exists to prevent. The identity is *referenced* -- the binding
    never computes it, and a read resolves the recorded object by the identity the row already
    holds rather than by digesting whatever the document now contains.
    """

    repository: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    document_path: str = Field(min_length=1, max_length=PATH_MAX_LENGTH)
    blob_object_id: str = Field(pattern=GIT_OBJECT_PATTERN)

    @model_validator(mode="after")
    def _require_a_confined_document_path(self) -> ProseOwnerRevision:
        """Refuse a document path that is not a confined repository-relative POSIX path.

        The owner revision names a path *inside the memory repository's tree*, so the same
        confinement the shipped anchor path applies holds here: an absolute, drive, UNC, backslash,
        NUL, empty, ``.`` or ``..`` spelling, or a Git-pathspec spelling, is refused in the
        vocabulary rather than at the tree lookup. A malformed spelling that reached the lookup
        would be answered as an absent document, which is a false statement about the repository.
        """

        cleaned = self.document_path.strip()
        if cleaned != self.document_path:
            raise ValueError(
                f"a document path must not carry surrounding space: {self.document_path!r}"
            )
        if cleaned.startswith(("/", "\\", "~")):
            raise ValueError(f"a document path must be repository-relative: {self.document_path!r}")
        if "\\" in cleaned or "\x00" in cleaned:
            raise ValueError(
                f"a document path must be a NUL-free POSIX path: {self.document_path!r}"
            )
        if any(part in {"", ".", ".."} for part in cleaned.split("/")):
            raise ValueError(
                f"a document path must not contain empty, '.' or '..' segments: {self.document_path!r}"
            )
        require_plain_git_path(cleaned, what="a document path")
        return self


# ---------------------------------------------------------------------------
# The local citation key, as written.


class WrittenSource(KnowledgeModel):
    """The ``path:start-end`` the prose itself wrote, kept as the local key's own written source.

    This is the second reading of "target reference and locator", carried rather than lost. The
    prose writes a location in the *code artifact*; the binding's own ``locator`` names a location
    inside the *target record's* recorded scope. Both are recorded, and neither is derived from the
    other. The path is a plain repository-relative spelling, because it is a quotation of what the
    document wrote rather than an address this module resolves.
    """

    path: str = Field(min_length=1, max_length=PATH_MAX_LENGTH)
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)

    @model_validator(mode="after")
    def _require_an_ordered_range(self) -> WrittenSource:
        if self.end_line < self.start_line:
            raise ValueError("end_line must not precede start_line")
        return self


class ProseCitationKey(KnowledgeModel):
    """One written ``cit:([<anchor>, ...], <path>:<start>-<end>)`` construct, as its parts.

    ``written`` is the construct **exactly as the document wrote it**, recorded as one opaque string
    and never re-derived. That is deliberate rather than lazy: this layer stores what a curator
    authored, and reconstructing the construct from parts would make the binding's record depend on a
    grammar implementation, which would be the second citation authority over Markdown that
    ``KS-R18@v1`` forbids. The producer of a binding reads the corpus with the shipped citation
    parser and states the construct; this record preserves it byte for byte, so comparing a recorded
    key against a document is a comparison and never a parse.

    ``anchor_texts`` and ``sources`` are the parts the authoring producer observed, stored beside the
    construct so a report says *which* part moved -- a key whose extent moved and a key whose anchor
    moved are different facts a curator acts on differently -- and so the written
    ``path:start-end`` the prose names stays available on the binding as the local key's own written
    source, which is the second reading of "target reference and locator" carried rather than lost.
    """

    form: Literal["prose_cit_body"] = "prose_cit_body"
    written: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)
    anchor_texts: tuple[str, ...] = ()
    sources: tuple[WrittenSource, ...] = ()

    @model_validator(mode="after")
    def _require_the_declared_form(self) -> ProseCitationKey:
        """Refuse a prose key that does not begin with the mark the shipped grammar declares.

        The check is a *shape* check on the recorded construct, not a parse: the mark is mandatory in
        the shipped form (``memory_quality/style/citations/prose.py``'s grammar states it), so a
        recorded prose key that does not carry it is not the key it claims to be. Everything after
        the mark is preserved verbatim, because interpreting it is the citation package's job and
        this layer does not take it.
        """

        if not self.written.startswith(CIT_MARK):
            raise ValueError(
                f"a prose citation key must begin with the declared mark {CIT_MARK!r}: "
                f"{self.written!r}"
            )
        return self


class TableRowKeyForm(KnowledgeModel):
    """One written table row's anchor cell paired with its source cell, as its parts.

    A row key is the second written form the corpus uses, and this increment **does not cover** it:
    :data:`COVERED_KEY_FORMS` declares the ``cit:`` body alone, so a row-form key is reported under
    ``uncovered_key_form`` with the row preserved and the view carrying its partial-coverage
    limitation. The form is declared and recorded rather than ignored, because a form nobody declared
    is a form nobody can report as unread -- and that is how a partial coverage comes to render
    exactly like a complete one.
    """

    form: Literal["table_row_anchor_source"] = "table_row_anchor_source"
    anchor_cell: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)
    source_cell: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)
    anchor_texts: tuple[str, ...] = ()
    sources: tuple[WrittenSource, ...] = ()


LocalCitationKey = Annotated[
    ProseCitationKey | TableRowKeyForm,
    Field(discriminator="form"),
]

CitationKeyForm = Literal["prose_cit_body", "table_row_anchor_source"]

KEY_FORMS: tuple[CitationKeyForm, ...] = (
    "prose_cit_body",
    "table_row_anchor_source",
)

# The written key forms this increment reads. The prose ``cit:(...)`` body ships first; the table
# row's anchor+source pair is declared as a form the corpus writes and this increment does **not**
# cover, so a row-form key resolves to ``uncovered_key_form`` -- counted, named in the view's
# limitations, and never omitted. Recorded here as the one declared place, so the coverage the
# closure reports and the coverage it applies cannot disagree.
COVERED_KEY_FORMS: tuple[CitationKeyForm, ...] = ("prose_cit_body",)


def covered_key_form_names() -> tuple[CitationKeyForm, ...]:
    """Return the covered key forms, for a caller that needs the declared tuple rather than the type."""

    return COVERED_KEY_FORMS


def render_local_key(local_key: ProseCitationKey | TableRowKeyForm) -> str:
    """Return the exact recorded text of one local citation key, as its document wrote it.

    This is the **recorded** text, never a re-read of the document: for a prose key it is the
    construct the producer preserved verbatim, and for a table row it is the anchor cell followed by
    the source cell, because that pair *is* the key rather than a construct with a mark. Nothing is
    reconstructed from a grammar here -- the recorded text is the fact, and comparing it against what
    a document now contains is a comparison rather than a parse.
    """

    if local_key.form == "prose_cit_body":
        return local_key.written
    return f"{local_key.anchor_cell} {local_key.source_cell}"


# ---------------------------------------------------------------------------
# The target reference and its locator.


TargetReferenceState = Literal["resolved", "record_absent", "revision_absent", "kind_mismatch"]


class CitationTargetReference(KnowledgeModel):
    """The knowledge record or record revision a key denotes, in the envelope's vocabulary.

    The target is a **typed identity**: the recorded ``record_id``, the declared ``kind``, and
    optionally the exact ``revision_id``. It is never a display label, a symbol name, a file path or
    a prose string, and the locator beside it is the shipped :data:`SourceLocator` union.

    ``state`` reports what the last observation of this reference found, and it is a fact rather
    than a verdict: ``record_absent`` means the envelope holds no such record, ``revision_absent``
    means the record exists and does not hold that revision, and ``kind_mismatch`` means the
    record's recorded kind is not the kind this reference declares. None of the three deletes the
    binding or rewrites its attribution, and none is answered with a nearest-match substitution.
    """

    record_id: str = Field(
        pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
    )
    kind: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    revision_id: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    )
    locator: SourceLocator
    state: TargetReferenceState = "resolved"


# ---------------------------------------------------------------------------
# The recorded binding payload and its authoring requests.


class CitationBindingPayload(KnowledgeModel):
    """The frozen payload shape the ``citation_binding`` record kind validates against.

    It holds the three authored facts that belong to the *record*: the prose owner revision, the
    local key as written, and the target reference with its locator. The binding's own recorded
    identity, its lifecycle, its provenance and its **governing route** come from the envelope --
    ``knowledge_record`` already carries a governing-route association, and this leaf adds its own
    per-binding association table beside the binding's own table rather than altering a
    generation-1 or generation-2 table.

    ``asserted_by_ref`` names the actor the *interpretation* is attributed to. Requirement 1.5 makes
    the binding authored rather than inferred: a curator or agent states that this key denotes this
    target, and this field is where that statement's author is recorded. It is deliberately a
    separate field from the provenance envelope's ``actor_ref``: the envelope records who wrote the
    row, this records whose interpretation it is, and the two are not always the same seat.
    """

    owner_revision: ProseOwnerRevision
    local_key: LocalCitationKey
    target: CitationTargetReference
    asserted_by_ref: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)


class CitationBindingRequest(KnowledgeModel):
    """One authored binding to record: its identity, its namespace, its route and its payload."""

    repository_id: str
    binding_id: str
    payload: CitationBindingPayload
    governing_route_id: str | None = None


class CitationBindingWriteIdentity(KnowledgeModel):
    """One row a binding write really wrote, with the digest the store computed for it."""

    state: Literal["written"] = "written"
    table: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    record_id: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class CitationBindingResult(KnowledgeModel):
    """The receipt of one authored binding write."""

    state: Literal["applied", "refused"]
    repository_id: str
    written: tuple[CitationBindingWriteIdentity, ...] = ()
    refusal: object | None = None


# ---------------------------------------------------------------------------
# The observed states. One closed vocabulary, shared literals reused verbatim.


CitationBindingState = Literal[
    "exact_recorded_blob",
    "recorded_blob_mismatch",
    "recorded_object_unavailable",
    "unsupported_locator",
    "uncovered_key_form",
    "ambiguous_key",
    "target_record_absent",
    "target_revision_absent",
    "target_kind_mismatch",
]

# The same members as the ``Literal`` above, as a tuple, so a caller can iterate the closed set and
# a case can assert the two agree. Keeping both is the shipped idiom (``ANCHOR_RESOLUTIONS`` beside
# ``AnchorResolutionState``): the ``Literal`` is the type a field is validated against and the tuple
# is the value a reader enumerates.
BINDING_STATES: tuple[CitationBindingState, ...] = (
    "exact_recorded_blob",
    "recorded_blob_mismatch",
    "recorded_object_unavailable",
    "unsupported_locator",
    "uncovered_key_form",
    "ambiguous_key",
    "target_record_absent",
    "target_revision_absent",
    "target_kind_mismatch",
)

# The members of this vocabulary that are also members of the shipped ``AnchorResolutionState``.
# Requirement 4.1a makes the identity *checked* rather than asserted: ``test_knowledge_citation_*``
# asserts that each of these is in ``ANCHOR_RESOLUTIONS`` and that the shipped anchor resolver
# produces the same literal for the same fact. The extension is one-directional -- no shipped
# vocabulary member is added by this leaf -- so an anchor resolution can never acquire a citation
# fact.
SHIPPED_BINDING_STATES: tuple[CitationBindingState, ...] = (
    "exact_recorded_blob",
    "recorded_blob_mismatch",
    "recorded_object_unavailable",
    "unsupported_locator",
)

# The observable fact each shared member reports, as prose a case can check the mapping against.
# This is a *declaration* of the correspondence, not a second vocabulary: the value on the right is
# the shipped literal itself.
SHIPPED_STATE_FACTS: Mapping[CitationBindingState, str] = {
    "exact_recorded_blob": "the key is present in its owner revision and the binding resolves",
    "recorded_blob_mismatch": "the key is not recorded in its owner revision",
    "recorded_object_unavailable": "the owner revision's bytes cannot be obtained",
    "unsupported_locator": "the locator is one this increment cannot resolve",
}


def shipped_literal_for(fact: str) -> CitationBindingState | None:
    """Return the binding state naming one shared fact, so a mapping cannot be spelled twice.

    A caller that needs "which state reports fact X" asks here rather than repeating the literal,
    and the answer is the vocabulary's own member -- which for the four shared facts *is* the
    shipped ``AnchorResolutionState`` literal.
    """

    for state, declared in SHIPPED_STATE_FACTS.items():
        if declared == fact:
            return state
    return None


# ---------------------------------------------------------------------------
# The closure of a selected prose view.


CitationBindingLimitation = Literal[
    "unresolved_keys_present",
    "stale_bindings_present",
    "partial_key_form_coverage",
    "no_semantic_completeness_claim",
]

# The one limitation every closure states unconditionally. Requirement 2.3 forbids a
# semantic-completeness field: the response exposes truncation or unresolved inputs rather than
# presenting itself as complete, and the statement that no semantic completeness is claimed is
# declared rather than left to be inferred from a missing field.
NO_SEMANTIC_COMPLETENESS_LIMITATION: CitationBindingLimitation = "no_semantic_completeness_claim"


class KeyFormCoverage(KnowledgeModel):
    """Which written key forms one observation read, and what it could therefore not conclude.

    ``uncovered_counts`` is populated for **every** form this increment does not cover, including
    the ones that count zero, so a form that was never read is visible as unread rather than as
    absent, and a form that happens to have no members today cannot silently become invisible
    tomorrow.
    """

    covered_forms: tuple[CitationKeyForm, ...]
    uncovered_counts: Mapping[str, int] = {}

    @model_validator(mode="after")
    def _require_every_uncovered_form_counted(self) -> KeyFormCoverage:
        uncovered = tuple(form for form in KEY_FORMS if form not in self.covered_forms)
        missing = tuple(form for form in uncovered if form not in self.uncovered_counts)
        if missing:
            raise ValueError(
                "a key-form coverage declaration names a count for every form it does not cover, "
                f"so an unread form is visible as unread rather than absent; missing {missing}"
            )
        return self

    def partial(self) -> bool:
        """Return whether this coverage is partial -- some declared form was not read."""

        return any(form not in self.covered_forms for form in KEY_FORMS)


class CitationBindingObservation(KnowledgeModel):
    """One recorded binding as observed: its five recorded facts and exactly one reported state.

    The recorded key is preserved on **every** state, including the failures, which is the shipped
    rule for a stored attribution (``memory/knowledge/anchors.py``: a missing source is never a
    reason to retire one). ``state`` is exactly one member of the closed vocabulary above.
    """

    binding_id: str
    owner_revision: ProseOwnerRevision
    local_key: LocalCitationKey
    target: CitationTargetReference
    governing_route_id: str | None = None
    asserted_by_ref: str
    state: CitationBindingState
    detail: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)


class CitationBindingItem(KnowledgeModel):
    """One indivisible closure item: a binding, its target closure, and nothing else.

    Indivisible is the point: a statement and the reference closure required to read it are never
    truncated apart (``design/retrieval-review-design.md:152``). The item carries the target
    record's **provenance and lifecycle** as well as its identity, because a page that shows the
    record without its attribution launders it.
    """

    observation: CitationBindingObservation
    target_kind: str | None = None
    target_lifecycle: str | None = None
    target_authority_home: str | None = None
    target_record_schema: str | None = None


class CitationBindingCounts(KnowledgeModel):
    """The counts one closure reports, over its **declared selected set**.

    Every state in the closed vocabulary has a member here, including the states that total zero
    today (requirement 4.1b): a state that cannot be populated is still declared rather than absent
    from the vocabulary, so a reader can tell "no binding is in this state" from "this state does
    not exist". ``selected`` is the declared set, ``returned`` is what this response carries, and
    ``remaining`` is what a continuation would carry; all three travel so a one-item page cannot be
    read as a one-item scope.
    """

    selected: int = Field(ge=0)
    returned: int = Field(ge=0)
    remaining: int = Field(ge=0)
    resolved: int = Field(ge=0)
    unresolved: int = Field(ge=0)
    stale: int = Field(ge=0)
    by_state: Mapping[str, int] = {}

    @model_validator(mode="after")
    def _require_every_declared_state_counted(self) -> CitationBindingCounts:
        missing = tuple(state for state in BINDING_STATES if state not in self.by_state)
        if missing:
            raise ValueError(
                "the aggregate counts declare every member of the closed state vocabulary, "
                "including the ones that total zero, so a state that cannot be populated today is "
                f"still declared rather than absent; missing {missing}"
            )
        total = sum(self.by_state[state] for state in BINDING_STATES)
        if total != self.selected:
            raise ValueError(
                "the per-state counts partition the declared selected set exactly: a key that "
                f"resolves to nothing is counted rather than dropped; counted {total}, "
                f"selected {self.selected}"
            )
        return self


class CitationBindingClosureRequest(KnowledgeModel):
    """One selected prose view's closure request, and the bound it is assembled under.

    ``selected_owner_revisions`` is the view's own **declared selected set**: the exact prose owner
    revisions it covers. The closure is assembled from recorded bindings whose owner revision is in
    that set, so counts are over the declared set rather than over all history or presumed actual
    prose.
    """

    repository_id: str
    selected_owner_revisions: tuple[ProseOwnerRevision, ...] = ()
    item_limit: int = Field(default=CITATION_SELECTION_ITEM_LIMIT, ge=1)


class CitationBindingClosureResult(KnowledgeModel):
    """One closure: the items, the counts, the declared coverage and the limitations.

    There is deliberately **no** semantic-completeness field. ``limitations`` carries the unresolved
    and stale keys and the partial-coverage declaration, and they stay visible even when the closure
    is enumerated completely.
    """

    state: Literal["assembled", "refused"]
    repository_id: str
    policy_version: str = CITATION_BINDING_POLICY_VERSION
    items: tuple[CitationBindingItem, ...] = ()
    counts: CitationBindingCounts | None = None
    key_form_coverage: KeyFormCoverage | None = None
    limitations: tuple[CitationBindingLimitation, ...] = ()
    refusal: object | None = None


# ---------------------------------------------------------------------------
# Ambiguity: the state that makes "author a disambiguation" possible.


class AmbiguousKey(KnowledgeModel):
    """More than one recorded binding claims one key in one owner revision.

    An arbitrary winner is a fabricated claim about which record the prose meant, so the ambiguity
    is reported and authored disambiguation is required. Nothing here picks one.
    """

    owner_revision: ProseOwnerRevision
    key_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    binding_ids: tuple[str, ...]
