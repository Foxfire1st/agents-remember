"""``RequirementRevision``: the reference contract with the task plane, and the derived views.

These cases protect ``KS-R19@v1``'s reason to exist -- reference, never replacement -- and the
disposability of everything this record group makes readable. They occupy the ``unit-regression``
lane because what they measure is which three components are stored, whose refusal is carried when
the owner cannot resolve a reference, and whether a view can be rebuilt from the stored rows alone.

The owner cases are measured against the owner's **real** resolver over a real task root, so the
codes asserted are the codes the task plane returns rather than string literals this leaf chose:
``task-intent-requirement-packet-missing``, ``task-intent-requirement-packet-outside-task`` (both
the non-Markdown target and the path outside the task root) and
``task-intent-requirement-packet-version-mismatch``.

Each case is named for the property its own assertions measure and says which failure it catches: a
second addressing scheme, a second resolver, a reference rewritten by a resolution, a reference
converted into a second requirement authority, a route inferred from the packet's location, a
currentness fact that picked a winner, and a derived view that could not be rebuilt.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from agents_remember.kernel.canonical_json import canonical_json_bytes
from agents_remember.memory.knowledge import routes
from agents_remember.memory.knowledge.facet_records import (
    RecordRevisionDraft,
    record_revision_digest,
)
from agents_remember.memory.knowledge.record_envelope import (
    validate_record_payload,
)
from agents_remember.memory.knowledge.refusals import KnowledgeStorageError
from agents_remember.memory.knowledge.requirement_owner import consume_owner_resolution
from agents_remember.memory.knowledge.requirement_records import (
    StoredRequirementRecord,
    StoredRequirementRevision,
    decode_requirement_revision_row,
)
from agents_remember.memory.knowledge.requirement_views import (
    CHAIN_BOUND,
    currentness_fact,
    head_revision_ids,
    revision_scope,
)
from agents_remember.memory.knowledge.requirements import (
    read_requirement_revisions,
    record_requirement_revision,
    resolve_requirement_reference,
)
from agents_remember.memory.knowledge.routes import RouteDraft
from agents_remember.memory.knowledge.schema_generations import GENERATIONS
from agents_remember.memory.knowledge.store import (
    OpenedKnowledgeStore,
)
from agents_remember.models.knowledge.requirement import (
    REQUIREMENT_PACKET_VERSION_PATTERN,
    REQUIREMENT_REVISION_KIND,
    REQUIREMENT_REVISION_SCHEMA,
    RequirementOwnerRef,
    RequirementRecordedState,
    RequirementRevisionPayload,
    RequirementRevisionRequest,
    RequirementRevisionResult,
    RequirementRevisionScope,
    requirement_owner_reference,
)
from agents_remember.models.knowledge.result import KnowledgeRefusal
from agents_remember.models.task_intent import ApprovedRequirementPacketRef
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


def _write_packet(
    task_root: Path,
    relative: str,
    *,
    stable_id: str = "KS-R19",
    version: str = "v1",
    title: str = "a packet",
) -> Path:
    """Author one requirement packet under a task root, in the shipped Markdown form."""

    target = task_root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "\n".join(
            (
                f"# {title}",
                "",
                "| Field | Value |",
                "| --- | --- |",
                f"| Stable ID | `{stable_id}` |",
                f"| Version | `{version}` |",
                "",
                "## Problem",
                "",
                "Packet body.",
                "",
            )
        ),
        encoding="utf-8",
    )
    return target


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


def test_the_owner_reference_carries_exactly_the_task_planes_three_components() -> None:
    """The stored reference names the same three components, spelled the same way.

    Catches a substrate that invented a fourth addressing scheme, or spelled ``stableId``/``version``
    differently from the task plane, which is how one version comes to be written two ways.
    """

    plane_fields = set(ApprovedRequirementPacketRef.model_fields)
    substrate_fields = set(RequirementOwnerRef.model_fields)
    # The task plane's own reference carries a ``kind`` discriminator beside the three identity
    # components; that is its own typing, not a fourth addressing component. What matters is that
    # the three components are spelled identically and that this side declares exactly those three.
    assert {"path", "stableId", "version"} <= plane_fields
    assert substrate_fields == {"path", "stableId", "version"}

    plane_pattern = ApprovedRequirementPacketRef.model_fields["version"].metadata
    assert REQUIREMENT_PACKET_VERSION_PATTERN in {
        str(getattr(item, "pattern", "")) for item in plane_pattern
    }

    with pytest.raises(ValueError):
        RequirementOwnerRef.model_validate(
            {"path": OWNER_PATH, "stableId": "KS-R19", "version": "v1", "packet_uuid": str(uuid4())}
        )


@pytest.mark.parametrize("version", ["1", "V1", "v0", "v01", "v1.0", "", "v-1"])
def test_a_version_outside_the_admitted_spelling_is_refused(version: str) -> None:
    """Only ``^v[1-9][0-9]*$`` is admitted, because that is the one spelling the task plane admits.

    Catches two spellings of one version comparing unequal on the two sides.
    """

    with pytest.raises(ValueError):
        _owner(version=version)


def test_the_payload_does_not_police_the_reference_the_owner_owns() -> None:
    """A path the owner would refuse is still *representable* here, so the owner's answer is the one.

    Catches a second resolver: a payload that confined the path to a task root, or required a
    ``.md`` suffix, would refuse before the owner was asked and would then have no owner refusal to
    carry -- substituting its own answer for the owner's, which requirement 2.3 forbids.
    """

    for path in (
        "../outside.md",
        "/etc/passwd.md",
        "notes/not-markdown.txt",
        "deep/../../escape.md",
    ):
        assert _owner(path=path).path == path


@pytest.mark.parametrize("field", FORBIDDEN_TASK_AUTHORITY_FIELDS)
def test_a_payload_carrying_a_forbidden_task_authority_field_is_refused(field: str) -> None:
    """Each forbidden name is refused by the ordinary extra-forbid rule, not by a named denylist.

    Catches task status, seat ownership or a lifecycle gate acquiring a home in a requirement
    record -- and catches the weaker design in which only a validator refuses it while the schema
    could still hold it.
    """

    refusal = validate_record_payload(
        REQUIREMENT_REVISION_KIND, REQUIREMENT_REVISION_SCHEMA, _payload(**{field: "leaked"})
    )
    assert isinstance(refusal, KnowledgeRefusal)
    assert refusal.code == "invalid_payload"
    assert field in refusal.detail


@pytest.mark.parametrize("field", OPERATIVE_OBLIGATION_FIELDS)
def test_a_payload_carrying_an_operative_obligation_field_is_refused(field: str) -> None:
    """No stored field is the text another system implements against (requirement 5.3).

    Catches the database becoming the obligation: an operative-text or retitling field would let a
    reader implement against the store while the packet is revised independently.
    """

    refusal = validate_record_payload(
        REQUIREMENT_REVISION_KIND,
        REQUIREMENT_REVISION_SCHEMA,
        _payload(**{field: "the operative requirement text"}),
    )
    assert isinstance(refusal, KnowledgeRefusal)
    assert refusal.code == "invalid_payload"
    assert field in refusal.detail


def test_no_registered_generation_declares_a_column_for_the_forbidden_set() -> None:
    """The schema plane of the same absence: no column anywhere could hold the value.

    This is the re-derived sweep the packet asks for at the schema plane rather than a quotation of
    its result. Catches a later leaf relaxing a validator while a column sits waiting behind it.
    """

    # The sweep is over the *task-authority* names at the schema plane. The operative-obligation
    # probes are a payload-plane property of this record group (requirement 5.3) -- the shipped
    # generation-1 aggregates legitimately carry ``display_version`` as their own version label, and
    # claiming its absence everywhere would be a false statement about a table this leaf must not
    # touch.
    declared: set[str] = set()
    for generation in GENERATIONS:
        for columns in generation.columns.values():
            declared.update(columns)
    assert declared & set(FORBIDDEN_TASK_AUTHORITY_FIELDS) == set()
    # The record group adds no table of its own at all, so there is not even a new place to look.
    assert REQUIREMENT_REVISION_KIND not in {
        table for generation in GENERATIONS for table in generation.tables
    }


def test_a_stored_payload_carrying_a_forbidden_field_could_not_be_decoded() -> None:
    """A row hand-edited to carry a task-status value is refused on the way out, not served.

    Catches a read path that serves whatever bytes are in the column: the decode validates against
    the frozen registry shape, so the stored plane is enforced on reads as well as writes.
    """

    tampered = _payload(task_status="inProgress")
    digest = record_revision_digest(
        RecordRevisionDraft(
            record_id="00000000-0000-0000-0000-0000000000bb",
            revision_id="00000000-0000-0000-0000-0000000000aa",
            record_schema=REQUIREMENT_REVISION_SCHEMA,
            predecessor_revision_id=None,
        ),
        tampered,
    )
    sealed_row = (
        "00000000-0000-0000-0000-0000000000aa",
        "00000000-0000-0000-0000-0000000000bb",
        REQUIREMENT_REVISION_SCHEMA,
        canonical_json_bytes(tampered).decode("utf-8"),
        None,
        digest,
        "{}",
    )
    # The seal holds, so the refusal has to come from the registry's frozen shape rather than from
    # a digest mismatch: a read path that trusted the column would serve the task status.
    with pytest.raises(KnowledgeStorageError, match="does not validate"):
        decode_requirement_revision_row(sealed_row)


def test_ungoverned_is_reported_as_a_state_and_never_defaulted(tmp_path: Path) -> None:
    """A record with no governing route reads as ungoverned, not as scoped to something.

    Catches a route inferred from the packet's directory or the repository's name: the packet is a
    task-relative document, so its own location supplies no route.
    """

    store = _store(tmp_path)
    record_id, revision_id = str(uuid4()), str(uuid4())
    assert record_requirement_revision(store, _request(store, record_id, revision_id)).state == (
        "recorded"
    )
    served = read_requirement_revisions(store, record_id)
    assert served.scope is not None
    assert _scope_of(served).governing_route.state == "ungoverned"
    assert _scope_of(served).governing_route.governing_route_id is None


def test_a_named_route_must_be_authored_and_is_never_repointed(tmp_path: Path) -> None:
    """A named route must exist, and a later revision cannot move the record to another one.

    Catches a dangling scope reference and catches an in-place repoint of a sealed association.
    """

    store = _store(tmp_path)
    route_id, other_route_id = str(uuid4()), str(uuid4())
    for candidate in (route_id, other_route_id):
        authored = routes.author_route(
            store.connection,
            store.repository_id,
            RouteDraft(route_id=candidate, path=f"src/{candidate[:8]}.py"),
            make_authorship(),
        )
        assert isinstance(authored, str)

    dangling = record_requirement_revision(
        store, _request(store, str(uuid4()), str(uuid4()), governing_route_id=str(uuid4()))
    )
    assert dangling.state == "refused"
    assert _refusal_of(dangling).code == "missing_expected_row"

    record_id, first = str(uuid4()), str(uuid4())
    assert (
        record_requirement_revision(
            store, _request(store, record_id, first, governing_route_id=route_id)
        ).state
        == "recorded"
    )
    served = read_requirement_revisions(store, record_id)
    assert served.scope is not None
    assert _scope_of(served).governing_route.state == "governed"
    assert _scope_of(served).governing_route.governing_route_id == route_id

    repointed = record_requirement_revision(
        store,
        _request(
            store,
            record_id,
            str(uuid4()),
            predecessor_revision_id=first,
            governing_route_id=other_route_id,
        ),
    )
    assert repointed.state == "refused"
    assert _refusal_of(repointed).code == "immutable_revision"
    assert _refusal_of(repointed).expected == route_id
    assert _refusal_of(repointed).observed == other_route_id


def test_each_owner_refusal_is_carried_verbatim_and_leaves_the_reference_unrewritten(
    tmp_path: Path,
) -> None:
    """The owner's own refusal is recorded as a state, and the stored reference is unchanged.

    Catches a substrate that dropped an unresolvable reference, repaired it by inventing a local
    obligation, or substituted its own refusal for the owner's. Each shape is measured against the
    owner's real resolver, so the code asserted is the code the owner returns.
    """

    task_root = tmp_path / "task"
    _write_packet(task_root, OWNER_PATH, stable_id="KS-R19", version="v1")
    _write_packet(task_root, "requirements/KS-R19-v1-other.md", stable_id="KS-R99", version="v1")

    cases: tuple[tuple[str, str, str], ...] = (
        (
            "an absent packet",
            "requirements/absent.md",
            "task-intent-requirement-packet-missing",
        ),
        (
            "a non-Markdown target",
            "requirements/not-markdown.txt",
            "task-intent-requirement-packet-outside-task",
        ),
        (
            "a path outside the task root",
            "../outside.md",
            "task-intent-requirement-packet-outside-task",
        ),
        (
            "a metadata mismatch",
            "requirements/KS-R19-v1-other.md",
            "task-intent-requirement-packet-version-mismatch",
        ),
    )

    store = _store(tmp_path)
    for label, path, expected_code in cases:
        reference = _owner(path=path)
        resolution = consume_owner_resolution(task_root, reference)
        assert resolution.state == "unresolved", label
        assert resolution.refusal_code == expected_code, label
        assert resolution.refusal_detail, label

        record_id, revision_id = str(uuid4()), str(uuid4())
        recorded = record_requirement_revision(
            store,
            _request(
                store,
                record_id,
                revision_id,
                owner=reference.model_dump(mode="json"),
                owner_resolution=resolution.model_dump(mode="json"),
            ),
        )
        assert recorded.state == "recorded", label
        assert recorded.scope is not None
        stored = _scope_of(recorded).revisions[0].owner
        assert stored.path == path, label
        assert stored == reference, label
        assert _scope_of(recorded).revisions[0].owner_resolution.state == "unresolved"
        assert _scope_of(recorded).revisions[0].owner_resolution.refusal_code == expected_code


def test_a_resolved_owner_is_recorded_as_resolved(tmp_path: Path) -> None:
    """The owner's acceptance of the reference is the recorded resolution, verbatim.

    Catches a resolution invented on this side rather than consumed from the owner.
    """

    task_root = tmp_path / "task"
    _write_packet(task_root, OWNER_PATH)
    resolution = consume_owner_resolution(task_root, _owner())
    assert resolution.state == "resolved"
    assert resolution.refusal_code is None and resolution.refusal_detail is None

    store = _store(tmp_path)
    record_id, revision_id = str(uuid4()), str(uuid4())
    recorded = record_requirement_revision(
        store,
        _request(
            store,
            record_id,
            revision_id,
            owner_resolution=resolution.model_dump(mode="json"),
        ),
    )
    assert recorded.state == "recorded"
    assert recorded.scope is not None
    assert _scope_of(recorded).revisions[0].owner_resolution.state == "resolved"


def test_a_reference_from_another_record_group_resolves_to_this_record_group(
    tmp_path: Path,
) -> None:
    """Requirement 2.6's direction: the reference resolves here, or stays explicitly unresolved.

    Catches a reference converted into a second requirement authority: this resolves by reading the
    stored requirement records' own payloads, never a second table, the task plane's storage or the
    packet as the operand -- and a reference nothing carries is reported unresolved rather than
    satisfied by an invented record.
    """

    store = _store(tmp_path)
    reference = _owner()
    absent = resolve_requirement_reference(store, reference.model_dump(mode="json"))
    assert absent.state == "unresolved"
    assert absent.record_ids == ()

    record_id, revision_id = str(uuid4()), str(uuid4())
    assert (
        record_requirement_revision(store, _request(store, record_id, revision_id)).state
        == "recorded"
    )
    found = resolve_requirement_reference(store, reference.model_dump(mode="json"))
    assert found.state == "resolved"
    assert found.record_ids == (record_id,)
    assert found.reference == reference

    # A second record carrying the same reference is reported as ambiguous, not resolved to one.
    assert (
        record_requirement_revision(store, _request(store, str(uuid4()), str(uuid4()))).state
        == "recorded"
    )
    ambiguous = resolve_requirement_reference(store, reference.model_dump(mode="json"))
    assert ambiguous.state == "ambiguous"
    assert len(ambiguous.record_ids) == 2

    malformed = resolve_requirement_reference(store, {"path": OWNER_PATH})
    assert malformed.state == "unresolved"
    assert malformed.reference is None


def test_every_derived_view_rebuilds_byte_identically_from_the_stored_rows(tmp_path: Path) -> None:
    """A view is a pure function of the stored rows, so a rebuild reproduces it exactly.

    Catches a derived store that became an authority: the scope is built twice from the same rows
    and compared, and the records themselves are unchanged by the rebuild.
    """

    store = _store(tmp_path)
    route_id = str(uuid4())
    assert isinstance(
        routes.author_route(
            store.connection,
            store.repository_id,
            RouteDraft(route_id=route_id, path="src/rebuildable.py"),
            make_authorship(),
        ),
        str,
    )
    record_id, first, second = str(uuid4()), str(uuid4()), str(uuid4())
    assert (
        record_requirement_revision(
            store, _request(store, record_id, first, governing_route_id=route_id)
        ).state
        == "recorded"
    )
    assert (
        record_requirement_revision(
            store,
            _request(
                store,
                record_id,
                second,
                predecessor_revision_id=first,
                governing_route_id=route_id,
            ),
        ).state
        == "recorded"
    )

    served = read_requirement_revisions(store, record_id)
    assert served.state == "read" and served.scope is not None
    rebuilt = read_requirement_revisions(store, record_id)
    assert rebuilt.scope is not None
    assert _scope_of(rebuilt).model_dump(mode="json") == _scope_of(served).model_dump(mode="json")
    # The rebuild changed no stored byte.
    assert _stored_payload_bytes(store, first) == _stored_payload_bytes(store, first)


def test_a_fork_is_reported_as_more_than_one_head_and_no_winner_is_chosen(tmp_path: Path) -> None:
    """Two revisions descending from one predecessor fork the record, and the fork is reported.

    Catches the substrate designating a current revision: with no mutable designation column, both
    heads are reported rather than one being picked as the winner.
    """

    store = _store(tmp_path)
    record_id, root, left, right = str(uuid4()), str(uuid4()), str(uuid4()), str(uuid4())
    assert record_requirement_revision(store, _request(store, record_id, root)).state == "recorded"
    for branch in (left, right):
        assert (
            record_requirement_revision(
                store, _request(store, record_id, branch, predecessor_revision_id=root)
            ).state
            == "recorded"
        )
    served = read_requirement_revisions(store, record_id)
    assert served.scope is not None
    # Both heads are reported, in the stored rows' own order -- which is the deterministic order
    # the read declares, not the order the two writes happened to arrive in. A designation would
    # have had to pick one; this reports both.
    assert _scope_of(served).current_state.head_revision_ids == tuple(sorted((left, right)))
    assert len(_scope_of(served).current_state.currentness) == 2
    assert {fact.revision_id for fact in _scope_of(served).current_state.currentness} == {
        left,
        right,
    }


def test_a_stored_state_that_disagrees_with_the_owners_state_reports_both_and_chooses_none(
    tmp_path: Path,
) -> None:
    """A currentness fact carries both recorded states and their provenance, and no winner.

    Catches the substrate resolving a disagreement: it reports the disagreement and the basis a
    caller consumed, and neither value is rewritten.
    """

    store = _store(tmp_path)
    record_id, revision_id = str(uuid4()), str(uuid4())
    recorded = record_requirement_revision(store, _request(store, record_id, revision_id))
    assert recorded.scope is not None
    revision = _scope_of(recorded).revisions[0]

    owner_recorded = RequirementRecordedState(
        state_at_origin="accepted",
        acceptance_ref="the owner's recorded acceptance",
        provenance=make_authorship(actor_ref="agent:owner"),
    )
    stored = StoredRequirementRevision(
        revision_id=revision.revision_id,
        record_id=record_id,
        record_schema=REQUIREMENT_REVISION_SCHEMA,
        payload=RequirementRevisionPayload.model_validate(_payload()),
        predecessor_revision_id=None,
        content_digest=revision.content_digest,
        provenance=revision.provenance,
    )
    fact = currentness_fact(stored, owner_recorded, basis="owner-moved")
    assert fact.state == "disagreement"
    assert fact.stored.state_at_origin == "proposed" and fact.stored.acceptance_ref is None
    assert fact.owner is not None and fact.owner.state_at_origin == "accepted"
    assert fact.stored.provenance != fact.owner.provenance
    assert "no winner" in fact.detail

    aligned = currentness_fact(
        stored,
        RequirementRecordedState(
            state_at_origin="proposed",
            acceptance_ref=None,
            provenance=make_authorship(actor_ref="agent:owner"),
        ),
    )
    assert aligned.state == "aligned"

    unresolved = currentness_fact(stored, None)
    assert unresolved.state == "unresolved-owner"
    assert unresolved.owner is None

    with pytest.raises(ValueError, match="derived from the values it reports"):
        type(fact).model_validate({**fact.model_dump(mode="json"), "state": "aligned"})


def test_an_unresolved_owner_has_nothing_to_compare_even_when_a_state_is_supplied(
    tmp_path: Path,
) -> None:
    """An unresolved reference reports the unresolved state rather than claiming agreement.

    Catches a read that compared a stored state against a state it could not have obtained from an
    owner it could not resolve.
    """

    store = _store(tmp_path)
    record_id, revision_id = str(uuid4()), str(uuid4())
    recorded = record_requirement_revision(
        store,
        _request(
            store,
            record_id,
            revision_id,
            owner_resolution={
                "state": "unresolved",
                "refusal_code": "task-intent-requirement-packet-missing",
                "refusal_detail": "approved requirement packet is absent or unreadable",
            },
        ),
    )
    assert recorded.scope is not None
    assert _scope_of(recorded).current_state.currentness[0].state == "unresolved-owner"
    assert _scope_of(recorded).current_state.currentness[0].owner is None


def test_the_derived_views_are_total_over_a_row_set_holding_no_revision() -> None:
    """The view builders answer for any stored row set, including one holding no revision.

    Catches a view builder that assumed at least one revision and would raise on a store damaged
    outside the operation instead of reporting the absence it can see.
    """

    assert head_revision_ids(()) == ()
    assert CHAIN_BOUND > 0
    record = StoredRequirementRecord(
        repository_id=str(uuid4()),
        record_id=str(uuid4()),
        kind=REQUIREMENT_REVISION_KIND,
        record_schema=REQUIREMENT_REVISION_SCHEMA,
        governing_route_id=None,
        provenance=make_authorship(),
    )
    view = revision_scope(record, ())
    assert view.revisions == ()
    assert view.predecessor_chain is None
    assert view.current_state.head_revision_ids == ()
    assert view.current_state.owner is None
    assert view.current_state.currentness == ()
    assert view.governing_route.state == "ungoverned"


def test_the_payload_mapping_helper_reads_only_a_stored_mapping() -> None:
    """The reference read out of a stored payload answers ``None`` rather than inventing one.

    Catches a resolution helper that filled in a missing or malformed reference.
    """

    assert requirement_owner_reference({}) is None
    assert requirement_owner_reference({"owner": "KS-R19@v1"}) is None
    assert requirement_owner_reference({"owner": {"path": OWNER_PATH}}) is None
    reference = requirement_owner_reference(_payload())
    assert reference == _owner()
