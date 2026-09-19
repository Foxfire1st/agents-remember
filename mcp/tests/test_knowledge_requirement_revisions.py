"""``RequirementRevision``: the record group's own boundary -- kind, payload, state and lineage.

These cases protect ``KS-R19@v1``'s record group at the envelope the substrate already has. They
occupy the ``unit-regression`` lane because what they measure is a typed record's own construction
and storage boundary -- which payload is admissible for the kind, which state pair is consistent,
which lineage edge is refused, and which route is authored once -- and not a process, a publication
or a Git object. The task-plane reference contract beside them is
``test_knowledge_requirement_reference_contract.py``.

Each case is named for the property its own assertions measure and says which failure it catches: a
payload carrying task authority, a promotion in place, a blank explanation, an authorship claimed
twice, a self-predecessor that left half a batch behind, and a predecessor that belongs to a
neighbouring record.

The forbidden task-authority set is asserted as an **absence** at both planes, because that is the
packet's own enforcement style: the payload model refuses an undeclared field through its ordinary
``extra="forbid"`` rule -- there is no named denylist to relax -- and no column of any registered
generation carries one of the names. A case that asserted only the validator half would leave the
schema free to hold the value later.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any, get_args
from uuid import uuid4

import pytest
from agents_remember.kernel.canonical_json import decoded_json
from agents_remember.memory.knowledge.facet_records import (
    RecordRevisionDraft,
    record_revision_digest,
)
from agents_remember.memory.knowledge.record_envelope import (
    KIND_SCHEMAS,
    PAYLOAD_MODELS,
    validate_record_payload,
)
from agents_remember.memory.knowledge.requirement_records import (
    decode_requirement_revision_row,
    requirement_revision_row,
)
from agents_remember.memory.knowledge.requirements import (
    read_requirement_revisions,
    record_requirement_revision,
)
from agents_remember.memory.knowledge.store import (
    OpenedKnowledgeStore,
    open_existing_knowledge_store,
)
from agents_remember.models.knowledge.requirement import (
    REQUIREMENT_REVISION_KIND,
    REQUIREMENT_REVISION_SCHEMA,
    RequirementOwnerRef,
    RequirementPredecessorChainView,
    RequirementRevisionPayload,
    RequirementRevisionRequest,
    RequirementRevisionResult,
    RequirementRevisionScope,
)
from agents_remember.models.knowledge.result import KnowledgeOperation, KnowledgeRefusal
from generation_test_support import create_current_generation_store
from knowledge_fixture_test_support import make_authorship

# The five forbidden names ``KS-R19@v1`` Example 5 measures, plus the fields that would make a
# stored value into the operative obligation. Both lists are *probes*: a case asserts each is
# refused, so the test cannot pass by there being no probe.
FORBIDDEN_TASK_AUTHORITY_FIELDS: tuple[str, ...] = (
    "task_status",
    "seat_owner",
    "lifecycle_gate",
    "implemented",
    "approved_by_substrate",
)

OPERATIVE_OBLIGATION_FIELDS: tuple[str, ...] = (
    "obligation_text",
    "requirement_text",
    "title",
    "display_version",
)

OWNER_PATH = "requirements/KS-R19-v1-requirement-revision-as-substrate-record.md"


def _store(tmp_path: Path) -> OpenedKnowledgeStore:
    """Return a current-generation knowledge store bound to one repository identity."""

    return create_current_generation_store(tmp_path / "knowledge.sqlite", str(uuid4()))


def _owner(
    path: str = OWNER_PATH, stable_id: str = "KS-R19", version: str = "v1"
) -> RequirementOwnerRef:
    return RequirementOwnerRef(path=path, stableId=stable_id, version=version)


def _payload(**overrides: Any) -> dict[str, Any]:
    """Return one admissible requirement payload mapping, with named fields replaced."""

    payload: dict[str, Any] = {
        "requirement_kind": REQUIREMENT_REVISION_KIND,
        "owner": _owner().model_dump(mode="json"),
        "owner_resolution": {"state": "resolved"},
        "explanation": (
            "The obligation gives requirement meaning a database home while the packet stays the "
            "one canonical owner, so no second task authority is created."
        ),
        "state_at_origin": "proposed",
        "acceptance_ref": None,
    }
    payload.update(overrides)
    return payload


def _request(
    store: OpenedKnowledgeStore,
    record_id: str,
    revision_id: str,
    *,
    predecessor_revision_id: str | None = None,
    governing_route_id: str | None = None,
    **payload_overrides: Any,
) -> RequirementRevisionRequest:
    return RequirementRevisionRequest(
        repository_id=store.repository_id,
        provenance=make_authorship(),
        record_id=record_id,
        revision_id=revision_id,
        payload=RequirementRevisionPayload.model_validate(_payload(**payload_overrides)),
        governing_route_id=governing_route_id,
        predecessor_revision_id=predecessor_revision_id,
    )


def _refusal_of(result: RequirementRevisionResult) -> KnowledgeRefusal:
    """Return one receipt's refusal, asserting that the receipt carries one.

    The receipt models ``refusal`` as optional because a served receipt has none; a case that reads a
    refusal is a case that just asserted one, and this keeps that assertion in one place.
    """

    assert result.refusal is not None
    return result.refusal


def _scope_of(result: RequirementRevisionResult) -> RequirementRevisionScope:
    """Return one receipt's derived scope, asserting that the receipt carries one."""

    assert result.scope is not None
    return result.scope


def _chain_of(result: RequirementRevisionResult) -> RequirementPredecessorChainView:
    """Return one receipt's predecessor-chain view, asserting that the receipt carries one."""

    chain = _scope_of(result).predecessor_chain
    assert chain is not None
    return chain


def _payload_refusal(kind: str, schema: str, payload: Mapping[str, Any]) -> KnowledgeRefusal:
    """Validate one payload through the seam and return the refusal it must have produced."""

    outcome = validate_record_payload(kind, schema, payload)
    assert isinstance(outcome, KnowledgeRefusal)
    return outcome


def _stored_payload_bytes(store: OpenedKnowledgeStore, revision_id: str) -> str:
    rows = tuple(
        store.connection.execute(
            "SELECT payload FROM record_revision WHERE repository_id = ? AND revision_id = ?",
            (store.repository_id, revision_id),
        )
    )
    assert rows, f"revision {revision_id} is not stored"
    return str(rows[0][0])


# ---------------------------------------------------------------------------
# The kind and its registry entry.


def test_the_requirement_kind_resolves_only_to_its_own_declared_pair() -> None:
    """A kind's admissible shapes are the registered pair, not "any schema that parses".

    Catches a record written under a schema the registry never admitted for its kind -- the failure
    that would let a second revision aggregate share this kind's identity.
    """

    pair = (REQUIREMENT_REVISION_KIND, REQUIREMENT_REVISION_SCHEMA)
    assert pair in PAYLOAD_MODELS
    assert PAYLOAD_MODELS[pair] is RequirementRevisionPayload
    assert KIND_SCHEMAS[REQUIREMENT_REVISION_KIND] == frozenset({REQUIREMENT_REVISION_SCHEMA})

    wrong_schema = validate_record_payload(
        REQUIREMENT_REVISION_KIND, "requirement-revision/v2", _payload()
    )
    assert isinstance(wrong_schema, KnowledgeRefusal)
    assert wrong_schema.code == "invalid_payload"
    assert wrong_schema.expected == REQUIREMENT_REVISION_SCHEMA

    unknown_kind = validate_record_payload(
        "requirement_obligation", REQUIREMENT_REVISION_SCHEMA, {}
    )
    assert isinstance(unknown_kind, KnowledgeRefusal)
    assert unknown_kind.code == "invalid_payload"
    assert unknown_kind.observed == "requirement_obligation"


def test_the_record_group_declares_two_operations_beside_the_shipped_vocabulary() -> None:
    """Both operations this record group performs are members of the shipped vocabulary.

    Membership rather than an exact set, so a sibling leaf appending its own operation beside these
    does not falsify the case; what it catches is an operation name a refusal could carry but the
    vocabulary does not admit.
    """

    operations = set(get_args(KnowledgeOperation))
    assert {"record_requirement_revision", "read_requirement_revisions"} <= operations


def test_an_accepted_origin_state_without_an_acceptance_reference_is_refused() -> None:
    """``accepted`` requires a nonempty ``acceptance_ref``, at construction.

    Catches a record claiming an acceptance nobody recorded.
    """

    with pytest.raises(ValueError, match="nonempty acceptance_ref"):
        RequirementRevisionPayload.model_validate(_payload(state_at_origin="accepted"))


def test_a_proposed_origin_state_carrying_an_acceptance_reference_is_refused() -> None:
    """A proposed revision must not carry an ``acceptance_ref``, at construction.

    Catches a proposed record claiming an acceptance that never happened.
    """

    with pytest.raises(ValueError, match="must not carry an acceptance_ref"):
        RequirementRevisionPayload.model_validate(
            _payload(state_at_origin="proposed", acceptance_ref="accepted at /somewhere")
        )


def test_a_promotion_attempt_is_refused_and_no_stored_byte_changes(tmp_path: Path) -> None:
    """Re-recording a stored revision as accepted is refused with ``promotion_not_supported``.

    Catches promotion in place: the exact "no promotion operation exists" property, measured as the
    stored payload being byte-identical before and after the attempt.
    """

    store = _store(tmp_path)
    record_id, revision_id = str(uuid4()), str(uuid4())
    recorded = record_requirement_revision(store, _request(store, record_id, revision_id))
    assert recorded.state == "recorded"
    before = _stored_payload_bytes(store, revision_id)

    promoted = record_requirement_revision(
        store,
        _request(
            store,
            record_id,
            revision_id,
            state_at_origin="accepted",
            acceptance_ref="the owner's recorded acceptance",
        ),
    )
    assert promoted.state == "refused"
    assert _refusal_of(promoted).code == "promotion_not_supported"
    assert _refusal_of(promoted).observed == "accepted"
    assert "owner" in _refusal_of(promoted).next_action
    assert _stored_payload_bytes(store, revision_id) == before


def test_recording_an_accepted_revision_stores_the_state_and_produces_no_approval(
    tmp_path: Path,
) -> None:
    """The owner's acceptance is recorded as data; the substrate mints nothing.

    Catches the substrate granting an approval: the payload's state is ``accepted`` with the owner's
    own reference, while the envelope's lifecycle stays ``proposed`` -- this record group added no
    approval gate and moved no approval state.
    """

    store = _store(tmp_path)
    record_id, revision_id = str(uuid4()), str(uuid4())
    result = record_requirement_revision(
        store,
        _request(
            store,
            record_id,
            revision_id,
            state_at_origin="accepted",
            acceptance_ref="requirements/README.md cold-read verdict row",
        ),
    )
    assert result.state == "recorded"
    assert result.scope is not None
    assert _scope_of(result).revisions[0].state_at_origin == "accepted"
    assert _scope_of(result).revisions[0].acceptance_ref == (
        "requirements/README.md cold-read verdict row"
    )
    rows = tuple(
        store.connection.execute(
            "SELECT lifecycle FROM knowledge_record WHERE repository_id = ? AND record_id = ?",
            (store.repository_id, record_id),
        )
    )
    assert [str(row[0]) for row in rows] == ["proposed"]


@pytest.mark.parametrize("explanation", ["", "   ", None])
def test_an_empty_or_absent_explanation_is_refused(explanation: object) -> None:
    """A pointer without meaning is not this record (requirement 5.4).

    Catches a stored revision that adds no meaning the owner did not already have.
    """

    with pytest.raises(ValueError):
        RequirementRevisionPayload.model_validate(_payload(explanation=explanation))


@pytest.mark.parametrize("field", ["actor_ref", "authorization_ref", "operation_id", "recorded_at"])
def test_no_payload_field_can_substitute_for_the_revisions_authorship(field: str) -> None:
    """The explanation's authorship is the revision's provenance, not a payload field.

    Catches a second, competing statement of one fact: two authors named for one revision, with no
    rule deciding which is the record's.
    """

    refusal = _payload_refusal(
        REQUIREMENT_REVISION_KIND,
        REQUIREMENT_REVISION_SCHEMA,
        _payload(**{field: "someone else"}),
    )
    assert refusal.code == "invalid_payload"
    assert field in refusal.detail


def test_the_stored_revision_carries_the_authorship_it_was_authored_under(tmp_path: Path) -> None:
    """The provenance the write received is the provenance the read serves.

    Catches a store that manufactured an author, or an import that replaced the envelope with its
    own: the actor, authority, operation and recorded time survive a reopen unchanged.
    """

    store = _store(tmp_path)
    authorship = make_authorship(actor_ref="agent:l19-worker")
    request = RequirementRevisionRequest(
        repository_id=store.repository_id,
        provenance=authorship,
        record_id=str(uuid4()),
        revision_id=str(uuid4()),
        payload=RequirementRevisionPayload.model_validate(_payload()),
    )
    assert record_requirement_revision(store, request).state == "recorded"

    store.close()
    reopened = open_existing_knowledge_store(store.database_path, store.repository_id)
    served = read_requirement_revisions(reopened, request.record_id)
    assert served.state == "read"
    assert served.scope is not None
    assert _scope_of(served).revisions[0].provenance == authorship


def test_a_successor_records_its_predecessor_and_leaves_it_byte_identical(tmp_path: Path) -> None:
    """A change is a new revision; the predecessor is not edited.

    Catches an in-place edit: the v1 payload is byte-identical after v2 is recorded, and v2 names v1.
    """

    store = _store(tmp_path)
    record_id, first, second = str(uuid4()), str(uuid4()), str(uuid4())
    assert record_requirement_revision(store, _request(store, record_id, first)).state == "recorded"
    before = _stored_payload_bytes(store, first)

    successor = record_requirement_revision(
        store,
        _request(
            store,
            record_id,
            second,
            predecessor_revision_id=first,
            explanation="The owner published a successor version, so this revision records it.",
        ),
    )
    assert successor.state == "recorded"
    assert _stored_payload_bytes(store, first) == before
    assert successor.scope is not None
    # The head is the revision nothing names as its predecessor -- the successor. The chain is the
    # predecessor walk below it, so its one entry is the revision the successor named.
    chain = _chain_of(successor)
    assert chain.head_revision_id == second
    assert chain.chain == (first,)
    assert _scope_of(successor).current_state.head_revision_ids == (second,)
    by_id = {view.revision_id: view for view in _scope_of(successor).revisions}
    assert by_id[second].predecessor_revision_id == first
    assert by_id[first].predecessor_revision_id is None


def test_a_self_predecessor_is_refused_as_a_cycle_and_writes_nothing(tmp_path: Path) -> None:
    """A revision does not become its own predecessor, and the whole transaction rolls back.

    Catches partial lineage: the refusal names the revision, and neither the record nor the revision
    is stored afterwards.
    """

    store = _store(tmp_path)
    record_id, revision_id = str(uuid4()), str(uuid4())
    refused = record_requirement_revision(
        store, _request(store, record_id, revision_id, predecessor_revision_id=revision_id)
    )
    assert refused.state == "refused"
    assert _refusal_of(refused).code == "lineage_cycle"
    assert _refusal_of(refused).observed == revision_id
    stored = tuple(
        store.connection.execute(
            "SELECT record_id FROM knowledge_record WHERE repository_id = ?", (store.repository_id,)
        )
    )
    assert stored == ()


def test_a_predecessor_belonging_to_another_record_is_refused(tmp_path: Path) -> None:
    """A predecessor is a revision of *this* record, never of a neighbouring one.

    Catches a cross-record successor graph: a chain that silently spanned two obligations.
    """

    store = _store(tmp_path)
    other_record, other_revision = str(uuid4()), str(uuid4())
    assert (
        record_requirement_revision(store, _request(store, other_record, other_revision)).state
        == "recorded"
    )

    refused = record_requirement_revision(
        store,
        _request(store, str(uuid4()), str(uuid4()), predecessor_revision_id=other_revision),
    )
    assert refused.state == "refused"
    assert _refusal_of(refused).code == "invalid_reference"
    assert _refusal_of(refused).observed == other_revision


def test_a_dangling_predecessor_is_refused(tmp_path: Path) -> None:
    """A predecessor nothing stores is refused, not accepted as a root.

    Catches a chain that silently loses its head.
    """

    store = _store(tmp_path)
    refused = record_requirement_revision(
        store, _request(store, str(uuid4()), str(uuid4()), predecessor_revision_id=str(uuid4()))
    )
    assert refused.state == "refused"
    assert _refusal_of(refused).code == "missing_expected_row"


def test_a_stored_cycle_refuses_a_descendant_and_names_the_cycle(tmp_path: Path) -> None:
    """The shared rule's second branch: a revision below a stored cycle is refused.

    Catches a write path that judges only adjacency, letting a new successor inherit a cycle its
    ancestors already formed outside the operation.
    """

    store = _store(tmp_path)
    record_id, first, second = str(uuid4()), str(uuid4()), str(uuid4())
    assert record_requirement_revision(store, _request(store, record_id, first)).state == "recorded"
    assert (
        record_requirement_revision(
            store, _request(store, record_id, second, predecessor_revision_id=first)
        ).state
        == "recorded"
    )
    # Damage the store outside the operation, which is the only way a cycle can exist at all: the
    # trigger refuses an in-place rewrite, so the edge is forced past it here to reach the rule.
    store.connection.execute("DROP TRIGGER record_revision_no_rewrite")
    payload_text = _stored_payload_bytes(store, first)
    store.connection.execute(
        "UPDATE record_revision SET predecessor_revision_id = ? WHERE repository_id = ? AND "
        "revision_id = ?",
        (second, store.repository_id, first),
    )
    # The predecessor is inside the seal, so the forced edge is re-sealed: the case must reach the
    # lineage rule, not stop at a damaged-row report.
    store.connection.execute(
        "UPDATE record_revision SET content_digest = ? WHERE repository_id = ? AND revision_id = ?",
        (
            record_revision_digest(
                RecordRevisionDraft(
                    record_id=record_id,
                    revision_id=first,
                    record_schema=REQUIREMENT_REVISION_SCHEMA,
                    predecessor_revision_id=second,
                ),
                decoded_json(payload_text),
            ),
            store.repository_id,
            first,
        ),
    )

    refused = record_requirement_revision(
        store, _request(store, record_id, str(uuid4()), predecessor_revision_id=second)
    )
    assert refused.state == "refused"
    assert _refusal_of(refused).code == "lineage_cycle"
    assert first in str(_refusal_of(refused).observed) and second in str(
        _refusal_of(refused).observed
    )


def test_the_read_refuses_a_record_that_is_not_stored(tmp_path: Path) -> None:
    """A record nothing stores is refused, not answered with an empty scope.

    Catches a read that reported a missing obligation as an obligation with no revisions.
    """

    store = _store(tmp_path)
    refused = read_requirement_revisions(store, str(uuid4()))
    assert refused.state == "refused"
    assert _refusal_of(refused).code == "missing_expected_row"
    assert refused.scope is None


def test_a_request_addressed_to_another_namespace_is_refused(tmp_path: Path) -> None:
    """The bound namespace is the only one a write may address.

    Catches a record written into a repository identity the store is not bound to.
    """

    store = _store(tmp_path)
    request = RequirementRevisionRequest(
        repository_id=str(uuid4()),
        provenance=make_authorship(),
        record_id=str(uuid4()),
        revision_id=str(uuid4()),
        payload=RequirementRevisionPayload.model_validate(_payload()),
    )
    refused = record_requirement_revision(store, request)
    assert refused.state == "refused"
    assert _refusal_of(refused).code == "unauthorized_scope"


def test_the_revision_row_codec_round_trips_through_the_envelope_tuple() -> None:
    """One revision's stored tuple is the envelope's, with the seal over the payload it was given.

    Catches a record group that grew its own column tuple, or a seal computed over something other
    than the payload the revision stores.
    """

    payload = _payload()
    row = requirement_revision_row(
        "00000000-0000-0000-0000-0000000000cc",
        "00000000-0000-0000-0000-0000000000dd",
        payload,
        None,
        make_authorship(),
    )
    assert len(row) == 7
    decoded = decode_requirement_revision_row(
        (
            row[0],
            row[1],
            row[2],
            row[3],
            row[4],
            row[5],
            row[6],
        )
    )
    assert decoded.payload.explanation == payload["explanation"]
    assert decoded.content_digest == row[5]

    tampered = list(row)
    tampered[3] = str(row[3]).replace("database home", "database cellar")
    with pytest.raises(Exception, match="content digest"):
        decode_requirement_revision_row(tuple(tampered))
