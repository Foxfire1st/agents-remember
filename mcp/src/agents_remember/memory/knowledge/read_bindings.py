"""Observe one recorded citation binding: which fact it reports, out of one closed vocabulary.

A binding records five authored facts -- the prose owner revision, the local key as written, the
typed target reference, the locator, and its governing route -- and observing it reports **exactly
one** state from the closed vocabulary :data:`…models.knowledge.citation.BINDING_STATES`. Nothing is
dropped, nothing is repaired, and the recorded key is preserved on every state including the
failures, which is the shipped rule for a stored attribution
(``memory/knowledge/anchors.py``: a missing source is never a reason to retire one).

Three rules shape this module:

* **A shared fact reports the shipped literal, not a binding-local spelling.** Requirement 4.1a
  fixes that where the fact is one the shipped ``AnchorResolutionState`` already names, the reported
  value *is* that literal. The three coincidences this leaf uses are stated once, beside the code
  that produces them, and a case asserts each against ``ANCHOR_RESOLUTIONS`` so the identity is
  checked rather than asserted:

  - the key is present and the binding resolves -> ``exact_recorded_blob``
  - the key is not recorded in its owner revision -> ``recorded_blob_mismatch``. The owner revision
    *is* a recorded blob, and the test is whether the recorded key is in it; a key the recorded blob
    does not contain is a mismatch against the blob the binding recorded, which is what the literal
    says and what makes it the right shared literal rather than a coincidence of spelling.
  - the owner revision's bytes cannot be obtained -> ``recorded_object_unavailable``
  - the locator is one this increment cannot resolve -> ``unsupported_locator``

  The vocabulary is extended **one-directionally**: ``AnchorResolutionState`` gains no member, so an
  anchor resolution can never acquire a citation fact.

* **The observation order is the order the facts depend on, and it is declared.** The owner revision
  comes first, because a key cannot be looked for in bytes that were not obtained and a target cannot
  be resolved for a revision whose own identity is unknown. The key form follows, because a form the
  increment does not read is a fact about the *reader* rather than about the corpus, and reporting it
  before any lookup is what keeps it distinct from "the key is absent". The locator and the target
  follow, in that order, because the locator is a property of the record this leaf wrote while the
  target is a fact about the store it points into.

* **A key form this increment does not cover is a state, not a gap.** :data:`COVERED_KEY_FORMS`
  declares the ``cit:`` body alone, so a table-row key -- whose form is recorded and recognized --
  reports ``uncovered_key_form``. It is counted in the denominator exactly like every other
  unresolved state and it makes the view's coverage partial, which is what stops a partial coverage
  rendering like a complete one.

Nothing here re-parses the corpus, searches for a plausible target, relocates a moved line,
re-anchors a range, or consults a working tree or ``HEAD`` to fill a gap. The read path's precedent
is the shipped anchor reader's own refusal: *"no locator search and no line re-anchoring"*.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from agents_remember.memory.knowledge.read_owner_revisions import OwnerRevisionResolver
from agents_remember.models.knowledge.citation import (
    BINDING_STATES,
    COVERED_KEY_FORMS,
    KEY_FORMS,
    CitationBindingState,
    CitationTargetReference,
    LocalCitationKey,
    ProseCitationKey,
    ProseOwnerRevision,
    TableRowKeyForm,
    render_local_key,
)

__all__ = [
    "STATE_FACTS",
    "RecordedBinding",
    "observe_binding",
    "observe_target_reference",
]

# The fact each state reports, as one sentence a case can check the code's branch against. It is a
# declaration beside the branches rather than a second vocabulary: the *values* the observation
# returns are this module's own constants below, and four of them are members of the shipped
# ``ANCHOR_RESOLUTIONS``.
STATE_FACTS: Mapping[CitationBindingState, str] = {
    "exact_recorded_blob": "the key is present in its owner revision and the binding resolves",
    "recorded_blob_mismatch": "the key is not recorded in its owner revision",
    "recorded_object_unavailable": "the owner revision's bytes cannot be obtained",
    "unsupported_locator": "the locator is one this increment cannot resolve",
    "uncovered_key_form": "the key is present and was recognized, in a form this increment cannot read",
    "ambiguous_key": "more than one recorded binding claims this key in this owner revision",
    "target_record_absent": "the target record is not in the envelope",
    "target_revision_absent": "the target record holds no such revision",
    "target_kind_mismatch": "the target's recorded kind is not the kind the reference declares",
}


# The recorded-key text one prose key carries, spelled the way the document wrote it. A ``cit:`` body
# is stored as the text between the mark and its matching ``)``, so :func:`render_local_key` re-adds
# the mark -- which is how the *recorded* key, not the document's current text, becomes the string
# this module looks for.


@dataclass(frozen=True)
class RecordedBinding:
    """One stored binding row, decoded: what was recorded, and nothing the store did not hold.

    ``revision_payload`` is the sealed payload of the binding's own revision. It is carried so the
    locator travels as the typed shipped union rather than as a discriminator string that a second
    reader would have to re-hydrate; the columns that came from the row are kept flat because they
    are what the row's own unique key and foreign keys are declared over.
    """

    binding_id: str
    owner_revision: ProseOwnerRevision
    local_key: LocalCitationKey
    target: CitationTargetReference
    governing_route_id: str | None
    asserted_by_ref: str
    row_digest: str


def observe_binding(
    binding: RecordedBinding,
    *,
    resolver: OwnerRevisionResolver,
    target_kinds: Mapping[str, str],
    target_revisions: Mapping[str, str],
    ambiguous: frozenset[str] = frozenset(),
) -> tuple[CitationBindingState, str]:
    """Return the one state one recorded binding reports, and the recorded basis of that report.

    The ``detail`` returned is a *rendering of the recorded basis* -- the owner revision, the key as
    written and the state -- rather than authored prose, so a verdict cannot be written into it.
    """

    owner = binding.owner_revision
    state = _observe_owner_and_key(binding, resolver=resolver)
    if state is None and binding.binding_id in ambiguous:
        state = "ambiguous_key"
    if state is None:
        state = _observe_locator_and_target(
            binding, target_kinds=target_kinds, target_revisions=target_revisions
        )
    return state, _render_basis(owner, binding, state)


def _observe_owner_and_key(
    binding: RecordedBinding, *, resolver: OwnerRevisionResolver
) -> CitationBindingState | None:
    """Return the state the owner revision and the written key decide, or ``None`` to continue."""

    observation = resolver.observe(binding.owner_revision)
    if not observation.resolved:
        # The shipped literal for "the bytes this record names cannot be obtained". Reported before
        # anything is looked for inside them: a key cannot be missing from a revision that could not
        # be read, and saying so would be a claim about bytes nobody obtained.
        return "recorded_object_unavailable"
    if binding.local_key.form not in COVERED_KEY_FORMS:
        # The key is present and was recognized, in a form this increment declares it cannot read.
        # Reported as its own state rather than as an absence, so a form that was never read cannot
        # render as a form that was read and found empty.
        return "uncovered_key_form"
    recorded_bytes = resolver.read_recorded_bytes(binding.owner_revision)
    if recorded_bytes is None:
        return "recorded_object_unavailable"
    if render_local_key(binding.local_key).encode("utf-8") not in recorded_bytes:
        # The shipped literal for "the recorded blob does not hold this recorded text". The owner
        # revision is a recorded blob and the test is membership in it, which is the same fact the
        # literal names on the anchor side -- a recorded identity that does not match the recorded
        # bytes -- and it is why this is the shared literal rather than a binding-local spelling.
        return "recorded_blob_mismatch"
    return None


def _observe_locator_and_target(
    binding: RecordedBinding,
    *,
    target_kinds: Mapping[str, str],
    target_revisions: Mapping[str, str],
) -> CitationBindingState:
    """Return the state the locator and the typed target decide.

    The locator is checked first because it is a property of the record this leaf wrote: a locator no
    resolver supports is reported as such while the recorded locator stays readable, exactly as a
    stored symbol locator does today. The target facts follow, and each is a fact about the *store*
    rather than a verdict about the binding -- none of them deletes the binding or rewrites its
    attribution, and none is answered with a nearest-match substitution.
    """

    if binding.target.locator.kind == "symbol":
        return "unsupported_locator"
    record_id = binding.target.record_id
    if record_id not in target_kinds:
        return "target_record_absent"
    if target_kinds[record_id] != binding.target.kind:
        return "target_kind_mismatch"
    revision_id = binding.target.revision_id
    if revision_id is not None and target_revisions.get(revision_id) != record_id:
        return "target_revision_absent"
    return "exact_recorded_blob"


def observe_target_reference(
    target: CitationTargetReference, *, known: Mapping[str, str]
) -> tuple[CitationTargetReference, CitationBindingState]:
    """Return one target reference with the state its identity earns against the envelope.

    This is the target half of an observation, exposed separately because the projection rule needs
    it on its own: a projection displays an assessment with its author, its examined inputs and its
    status, and the status it displays is the *target reference's* state rather than a second,
    independently-derived one.
    """

    record_id = target.record_id
    if record_id not in known:
        return target.model_copy(update={"state": "record_absent"}), "target_record_absent"
    if known[record_id] != target.kind:
        return target.model_copy(update={"state": "kind_mismatch"}), "target_kind_mismatch"
    return target, "exact_recorded_blob"


def _render_basis(
    owner: ProseOwnerRevision, binding: RecordedBinding, state: CitationBindingState
) -> str:
    """Return the recorded basis of one observation, as the one admitted detail spelling."""

    return (
        f"the recorded key {render_local_key(binding.local_key)} for {owner.document_path} at "
        f"recorded blob {owner.blob_object_id} in {owner.repository}: {state}"
    )


def declared_state_members() -> tuple[str, ...]:
    """Return the closed vocabulary as a tuple, so a case can assert the two spellings agree."""

    return tuple(BINDING_STATES)


def key_forms_of(bindings: Sequence[RecordedBinding]) -> Mapping[str, int]:
    """Return one count per written key form present in a set of recorded bindings.

    A form that is present in no binding is reported with a zero rather than omitted, so a form the
    increment does not read is visible as *declared and unread* rather than absent from the report.
    """

    counts: dict[str, int] = {form: 0 for form in KEY_FORMS}
    for binding in bindings:
        counts[binding.local_key.form] = counts.get(binding.local_key.form, 0) + 1
    return counts


def decode_local_key(row: Any, payload: Mapping[str, Any]) -> LocalCitationKey:
    """Return one stored binding row's local key, from its own columns and its sealed payload."""

    form = str(row[4])
    if form == "prose_cit_body":
        return ProseCitationKey.model_validate(payload["local_key"])
    return TableRowKeyForm.model_validate(payload["local_key"])
