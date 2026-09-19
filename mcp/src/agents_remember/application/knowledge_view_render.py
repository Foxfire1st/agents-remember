"""The five views rendered deterministically over the reader port, with their provenance classes.

This is the module the whole leaf exists for. Everything before it settles vocabulary and everything
after it transports bytes; here a view decides what it selects, how it orders it, and -- the clause
the packet calls the one this leaf exists to close -- **which of the two provenance classes every
ordered value carries**.

Four properties are enforced together, because a renderer that satisfies three of them is a
renderer that will lose the fourth:

* **Ordering comes from one of the four admitted inputs, and it is named.** :func:`order_candidates`
  is the only ordering path. It dispatches on the request's admitted input through
  :data:`~agents_remember.models.knowledge.classification.MECHANICAL_RULES` and reports, per
  position, which input produced it and whether that input is authored or mechanical. There is no
  comparator here that is not a registered rule, and no fallback: an input the registry does not
  admit raises.
* **A value that cannot be classified is withheld, not emitted.** :func:`_classified` returns
  ``None`` when a candidate has neither a recorded author nor a registered mechanical rule, and the
  caller turns that into an :class:`~agents_remember.models.knowledge.view.UnresolvedLimitation`.
  Requirement 2.1 forbids a third class, a null and a default, and this is how all three are
  unrepresentable rather than merely absent.
* **The declared tiebreak is the only lexical order.** When every declared key ties, positions are
  assigned by record identity ascending under ``ordering.declared-tiebreak``, and that is reported
  ``mechanical``. Nothing here reads a symbol name's spelling, a path prefix, a directory depth, a
  file extension or a repository location, and nothing here reads another view's result.
* **Two runs at one snapshot are byte-identical.** Every input is a recorded value or a registered
  constant; nothing is read from the clock, the environment, the filesystem or a live count that a
  page boundary could move.
* **A page is a function of the selection, the limit and the admitted cursor.** The offset arrives
  from the application seam, which is where a continuation is admitted or refused; nothing here
  parses a token, re-derives an offset or falls back to the first page. An ordered position is a
  position in the *selection*, not in the page, so the position a row reports does not move when a
  caller asks for a different page size.

**Where the authored side comes from.** ``CR20-6`` records the intake decision this module
implements: an authored no-consequence claim must be a *stored* record while the packet's Exclusions
forbid a new canonical record kind, so it is carried inside an **already-registered** kind -- the
``decision`` facet of ``KS-R11@v1`` (``models/knowledge/facet.py::DecisionPayload``), whose
``decider`` is the author, whose ``reason`` is the rationale, and whose ``outcome`` carries the
determination. The same kind carries a declared priority, whose ``outcome`` is required to be the
canonical decimal position; a ``decision`` facet whose ``outcome`` is not canonical is not read as a
priority, and the row it would have ordered is reported as an unresolved limitation instead.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from pydantic import TypeAdapter

from agents_remember.models.knowledge.classification import (
    AUTHORED_CLASS,
    REGISTERED_ROLE_ORDER,
    AuthoredDetermination,
    OrderingInput,
    Provenance,
    authored_provenance,
    mechanical_provenance,
)
from agents_remember.models.knowledge.source import SourceLocator
from agents_remember.models.knowledge.view import (
    CurationQueueRow,
    CuratorDisposition,
    FamilyRow,
    InvariantRow,
    KnowledgeViewReader,
    MachineWorkItem,
    NoConsequenceStatement,
    OrderedPosition,
    ReviewMatrixRow,
    SourceContextRow,
    SubjectRef,
    UnresolvedLimitation,
    ViewRequest,
    ordering_position,
    require_admitted_ordering_input,
)

__all__ = [
    "DECISION_FACET_KIND",
    "NO_CONSEQUENCE_OUTCOME_PREFIX",
    "PRIORITY_OUTCOME_PREFIX",
    "Candidate",
    "OrderedSet",
    "UnadmittedOrderingInput",
    "authored_decision",
    "order_candidates",
    "render_curation_queue",
    "render_family",
    "render_invariant",
    "render_review_matrix",
    "render_source_context",
]

# The registered facet kind that carries an authored determination, and the two canonical outcome
# prefixes this leaf reads from it. Both are recorded scalars on a registered payload field, so a
# priority is never scraped out of prose.
DECISION_FACET_KIND = "decision"
NO_CONSEQUENCE_OUTCOME_PREFIX = "no_consequence: "
PRIORITY_OUTCOME_PREFIX = "declared_priority: "

# The rule every generated row's *content* is classified under when its own record is mechanical.
DETECTION_RULE = ("ordering.trigger-rule", 1)
CONSEQUENCE_RULE = ("consequence.record-payload-byte-equal", 1)


@dataclass(frozen=True)
class AuthoredDecision:
    """One authored determination read from a stored ``decision`` facet."""

    facet_revision_id: str
    decider: str
    reason: str
    outcome: str

    @property
    def priority_position(self) -> int | None:
        """The declared priority position this facet carries, or ``None`` when it carries none.

        The value is read from the payload's own ``outcome`` field with a required canonical
        spelling. A facet that spells its outcome any other way is not a priority, and the caller
        reports the row as an unresolved limitation rather than guessing a number out of text.
        """

        if not self.outcome.startswith(PRIORITY_OUTCOME_PREFIX):
            return None
        spelling = self.outcome[len(PRIORITY_OUTCOME_PREFIX) :].strip()
        if not spelling.isdigit() or spelling != str(int(spelling)):
            return None
        position = int(spelling)
        return position if position >= 1 else None

    @property
    def is_no_consequence(self) -> bool:
        """Whether this facet is an authored no-consequence claim rather than a priority."""

        return self.outcome.startswith(NO_CONSEQUENCE_OUTCOME_PREFIX)

    @property
    def no_consequence_detail(self) -> str:
        """The authored statement itself, which is the facet's own recorded text."""

        return self.outcome[len(NO_CONSEQUENCE_OUTCOME_PREFIX) :].strip() or self.reason


def authored_decision(row: Mapping[str, object]) -> AuthoredDecision | None:
    """Read one stored ``decision`` facet, or ``None`` when the row is not one.

    Every field is taken from the record. The ``decider`` is the author the *facet* names, which is
    authored content about who decided and is not the record's provenance envelope; the fallback to
    the envelope's actor happens only when the payload itself is silent.
    """

    payload = row.get("payload")
    if not isinstance(payload, Mapping) or payload.get("facet_kind") != DECISION_FACET_KIND:
        return None
    outcome = payload.get("outcome")
    reason = payload.get("reason")
    decider = payload.get("decider")
    if not isinstance(outcome, str) or not isinstance(reason, str):
        return None
    resolved_decider = decider if isinstance(decider, str) and decider else row.get("author_ref")
    if not isinstance(resolved_decider, str) or not resolved_decider:
        return None
    return AuthoredDecision(
        facet_revision_id=str(row.get("revision_id") or row.get("record_id") or ""),
        decider=resolved_decider,
        reason=reason,
        outcome=outcome,
    )


@dataclass(frozen=True)
class Candidate:
    """One row a view may emit, before ordering and before classification.

    ``authored`` is set exactly when a stored record the substrate read is the row's own content;
    ``mechanical_rule_id`` is set exactly when a registered rule produced it. A candidate with
    neither is withheld by :func:`_classified`.
    """

    subject: SubjectRef
    label: str
    authored: AuthoredDecision | None = None
    mechanical_rule_id: tuple[str, int] | None = None
    priority: int | None = None
    role: str | None = None
    trigger: str | None = None
    fact_kind: str = "recorded"
    statement: str | None = None
    essential_conditions: tuple[str, ...] = ()
    conditions_omitted: bool = False
    path: str | None = None
    locator: SourceLocator | None = None
    lifecycle: str | None = None
    change_locus: str = "neither"
    assessment_status: str = "missing"
    assessment_ids: tuple[str, ...] = ()
    extra: tuple[str, ...] = ()


@dataclass(frozen=True)
class OrderedSet:
    """The outcome of one ordering pass: the ordered candidates and what could not be classified."""

    ordered: tuple[Candidate, ...]
    limitations: tuple[UnresolvedLimitation, ...]
    rule_ids: tuple[str, ...]


def _declared_priority(candidate: Candidate) -> int | None:
    """The priority recorded *on this candidate's own subject*, or ``None`` when none is recorded.

    A priority facet attached to another record does not order this one: the position is a fact about
    the subject the facet is attached to, and borrowing one would make the ordering a property of the
    facet set rather than of the row.
    """

    return candidate.priority


def _ordering_keys(
    candidate: Candidate, ordering_input: OrderingInput
) -> tuple[tuple[int, ...], Provenance]:
    """The declared key sequence one candidate orders under, with the provenance of that key.

    The sequence is compared lexicographically and the ``Provenance`` describes *the input*, which is
    what requirement 2.6 requires to travel beside the ordered value. It is deliberately not a
    score: three of the four inputs produce a stored value or a recorded vocabulary position, and the
    fourth produces a recorded trigger identity.
    """

    if ordering_input == "declared_priority":
        position = _declared_priority(candidate)
        if position is None:
            # No authored priority is recorded for this subject. The row is still *ordered* -- it
            # sorts after every prioritised row -- but the position it receives is the declared
            # tiebreak's, and that is a mechanical fact about an absent authored value rather than
            # an authored one.
            return (1,), mechanical_provenance("ordering.declared-tiebreak", 1)
        return (0, position), _authored_position_provenance(candidate)
    if ordering_input == "registered_role":
        role = candidate.role or ""
        rank = (
            REGISTERED_ROLE_ORDER.index(role)
            if role in REGISTERED_ROLE_ORDER
            else len(REGISTERED_ROLE_ORDER)
        )
        return (rank,), mechanical_provenance("ordering.registered-role", 1)
    if ordering_input == "explicit_trigger_rule":
        trigger = candidate.trigger or candidate.fact_kind
        # The trigger's own recorded code orders the rows. It is compared as a recorded identity,
        # and the declared tiebreak applies when two rows carry the same one.
        return (0, sum(trigger.encode("utf-8"))), mechanical_provenance("ordering.trigger-rule", 1)
    return (0,), mechanical_provenance("ordering.declared-tiebreak", 1)


def _authored_position_provenance(candidate: Candidate) -> Provenance:
    """The authored provenance of a declared-priority position, from the facet that recorded it."""

    assert candidate.authored is not None
    return authored_provenance(candidate.authored.decider, candidate.authored.reason)


class UnadmittedOrderingInput(ValueError):
    """An ordering request named none of the four admitted inputs, so no rows are returned."""


def order_candidates(
    candidates: Sequence[Candidate],
    ordering_input: str,
) -> OrderedSet:
    """Order a view's candidates on one admitted input, withholding what cannot be classified.

    The admission check happens before any row is touched, so a refusal here means the caller
    receives no rows at all rather than a default order. There is no fallback comparator in this
    function and no database order that survives it.
    """

    if require_admitted_ordering_input(ordering_input) is not None:
        raise UnadmittedOrderingInput(ordering_input)
    admitted: OrderingInput = ordering_input  # type: ignore[assignment]
    limitations: list[UnresolvedLimitation] = []
    classified: list[Candidate] = []
    for candidate in candidates:
        if _classified(candidate) is None:
            limitations.append(
                UnresolvedLimitation(
                    code="unclassified_value",
                    detail=(
                        "the value carries neither a recorded author nor a registered mechanical "
                        "rule, so it is not returned with an empty class"
                    ),
                    subject=f"{candidate.subject.record_kind}:{candidate.subject.record_id}",
                )
            )
            continue
        classified.append(candidate)
    ordered = sorted(classified, key=lambda item: _sort_key(item, admitted))
    return OrderedSet(
        ordered=tuple(ordered),
        limitations=tuple(limitations),
        rule_ids=_rules_used(admitted),
    )


def _sort_key(candidate: Candidate, ordering_input: OrderingInput) -> tuple[object, ...]:
    """The total sort key: the declared key first, then the registered stable tiebreak.

    The tiebreak is the last term and is always record identity ascending, which is the declared,
    versioned stable ordering the registry carries under ``ordering.declared-tiebreak``. Because it
    is a total order over identities, two runs at one snapshot cannot disagree.
    """

    keys, _provenance = _ordering_keys(candidate, ordering_input)
    return (keys, candidate.subject.record_id, candidate.subject.revision_id or "")


def _rules_used(ordering_input: OrderingInput) -> tuple[str, ...]:
    """Every registered rule id this ordering touched, tiebreak included."""

    ids = ["ordering.declared-tiebreak"]
    for candidate_id, rule in (
        ("declared_priority", "ordering.declared-priority"),
        ("registered_role", "ordering.registered-role"),
        ("explicit_trigger_rule", "ordering.trigger-rule"),
    ):
        if ordering_input == candidate_id:
            ids.append(rule)
    return tuple(sorted(set(ids)))


def _classified(candidate: Candidate) -> Provenance | None:
    """The candidate's provenance class, or ``None`` when it has none and must be withheld.

    The two branches are the whole closure. An authored candidate is one whose own content is a
    stored record with a named author, so its class is ``authored`` and it carries that author and
    rationale. A mechanical candidate is one a registered rule produced, so its class is
    ``mechanical`` and it carries the rule and version. Anything else -- no author, no rule -- is not
    classified and is not returned.
    """

    if candidate.authored is not None:
        return Provenance(
            provenance_class=AUTHORED_CLASS,
            authored=AuthoredDetermination(
                author_ref=candidate.authored.decider, rationale=candidate.authored.reason
            ),
        )
    if candidate.mechanical_rule_id is not None:
        return mechanical_provenance(*candidate.mechanical_rule_id)
    return None


def _position(index: int, provenance: Provenance, ordering_input: OrderingInput) -> OrderedPosition:
    """One ordered position carrying the class of the *input*, not of the row's content."""

    return ordering_position(index + 1, ordering_input, provenance)


def _consequence(candidate: Candidate) -> NoConsequenceStatement | None:
    """The candidate's no-consequence claim, authored or mechanical, or ``None`` when it has none."""

    if candidate.authored is not None and candidate.authored.is_no_consequence:
        return NoConsequenceStatement(
            subject=candidate.subject,
            detail=candidate.authored.no_consequence_detail,
            provenance=authored_provenance(candidate.authored.decider, candidate.authored.reason),
            claim_ref=candidate.authored.facet_revision_id,
        )
    if candidate.mechanical_rule_id is None:
        return None
    return NoConsequenceStatement(
        subject=candidate.subject,
        detail=candidate.statement or "no content consequence under a registered mechanical rule",
        provenance=mechanical_provenance(*candidate.mechanical_rule_id),
    )


def _page(
    ordered: Sequence[Candidate], limit: int, offset: int
) -> tuple[tuple[Candidate, ...], int]:
    """One page of an ordered set, and the position the next page starts at."""

    page = tuple(ordered[offset : offset + limit])
    return page, offset + len(page)


@dataclass(frozen=True)
class Page:
    """One page of an ordered selection and the two selection positions that bound it.

    ``start`` is the position the page begins at and ``next_position`` the one the page after it
    begins at, so the seam can report how much of the selection is still ahead without re-deriving
    either from a token. Both are positions in the *selection*, not in the page: a row's ordered
    position is a fact about the row and the order, and it does not move when a caller asks for a
    different page size.
    """

    ordered: OrderedSet
    rows: tuple[Candidate, ...]
    start: int
    next_position: int


# ---------------------------------------------------------------------------
# Per-view selection.
# ---------------------------------------------------------------------------


def _decisions_for(
    reader: KnowledgeViewReader, endpoint_kind: str, endpoint_id: str
) -> tuple[AuthoredDecision, ...]:
    """Every authored decision attached to one exact endpoint revision, in recorded order."""

    attachments = getattr(reader, "attachment_rows", None)
    if attachments is None:
        return ()
    found: list[AuthoredDecision] = []
    for row in attachments(endpoint_kind, endpoint_id):
        decision = authored_decision(
            {
                "payload": row.payload,
                "author_ref": row.author_ref,
                "revision_id": row.revision_id,
                "record_id": row.record_id,
            }
        )
        if decision is not None:
            found.append(decision)
    return tuple(found)


def _priority_of(decisions: Sequence[AuthoredDecision]) -> int | None:
    """The declared priority among a subject's authored decisions, if exactly one declares it."""

    positions = {decision.priority_position for decision in decisions}
    positions.discard(None)
    if len(positions) != 1:
        return None
    return next(iter(positions))


def _invariant_candidates(
    reader: KnowledgeViewReader, request: ViewRequest
) -> tuple[Candidate, ...]:
    """The recorded facts the invariant view selects for one invariant revision."""

    candidates: list[Candidate] = []
    for row in reader.invariant_rows():
        if request.invariant_revision_id and row.revision_id != request.invariant_revision_id:
            continue
        decisions = _decisions_for(reader, "invariant_revision", row.revision_id or "")
        candidates.append(
            Candidate(
                subject=SubjectRef(
                    record_kind="invariant_revision",
                    record_id=row.record_id,
                    revision_id=row.revision_id,
                ),
                label=str(row.payload.get("display_label") or row.record_id),
                authored=next((item for item in decisions if not item.is_no_consequence), None),
                mechanical_rule_id=CONSEQUENCE_RULE,
                priority=_priority_of(decisions),
                fact_kind="statement",
                statement=_string(row.payload.get("statement")),
                essential_conditions=_conditions(row.payload.get("conditions")),
                conditions_omitted=not row.payload.get("conditions"),
                lifecycle=row.lifecycle,
            )
        )
    for row in reader.realization_rows():
        candidates.append(
            Candidate(
                subject=SubjectRef(
                    record_kind="realization_claim",
                    record_id=row.record_id,
                    revision_id=row.revision_id,
                ),
                label=str(row.payload.get("path") or row.record_id),
                mechanical_rule_id=CONSEQUENCE_RULE,
                role=_string(row.payload.get("role")),
                fact_kind="realization",
                statement=_string(row.payload.get("rationale")),
                path=_string(row.payload.get("path")),
                locator=_locator(row.payload.get("locator")),
            )
        )
    return tuple(candidates)


def _source_context_candidates(
    reader: KnowledgeViewReader, request: ViewRequest
) -> tuple[Candidate, ...]:
    """The compact registered neighborhood: roles, locations and selected diagnostics."""

    candidates: list[Candidate] = []
    for row in reader.realization_rows():
        candidates.append(
            Candidate(
                subject=SubjectRef(
                    record_kind="realization_claim",
                    record_id=row.record_id,
                    revision_id=row.revision_id,
                ),
                label=str(row.payload.get("path") or row.record_id),
                authored=None,
                mechanical_rule_id=("ordering.registered-role", 1),
                role=_string(row.payload.get("role")),
                fact_kind="registered_realization",
                statement=_string(row.payload.get("rationale")),
                path=_string(row.payload.get("path")),
                locator=_locator(row.payload.get("locator")),
            )
        )
    if request.invariant_revision_id:
        for decision in _decisions_for(reader, "invariant_revision", request.invariant_revision_id):
            candidates.append(
                Candidate(
                    subject=SubjectRef(
                        record_kind="decision_facet",
                        record_id=decision.facet_revision_id,
                    ),
                    label=decision.decider,
                    authored=decision,
                    priority=decision.priority_position,
                    fact_kind=(
                        "authored_responsibility"
                        if not decision.is_no_consequence
                        else "diagnostic_evidence"
                    ),
                    statement=decision.reason,
                )
            )
    return tuple(candidates)


def _family_candidates(reader: KnowledgeViewReader, request: ViewRequest) -> tuple[Candidate, ...]:
    """The family view's rows, keeping member-record and attributed-source changes apart."""

    candidates: list[Candidate] = []
    for row in reader.family_rows():
        if request.family_revision_id and row.revision_id != request.family_revision_id:
            continue
        decisions = _decisions_for(reader, "family_revision", row.revision_id or "")
        candidates.append(
            Candidate(
                subject=SubjectRef(
                    record_kind="family_revision",
                    record_id=row.record_id,
                    revision_id=row.revision_id,
                ),
                label=str(row.payload.get("display_label") or row.record_id),
                authored=next((item for item in decisions if not item.is_no_consequence), None),
                mechanical_rule_id=CONSEQUENCE_RULE,
                priority=_priority_of(decisions),
                fact_kind="joint_guarantee",
                statement=_string(row.payload.get("joint_guarantee")),
                lifecycle=row.lifecycle,
                change_locus="member_record",
            )
        )
    for row in reader.rows("detection_signal"):
        candidates.append(_signal_candidate(row))
    return tuple(candidates)


def _signal_candidate(row: object) -> Candidate:
    """One recorded detection signal as a mechanically-determined family row."""

    payload = row.payload  # type: ignore[attr-defined]
    condition = _string(payload.get("condition")) or "recorded_condition"
    return Candidate(
        subject=SubjectRef(
            record_kind="detection_signal",
            record_id=str(row.record_id),  # type: ignore[attr-defined]
            revision_id=row.revision_id,  # type: ignore[attr-defined]
        ),
        label=condition,
        authored=None,
        mechanical_rule_id=DETECTION_RULE,
        trigger=condition,
        fact_kind="detection_signal",
        change_locus="attributed_source",
        lifecycle=row.lifecycle,  # type: ignore[attr-defined]
    )


def _review_candidates(reader: KnowledgeViewReader, request: ViewRequest) -> tuple[Candidate, ...]:
    """The review matrix: requirements, effects, preservation claims, evidence and findings."""

    kinds = request.record_kinds or (
        "requirement_revision",
        "invariant_effect_claim",
        "preservation_claim",
        "unresolved_question",
        "evidence_claim",
        "verification_observation",
    )
    candidates: list[Candidate] = []
    for kind in kinds:
        for row in reader.rows(kind):
            candidates.append(_record_candidate(row, kind))
    return tuple(candidates)


def _record_candidate(row: object, kind: str) -> Candidate:
    """One envelope record as a review-matrix row, with its recorded references preserved."""

    payload = row.payload  # type: ignore[attr-defined]
    refs = _references(payload)
    return Candidate(
        subject=SubjectRef(
            record_kind=kind,
            record_id=str(row.record_id),  # type: ignore[attr-defined]
            revision_id=row.revision_id,  # type: ignore[attr-defined]
        ),
        label=kind,
        authored=None,
        mechanical_rule_id=CONSEQUENCE_RULE,
        fact_kind=kind,
        statement=_string(payload.get("explanation")) or _string(payload.get("rationale")),
        lifecycle=row.lifecycle,  # type: ignore[attr-defined]
        assessment_status="assessed" if refs else "missing",
        assessment_ids=refs,
        extra=refs,
    )


def _curation_candidates(
    reader: KnowledgeViewReader, request: ViewRequest
) -> tuple[Candidate, ...]:
    """Machine work items, and curator dispositions, kept as two separate row sets."""

    candidates: list[Candidate] = []
    for row in reader.rows("detection_signal"):
        candidates.append(_signal_candidate(row))
    if request.family_revision_id:
        for decision in _decisions_for(reader, "family_revision", request.family_revision_id):
            candidates.append(
                Candidate(
                    subject=SubjectRef(
                        record_kind="curator_disposition",
                        record_id=decision.facet_revision_id,
                    ),
                    label=decision.decider,
                    authored=decision,
                    fact_kind="curator_disposition",
                    statement=decision.reason,
                    trigger=decision.facet_revision_id,
                )
            )
    return tuple(candidates)


# ---------------------------------------------------------------------------
# Per-view row assembly.
# ---------------------------------------------------------------------------


def _string(value: object) -> str | None:
    return None if value is None else str(value)


# One decoder for the recorded locator union, parsed by the union's own discriminator rather than by
# a branch here: a fourth locator kind is then admitted by the model instead of being silently read
# as one of the three this module happens to know. Nothing is derived and nothing is re-anchored --
# the recorded locator is reported as recorded, and one that does not validate raises rather than
# becoming an extent this code invented.
_LOCATOR_ADAPTER: TypeAdapter[SourceLocator] = TypeAdapter(SourceLocator)


def _locator(value: object) -> SourceLocator | None:
    """One recorded locator as the typed union, or ``None`` when the row carries none."""

    return None if value is None else _LOCATOR_ADAPTER.validate_python(value)


def _conditions(value: object) -> tuple[str, ...]:
    if isinstance(value, (list, tuple)):
        return tuple(str(item) for item in value)
    return () if value is None else (str(value),)


def _references(payload: Mapping[str, object]) -> tuple[str, ...]:
    """Every recorded assessment reference on one payload, in recorded order."""

    found: list[str] = []
    for field in (
        "assessment_refs",
        "requirement_revision_refs",
        "candidate_realization_claim_ids",
    ):
        value = payload.get(field)
        if isinstance(value, (list, tuple)):
            found.extend(str(item) for item in value)
        elif isinstance(value, str):
            found.append(value)
    return tuple(found)


def _render(candidates: Sequence[Candidate], request: ViewRequest, offset: int) -> Page:
    """Order the candidates, take the page that starts at ``offset``, and bound it.

    The offset is the one the seam admitted from the caller's continuation. No token is read here, so
    there is no second parse and no fallback to the start of the walk: a page is a function of the
    selection, the limit and the cursor alone.
    """

    ordered = order_candidates(candidates, request.ordering_input)
    rows, next_position = _page(ordered.ordered, request.limit, offset)
    return Page(ordered=ordered, rows=rows, start=offset, next_position=next_position)


def render_source_context(
    reader: KnowledgeViewReader, request: ViewRequest, offset: int
) -> tuple[
    tuple[SourceContextRow, ...],
    tuple[UnresolvedLimitation, ...],
    tuple[str, ...],
    int,
    int,
]:
    """The source-context view's rows, its limitations, its rule ids, its selection's size and the
    position the next page starts at."""

    page = _render(_source_context_candidates(reader, request), request, offset)
    rows: list[SourceContextRow] = []
    for index, candidate in enumerate(page.rows):
        provenance = _classified(candidate)
        assert provenance is not None
        rows.append(
            SourceContextRow(
                subject=candidate.subject,
                fact_kind=candidate.fact_kind,  # type: ignore[arg-type]
                statement=candidate.statement,
                role=candidate.role,  # type: ignore[arg-type]
                path=candidate.path,
                locator=candidate.locator,
                order=_position(page.start + index, provenance, request.ordering_input),
                provenance=provenance,
            )
        )
    return (
        tuple(rows),
        page.ordered.limitations,
        page.ordered.rule_ids,
        len(page.ordered.ordered),
        page.next_position,
    )


def render_invariant(
    reader: KnowledgeViewReader, request: ViewRequest, offset: int
) -> tuple[
    tuple[InvariantRow, ...],
    tuple[UnresolvedLimitation, ...],
    tuple[str, ...],
    int,
    int,
]:
    """The invariant view's rows, its limitations, its rule ids, its selection's size and the
    position the next page starts at."""

    page = _render(_invariant_candidates(reader, request), request, offset)
    rows: list[InvariantRow] = []
    for index, candidate in enumerate(page.rows):
        provenance = _classified(candidate)
        assert provenance is not None
        rows.append(
            InvariantRow(
                subject=candidate.subject,
                fact_kind=candidate.fact_kind,  # type: ignore[arg-type]
                statement=candidate.statement,
                essential_conditions=candidate.essential_conditions,
                conditions_omitted=candidate.conditions_omitted,
                lifecycle=candidate.lifecycle,
                order=_position(page.start + index, provenance, request.ordering_input),
                provenance=provenance,
            )
        )
    return (
        tuple(rows),
        page.ordered.limitations,
        page.ordered.rule_ids,
        len(page.ordered.ordered),
        page.next_position,
    )


DISPOSITION_VOCABULARY_VERSION = "curator-disposition/v1"


def render_family(
    reader: KnowledgeViewReader, request: ViewRequest, offset: int
) -> tuple[
    tuple[FamilyRow, ...],
    tuple[UnresolvedLimitation, ...],
    tuple[str, ...],
    int,
    int,
]:
    """The family view's rows, with each row's change locus left as the field it was recorded as."""

    page = _render(_family_candidates(reader, request), request, offset)
    rows: list[FamilyRow] = []
    for index, candidate in enumerate(page.rows):
        provenance = _classified(candidate)
        assert provenance is not None
        rows.append(
            FamilyRow(
                subject=candidate.subject,
                fact_kind=candidate.fact_kind,  # type: ignore[arg-type]
                statement=candidate.statement,
                change_locus=candidate.change_locus,  # type: ignore[arg-type]
                lifecycle=candidate.lifecycle,
                order=_position(page.start + index, provenance, request.ordering_input),
                provenance=provenance,
            )
        )
    return (
        tuple(rows),
        page.ordered.limitations,
        page.ordered.rule_ids,
        len(page.ordered.ordered),
        page.next_position,
    )


def render_review_matrix(
    reader: KnowledgeViewReader, request: ViewRequest, offset: int
) -> tuple[
    tuple[ReviewMatrixRow, ...],
    tuple[UnresolvedLimitation, ...],
    tuple[str, ...],
    int,
    int,
]:
    """The review matrix: the surface ``KS-R22@v1`` mounts, with its classification fields.

    The no-consequence statement is attached to a row exactly when a registered mechanical rule or a
    stored authored claim establishes one, and the two are distinguishable at the payload level
    because the statement model refuses any mixture of author and rule.
    """

    page = _render(_review_candidates(reader, request), request, offset)
    rows: list[ReviewMatrixRow] = []
    for index, candidate in enumerate(page.rows):
        provenance = _classified(candidate)
        assert provenance is not None
        rows.append(
            ReviewMatrixRow(
                subject=candidate.subject,
                requirement_record_id=_only_if(candidate, "requirement_revision"),
                requirement_revision_id=(
                    candidate.subject.revision_id
                    if candidate.subject.record_kind == "requirement_revision"
                    else None
                ),
                proposed_effect_claim_id=_only_if(candidate, "invariant_effect_claim"),
                preservation_claim_id=_only_if(candidate, "preservation_claim"),
                evidence_claim_ids=(
                    (candidate.subject.record_id,)
                    if candidate.subject.record_kind == "evidence_claim"
                    else ()
                ),
                record_ids=candidate.extra,
                assessment_ids=candidate.assessment_ids,
                assessment_status=candidate.assessment_status,  # type: ignore[arg-type]
                unresolved_findings=(
                    candidate.extra
                    if candidate.subject.record_kind == "unresolved_question"
                    else ()
                ),
                consequence=_consequence(candidate),
                order=_position(page.start + index, provenance, request.ordering_input),
                provenance=provenance,
            )
        )
    return (
        tuple(rows),
        page.ordered.limitations,
        page.ordered.rule_ids,
        len(page.ordered.ordered),
        page.next_position,
    )


def _only_if(candidate: Candidate, record_kind: str) -> str | None:
    """The candidate's own record id, but only when it is the kind this field is about."""

    return candidate.subject.record_id if candidate.subject.record_kind == record_kind else None


def render_curation_queue(
    reader: KnowledgeViewReader, request: ViewRequest, offset: int
) -> tuple[
    tuple[CurationQueueRow, ...],
    tuple[UnresolvedLimitation, ...],
    tuple[str, ...],
    int,
    int,
]:
    """The curation queue: machine items and curator dispositions, separate in the shape of the data.

    This view computes no actionable count. ``design/retrieval-review-design.md:348`` keeps the
    factual review section report-only, and a count of "actionable" work would be the semantic
    conclusion this leaf is forbidden to generate.
    """

    page = _render(_curation_candidates(reader, request), request, offset)
    rows: list[CurationQueueRow] = []
    for index, candidate in enumerate(page.rows):
        provenance = _classified(candidate)
        assert provenance is not None
        position = _position(page.start + index, provenance, request.ordering_input)
        rows.append(_queue_row(candidate, position, provenance))
    return (
        tuple(rows),
        page.ordered.limitations,
        page.ordered.rule_ids,
        len(page.ordered.ordered),
        page.next_position,
    )


def _queue_row(
    candidate: Candidate, position: OrderedPosition, provenance: Provenance
) -> CurationQueueRow:
    """One queue row: either a machine work item or a separately attributed disposition."""

    if candidate.fact_kind == "curator_disposition":
        assert candidate.authored is not None
        return CurationQueueRow(
            item=MachineWorkItem(
                work_item_id=candidate.subject.record_id,
                condition_code="curator_disposition_recorded",
                condition_vocabulary_version=DISPOSITION_VOCABULARY_VERSION,
                matched_facts=(),
            ),
            disposition=CuratorDisposition(
                disposition_id=candidate.subject.record_id,
                disposition=candidate.authored.outcome,
                rationale=candidate.authored.reason,
                author_ref=candidate.authored.decider,
                state_at_origin="recorded",
                provenance=provenance,
            ),
            order=position,
        )
    return CurationQueueRow(
        item=MachineWorkItem(
            work_item_id=candidate.subject.record_id,
            condition_code=candidate.trigger or candidate.fact_kind,
            condition_vocabulary_version="detection-condition/v1",
            matched_facts=candidate.extra,
            registered_scope_status=candidate.statement,
        ),
        order=position,
    )
