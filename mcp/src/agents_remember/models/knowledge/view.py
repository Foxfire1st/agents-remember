"""The five query views: their names, their typed payloads, their counts and their continuation.

``Doc13:233`` states the rule this module exists to make structural: the product's views are
"deterministic selections and renderings of stored knowledge, observed changes, and existing
assessments -- not new model-authored answers". Two consequences follow, and both are enforced here
rather than documented:

* **A view returns recorded facts and their provenance classes, and nothing it computed.** Every row
  that orders or qualifies a value carries a :class:`~agents_remember.models.knowledge.classification.Provenance`
  as a *sibling* field of that value (requirement 2.6), so a consumer can tell an authored finding
  from a mechanical observation without knowing which renderer produced the payload. There is no
  field anywhere below for a score, a rank, a weight, a severity, a percentage, a summary or a
  conclusion -- so a view cannot acquire one by accident.
* **A view reads through a port and never through a database.** :class:`KnowledgeViewReader` is the
  only way this vocabulary obtains a row. A view module that opened a connection, or that read the
  candidate tree to reconstruct what a record says, would not typecheck against this interface at
  all (requirement 1.5).

**The five names are the contract.** :data:`VIEW_NAMES` is ``Doc13:235-239``'s list, spelled the
same and in the same order: source context, invariant, family, review matrix, curation queue.
There is no synonym, no sixth view, and no view assembled at a caller's convenience; the name a
caller passes is one of these five and the payload's own ``view`` field says which one it got.

**Bounded responses say what they are bounded by.** :class:`ViewCompleteness` is scoped to the four
inputs ``Doc13:227`` names -- the snapshot, the recorded graph, the traversal policy and the
supported extractors -- and its field is spelled ``complete_within_declared_scope`` so that no
reader can take it for a statement about the project's actual semantics. A payload that returned a
first page without a continuation would fail :class:`ViewPayload`'s own validator, which is the
structural form of "a bounded response never presents its first page as the entire registered
scope".
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Annotated, Literal, Protocol, get_args, runtime_checkable

from pydantic import Field, model_validator

from agents_remember.models.knowledge.base import (
    LABEL_MAX_LENGTH,
    PROSE_MAX_LENGTH,
    REFERENCE_MAX_LENGTH,
    SHA256_PATTERN,
    KnowledgeModel,
)
from agents_remember.models.knowledge.classification import (
    AUTHORED_CLASS,
    MECHANICAL_RULES,
    ORDERING_INPUTS,
    OrderingInput,
    Provenance,
    mechanical_rule,
)
from agents_remember.models.knowledge.graph import RealizationRole
from agents_remember.models.knowledge.read import AnchorResolutionState, KnowledgeReadSnapshot

__all__ = [
    "COUNTED",
    "COUNT_QUANTITIES",
    "MAX_VIEW_ROWS",
    "NOT_APPLICABLE",
    "ORDERING_PROVENANCE_RULE",
    "VIEW_NAMES",
    "VIEW_PAYLOADS",
    "VIEW_PURPOSES",
    "CountQuantity",
    "CountState",
    "CurationQueueRow",
    "CurationQueueView",
    "CuratorDisposition",
    "FamilyRow",
    "FamilyView",
    "InvariantRow",
    "InvariantView",
    "KnowledgeViewReader",
    "MachineWorkItem",
    "NoConsequenceStatement",
    "OrderedPosition",
    "ReviewMatrixRow",
    "ReviewMatrixView",
    "SourceContextRow",
    "SourceContextView",
    "SubjectRef",
    "UnresolvedLimitation",
    "ViewCompleteness",
    "ViewContinuation",
    "ViewCounts",
    "ViewPayload",
    "ViewPayloadUnion",
    "ViewReaderError",
    "ViewRefusal",
    "ViewRefusalCode",
    "ViewRequest",
    "ViewResult",
    "ViewScope",
    "ViewSourceCounts",
    "ViewSourceRow",
    "continuation_for",
    "continuation_position",
    "ordering_position",
    "rebuild_continuation",
    "require_admitted_ordering_input",
    "require_continuation_snapshot",
    "require_distinct_row_subjects",
]

# ---------------------------------------------------------------------------
# The five names, in ``Doc13``'s order.
# ---------------------------------------------------------------------------

ViewName = Literal["source_context", "invariant", "family", "review_matrix", "curation_queue"]
# ``VIEW_NAMES`` is the five names themselves, not five arbitrary strings: a caller that iterates it
# is iterating admitted view names, and a caller holding a plain ``str`` must still satisfy the
# request model's own check. Declaring the element type as the literal union says that once, instead
# of leaving every consumer to re-narrow what ``get_args`` erased.
VIEW_NAMES: tuple[ViewName, ...] = get_args(ViewName)

# One phrase per view, for the published interface ``KS-R22@v1`` mounts. These describe what the
# view selects; they are not a second content list and they carry no ordering claim.
VIEW_PURPOSES: Mapping[str, str] = {
    "source_context": (
        "A compact registered neighborhood: authored local responsibility, accepted invariant "
        "statements, registered realization roles, other linked source locations and selected "
        "diagnostic records, with claim provenance and missing assessments exposed."
    ),
    "invariant": (
        "One invariant's full authored statement and conditions, its recorded families and "
        "realizations, and the evidence, observations, decisions, contradictions and lineage "
        "recorded against it."
    ),
    "family": (
        "One family's authored joint guarantee, recorded members and composition links, the source "
        "changes joined to those links, member-record changes, detection signals and curator "
        "assessments -- keeping a member's record changing distinct from its attributed source "
        "changing."
    ),
    "review_matrix": (
        "Requirement records, proposed invariant effects and independent assessments, "
        "changed/unchanged source selected by registered links, authored preservation claims, "
        "evidence observations and unresolved findings -- each ordered value carrying its "
        "authored-or-mechanical provenance class."
    ),
    "curation_queue": (
        "Machine-selected work items with their condition codes and matched facts, mapping and "
        "scan limitations, and separately attributed curator dispositions."
    ),
}

# The one rule that orders the positions a view assigns when every declared key ties. It is a
# registered, versioned rule (``stable_ordering``, the third admitted ordering input) and it is
# reported ``mechanical`` wherever it is used -- which is what requirement 2.4's permitted
# alphabetical tiebreak means: permitted only as this declared ordering, and only when everything
# declared has tied.
ORDERING_PROVENANCE_RULE = ("ordering.declared-tiebreak", 1)


class ViewReaderError(RuntimeError):
    """A read that the reader port could not answer, raised rather than absorbed.

    It exists so a view cannot convert an unavailable read into an empty row set: an empty result
    and an unreadable input are different facts, and rendering the second as the first is how a
    truncated view comes to look like a complete one.
    """


ViewRefusalCode = Literal[
    "unknown_view",
    "unadmitted_ordering_input",
    "continuation_unreadable",
    "continuation_binding_mismatch",
    "snapshot_unavailable",
    "unclassified_value",
    "ambiguous_row_identity",
]


class ViewRefusal(KnowledgeModel):
    """One typed view refusal, naming the offending input and the concrete next action.

    A refusal is a state, never a degraded success: a caller that receives one has no rows, and
    must not be able to read the absence of rows as "the selection was empty".
    """

    code: ViewRefusalCode
    view: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    detail: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)
    offending_input: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
    expected: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
    observed: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
    next_action: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)


# ---------------------------------------------------------------------------
# Counts.
# ---------------------------------------------------------------------------

CountState = Literal["counted", "not_applicable"]
COUNTED: CountState = "counted"
NOT_APPLICABLE: CountState = "not_applicable"

# The quantity set ``Doc13:220-224`` requires a bounded response to expose. The names are the
# vocabulary's; ``COUNT_QUANTITIES`` is what the completeness scan reads, so a view cannot satisfy
# "every quantity is present" by omitting one field.
COUNT_QUANTITIES: tuple[str, ...] = (
    "registered_realizations",
    "registered_families",
    "rows_returned",
    "rows_remaining",
    "facets_omitted",
    "unmapped_changed_paths",
    "ambiguous_references",
    "unresolved_references",
    "dependency_content_identity_mismatches",
)


class CountQuantity(KnowledgeModel):
    """One named quantity, either counted or explicitly without meaning for this view.

    ``not_applicable`` is a *stated* state carrying its reason, which is the whole point: a view
    that has no meaning for a quantity says so, and a reader never has to guess whether a zero
    means "none" or "not measured here". ``value`` is ``None`` exactly when the state is
    ``not_applicable``, so a zero can never be confused with an absent measurement.
    """

    state: CountState
    value: int | None = Field(default=None, ge=0)
    reason: str | None = Field(default=None, max_length=PROSE_MAX_LENGTH)

    @model_validator(mode="after")
    def _require_a_stated_state(self) -> CountQuantity:
        if self.state == COUNTED:
            if self.value is None:
                raise ValueError("a counted quantity carries its value; a count is not optional")
            if self.reason is not None:
                raise ValueError("a counted quantity carries no not-applicable reason")
        else:
            if self.value is not None:
                raise ValueError(
                    "a quantity without meaning for this view carries no value; reporting a zero "
                    "here would read as a measurement"
                )
            if self.reason is None:
                raise ValueError(
                    "a quantity without meaning for this view states why, so omission is never "
                    "silent"
                )
        return self


class ViewCounts(KnowledgeModel):
    """Every required quantity, present on every view, with its measurement or its stated absence.

    The named quantities of ``Doc13:220-224`` map onto these fields one for one, except the
    continuation: a continuation is not a number, so the quantity "continuation bound to the same
    snapshot" is expressed by ``rows_remaining`` together with the payload's own ``continuation``
    field, whose validator requires the two to agree.
    """

    registered_realizations: CountQuantity
    registered_families: CountQuantity
    rows_returned: CountQuantity
    rows_remaining: CountQuantity
    facets_omitted: CountQuantity
    unmapped_changed_paths: CountQuantity
    ambiguous_references: CountQuantity
    unresolved_references: CountQuantity
    dependency_content_identity_mismatches: CountQuantity


def _counted(value: int) -> CountQuantity:
    return CountQuantity(state=COUNTED, value=value)


def _not_applicable(reason: str) -> CountQuantity:
    return CountQuantity(state=NOT_APPLICABLE, reason=reason)


def view_counts(  # noqa: PLR0913  -- one keyword per required quantity, each named for the
    # quantity it measures; a container here would hide which quantity a caller left out.
    *,
    registered_realizations: int | None,
    registered_families: int | None,
    rows_returned: int,
    rows_remaining: int,
    facets_omitted: int | None = None,
    unmapped_changed_paths: int | None = None,
    ambiguous_references: int | None = None,
    unresolved_references: int | None = None,
    dependency_content_identity_mismatches: int | None = None,
) -> ViewCounts:
    """Build the quantity set, turning ``None`` into an explicit stated absence.

    A caller therefore cannot leave a quantity out: it either has a measurement or it has a reason
    named here. The default reasons are per quantity and say what the view is, not that something
    went wrong.
    """

    return ViewCounts(
        registered_realizations=(
            _not_applicable("this view is not scoped to a registered realization set")
            if registered_realizations is None
            else _counted(registered_realizations)
        ),
        registered_families=(
            _not_applicable("this view is not scoped to a registered family set")
            if registered_families is None
            else _counted(registered_families)
        ),
        rows_returned=_counted(rows_returned),
        rows_remaining=_counted(rows_remaining),
        facets_omitted=(
            _not_applicable("this view returns no facet rows")
            if facets_omitted is None
            else _counted(facets_omitted)
        ),
        unmapped_changed_paths=(
            _not_applicable("this view compares no two exact states")
            if unmapped_changed_paths is None
            else _counted(unmapped_changed_paths)
        ),
        ambiguous_references=(
            _not_applicable("this view resolves no cited references")
            if ambiguous_references is None
            else _counted(ambiguous_references)
        ),
        unresolved_references=(
            _not_applicable("this view resolves no cited references")
            if unresolved_references is None
            else _counted(unresolved_references)
        ),
        dependency_content_identity_mismatches=(
            _not_applicable("this view reads no recorded dependency bytes")
            if dependency_content_identity_mismatches is None
            else _counted(dependency_content_identity_mismatches)
        ),
    )


# ---------------------------------------------------------------------------
# Scope, completeness, continuation.
# ---------------------------------------------------------------------------


class ViewScope(KnowledgeModel):
    """The four inputs a completeness statement is scoped to, named individually.

    ``Doc13:227``: "Code may report completion only relative to the specified snapshot, recorded
    graph, traversal policy, and supported extractors." Naming them as four fields means a caller
    can see exactly which inputs the statement was made about, and a changed extractor set makes two
    otherwise identical payloads distinguishable.
    """

    snapshot_logical_digest: str = Field(pattern=SHA256_PATTERN)
    recorded_graph: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    traversal_policy: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    extractors: tuple[str, ...] = ()


class ViewCompleteness(KnowledgeModel):
    """The completeness statement, scoped and spelled so it cannot claim project semantics.

    The field is ``complete_within_declared_scope`` and not ``complete`` on purpose. "Complete"
    alone invites exactly the reading ``Doc13:227`` forbids -- a claim about the project's actual
    semantics -- so the name carries its own scope and :class:`ViewScope` says what that scope was.
    """

    complete_within_declared_scope: bool
    scope: ViewScope


class ViewContinuation(KnowledgeModel):
    """An opaque continuation, bound to the one snapshot it was minted at and to its view.

    The binding is the point. A token is not a position in "the database at this path"; it is a
    position in one named snapshot of one selected set, so presenting it against another snapshot is
    refused rather than silently re-resolved (requirement 3.1, and ``Doc13:224``'s "continuation
    bound to the same snapshot").
    """

    token: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)
    view: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    snapshot_logical_digest: str = Field(pattern=SHA256_PATTERN)


def continuation_for(
    *, view: str, snapshot: KnowledgeReadSnapshot, position: int
) -> ViewContinuation:
    """Mint one continuation for one snapshot and one walk position."""

    return ViewContinuation(
        token=f"{view}:{snapshot.logical_digest}:{position}",
        view=view,
        snapshot_logical_digest=snapshot.logical_digest,
    )


# The one encoding :func:`continuation_for` mints: the view, the snapshot digest the walk is bound
# to, and the selection position, separated by a character none of the three contains. The codec
# below is its exact inverse, and the exactness is the point -- the page a caller receives must be a
# function of the snapshot and the cursor, so a cursor that does not read back to the fields it
# claims is refused rather than reinterpreted as position zero.
CONTINUATION_TOKEN_SEPARATOR = ":"
CONTINUATION_TOKEN_FIELDS = 3
CONTINUATION_DIGEST_LENGTH = 64
# No selection this store can hold reaches a twelve-digit position, so a longer decimal spelling is
# not one of this module's tokens; bounding the read here keeps it from being converted at all.
CONTINUATION_POSITION_DIGITS = 12
CONTINUATION_TOKEN_MAX_LENGTH = (
    LABEL_MAX_LENGTH + CONTINUATION_DIGEST_LENGTH + CONTINUATION_POSITION_DIGITS + 2
)
# The digest spelling the codec reads back, compiled from the vocabulary's own pattern so the token a
# view accepts and the field a model validates cannot drift apart.
SHA256_SPELLING = re.compile(SHA256_PATTERN)


def _minted_token_fields(token: str) -> tuple[str, str, int] | None:
    """The three fields one minted token encodes, or ``None`` when the text is not one.

    Every field is measured against the spelling :func:`continuation_for` writes -- one of the five
    view names, one snapshot digest, one canonical decimal position -- so text that fails the measure
    is text this module did not mint.
    """

    if len(token) > CONTINUATION_TOKEN_MAX_LENGTH:
        return None
    parts = token.split(CONTINUATION_TOKEN_SEPARATOR)
    if len(parts) != CONTINUATION_TOKEN_FIELDS:
        return None
    view, digest, position = parts
    if view not in VIEW_NAMES or SHA256_SPELLING.fullmatch(digest) is None:
        return None
    if not position.isdigit() or len(position) > CONTINUATION_POSITION_DIGITS:
        return None
    spelled = int(position)
    if position != str(spelled):
        return None
    return view, digest, spelled


def _bounded_spelling(text: str) -> str:
    """One spelling a refusal field can carry, since a caller may present text longer than one.

    The refusal names the input it refused instead of failing to be constructed about it: a
    continuation token arrives from a caller and is not bounded by the field a refusal quotes it in.
    """

    if len(text) <= REFERENCE_MAX_LENGTH:
        return text
    return f"{text[: REFERENCE_MAX_LENGTH - 1]}…"


def _unreadable_continuation(
    *, view: str, token: str, detail: str, expected: str, observed: str
) -> ViewRefusal:
    """The refusal a continuation token this module did not mint for this view earns."""

    return ViewRefusal(
        code="continuation_unreadable",
        view=view,
        detail=detail,
        offending_input=_bounded_spelling(token),
        expected=_bounded_spelling(expected),
        observed=_bounded_spelling(observed),
        next_action=(
            "present the token a previous page of this view returned, or read the first page without "
            "a continuation"
        ),
    )


def _minted_binding(token: str, *, view: str) -> tuple[str, str, int] | ViewRefusal:
    """The three fields one token encodes for one view, or the refusal the text earns.

    The minted shape is checked first and the view's ownership second, so a caller hears which of the
    two disagreed -- text that is not a token at all, or a token that belongs to another walk.
    """

    fields = _minted_token_fields(token)
    if fields is None:
        return _unreadable_continuation(
            view=view,
            token=token,
            detail=(
                "the continuation is not a token this substrate minted: a token is one view name, "
                "one snapshot digest and one position, and this text reads back as none of them, so "
                "the walk is not restarted from its first page"
            ),
            expected=f"{view}{CONTINUATION_TOKEN_SEPARATOR}<snapshot digest>"
            f"{CONTINUATION_TOKEN_SEPARATOR}<position>",
            observed=f"{token.count(CONTINUATION_TOKEN_SEPARATOR) + 1} fields",
        )
    minted_view, digest, position = fields
    if minted_view != view:
        return _unreadable_continuation(
            view=view,
            token=token,
            detail=(
                "the continuation was minted for another view's walk; a position in one selection "
                "is not a position in another, and this view returns no page rather than a slice of "
                "its own selection taken at a stranger's cursor"
            ),
            expected=view,
            observed=minted_view,
        )
    return minted_view, digest, position


def rebuild_continuation(token: str, *, view: str) -> ViewContinuation | ViewRefusal:
    """Rebuild the continuation one minted token encodes for one view, or refuse the text.

    The binding travels *inside* the token, which is what lets a transport carry it as one opaque
    string: a mounted tool knows which view a caller asked for but not which snapshot it is reading,
    and it rebuilds the continuation here so the seam compares the snapshot the token was actually
    minted at instead of one the transport invented. Text this module did not mint, and text minted
    for another view's walk, rebuilds nothing and is refused.
    """

    binding = _minted_binding(token, view=view)
    if isinstance(binding, ViewRefusal):
        return binding
    minted_view, digest, _position = binding
    return ViewContinuation(token=token, view=minted_view, snapshot_logical_digest=digest)


def continuation_position(continuation: ViewContinuation, *, view: str) -> int | ViewRefusal:
    """The selection position one continuation round-trips to for one view, or the refusal it earns.

    A token this module did not mint, a token minted for another view, and a token whose own
    recorded view or snapshot disagrees with the binding it encodes are each a refusal. None of them
    is position zero: reading an unreadable cursor as the start of the walk is how a caller receives
    a page it already received -- and a caller that pages until exhausted never leaves it.
    """

    binding = _minted_binding(continuation.token, view=view)
    if isinstance(binding, ViewRefusal):
        return binding
    minted_view, digest, position = binding
    if minted_view != continuation.view or digest != continuation.snapshot_logical_digest:
        return _unreadable_continuation(
            view=view,
            token=continuation.token,
            detail=(
                "the continuation's recorded binding disagrees with the binding its own token "
                "encodes, so it is not a value this substrate minted and no page is returned"
            ),
            expected=f"{minted_view}{CONTINUATION_TOKEN_SEPARATOR}{digest}",
            observed=f"{continuation.view}{CONTINUATION_TOKEN_SEPARATOR}"
            f"{continuation.snapshot_logical_digest}",
        )
    return position


def require_continuation_snapshot(
    continuation: ViewContinuation, snapshot: KnowledgeReadSnapshot
) -> ViewRefusal | None:
    """Refuse a continuation presented against a snapshot other than the one that minted it.

    Both identities are named in the refusal, because a caller that cannot see which two snapshots
    disagreed cannot fix the call.
    """

    if continuation.snapshot_logical_digest == snapshot.logical_digest:
        return None
    return ViewRefusal(
        code="continuation_binding_mismatch",
        view=continuation.view,
        detail=(
            "the continuation was minted at one snapshot and presented against another; the view "
            "does not re-resolve it and returns no page"
        ),
        offending_input=continuation.view,
        expected=continuation.snapshot_logical_digest,
        observed=snapshot.logical_digest,
        next_action="re-read the scope at the snapshot the continuation names, or start a new walk",
    )


class UnresolvedLimitation(KnowledgeModel):
    """One input the view could not resolve, reported instead of guessed.

    This is where requirement 2.1's "a value whose classification cannot be determined is not
    returned with an empty classification" lands: the value is not emitted at all, and this record
    says which value it was and why. It carries no class, no severity and no ranking -- a limitation
    is a fact about the read, and giving it one of those would be the semantic conclusion this leaf
    forbids.
    """

    code: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    detail: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)
    subject: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)


# ---------------------------------------------------------------------------
# Rows.
# ---------------------------------------------------------------------------


class SubjectRef(KnowledgeModel):
    """The recorded record a row is about, by the identity the store holds."""

    record_kind: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    record_id: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    revision_id: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)


class OrderedPosition(KnowledgeModel):
    """One ordered position and the provenance of *the position*, not of the row's content.

    ``ordering_input`` names which of the four admitted inputs produced the order (requirement 2.5),
    and ``provenance`` says whether that input is an authored value or a mechanical rule. The two
    are separate on purpose: a curator's declared priority (authored) and the declared tiebreak
    (mechanical) can order the same row list, and Example 1 of the packet shows both in one payload.
    """

    position: int = Field(ge=1)
    ordering_input: OrderingInput
    provenance: Provenance
    # The rule that actually produced this position. For a mechanical ordering it is the rule
    # named by ``provenance``; for an authored ordering it is the declared tiebreak that placed the
    # row among the authored positions.
    tiebreak_rule_id: str | None = Field(default=None, max_length=LABEL_MAX_LENGTH)
    tiebreak_rule_version: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _require_the_declared_tiebreak(self) -> OrderedPosition:
        """Every position names the declared tiebreak rule, and it must be the registered one.

        A position that named no tiebreak could not be reproduced, and one that named an
        unregistered rule would be exactly the inline comparator requirement 2.2 forbids.
        """

        if self.tiebreak_rule_id is None or self.tiebreak_rule_version is None:
            raise ValueError(
                "an ordered position names the declared tiebreak rule that placed it, so two runs "
                "at one snapshot can be compared"
            )
        mechanical_rule(self.tiebreak_rule_id, self.tiebreak_rule_version)
        return self


def ordering_position(
    position: int,
    ordering_input: OrderingInput,
    provenance: Provenance,
) -> OrderedPosition:
    """Build one position against the registered declared tiebreak, checking the input is admitted."""

    require_admitted_ordering_input(ordering_input)
    rule_id, rule_version = ORDERING_PROVENANCE_RULE
    return OrderedPosition(
        position=position,
        ordering_input=ordering_input,
        provenance=provenance,
        tiebreak_rule_id=rule_id,
        tiebreak_rule_version=rule_version,
    )


def require_admitted_ordering_input(ordering_input: str) -> ViewRefusal | None:
    """Refuse an ordering request that names none of the four admitted inputs.

    Requirement 2.5 is a closure: "An ordering that cannot name which of the four produced it is not
    admitted." The refusal names the request and lists the admitted set, and the caller receives no
    rows -- there is no fallback order and no database order dressed up as a declared one.
    """

    if ordering_input in ORDERING_INPUTS:
        return None
    return ViewRefusal(
        code="unadmitted_ordering_input",
        view="*",
        detail=(
            "an ordering was requested whose input is none of the four admitted ordering inputs; "
            "the view does not fall back to a default order"
        ),
        offending_input=ordering_input,
        expected=", ".join(ORDERING_INPUTS),
        observed=ordering_input,
        next_action=(
            "name a declared priority, a registered role, the declared stable ordering or an "
            "explicit trigger rule"
        ),
    )


class NoConsequenceStatement(KnowledgeModel):
    """A "no consequence" claim with the class that produced it -- requirement 2.3, at the payload.

    The two branches are distinguishable at a glance, which is the whole obligation: a mechanical
    determination names the registered rule and version that established it and carries **no**
    author; an authored determination names the stored claim, its author and its rationale and cites
    **no** rule. ``Provenance`` refuses any mixture, so "no consequence" can never be shown as
    though the classification were self-evident.
    """

    subject: SubjectRef
    detail: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)
    provenance: Provenance
    # The stored authored record this statement is, when it is authored. It is the record path
    # requirement 2.3 requires, and it is absent exactly when the statement is mechanical.
    claim_ref: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)

    @model_validator(mode="after")
    def _require_the_claim_only_when_authored(self) -> NoConsequenceStatement:
        if (self.claim_ref is not None) != (self.provenance.provenance_class == AUTHORED_CLASS):
            raise ValueError(
                "an authored no-consequence statement names the stored claim it is, and a "
                "mechanical determination writes no authored record to name"
            )
        return self


def require_distinct_row_subjects(rows: Sequence[SubjectRef]) -> ViewRefusal | None:
    """Refuse a row set in which one recorded subject appears at two positions.

    Two rows for one subject is not a richer view; it is an ordering that cannot be read, and it is
    the shape a renderer produces when it concatenates two selections instead of joining them. The
    refusal names the subject rather than silently keeping the first, because which of the two rows
    is "the" row would be a choice the view made.
    """

    seen: set[tuple[str, str, str | None]] = set()
    for subject in rows:
        key = (subject.record_kind, subject.record_id, subject.revision_id)
        if key in seen:
            return ViewRefusal(
                code="ambiguous_row_identity",
                view="*",
                detail=(
                    "one recorded subject appears at two positions in a single view; the view "
                    "refuses rather than choosing which row is authoritative"
                ),
                offending_input=f"{subject.record_kind}:{subject.record_id}",
                next_action="narrow the selection so each recorded subject yields one row",
            )
        seen.add(key)
    return None


# --- per-view rows ---------------------------------------------------------


class SourceContextRow(KnowledgeModel):
    """One recorded fact in a compact registered neighborhood, with its order and its class."""

    subject: SubjectRef
    fact_kind: Literal[
        "authored_responsibility",
        "accepted_invariant_statement",
        "registered_realization",
        "linked_location",
        "diagnostic_evidence",
    ]
    statement: str | None = Field(default=None, max_length=PROSE_MAX_LENGTH)
    role: RealizationRole | None = None
    path: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
    anchor_state: AnchorResolutionState | None = None
    # "expose claim provenance and missing assessments": a fact whose assessment is absent says so
    # here, and the absence is never filled with a favourable default.
    assessment_state: Literal["assessed", "missing", "not_applicable"] = "not_applicable"
    order: OrderedPosition
    provenance: Provenance


class InvariantRow(KnowledgeModel):
    """One recorded fact about an invariant: its statement, its conditions, or a related record."""

    subject: SubjectRef
    fact_kind: Literal[
        "statement",
        "essential_conditions",
        "recorded_family",
        "realization",
        "evidence_claim",
        "execution_observation",
        "decision",
        "curator_contradiction",
        "lineage",
    ]
    statement: str | None = Field(default=None, max_length=PROSE_MAX_LENGTH)
    essential_conditions: tuple[str, ...] = ()
    # Requirement 4.8: a compact view may shorten prose it is licensed to shorten, but it may not
    # drop the conditions under which the invariant applies. When a view was too narrow to carry
    # them the row reports the omission rather than emitting a bare statement.
    conditions_omitted: bool = False
    lifecycle: str | None = Field(default=None, max_length=LABEL_MAX_LENGTH)
    acceptance_ref: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
    order: OrderedPosition
    provenance: Provenance


class FamilyRow(KnowledgeModel):
    """One recorded fact about a family, with the change locus kept explicit.

    ``change_locus`` is requirement 1.2's distinction made into a field: a member's *record*
    changing and its *attributed source* changing are different facts about a family, and a view
    that merged them would report a source edit as a knowledge change or the reverse.
    """

    subject: SubjectRef
    fact_kind: Literal[
        "joint_guarantee",
        "member",
        "composition_link",
        "source_change",
        "member_record_change",
        "detection_signal",
        "curator_assessment",
    ]
    statement: str | None = Field(default=None, max_length=PROSE_MAX_LENGTH)
    change_locus: Literal["member_record", "attributed_source", "both", "neither"] = "neither"
    lifecycle: str | None = Field(default=None, max_length=LABEL_MAX_LENGTH)
    order: OrderedPosition
    provenance: Provenance


class MachineWorkItem(KnowledgeModel):
    """A machine-selected work item: what the detector matched, and nothing a person judged.

    There is deliberately no field on this model for a disposition, a rationale or an author. The
    packet's requirement 1.6 is a shape obligation, not a prose one: "There is no field on a work
    item that holds a curator's judgment."
    """

    work_item_id: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    condition_code: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    condition_vocabulary_version: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    matched_facts: tuple[str, ...] = ()
    registered_scope_status: str | None = Field(default=None, max_length=LABEL_MAX_LENGTH)


class CuratorDisposition(KnowledgeModel):
    """A curator's own authored disposition, separately attributed.

    And symmetrically: there is no field here for a condition code, a matched fact or a detection
    identity. A disposition that needed one would be the detector's row wearing a curator's name.
    """

    disposition_id: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    disposition: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    rationale: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)
    author_ref: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    state_at_origin: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    provenance: Provenance


class CurationQueueRow(KnowledgeModel):
    """One work item with, at most, the separately attributed disposition recorded against it."""

    item: MachineWorkItem
    disposition: CuratorDisposition | None = None
    limitations: tuple[str, ...] = ()
    order: OrderedPosition


class ReviewMatrixRow(KnowledgeModel):
    """One review-matrix row: the recorded facts requirement 1.2 names, plus the classification.

    The row deliberately holds references rather than re-rendered content for the records other
    leaves own, because a view that restated a requirement revision or an effect claim would become
    a second renderer of it. What the row owns is the *selection*, the *order* and the
    *classification*, and those are the three things this leaf exists to settle.
    """

    subject: SubjectRef
    requirement_record_id: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
    requirement_revision_id: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
    proposed_effect_claim_id: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
    preservation_claim_id: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
    evidence_claim_ids: tuple[str, ...] = ()
    record_ids: tuple[str, ...] = ()
    # The assessment as recorded, with its stored status preserved -- never a status this view
    # computed, upgraded or summarised (requirement 4.5).
    assessment_ids: tuple[str, ...] = ()
    assessment_status: Literal["assessed", "missing", "stale"] = "missing"
    # Changed/unchanged source selected by the *registered links*, not by a path comparison.
    source_change_state: Literal["changed", "unchanged", "not_selected"] = "not_selected"
    unresolved_findings: tuple[str, ...] = ()
    consequence: NoConsequenceStatement | None = None
    order: OrderedPosition
    provenance: Provenance


# ---------------------------------------------------------------------------
# Payloads.
# ---------------------------------------------------------------------------


class ViewPayload(KnowledgeModel):
    """What every view returns: its rows, its counts, its scope and its continuation.

    The validator is the structural form of requirement 3.3. A payload either has no rows remaining
    and carries no continuation, or it has rows remaining and carries one -- so a truncated payload
    cannot be constructed without the token a caller needs to continue it, and a complete one cannot
    carry a token that would suggest more.
    """

    view: ViewName
    snapshot: KnowledgeReadSnapshot
    counts: ViewCounts
    completeness: ViewCompleteness
    continuation: ViewContinuation | None = None
    limitations: tuple[UnresolvedLimitation, ...] = ()
    ordering_rule_ids: tuple[str, ...] = ()
    renderer_version: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)

    @model_validator(mode="after")
    def _require_honest_bounding(self) -> ViewPayload:
        remaining = self.counts.rows_remaining
        if remaining.value is None:
            raise ValueError("rows_remaining is always counted for every view")
        if (remaining.value > 0) != (self.continuation is not None):
            raise ValueError(
                "a payload with rows remaining must carry a continuation and one that has none "
                "must not; a bounded response never presents its first page as the whole scope"
            )
        if self.continuation is not None:
            if self.continuation.view != self.view:
                raise ValueError("a continuation belongs to the view that minted it")
            if self.continuation.snapshot_logical_digest != self.snapshot.logical_digest:
                raise ValueError(
                    "a continuation is bound to the snapshot it was minted at and to no other"
                )
        if self.completeness.scope.snapshot_logical_digest != self.snapshot.logical_digest:
            raise ValueError("a completeness statement is scoped to the payload's own snapshot")
        registered = {rule.rule_id for rule in MECHANICAL_RULES}
        unregistered = sorted(set(self.ordering_rule_ids) - registered)
        if unregistered:
            raise ValueError(
                "every ordering rule a payload names must be registered; an inline comparator "
                f"cannot acquire a rule id by being spelled like one: {unregistered}"
            )
        return self


class SourceContextView(ViewPayload):
    """The compact registered neighborhood, with its rows and its advertised expansions."""

    view: Literal["source_context"] = "source_context"
    rows: tuple[SourceContextRow, ...] = ()


class InvariantView(ViewPayload):
    """The invariant view's rows: statement, conditions and the records made about it."""

    view: Literal["invariant"] = "invariant"
    rows: tuple[InvariantRow, ...] = ()


class FamilyView(ViewPayload):
    """The family view's rows, with member-record and attributed-source changes kept apart."""

    view: Literal["family"] = "family"
    rows: tuple[FamilyRow, ...] = ()


class ReviewMatrixView(ViewPayload):
    """The review matrix: the surface ``KS-R22@v1``'s Intent Reviewer is specified against."""

    view: Literal["review_matrix"] = "review_matrix"
    rows: tuple[ReviewMatrixRow, ...] = ()


class CurationQueueView(ViewPayload):
    """The curation queue: machine work items and curator dispositions, separate in the data."""

    view: Literal["curation_queue"] = "curation_queue"
    rows: tuple[CurationQueueRow, ...] = ()


# The discriminated union a result carries, so a payload keeps its concrete row type instead of
# being validated down to the base class and losing the fields a consumer needs.
ViewPayloadUnion = Annotated[
    SourceContextView | InvariantView | FamilyView | ReviewMatrixView | CurationQueueView,
    Field(discriminator="view"),
]

# The declared page size. It bounds one response; it never narrows a selection, and a truncated
# response always carries the continuation that reaches the rest.
MAX_VIEW_ROWS = 64

VIEW_PAYLOADS: tuple[type[ViewPayload], ...] = (
    SourceContextView,
    InvariantView,
    FamilyView,
    ReviewMatrixView,
    CurationQueueView,
)


# ---------------------------------------------------------------------------
# The reader port.
# ---------------------------------------------------------------------------


class ViewSourceRow(KnowledgeModel):
    """One recorded row as the reader port hands it to a view, before any selection or ordering.

    ``payload`` is the record revision's recorded payload, decoded from the store's ``TEXT_JSON``
    column and validated against the envelope registry on the way in. A view reads these values; it
    does not recompose them, and it never derives a field from a symbol name, a path prefix, a
    directory depth or a file extension.
    """

    record_kind: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    record_schema: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    record_id: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    revision_id: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
    lifecycle: str | None = Field(default=None, max_length=LABEL_MAX_LENGTH)
    governing_route_id: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
    author_ref: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
    recorded_at_state: str | None = Field(default=None, max_length=LABEL_MAX_LENGTH)
    payload: Mapping[str, object] = Field(default_factory=dict)


class ViewSourceCounts(KnowledgeModel):
    """The registered totals the reader port can report for one snapshot."""

    registered_realizations: int = Field(ge=0)
    registered_families: int = Field(ge=0)


@runtime_checkable
class KnowledgeViewReader(Protocol):
    """The reader port a view selects through: the snapshot, the rows, and the anchor states.

    This is ``Doc13`` §6's reader port as a view consumes it. A view holds one of these and nothing
    else; it has no path, no connection and no candidate tree, so "a view that opens the database
    directly has left the contract" is a property of the type rather than of a reviewer's attention.
    """

    def snapshot(self) -> KnowledgeReadSnapshot:
        """The one snapshot every row this reader returns belongs to."""
        ...

    def registered_counts(self) -> ViewSourceCounts:
        """The registered realization and family totals for that snapshot."""
        ...

    def rows(self, record_kind: str) -> tuple[ViewSourceRow, ...]:
        """Every recorded row of one registered record kind, in recorded order."""
        ...

    def invariant_rows(self) -> tuple[ViewSourceRow, ...]:
        """Every recorded invariant revision, one row per identity and revision."""
        ...

    def family_rows(self) -> tuple[ViewSourceRow, ...]:
        """Every recorded family revision, one row per identity and revision."""
        ...

    def realization_rows(self) -> tuple[ViewSourceRow, ...]:
        """Every recorded realization claim with the source location it attributes."""
        ...

    def anchor_state(self, locator: Mapping[str, object]) -> AnchorResolutionState:
        """How one recorded source locator resolves against the recorded tree."""
        ...


class ViewRequest(KnowledgeModel):
    """One view request: which view, over which recorded subject, ordered on which admitted input.

    ``ordering_input`` is one of the four admitted values and defaults to the declared stable
    ordering, which is the only one that needs nothing authored to exist. A caller that names a
    fifth value is refused as ``unadmitted_ordering_input`` rather than served the default: a
    fallback order would be this leaf's own semantic choice wearing a caller's request.
    """

    view: ViewName
    repository_id: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    invariant_revision_id: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
    family_revision_id: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
    record_kinds: tuple[str, ...] = ()
    ordering_input: OrderingInput = "stable_ordering"
    limit: int = Field(default=MAX_VIEW_ROWS, ge=1, le=MAX_VIEW_ROWS)
    continuation: ViewContinuation | None = None


class ViewResult(KnowledgeModel):
    """The typed outcome of one view read: a payload, or one typed refusal."""

    state: Literal["view", "refused"]
    operation: Literal["read_knowledge_view"] = "read_knowledge_view"
    repository_id: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    payload: ViewPayloadUnion | None = None
    refusal: ViewRefusal | None = None

    @model_validator(mode="after")
    def _require_one_outcome(self) -> ViewResult:
        if self.state == "view" and (self.payload is None or self.refusal is not None):
            raise ValueError("a view result carries a payload and no refusal")
        if self.state == "refused" and (self.refusal is None or self.payload is not None):
            raise ValueError("a refused result carries its refusal and no payload")
        return self
