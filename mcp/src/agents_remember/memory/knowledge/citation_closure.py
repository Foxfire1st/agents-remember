"""The selected prose view's citation closure, and the per-state enumeration a census counts.

This module reads **recorded bindings and recorded targets**, and nothing else. It does not re-parse
the corpus, does not search for a plausible target, and does not consult a working tree, ``HEAD`` or
a branch name to fill a gap -- the shipped precedent for that refusal is the anchor reader's own
``no locator search and no line re-anchoring``. The one object store it touches is the memory
repository, and it touches it only to resolve the **recorded** identity each binding already holds.

Five properties are enforced here rather than documented:

* **Closure is part of the view.** A selected prose view declares its selected set -- the exact prose
  owner revisions it covers -- and the closure includes, as indivisible items, every recorded binding
  whose owner revision is in that set, together with each target's reference closure: the target
  record/revision identity, its kind, its provenance, its lifecycle and the exact locator. Counts are
  over the **declared selected set**, never over all history and never over presumed actual prose.

* **A bounded closure refuses rather than truncates.** If the declared execution bound prevents
  complete assembly, the operation refuses with ``selection_incomplete`` and the bound reached, via
  the shipped :func:`…read_refusals.selection_incomplete_refusal` -- never with invented totals and
  never with a partial closure presented as the closure. The refusal happens **before** any item is
  emitted, so no caller ever holds a truncated closure plus a total it did not compute.

* **Unresolved and stale keys travel as separate limitations, and the counts partition the set.**
  ``unresolved`` is the number of observations that are not ``exact_recorded_blob``, ``stale`` is the
  number whose owner revision resolved but whose recorded key is no longer in it, and the per-state
  counts name every member of the closed vocabulary including the ones that total zero. The
  validator on :class:`…models.knowledge.citation.CitationBindingCounts` refuses a report whose
  per-state counts do not sum to the declared selected set, so a key that resolves to nothing cannot
  be dropped from the denominator.

* **There is no semantic-completeness field, and partial coverage reads as partial.** The response
  exposes its limitations and its declared key-form coverage instead of presenting itself as
  complete. A form this increment does not read is counted and named, and the view carries
  ``partial_key_form_coverage``, so an increment that read one of two written forms cannot render
  like an increment that read both.

* **A stale binding is readable and attributed, and it is never re-bound.** When the owner revision
  has moved, the binding is reported against the revision it was *authored* against -- stale and/or
  unresolved as observed, with the recorded key preserved. No resolver here relocates a moved line,
  re-anchors a range, or picks a target by similarity, filename or prose search; a curator authors
  the re-binding.

Ordering is deterministic over declared priorities and stable identities -- the owner revision, then
the key form, then the recorded key text, then the binding identity -- and it never invents semantic
importance from a symbol name or presents a mechanical score as assessed risk.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from pydantic import TypeAdapter

from agents_remember.kernel.canonical_json import decoded_json
from agents_remember.memory.knowledge.citations import binding_row_digest
from agents_remember.memory.knowledge.read_bindings import (
    RecordedBinding,
    key_forms_of,
    observe_binding,
)
from agents_remember.memory.knowledge.read_owner_revisions import OwnerRevisionResolver
from agents_remember.memory.knowledge.read_refusals import selection_incomplete_refusal
from agents_remember.models.knowledge.citation import (
    BINDING_STATES,
    COVERED_KEY_FORMS,
    KEY_FORMS,
    NO_SEMANTIC_COMPLETENESS_LIMITATION,
    CitationBindingClosureRequest,
    CitationBindingClosureResult,
    CitationBindingCounts,
    CitationBindingItem,
    CitationBindingLimitation,
    CitationBindingObservation,
    CitationTargetReference,
    KeyFormCoverage,
    ProseCitationKey,
    ProseOwnerRevision,
    TableRowKeyForm,
    render_local_key,
)
from agents_remember.models.knowledge.result import KnowledgeRefusal
from agents_remember.models.knowledge.source import SourceLocator

if TYPE_CHECKING:
    from agents_remember.memory.knowledge.store import OpenedKnowledgeStore

__all__ = [
    "CLOSURE_OPERATION",
    "assemble_citation_closure",
    "enumerate_recorded_bindings",
    "load_recorded_bindings",
    "selected_set_of",
]

CLOSURE_OPERATION = "read_citation_closure"

# The one locator adapter, built once. The locator travels as the shipped ``SourceLocator`` union and
# is validated as that union on the way out, so a locator no resolver supports still decodes to a
# well-formed record and is reported as unsupported rather than being dropped.
_LOCATOR_ADAPTER: TypeAdapter[SourceLocator] = TypeAdapter(SourceLocator)

# The recorded bindings of one namespace, ordered by the tuple the closure's deterministic order
# begins with. The `UNIQUE` key over the owner revision and the recorded key text is not repeated
# here -- an unordered read is what makes a rewritten store visible instead of silently collapsed.
_BINDINGS_IN_NAMESPACE = "SELECT * FROM citation_binding WHERE repository_id = ?"

# Every target record this namespace holds, as `(record_id, kind, authority_home, lifecycle,
# record_schema)`, so one closure resolves every target it names against one read.
_TARGET_RECORDS = (
    "SELECT record_id, kind, authority_home, lifecycle, record_schema FROM knowledge_record "
    "WHERE repository_id = ?"
)

# Every stored revision of the targets, so "the target record holds no such revision" is a fact
# about the store rather than about one record's own revisions only: a revision stored under another
# record is reported as absent for *this* record, which is exactly what the reference claims.
_TARGET_REVISIONS = "SELECT revision_id, record_id FROM record_revision WHERE repository_id = ?"

_REVISION_PAYLOAD = (
    "SELECT payload FROM record_revision WHERE repository_id = ? AND revision_id = ?"
)


def selected_set_of(request: CitationBindingClosureRequest) -> tuple[ProseOwnerRevision, ...]:
    """Return the view's declared selected set, in the request's own declared order.

    The set is **declared**, not discovered: nothing here walks a corpus, a directory or a route to
    decide which documents a view covers. A view that declares nothing selects nothing, and that is
    reported as an empty selection rather than as a failed one.
    """

    return tuple(request.selected_owner_revisions)


def selected_set_key(revision: ProseOwnerRevision) -> tuple[str, str, str]:
    """Return the stable identity tuple one selected owner revision is matched by."""

    return (revision.repository, revision.document_path, revision.blob_object_id)


def load_recorded_bindings(store: OpenedKnowledgeStore) -> tuple[RecordedBinding, ...]:
    """Return every recorded binding of one namespace, decoded from its own row and sealed payload.

    The payload is read from the **sealed revision** rather than reconstructed from the columns, so
    the typed locator and the key's parts arrive as the values the write path validated. A row whose
    sealed revision is missing is reported as a store integrity failure rather than skipped: a
    binding silently dropped from a closure is precisely the failure this leaf exists to prevent.
    """

    return tuple(
        _recorded_binding(row, _sealed_payload(store, str(row[1])))
        for row in store.connection.execute(_BINDINGS_IN_NAMESPACE, (store.repository_id,))
    )


def enumerate_recorded_bindings(
    store: OpenedKnowledgeStore,
    *,
    resolver: OwnerRevisionResolver,
) -> tuple[CitationBindingItem, ...]:
    """Return every recorded binding of one namespace with exactly one reported state each.

    This is the **readable per-state binding enumeration** a census counts: this leaf's closure read
    exposed as a countable enumeration over *all* recorded bindings rather than over one declared
    selected set. It is a read path over the recorded bindings, not a second store, and it is the
    same observation function the closure uses, so the two cannot disagree about what a state means.
    """

    targets = _target_facts(store)
    revisions = _revision_facts(store)
    bindings = load_recorded_bindings(store)
    ambiguous = _ambiguous_binding_ids(bindings)
    return tuple(
        _observe_one(
            binding, resolver=resolver, targets=targets, revisions=revisions, ambiguous=ambiguous
        )
        for binding in bindings
    )


def assemble_citation_closure(
    store: OpenedKnowledgeStore,
    request: CitationBindingClosureRequest,
    *,
    resolver: OwnerRevisionResolver,
) -> CitationBindingClosureResult:
    """Assemble one selected prose view's citation closure, or refuse at the declared bound.

    The bound check runs over the **declared selected set's** matching bindings before any item is
    emitted, and it refuses rather than truncating. The refusal is the shipped
    ``selection_incomplete`` carrying the bound reached, so the alternative -- a partial closure with
    a total that was never computed -- stays unrepresentable.
    """

    selected = set(selected_set_of(request))
    selected_keys = {selected_set_key(revision) for revision in selected}
    matching = tuple(
        binding
        for binding in load_recorded_bindings(store)
        if selected_set_key(binding.owner_revision) in selected_keys
    )
    if len(matching) > request.item_limit:
        return CitationBindingClosureResult(
            state="refused",
            repository_id=request.repository_id,
            refusal=selection_incomplete_refusal(
                item_count=len(matching), bound=request.item_limit, operation=CLOSURE_OPERATION
            ),
        )
    targets = _target_facts(store)
    revisions = _revision_facts(store)
    ambiguous = _ambiguous_binding_ids(matching)
    items = tuple(
        _observe_one(
            binding, resolver=resolver, targets=targets, revisions=revisions, ambiguous=ambiguous
        )
        for binding in sorted(matching, key=_binding_order_key)
    )
    return CitationBindingClosureResult(
        state="assembled",
        repository_id=request.repository_id,
        items=items,
        counts=_counts(len(matching), len(items), items),
        key_form_coverage=_coverage(matching),
        limitations=_limitations(items, _coverage(matching)),
    )


# ---------------------------------------------------------------------------
# The observation of one binding, through the one observation function.


def _observe_one(
    binding: RecordedBinding,
    *,
    resolver: OwnerRevisionResolver,
    targets: Mapping[str, tuple[str, str, str, str]],
    revisions: Mapping[str, str],
    ambiguous: frozenset[str],
) -> CitationBindingItem:
    """Observe one recorded binding and build the indivisible item it becomes."""

    state, detail = observe_binding(
        binding,
        resolver=resolver,
        target_kinds={record_id: facts[0] for record_id, facts in targets.items()},
        target_revisions=revisions,
        ambiguous=ambiguous,
    )
    target_facts = targets.get(binding.target.record_id)
    observation = CitationBindingObservation(
        binding_id=binding.binding_id,
        owner_revision=binding.owner_revision,
        local_key=binding.local_key,
        target=_reported_target(binding.target, target_facts, state),
        governing_route_id=binding.governing_route_id,
        asserted_by_ref=binding.asserted_by_ref,
        state=state,
        detail=detail,
    )
    return CitationBindingItem(
        observation=observation,
        target_kind=None if target_facts is None else target_facts[0],
        target_lifecycle=None if target_facts is None else target_facts[2],
        target_authority_home=None if target_facts is None else target_facts[1],
        target_record_schema=None if target_facts is None else target_facts[3],
    )


def _reported_target(
    target: CitationTargetReference,
    facts: tuple[str, str, str, str] | None,
    state: str,
) -> CitationTargetReference:
    """Return the target reference with the state its own identity earned, and nothing else changed.

    The reference's ``state`` reports what the envelope holds; the observation's ``state`` reports
    what the whole binding observed. They are two facts and neither is derived from the other, which
    is why a target that exists under the right kind still reports ``resolved`` on a binding whose
    locator this increment cannot resolve.
    """

    if state == "target_record_absent" or facts is None:
        return target.model_copy(update={"state": "record_absent"})
    if state == "target_kind_mismatch" or facts[0] != target.kind:
        return target.model_copy(update={"state": "kind_mismatch"})
    if state == "target_revision_absent":
        return target.model_copy(update={"state": "revision_absent"})
    return target


# ---------------------------------------------------------------------------
# The stored facts one closure resolves against, read once.


def _target_facts(store: OpenedKnowledgeStore) -> dict[str, tuple[str, str, str, str]]:
    """Return every target record's `(kind, authority_home, lifecycle, record_schema)` by identity."""

    return {
        str(row[0]): (str(row[1]), str(row[2]), str(row[3]), str(row[4]))
        for row in store.connection.execute(_TARGET_RECORDS, (store.repository_id,))
    }


def _revision_facts(store: OpenedKnowledgeStore) -> dict[str, str]:
    """Return every stored revision's owning record identity, by revision identity."""

    return {
        str(row[0]): str(row[1])
        for row in store.connection.execute(_TARGET_REVISIONS, (store.repository_id,))
    }


def _sealed_payload(store: OpenedKnowledgeStore, binding_id: str) -> Mapping[str, Any]:
    """Return one binding's sealed revision payload, decoded from the recorded JSON text."""

    rows = tuple(store.connection.execute(_REVISION_PAYLOAD, (store.repository_id, binding_id)))
    if not rows:
        raise _BindingStoreDamage(binding_id)
    decoded = decoded_json(str(rows[0][0]))
    if not isinstance(decoded, Mapping):
        raise _BindingStoreDamage(binding_id)
    return decoded


class _BindingStoreDamage(Exception):
    """A binding row whose sealed revision is missing or is not a JSON object.

    This is a store defect rather than an expected outcome, and it is raised rather than folded into
    a reported state: reporting it as "the target is missing" would answer a question about the store
    with a claim about the binding, and skipping the row would silently drop a recorded attribution
    out of a denominator -- the exact failure this leaf exists to make impossible.
    """

    def __init__(self, binding_id: str) -> None:
        super().__init__(f"citation binding {binding_id} has no readable sealed revision payload")
        self.binding_id = binding_id


def _recorded_binding(row: Sequence[Any], payload: Mapping[str, Any]) -> RecordedBinding:
    """Decode one stored binding row plus its sealed payload into the recorded binding value."""

    owner = ProseOwnerRevision.model_validate(payload["owner_revision"])
    form = str(row[4])
    local_key: ProseCitationKey | TableRowKeyForm
    if form == "prose_cit_body":
        local_key = ProseCitationKey.model_validate(payload["local_key"])
    else:
        local_key = TableRowKeyForm.model_validate(payload["local_key"])
    target = CitationTargetReference(
        record_id=str(row[6]),
        kind=str(row[7]),
        revision_id=None if row[8] is None else str(row[8]),
        locator=_locator(payload),
    )
    return RecordedBinding(
        binding_id=str(row[1]),
        owner_revision=owner,
        local_key=local_key,
        target=target,
        governing_route_id=None if row[10] is None else str(row[10]),
        asserted_by_ref=str(payload.get("asserted_by_ref", "")),
        row_digest=binding_row_digest(row),
    )


def _locator(payload: Mapping[str, Any]) -> SourceLocator:
    """Return the typed locator one sealed binding payload carries, as the shipped union."""

    return _LOCATOR_ADAPTER.validate_python(payload["target"]["locator"])


# ---------------------------------------------------------------------------
# Counts, coverage and limitations.


def _counts(
    selected: int, returned: int, items: Sequence[CitationBindingItem]
) -> CitationBindingCounts:
    """Return the closure's counts, with every declared state present including the zero ones."""

    by_state: dict[str, int] = {state: 0 for state in BINDING_STATES}
    for item in items:
        by_state[item.observation.state] += 1
    resolved = by_state["exact_recorded_blob"]
    return CitationBindingCounts(
        selected=selected,
        returned=returned,
        remaining=selected - returned,
        resolved=resolved,
        unresolved=selected - resolved,
        stale=by_state["recorded_blob_mismatch"],
        by_state=by_state,
    )


def _coverage(bindings: Sequence[RecordedBinding]) -> KeyFormCoverage:
    """Return the declared key-form coverage of one observation, with every form present."""

    counts = key_forms_of(bindings)
    return KeyFormCoverage(
        covered_forms=tuple(COVERED_KEY_FORMS),
        uncovered_counts={
            form: counts.get(form, 0) for form in KEY_FORMS if form not in COVERED_KEY_FORMS
        },
    )


def _limitations(
    items: Sequence[CitationBindingItem], coverage: KeyFormCoverage
) -> tuple[CitationBindingLimitation, ...]:
    """Return the limitations one closure states, derived from what it actually observed.

    Each is a statement about what the closure cannot claim, and each is derived from the recorded
    data rather than asserted unconditionally, so a limitation that is present says something:

    * ``unresolved_keys_present`` when at least one observed binding is not resolved;
    * ``stale_bindings_present`` when the owner revision resolved and the recorded key is no longer
      in it -- stale is its own limitation because a stale binding is *readable and attributed*,
      which is a different thing from an unresolved one;
    * ``partial_key_form_coverage`` when a declared key form was not read;
    * ``no_semantic_completeness_claim`` always, so the absence of a completeness field is declared
      rather than inferred from a missing one.
    """

    found: list[CitationBindingLimitation] = []
    if any(item.observation.state != "exact_recorded_blob" for item in items):
        found.append("unresolved_keys_present")
    if any(item.observation.state == "recorded_blob_mismatch" for item in items):
        found.append("stale_bindings_present")
    if coverage.partial():
        found.append("partial_key_form_coverage")
    found.append(NO_SEMANTIC_COMPLETENESS_LIMITATION)
    return tuple(found)


def _ambiguous_binding_ids(bindings: Sequence[RecordedBinding]) -> frozenset[str]:
    """Return the binding identities that share a key with another binding in one owner revision.

    The recorded text is what is compared, because the recorded text *is* the fact at issue: two
    bindings that record the same key for the same owner revision claim the same sentence cited two
    different records, and picking either one would be a fabricated claim about what the prose meant.
    The stored `UNIQUE` key makes this state deliberately constructible only by an unreviewed write,
    which is why it is reported rather than resolved.
    """

    seen: dict[tuple[str, str, str, str, str], list[str]] = {}
    for binding in bindings:
        key = (
            *selected_set_key(binding.owner_revision),
            binding.local_key.form,
            _key_text(binding),
        )
        seen.setdefault(key, []).append(binding.binding_id)
    return frozenset(
        binding_id for grouped in seen.values() if len(grouped) > 1 for binding_id in grouped
    )


def _key_text(binding: RecordedBinding) -> str:
    """Return the recorded key text one binding is uniquely keyed by."""

    return render_local_key(binding.local_key)


def _binding_order_key(binding: RecordedBinding) -> tuple[str, str, str, str]:
    """Return the declared deterministic order key of one binding.

    Ordering uses declared priorities and stable identities only: the owner revision's own recorded
    identity, the key form, the recorded key text, then the binding identity. Nothing here invents
    semantic importance from a symbol name, and no mechanical score is presented as assessed risk.
    """

    return (
        binding.owner_revision.document_path,
        binding.owner_revision.blob_object_id,
        binding.local_key.form,
        binding.binding_id,
    )


def refusal_result(
    request: CitationBindingClosureRequest, refusal_value: KnowledgeRefusal
) -> CitationBindingClosureResult:
    """Build the result of a closure that refused, reporting no item and no total."""

    return CitationBindingClosureResult(
        state="refused", repository_id=request.repository_id, refusal=refusal_value
    )
