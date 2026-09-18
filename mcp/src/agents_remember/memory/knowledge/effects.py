"""The authored-effect record group's write path, its preconditions and its read.

This module is the *rules* the record group obeys, and every one of them is a rule the requirement
states as a refusal or as a state. The records themselves are envelope records written through the
candidate batch's one write path, so what this module adds is:

* **The shape check, applied where the requirement puts it.** The construction half is the payload
  models' own validators; the storage half is here. The label must be one of the nine admitted
  members and the declared counts must satisfy :func:`…models.knowledge.effect.cardinality_violation`
  -- and the refusal is built from *that* function rather than from a second copy of the predicate, so
  the two enforcement points cannot disagree about what is admitted. Every refusal this module raises
  before a row exists; the batch rolls the whole transaction back, so a refused claim leaves no record
  and no revision behind.
* **Reference resolution through the shared endpoint checks.** An effect claim's inputs and outputs,
  a member's change set, a change set's candidate realization claims and a change set's predecessors
  are all resolved by :mod:`…endpoints`, which is the one place a relation write says which endpoint
  kinds exist. A second resolution path beside it is what the shipped doctrine forbids, so none is
  written here.
* **One succession edge, written with its successor.** There is no standalone predecessor-append
  operation: the edge is inserted inside the successor's own creation batch, exactly as the shipped
  store rule states, and the graph the batch leaves is checked by the shared acyclic walk.
* **Nothing is derived.** No code path here computes, infers, suggests, ranks, defaults or repairs an
  effect label from a comparison, a text difference, a condition count or a source change, and no
  code path converts an unchanged file, row or revision into a preservation claim. Those acts are
  absent from the vocabulary rather than refused by it: the command union has no member that could
  carry one, and the payload models have no field one could land in.
* **A duplicate is a duplicate, and a disagreement is not.** Two claims declaring the same label and
  the same input and output reference sets for one change set are refused as ``duplicate_identity``;
  two differently labelled claims for one comparison are both stored, because that is the authored
  disagreement the design requires to stay visible.

One ordering rule is this record group's own and is stated where it is enforced: a command that
*cites* an identity the same batch also creates must appear **after** the command that creates it.
That is the shipped ``ChangeBatch`` contract -- commands are applied in the order given -- and a
citation that arrives first is refused as ``invalid_reference`` naming the identity and the remedy,
rather than left to a foreign key to report as an unnamed constraint failure.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from agents_remember.memory.knowledge import endpoints, lineage, routes
from agents_remember.memory.knowledge.effect_records import (
    CHANGE_SET_PREDECESSOR_EDGES,
    CHANGE_SET_PREDECESSOR_INSERT,
    EFFECT_RECORD_INSERT,
    EFFECT_REVISION_INSERT,
    EffectRecordDraft,
    change_set_predecessor_row,
    effect_record_row,
    effect_record_row_digest,
    effect_revision_row,
    stored_revisions_of_kind,
)
from agents_remember.memory.knowledge.effect_refusals import (
    EFFECT_OPERATION,
    duplicate_effect_claim_refusal,
    effect_cardinality_refusal,
    effect_label_refusal,
    succession_cycle_refusal,
)
from agents_remember.memory.knowledge.effect_views import effect_scope
from agents_remember.memory.knowledge.facet_records import record_revision_content_digest
from agents_remember.memory.knowledge.record_envelope import validate_record_payload
from agents_remember.memory.knowledge.refusals import (
    KnowledgeRefused,
    KnowledgeStorageError,
    RefusalFacts,
    generation_mismatch_refusal,
    missing_expected_row_refusal,
    refusal,
)
from agents_remember.memory.knowledge.schema_generations import GENERATION_8
from agents_remember.models.knowledge.authorship import PROPOSED_STATE, Authorship
from agents_remember.models.knowledge.candidate import (
    AddInvariantEffectClaim,
    AddPreservationClaim,
    AddSemanticChangeSet,
    AddUnresolvedQuestion,
    EffectCommand,
    RecordIdentity,
)
from agents_remember.models.knowledge.change_set import (
    SEMANTIC_CHANGE_SET_KIND,
    SEMANTIC_CHANGE_SET_SCHEMA,
    AuthoredEffectScope,
    EffectReadOperation,
    EffectReadResult,
)
from agents_remember.models.knowledge.effect import (
    ADMITTED_EFFECT_LABELS,
    INVARIANT_EFFECT_CLAIM_KIND,
    INVARIANT_EFFECT_CLAIM_SCHEMA,
    PRESERVATION_CLAIM_KIND,
    PRESERVATION_CLAIM_SCHEMA,
    UNRESOLVED_QUESTION_KIND,
    UNRESOLVED_QUESTION_SCHEMA,
    InvariantEffectClaimPayload,
    cardinality_rule_text,
    cardinality_violation,
)
from agents_remember.models.knowledge.result import KnowledgeOperation, KnowledgeRefusal

if TYPE_CHECKING:
    from agents_remember.memory.knowledge.store import OpenedKnowledgeStore

# The operation the refusals below belong to. Recording authored work is the candidate batch's act --
# the requirement places these records on that one write path and this group has no standalone
# operation beside it -- so a refusal raised here names the batch even before the batch restates it
# with the failing command's own position.
#
# The record group's own read, and the generation whose registry carries its succession table.
READ_OPERATION: EffectReadOperation = "read_effect_scope"
REQUIRED_EFFECT_GENERATION = GENERATION_8

# The record kind and the frozen shape each command writes. Declared as the command's own pair rather
# than read out of the payload, so a caller cannot store a pair the envelope's registry would refuse
# and a refusal can name both without having decoded anything.
_COMMAND_DECLARATION: Mapping[str, tuple[str, str]] = {
    "add_invariant_effect_claim": (INVARIANT_EFFECT_CLAIM_KIND, INVARIANT_EFFECT_CLAIM_SCHEMA),
    "add_preservation_claim": (PRESERVATION_CLAIM_KIND, PRESERVATION_CLAIM_SCHEMA),
    "add_unresolved_question": (UNRESOLVED_QUESTION_KIND, UNRESOLVED_QUESTION_SCHEMA),
    "add_semantic_change_set": (SEMANTIC_CHANGE_SET_KIND, SEMANTIC_CHANGE_SET_SCHEMA),
}


def apply_effect_command(
    store: OpenedKnowledgeStore, command: EffectCommand, authorship: Authorship
) -> tuple[RecordIdentity, ...]:
    """Apply one authored-effect command inside the caller's open transaction.

    Every refusal below is raised, not returned, so the transaction that carries the record and its
    revision is aborted whole -- a refused command leaves no envelope row, no revision and no
    succession edge behind, and an earlier command's rows in the same batch are rolled back with it.
    """

    require_proposed_origin(command)
    kind, record_schema = _COMMAND_DECLARATION[command.kind]
    require_governing_route(store, command.governing_route_id)
    draft = EffectRecordDraft(
        record_id=command.record_id,
        kind=kind,
        record_schema=record_schema,
        authority_home=_authority_home(store),
        lifecycle=command.state_at_origin,
        governing_route_id=command.governing_route_id,
    )
    payload = _admissible_payload(store, command, kind, record_schema)
    _write_record(store, command, draft, payload, authorship)
    if isinstance(command, AddSemanticChangeSet):
        _write_succession_edges(store, command, authorship)
    return _written_entries(store, command, draft, authorship)


def _admissible_payload(
    store: OpenedKnowledgeStore,
    command: EffectCommand,
    kind: str,
    record_schema: str,
) -> Mapping[str, Any]:
    """Resolve and resolve-check one payload, in that order, before any row is written.

    The envelope seam decides admissibility -- that is its whole contract -- and the reference checks
    run on the **validated** payload, so a reference is only ever resolved once the shape that carries
    it is known to be the shape the kind declares.
    """

    validated = validate_record_payload(
        kind,
        record_schema,
        command.payload,
        operation=EFFECT_OPERATION,
        record_id=command.revision_id,
    )
    if isinstance(validated, KnowledgeRefusal):
        raise KnowledgeRefused(validated)
    frozen = validated.model_dump(mode="json")
    require_payload_references(store, command, frozen)
    require_no_stored_duplicate(store, command, frozen)
    return frozen


def _write_record(
    store: OpenedKnowledgeStore,
    command: EffectCommand,
    draft: EffectRecordDraft,
    payload: Mapping[str, Any],
    authorship: Authorship,
) -> None:
    """Write the envelope row and its one sealed revision."""

    store.write(
        EFFECT_RECORD_INSERT,
        (store.repository_id, *effect_record_row(draft, authorship)),
    )
    store.write(
        EFFECT_REVISION_INSERT,
        (
            store.repository_id,
            *effect_revision_row(
                command.revision_id, command.record_id, draft.record_schema, payload, authorship
            ),
        ),
    )


def _write_succession_edges(
    store: OpenedKnowledgeStore, command: AddSemanticChangeSet, authorship: Authorship
) -> None:
    """Write the successor's declared successor-to-predecessor edges and refuse a cycle."""

    for predecessor in command.predecessor_change_set_ids:
        store.write(
            CHANGE_SET_PREDECESSOR_INSERT,
            (
                store.repository_id,
                *change_set_predecessor_row(command.record_id, predecessor, authorship),
            ),
        )
    if not command.predecessor_change_set_ids:
        return
    cycle = require_acyclic_successions(store, command.record_id)
    if cycle is not None:
        raise KnowledgeRefused(cycle)


def _written_entries(
    store: OpenedKnowledgeStore,
    command: EffectCommand,
    draft: EffectRecordDraft,
    authorship: Authorship,
) -> tuple[RecordIdentity, ...]:
    """Report the two rows one command wrote, with the digests the store computed for them."""

    return (
        _written(
            "knowledge_record",
            command.record_id,
            effect_record_row_digest(store.repository_id, draft, authorship),
        ),
        _written(
            "record_revision",
            command.revision_id,
            _stored_revision_digest(store, command.revision_id),
        ),
    )


# -- the preconditions ------------------------------------------------------------------------


def require_admitted_declaration(command: EffectCommand) -> None:
    """Refuse a declared effect claim this record group's shape rules do not admit.

    Two facts and no more: the label must be one of the nine admitted members, and the declared counts
    must satisfy the one cardinality rule. Anything else about the payload -- a missing field, an
    undeclared field, a value of the wrong type -- is deliberately left to the envelope seam, which is
    the one place a payload's admissibility is decided. Reading only the three declared values here is
    what keeps this a shape check: it inspects no content, no diff and no text.
    """

    if not isinstance(command, AddInvariantEffectClaim):
        return
    label = command.payload.get("effect")
    if isinstance(label, str) and label not in ADMITTED_EFFECT_LABELS:
        raise KnowledgeRefused(
            effect_label_refusal(EFFECT_OPERATION, record_id=command.revision_id, observed=label)
        )
    if not isinstance(label, str):
        return
    inputs = _declared_references(command.payload.get("inputs"))
    outputs = _declared_references(command.payload.get("outputs"))
    violation = cardinality_violation(label, inputs, outputs)
    if violation is None:
        return
    raise KnowledgeRefused(
        effect_cardinality_refusal(
            EFFECT_OPERATION,
            record_id=command.revision_id,
            observed=_observed_declaration(label, inputs, outputs),
            detail=violation,
            expected=cardinality_rule_text(label),
        )
    )


def _declared_references(value: object) -> tuple[str, ...]:
    """Return the reference set one declared payload field carries, or the empty set.

    A field that is absent, or that carries anything other than reference text, is not this check's
    refusal: the seam refuses it as an inadmissible payload, and reporting it here as a cardinality
    problem would name the wrong fact.
    """

    if not isinstance(value, list | tuple):
        return ()
    return tuple(item for item in value if isinstance(item, str))


def _observed_declaration(label: str, inputs: tuple[str, ...], outputs: tuple[str, ...]) -> str:
    """Render the declared label and the observed input and output counts, as a refusal's fact."""

    observed = f"effect={label!r} inputs={len(inputs)} outputs={len(outputs)}"
    shared = sorted(set(inputs) & set(outputs))
    if shared:
        observed = f"{observed} shared_reference={shared[0]!r}"
    return observed


def require_proposed_origin(command: EffectCommand) -> None:
    """Refuse a command that would store accepted origin data.

    A shared step rather than a batch-only check, because the same refusal is owed wherever the
    command is submitted. There is no promotion operation anywhere in this record group: acceptance
    belongs to the process that owns it, and every row this record group writes is the shipped
    ``proposed`` lifecycle state.
    """

    if command.state_at_origin == PROPOSED_STATE:
        return
    raise KnowledgeRefused(
        refusal(
            "promotion_not_supported",
            EFFECT_OPERATION,
            "the command would store an authored-effect record as accepted origin data, which this "
            "candidate-only operation never stores",
            facts=RefusalFacts(table="knowledge_record", record_id=command.record_id),
            next_action=(
                "Author the record as proposed. Acceptance is decided by the owner of that process, "
                "not by a candidate change batch, and no command of this record group promotes a "
                "proposal."
            ),
        )
    )


def require_effect_generation(
    store: OpenedKnowledgeStore, operation: KnowledgeOperation
) -> KnowledgeRefusal | None:
    """Refuse an authored-effect read or write against a dataset that predates its generation.

    The record group is registered by generation 8, which is where its succession table is declared.
    The dataset's **own** generation is read from the open store, so this compares against what the
    file declares rather than against what the build supports, and nothing is migrated, widened or
    written through.
    """

    observed = store.generation.user_version
    required = REQUIRED_EFFECT_GENERATION.user_version
    if observed >= required:
        return None
    return generation_mismatch_refusal(
        operation,
        "the authored-effect record group is registered by generation "
        f"{REQUIRED_EFFECT_GENERATION.schema_name} (user_version {required})",
        required=required,
        observed=observed,
    )


def require_effect_generation_or_raise(store: OpenedKnowledgeStore) -> None:
    """Refuse a batch command against a dataset whose generation predates the record group."""

    denied = require_effect_generation(store, EFFECT_OPERATION)
    if denied is not None:
        raise KnowledgeRefused(denied)


def require_payload_references(
    store: OpenedKnowledgeStore, command: EffectCommand, payload: Mapping[str, Any]
) -> None:
    """Resolve every reference one validated payload declares, through the shared endpoint checks.

    Four references reach this function and each is resolved against the table that owns it: an effect
    claim's inputs and outputs (exact stored revisions), a member's change set and a change set's
    predecessors (stored ``semantic_change_set`` records), and a change set's candidate realization
    claims (stored ``realization_claim`` rows). Assessment references and requirement-revision
    references are deliberately **not** resolved: they are stored verbatim and reported as unresolved,
    so a code path that resolved one here would be the second requirement authority this record group
    must not create.
    """

    if isinstance(command, AddInvariantEffectClaim):
        claim = payload
        references = (*_strings(claim.get("inputs")), *_strings(claim.get("outputs")))
        for reference in references:
            endpoints.require_effect_revision_endpoint(
                store, reference, command.revision_id, EFFECT_OPERATION
            )
        endpoints.require_change_set_endpoint(
            store, _string(claim.get("change_set_id")), command.revision_id, EFFECT_OPERATION
        )
        return
    if isinstance(command, AddPreservationClaim | AddUnresolvedQuestion):
        endpoints.require_change_set_endpoint(
            store, _string(payload.get("change_set_id")), command.revision_id, EFFECT_OPERATION
        )
        return
    for claim_id in _strings(payload.get("candidate_realization_claim_ids")):
        endpoints.require_realization_claim_endpoint(
            store, claim_id, command.revision_id, EFFECT_OPERATION
        )
    if command.record_id in command.predecessor_change_set_ids:
        raise KnowledgeRefused(succession_cycle_refusal(command.record_id, (command.record_id,)))
    for predecessor in command.predecessor_change_set_ids:
        endpoints.require_change_set_endpoint(
            store, predecessor, command.revision_id, EFFECT_OPERATION
        )


def _strings(value: object) -> tuple[str, ...]:
    """Return every string one validated payload field carries, or the empty tuple."""

    if not isinstance(value, list | tuple):
        return ()
    return tuple(item for item in value if isinstance(item, str))


def _string(value: object) -> str:
    """Return one validated payload field as text, or the empty string.

    A field that is absent cannot reach this function: the payload was validated against the kind's
    frozen model, which declares every reference field, so the fallback is unreachable in practice and
    exists only so the resolution call has one argument type.
    """

    return value if isinstance(value, str) else ""


def require_no_stored_duplicate(
    store: OpenedKnowledgeStore, command: EffectCommand, payload: Mapping[str, Any]
) -> None:
    """Refuse a claim that declares exactly what a stored claim of the same change set declares.

    The comparison is over the declared label and the two declared reference sets and nothing else, so
    two *differently labelled* claims for one comparison are two records and are both stored. The scan
    is bounded by the stored claims of the one change set the new claim names, and it reads only the
    frozen payload fields -- no content, no diff, no text.
    """

    if not isinstance(command, AddInvariantEffectClaim):
        return
    declaration = _declaration_of(payload)
    for stored in stored_revisions_of_kind(store, INVARIANT_EFFECT_CLAIM_KIND):
        candidate = stored.payload
        if not isinstance(candidate, InvariantEffectClaimPayload):
            continue  # pragma: no cover - the decoder validated it against the kind's own model
        if stored.record_id == command.record_id:
            continue
        if candidate.change_set_id != declaration[0]:
            continue
        if _declaration_of(candidate.model_dump(mode="json")) != declaration:
            continue
        raise KnowledgeRefused(
            duplicate_effect_claim_refusal(
                EFFECT_OPERATION,
                record_id=command.revision_id,
                observed=_observed_declaration(declaration[1], declaration[2], declaration[3]),
                detail=(
                    f"the stored claim {stored.record_id} declares {declaration[1]!r} with the same "
                    f"inputs and outputs for change set {declaration[0]}"
                ),
            )
        )


def _declaration_of(
    payload: Mapping[str, Any],
) -> tuple[str, str, tuple[str, ...], tuple[str, ...]]:
    """Return the four declared values a duplicate comparison reads, from a frozen payload."""

    change_set_id = _string(payload.get("change_set_id"))
    label = payload.get("effect")
    return (
        change_set_id,
        label if isinstance(label, str) else "",
        _strings(payload.get("inputs")),
        _strings(payload.get("outputs")),
    )


def require_acyclic_successions(
    store: OpenedKnowledgeStore, successor_change_set_id: str
) -> KnowledgeRefusal | None:
    """Refuse a succession graph that reaches itself, naming the change sets involved.

    The rule that decides is the shared one (:mod:`…lineage`), over this record group's own edge
    table: the walk is the same strongly-connected-component scan the two predecessor graphs and the
    decision supersession edge use, so no second cycle rule grows beside the first.
    """

    graph: dict[str, set[str]] = {}
    for child, parent in store.connection.execute(
        CHANGE_SET_PREDECESSOR_EDGES, (store.repository_id,)
    ):
        graph.setdefault(str(child), set()).add(str(parent))
        graph.setdefault(str(parent), set())
    cycle = lineage.cycle_vertices(graph)
    if not cycle:
        return None
    members = tuple(sorted(cycle))
    return succession_cycle_refusal(successor_change_set_id, members)


# -- the read ---------------------------------------------------------------------------------


def read_effect_scope(store: OpenedKnowledgeStore) -> EffectReadResult:
    """Serve this namespace's authored work as one derived scope, or return one typed refusal.

    The scope is built by :mod:`…effect_views` from the rows this module's readers hand it, and it is
    derived in full: nothing it reports is stored as a view, so deleting it changes no claim, no
    question and no change set, and reading it twice over unchanged rows reproduces it byte for byte.
    """

    denied = require_effect_generation(store, READ_OPERATION)
    if denied is not None:
        return EffectReadResult(
            state="refused",
            operation=READ_OPERATION,
            repository_id=store.repository_id,
            refusal=denied,
        )
    return EffectReadResult(
        state="read",
        operation=READ_OPERATION,
        repository_id=store.repository_id,
        scope=build_effect_scope(store),
    )


def build_effect_scope(store: OpenedKnowledgeStore) -> AuthoredEffectScope:
    """Build the derived authored-effect scope for one open store.

    The builder lives in :mod:`…effect_views` so this module stays the write path plus its two entry
    points, and it is a pure function of the rows the store holds.
    """

    return effect_scope(store)


def require_governing_route(store: OpenedKnowledgeStore, route_id: str | None) -> None:
    """Refuse a record whose declared governing route is not authored in this repository.

    An ungoverned record is the explicit ``None`` state and is never refused; a *named* route that
    does not exist is a dangling reference and is refused rather than stored.
    """

    if route_id is None:
        return
    if routes.route_exists(store.connection, store.repository_id, route_id):
        return
    raise KnowledgeRefused(
        missing_expected_row_refusal(operation=EFFECT_OPERATION, table="route", record_id=route_id)
    )


def _authority_home(store: OpenedKnowledgeStore) -> str:
    """Return the authority home the bound repository declares.

    A record's ``authority_home`` is a fact about the namespace it was written into, not a field a
    caller authors: the destination resolved that namespace, so the record inherits it.
    """

    repository = store.get_repository()
    if repository is None:  # pragma: no cover - an unbound store cannot reach a write
        raise KnowledgeStorageError(
            f"the store is not bound to repository namespace {store.repository_id}"
        )
    return repository.authority_home


def _written(table: str, record_id: str, digest: str) -> RecordIdentity:
    """Build one receipt entry without re-validating it: the store computed every field here."""

    return RecordIdentity.model_construct(
        state="written", table=table, record_id=record_id, digest=digest
    )


def _stored_revision_digest(store: OpenedKnowledgeStore, revision_id: str) -> str:
    """Return one just-written revision's seal, read back from the row that carries it."""

    digest = record_revision_content_digest(store, revision_id)
    if digest is None:  # pragma: no cover - the insert above either wrote the row or raised
        raise KnowledgeStorageError(f"authored-effect revision {revision_id} was not stored")
    return digest
