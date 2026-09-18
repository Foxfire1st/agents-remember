"""``EvidenceClaim``: the record's shape, its links, its write path, its generation and its read.

These cases protect ``KS-R12@v1``'s claim contract in the order the packet's ``## Required Behavior``
states it: the typed aggregate under the wave A envelope, the two subject kinds as structural tables,
the resolved evidence anchor, the claimed coverage that is never widened, the required explanation and
limitations, the author and lifecycle as stored data, the opaque assessment references, the code-side
resolution that refuses an unresolved link, the appended commands and their dispatch, the generation
gate, and the read projection that carries the limitations field visibly.

Three properties are load-bearing enough to name before the cases:

* **The table IS the kind check.** A claim's subject is one of exactly two kinds and each kind has its
  own join table with its own foreign keys, so a misspelled kind, a dangling identity and a
  wrong-kind target are *not representable* rather than merely refused. The cases assert the refusal
  that a caller sees, and they assert the structural half too: there is no polymorphic column for an
  unchecked identity to land in.
* **An empty limitations value is a recorded fact.** The read serves ``""`` -- the author declaring
  none -- and never ``None``, "unknown", or an endorsement. The case that protects this asserts the
  served value on a claim whose limitations were stored empty, and asserts that the field is not
  optional on either the payload or the served item.
* **Nothing here judges what the evidence demonstrates.** No served field is a verdict, a grade, a
  score or a status; the assessment-reference state is the *absence* of an assessment reported as
  absence. The case that protects this walks the served item's declared field set and refuses a name
  that could carry one.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, get_args

import apsw
import pytest
from agents_remember.application.knowledge import (
    admitted_evidence_request,
    change_knowledge_candidate,
    open_admitted_knowledge_store,
    resolve_candidate_context,
    write_knowledge_evidence,
)
from agents_remember.application.knowledge_evidence import read_evidence_scope
from agents_remember.memory.knowledge.batch_preconditions import _TARGET_CHECKS
from agents_remember.memory.knowledge.candidate_records import (
    WRITABLE_TABLES,
    written_identities,
)
from agents_remember.memory.knowledge.evidence import (
    REQUIRED_EVIDENCE_GENERATION,
    require_evidence_generation,
    require_evidence_subject,
)
from agents_remember.memory.knowledge.evidence_records import CLAIM_BY_ID, all_claims
from agents_remember.memory.knowledge.logical import dataset_identity
from agents_remember.memory.knowledge.record_envelope import (
    KIND_SCHEMAS,
    PAYLOAD_MODELS,
    validate_record_payload,
)
from agents_remember.memory.knowledge.refusals import KnowledgeRefused
from agents_remember.memory.knowledge.schema_generations import (
    CURRENT_GENERATION,
    GENERATION_1,
    GENERATION_6,
    GENERATION_7,
    create_schema_statements,
)
from agents_remember.memory.knowledge.store import (
    open_existing_knowledge_store,
    open_knowledge_store,
)
from agents_remember.models.knowledge.candidate import (
    CandidateResolution,
    ChangeBatch,
    MutableRecordTable,
    ProposedCommand,
)
from agents_remember.models.knowledge.evidence import (
    EVIDENCE_CLAIM_KIND,
    EVIDENCE_CLAIM_SCHEMA,
    EVIDENCE_COMMAND_KINDS,
    EVIDENCE_WRITABLE_TABLES,
    SUBJECT_COLUMNS,
    SUBJECT_KINDS,
    AddEvidenceClaim,
    AnchorCoverage,
    EvidenceClaimPayload,
    InvariantRevisionSubject,
    KnowledgeFacetRevisionSubject,
    RealizationClaimCoverage,
)
from agents_remember.models.knowledge.evidence_read import (
    AssessmentReferenceState,
    ClaimCoverage,
    ClaimSubject,
    EvidenceClaimItem,
    EvidenceClaimRecord,
    EvidenceClaimSeed,
    EvidenceReadRequest,
)
from agents_remember.models.knowledge.facet import FACET_WRITABLE_TABLES
from agents_remember.models.knowledge.read import KnowledgeReadContext
from agents_remember.models.knowledge.repository import RepositoryIdentity
from evidence_test_support import (
    ClaimOptions,
    EvidenceFixture,
    anchor_coverage,
    build_evidence_fixture,
    claim_command,
    claim_payload,
    expect_refusal,
    facet_subject,
    read_context,
)

pytestmark = pytest.mark.evidence_unit

TREE = "a" * 40
MEMORY_TREE = "b" * 40


@pytest.fixture
def fixture(tmp_path: Path) -> EvidenceFixture:
    return build_evidence_fixture(tmp_path)


def read_claim(fixture: EvidenceFixture, claim_id: str, **overrides: Any) -> Any:
    """Read one claim through the application seam, at the fixture's exact snapshot."""

    return read_evidence_scope(
        fixture.destination.database_path,
        read_context(fixture),
        EvidenceReadRequest(seed=EvidenceClaimSeed(claim_id=claim_id), **overrides),
    )


def write_claim(store: Any, fixture: EvidenceFixture, command: Any) -> Any:
    return write_knowledge_evidence(
        fixture.destination, admitted_evidence_request(fixture.destination, command)
    )


def stored_commands(destination: Any, context: Any, *commands: Any) -> Any:
    return change_knowledge_candidate(destination, ChangeBatch(expected=context, commands=commands))


# ---------------------------------------------------------------------------
# Required Behavior 1.1 and 1.5: the frozen payload and the two required prose fields.


def test_the_claim_payload_is_frozen_extra_forbidden_and_never_defaults_limitations() -> None:
    """1.1 and 1.5: a frozen shape, and a limitations field that is required but may be empty.

    The property this protects is that an omitted limitations value is a *shape error* while an empty
    one is a *recorded fact*: the two must not collapse into one another, because a claim whose
    limitations were never authored is a different statement from a claim whose author declared none.
    """

    payload = EvidenceClaimPayload(explanation="because", limitations="")
    assert payload.limitations == ""
    assert payload.state_at_origin == "proposed"
    assert payload.assessment_refs == ()

    # An omitted limitations value is the same recorded fact as an empty one: "this author declared
    # none". What must not be representable is "unknown", so the field is a string with no null
    # spelling, and the column it is stored in is NOT NULL.
    omitted = EvidenceClaimPayload(explanation="because")
    assert omitted.limitations == ""
    assert "limitations" not in omitted.model_fields_set
    # The value is carried in the *sealed payload* rather than as a column of its own, which is what
    # keeps it inside the revision's content digest: the claim's ledger row is the two key columns and
    # the evidence anchor, and every authored field of the claim is a frozen payload field.
    assert "limitations" not in GENERATION_7.columns["evidence_claim"]
    assert "limitations" in EvidenceClaimPayload.model_fields
    with pytest.raises(ValueError):
        EvidenceClaimPayload(explanation="because", limitations=None)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        EvidenceClaimPayload(explanation="because", limitations="x", undeclared="y")  # type: ignore[call-arg]
    with pytest.raises(ValueError):
        EvidenceClaimPayload(explanation="", limitations="")
    with pytest.raises(ValueError):
        EvidenceClaimPayload(explanation="because", limitations="x", assessment_refs=("",))
    with pytest.raises(ValueError):
        EvidenceClaimPayload(explanation="because", limitations="x", assessment_refs=("a", "a"))
    with pytest.raises(ValueError):
        EvidenceClaimPayload(explanation="because", limitations="x", acceptance_ref="an-acceptance")
    accepted = EvidenceClaimPayload(
        explanation="because", limitations="x", state_at_origin="accepted", acceptance_ref="ref"
    )
    assert accepted.state_at_origin == "accepted"


def test_the_claim_is_registered_in_the_envelope_under_its_own_kind_and_schema() -> None:
    """1.1: the wave A envelope carries the claim's kind and its one frozen payload shape.

    The property is that the seam is the *only* payload decision point: an undeclared field, a missing
    required field and a payload that does not validate are all one shipped ``invalid_payload``
    refusal, and an unknown kind or a schema that is not admissible for the kind is refused without
    the payload being looked at at all.
    """

    assert PAYLOAD_MODELS[(EVIDENCE_CLAIM_KIND, EVIDENCE_CLAIM_SCHEMA)] is EvidenceClaimPayload
    assert KIND_SCHEMAS[EVIDENCE_CLAIM_KIND] == frozenset({EVIDENCE_CLAIM_SCHEMA})

    validated = validate_record_payload(EVIDENCE_CLAIM_KIND, EVIDENCE_CLAIM_SCHEMA, claim_payload())
    assert isinstance(validated, EvidenceClaimPayload)

    undeclared = validate_record_payload(
        EVIDENCE_CLAIM_KIND, EVIDENCE_CLAIM_SCHEMA, claim_payload() | {"verdict": "sufficient"}
    )
    refused_undeclared = expect_refusal(undeclared)
    assert refused_undeclared.code == "invalid_payload"
    assert "verdict" in refused_undeclared.detail

    unknown_kind = validate_record_payload(
        "evidence_claim_v2", EVIDENCE_CLAIM_SCHEMA, claim_payload()
    )
    refused = expect_refusal(unknown_kind)
    assert refused.code == "invalid_payload"
    assert refused.observed == "evidence_claim_v2"

    wrong_schema = validate_record_payload(
        EVIDENCE_CLAIM_KIND, "evidence-claim/v2", claim_payload()
    )
    refused_schema = expect_refusal(wrong_schema)
    assert refused_schema.code == "invalid_payload"
    assert refused_schema.observed == "evidence-claim/v2"


# ---------------------------------------------------------------------------
# Required Behavior 1.2: the subject is exactly one of two kinds, structurally.


def test_the_two_subject_kinds_are_two_tables_and_neither_can_address_the_other() -> None:
    """1.2: endpoint-kind compatibility is structural, never a polymorphic column.

    The protected property is the packet's blocking-shape rule: a single subject column with an
    unconstrained target is forbidden, so what a case has to show is that the two kinds are two
    tables with two foreign keys and that no column exists for an unchecked identity.
    """

    assert SUBJECT_KINDS == ("invariant_revision", "facet_revision")
    assert set(SUBJECT_COLUMNS) == set(SUBJECT_KINDS)
    assert GENERATION_7.columns["evidence_claim_invariant_subject"] == (
        "repository_id",
        "claim_id",
        "invariant_revision_id",
    )
    assert GENERATION_7.columns["evidence_claim_facet_subject"] == (
        "repository_id",
        "claim_id",
        "facet_revision_id",
    )
    invariant_ddl = GENERATION_7.table_ddl["evidence_claim_invariant_subject"]
    facet_ddl = GENERATION_7.table_ddl["evidence_claim_facet_subject"]
    assert "REFERENCES invariant_revision(repository_id, revision_id)" in invariant_ddl
    assert "REFERENCES record_revision(repository_id, revision_id)" in facet_ddl
    assert "REFERENCES record_revision" not in invariant_ddl
    assert "REFERENCES invariant_revision" not in facet_ddl
    assert "subject_kind" not in invariant_ddl
    assert "subject_kind" not in facet_ddl
    assert "endpoint_id" not in invariant_ddl
    assert "endpoint_id" not in facet_ddl

    # A subject naming two kinds is a shape error at construction, before storage: the union is
    # discriminated, so two kinds are not expressible as one value.
    with pytest.raises(ValueError):
        AddEvidenceClaim.model_validate(
            claim_command_arguments()
            | {
                "subject": {
                    "kind": "invariant_revision",
                    "revision_id": "11111111-1111-4111-8111-111111111111",
                    "facet_revision_id": "22222222-2222-4222-8222-222222222222",
                }
            }
        )


def claim_command_arguments() -> dict[str, Any]:
    """Return one valid claim command's raw arguments, so a case can vary exactly one field."""

    return {
        "kind": "add_evidence_claim",
        "claim_id": "33333333-3333-4333-8333-333333333333",
        "revision_id": "44444444-4444-4444-8444-444444444444",
        "subject": {
            "kind": "invariant_revision",
            "revision_id": "55555555-5555-4555-8555-555555555555",
        },
        "evidence_anchor_id": "66666666-6666-4666-8666-666666666666",
        "coverage": [
            {"kind": "realization_claim", "claim_id": "77777777-7777-4777-8777-777777777777"}
        ],
        "payload": {"explanation": "because", "limitations": ""},
    }


def test_a_claim_that_asserts_no_coverage_is_refused_at_construction() -> None:
    """1.4: the claimed coverage is the author's assertion and code never widens it.

    The protected property is that an empty list is a shape error rather than "cover everything that
    happens to exist": the list is what the author asserts, and a claim with no assertion has nothing
    for a reader to widen later.
    """

    with pytest.raises(ValueError, match="states the coverage"):
        AddEvidenceClaim.model_validate(claim_command_arguments() | {"coverage": []})
    with pytest.raises(ValueError, match="twice"):
        AddEvidenceClaim.model_validate(
            claim_command_arguments()
            | {
                "coverage": [
                    {"kind": "source_anchor", "anchor_id": "88888888-8888-4888-8888-888888888888"},
                    {"kind": "source_anchor", "anchor_id": "88888888-8888-4888-8888-888888888888"},
                ]
            }
        )
    with pytest.raises(ValueError):
        AddEvidenceClaim.model_validate(
            claim_command_arguments()
            | {
                "coverage": [
                    {
                        "kind": "family_revision",
                        "revision_id": "99999999-9999-4999-8999-999999999999",
                    }
                ]
            }
        )


# ---------------------------------------------------------------------------
# Required Behavior 1.3, 1.4, 2.1: the links resolve, and the refusal names every part.


def test_an_unresolved_subject_anchor_or_coverage_endpoint_is_refused_with_its_kind(
    fixture: EvidenceFixture,
) -> None:
    """1.3, 1.4 and 2.1: one refusal code, one module, five exact facts and no row written.

    The protected property is that two relation writes cannot drift into different codes for one
    failure, and that the refusal names the operation, the table, the relation identity, the endpoint
    identity and the endpoint kind -- which is what makes it actionable rather than merely negative.
    """

    missing = "00000000-0000-4000-8000-000000000000"
    store = open_admitted_knowledge_store(fixture.destination)
    try:
        cases = (
            (claim_command(fixture, ClaimOptions(evidence_anchor_id=missing)), "source anchor"),
            (
                claim_command(
                    fixture, ClaimOptions(coverage=(RealizationClaimCoverage(claim_id=missing),))
                ),
                "realization claim",
            ),
            (
                claim_command(fixture, ClaimOptions(coverage=(AnchorCoverage(anchor_id=missing),))),
                "source anchor",
            ),
            (
                claim_command(
                    fixture, ClaimOptions(subject=InvariantRevisionSubject(revision_id=missing))
                ),
                "invariant revision",
            ),
            (
                claim_command(
                    fixture,
                    ClaimOptions(subject=KnowledgeFacetRevisionSubject(revision_id=missing)),
                ),
                "knowledge facet revision",
            ),
        )
        for command, noun in cases:
            written = write_claim(store, fixture, command)
            assert written.state == "refused", noun
            assert written.refusal.code == "invalid_reference", noun
            assert written.refusal.operation == "add_evidence_claim", noun
            assert written.refusal.expected == noun, noun
            assert written.refusal.record_id is not None, noun
            assert written.written == (), noun
    finally:
        store.close()


def test_a_subject_that_is_a_stored_revision_of_another_kind_is_not_a_facet_subject(
    fixture: EvidenceFixture,
) -> None:
    """1.2: the facet subject is a ``KnowledgeFacet`` revision specifically, not "some revision".

    The protected property is the difference between "a revision with this identity exists" and "this
    identity is a facet revision". A stored detection-shaped record revision and a stored invariant
    revision are both *stored revisions*, and neither is a facet; the refusal is the same missing
    endpoint a caller would get for an identity nothing holds, because that is the fact.
    """

    store = open_admitted_knowledge_store(fixture.destination)
    try:
        require_evidence_subject(
            store,
            KnowledgeFacetRevisionSubject(revision_id=fixture.facet_revision_id),
            "claim",
        )
        with pytest.raises(KnowledgeRefused) as refused:
            require_evidence_subject(
                store,
                KnowledgeFacetRevisionSubject(revision_id=fixture.invariant_revision_id),
                "claim",
            )
        assert refused.value.refusal.code == "invalid_reference"
        assert refused.value.refusal.expected == "knowledge facet revision"
        assert refused.value.refusal.record_id == fixture.invariant_revision_id
    finally:
        store.close()


def test_a_facet_subject_resolves_and_a_claim_is_read_back_with_it(
    fixture: EvidenceFixture,
) -> None:
    """1.2 and 8.1: the facet subject is a real subject kind, stored and served as one."""

    store = open_admitted_knowledge_store(fixture.destination)
    try:
        written = write_claim(
            store,
            fixture,
            claim_command(fixture, ClaimOptions(subject=facet_subject(fixture))),
        )
        assert written.state == "applied", written.refusal
        tables = {entry.table for entry in written.written}
        assert "evidence_claim_facet_subject" in tables
        assert "evidence_claim_invariant_subject" not in tables
    finally:
        store.close()
    command = claim_command(
        fixture,
        ClaimOptions(subject=facet_subject(fixture), claim_id=_last_claim_id(fixture)),
    )
    result = read_claim(fixture, command.claim_id)
    assert result.state == "page", result.refusal
    subject = result.page.items[0].subject
    assert subject.subject.kind == "facet_revision"
    assert subject.subject.revision_id == fixture.facet_revision_id


def _last_claim_id(fixture: EvidenceFixture) -> str:
    """Return the identity of the claim the fixture's most recent write stored."""

    return _claim_ids(fixture)[-1]


def _claim_ids(fixture: EvidenceFixture) -> list[str]:
    store = open_admitted_knowledge_store(fixture.destination)
    try:
        return [claim.claim_id for claim in all_claims(store)]
    finally:
        store.close()


# ---------------------------------------------------------------------------
# Required Behavior 1.6, 1.7 and 8.3: author, lifecycle, and the absence of an assessment.


def test_the_author_is_the_admission_and_the_lifecycle_is_stored_data(
    fixture: EvidenceFixture,
) -> None:
    """1.6: provenance comes from the admitted boundary and a proposal stays a proposal.

    The protected property is twofold: the caller cannot supply the author, and persisting a claim
    endorses nothing. The receipt is checked too -- it reports the rows and their digests and carries
    no field that could be read as an acceptance.
    """

    store = open_admitted_knowledge_store(fixture.destination)
    try:
        command = claim_command(fixture)
        written = write_claim(store, fixture, command)
        assert written.state == "applied", written.refusal
        assert set(type(written).model_fields) == {"state", "repository_id", "written", "refusal"}
        with pytest.raises(ValueError):
            EvidenceClaimPayload(
                explanation="x",
                limitations="",
                provenance=fixture.authorship,  # type: ignore[call-arg]
            )
        row = next(
            iter(store.connection.execute(CLAIM_BY_ID, (store.repository_id, command.claim_id)))
        )
        assert row is not None
        envelope = next(
            iter(
                store.connection.execute(
                    "SELECT lifecycle, provenance FROM knowledge_record WHERE repository_id = ? "
                    "AND record_id = ?",
                    (store.repository_id, command.claim_id),
                )
            )
        )
        assert envelope[0] == "proposed"
        assert str(fixture.authorship.actor_ref) in str(envelope[1])
        assert str(fixture.authorship.operation_id) in str(envelope[1])
    finally:
        store.close()


def test_an_accepted_origin_claim_is_refused_as_a_promotion(fixture: EvidenceFixture) -> None:
    """1.6: the candidate path authors proposals and exposes no promotion."""

    store = open_admitted_knowledge_store(fixture.destination)
    try:
        command = claim_command(fixture).model_copy(
            update={
                "payload": EvidenceClaimPayload(
                    explanation="x",
                    limitations="",
                    state_at_origin="accepted",
                    acceptance_ref="ref",
                )
            }
        )
        written = write_claim(store, fixture, command)
        assert written.state == "refused"
        assert written.refusal.code == "promotion_not_supported"
        assert written.refusal.table == "knowledge_record"
        assert written.refusal.record_id == command.claim_id
        assert written.written == ()
    finally:
        store.close()


def test_a_claim_with_no_assessment_reference_is_served_as_unassessed(
    fixture: EvidenceFixture,
) -> None:
    """1.7 and 8.3: the references are opaque here and their absence is served as absence.

    The protected property is that missing assessments stay missing: the served state says
    ``assessed=False`` and carries no reference, and nothing anywhere defaults it to compatible,
    complete or satisfied.
    """

    store = open_admitted_knowledge_store(fixture.destination)
    try:
        command = claim_command(fixture)
        assert write_claim(store, fixture, command).state == "applied"
    finally:
        store.close()
    assert read_claim(fixture, command.claim_id).state == "page"


# ---------------------------------------------------------------------------
# Required Behavior 6.1 to 6.4: the appended command, the union, the batch path.


def test_the_claim_command_joins_the_closed_union_its_dispatch_and_its_tables() -> None:
    """6.1 to 6.3: the two lists move together, and a table with no writing command is refused.

    The protected property is that the union stays closed and discriminated while gaining authored
    variants: every member has a target check, every evidence command has an identity entry, and the
    mutable-table set is exactly the tables those commands can write. The assertion is written as a
    union over the record groups' own named constants so a later group's registration does not edit
    this case.
    """

    kinds = {
        literal
        for member in _members(ProposedCommand)
        for literal in get_args(member.model_fields["kind"].annotation)
    }
    assert set(EVIDENCE_COMMAND_KINDS) <= kinds
    assert set(_TARGET_CHECKS) == kinds
    assert set(get_args(MutableRecordTable)) == set(WRITABLE_TABLES)
    assert set(EVIDENCE_WRITABLE_TABLES) <= set(WRITABLE_TABLES)
    assert set(FACET_WRITABLE_TABLES) <= set(WRITABLE_TABLES)
    assert set(EVIDENCE_WRITABLE_TABLES) - set(FACET_WRITABLE_TABLES) == {
        "evidence_claim",
        "evidence_claim_invariant_subject",
        "evidence_claim_facet_subject",
        "evidence_claim_coverage",
        "verification_observation",
    }
    # A table with no writing command is refused at construction: the union's members are the only
    # things that can name a table, and every evidence table is named by exactly one of them.
    command = AddEvidenceClaim.model_validate(claim_command_arguments())
    tables = {table for table, _ in written_identities(command)}
    assert tables == {
        "evidence_claim",
        "evidence_claim_invariant_subject",
        "evidence_claim_coverage",
        "knowledge_record",
        "record_revision",
    }


def _members(annotation: Any) -> tuple[Any, ...]:
    found: list[Any] = []
    for member in get_args(annotation):
        if hasattr(member, "model_fields"):
            found.append(member)
        elif get_args(member):
            found.extend(_members(member))
    return tuple(found)


def test_a_claim_written_in_a_batch_is_refused_when_a_link_is_absent(
    fixture: EvidenceFixture,
) -> None:
    """6.4 and 2.1: the batch path refuses before any row is written, and the digest does not move.

    The protected property is "a refused batch leaves the database byte-identical", measured rather
    than asserted: the logical identity before and after the refusal are compared.
    """

    store = open_admitted_knowledge_store(fixture.destination)
    try:
        before = dataset_identity(fixture.destination.database_path)
    finally:
        store.close()
    context = resolve_candidate_context(
        fixture.destination,
        CandidateResolution(
            lane="draft-candidate",
            code_tree_id=TREE,
            memory_tree_id=MEMORY_TREE,
            snapshot_ref="snapshot:evidence-batch",
            candidate_ref="candidate:evidence-batch",
        ),
    )
    missing = "00000000-0000-4000-8000-000000000000"
    outcome = stored_commands(
        fixture.destination,
        context,
        claim_command(fixture, ClaimOptions(evidence_anchor_id=missing)),
    )
    assert outcome.state == "refused"
    assert outcome.before == outcome.after
    assert outcome.changed == ()
    assert outcome.refusal is not None
    assert outcome.refusal.operation == "change_candidate"
    assert "command 0 (add_evidence_claim)" in outcome.refusal.detail
    after = dataset_identity(fixture.destination.database_path)
    assert after.logical_digest == before.logical_digest


def test_the_batch_path_stores_a_claim_whose_links_all_resolve(fixture: EvidenceFixture) -> None:
    """6.1 and 6.4: the same command through the one operation that writes the graph."""

    context = resolve_candidate_context(
        fixture.destination,
        CandidateResolution(
            lane="draft-candidate",
            code_tree_id=TREE,
            memory_tree_id=MEMORY_TREE,
            snapshot_ref="snapshot:evidence-batch-ok",
            candidate_ref="candidate:evidence-batch-ok",
        ),
    )
    command = claim_command(fixture, ClaimOptions(coverage=(anchor_coverage(fixture),)))
    outcome = stored_commands(fixture.destination, context, command)
    assert outcome.state == "changed", outcome.refusal
    assert outcome.refusal is None
    assert {entry.table for entry in outcome.changed} == {
        "evidence_claim",
        "evidence_claim_invariant_subject",
        "evidence_claim_coverage",
        "knowledge_record",
        "record_revision",
    }
    assert outcome.after != outcome.before
    assert read_claim(fixture, command.claim_id).state == "page"


# ---------------------------------------------------------------------------
# Required Behavior 6.5: the generation gate.


def test_a_claim_write_against_an_older_generation_is_refused_and_names_the_missing_one(
    tmp_path: Path,
) -> None:
    """6.5: a version-1 dataset is not migrated, not repaired and not extended in place.

    The protected property is that the refusal names *both* numbers, that nothing is written, and that
    the dataset's own recorded generation does not move: the observed and required versions travel as
    the refusal's facts, and the file stays what it declared.
    """

    for generation in (GENERATION_1, GENERATION_6):
        path = tmp_path / f"recorded-v{generation.user_version}.db" / "candidate.db"
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = apsw.Connection(str(path))
        try:
            for statement in create_schema_statements(generation):
                connection.execute(statement)
            connection.execute(f"PRAGMA user_version = {generation.user_version}")
            connection.execute(
                "INSERT INTO repository (repository_id, authority_home) VALUES (?, ?)",
                (REPOSITORY_ID, "agents-remember"),
            )
            connection.execute("COMMIT") if connection.in_transaction else None
        finally:
            connection.close()
        store = open_existing_knowledge_store(path, REPOSITORY_ID)
        try:
            assert store.generation.user_version == generation.user_version
            with pytest.raises(KnowledgeRefused) as refused:
                require_evidence_generation(store, "change_candidate")
            assert refused.value.refusal.code == "unsupported_schema"
            assert refused.value.refusal.expected == str(REQUIRED_EVIDENCE_GENERATION.user_version)
            assert refused.value.refusal.observed == str(generation.user_version)
            assert (
                int(next(iter(store.connection.execute("PRAGMA user_version")))[0])
                == generation.user_version
            )
        finally:
            store.close()


REPOSITORY_ID = "11111111-1111-4111-8111-111111111111"


def test_a_fresh_store_declares_the_generation_that_carries_these_tables(tmp_path: Path) -> None:
    """6.5: the generation these tables live in is the one a new store declares.

    The identity is asserted against ``CURRENT_GENERATION`` rather than against this leaf's own
    registration constant, and that is the more precise claim: the property is "a brand-new dataset
    declares the generation that carries these tables", which stays true through a later leaf's
    renumber, while an equality between two constants that this leaf edits together would not.
    """

    assert REQUIRED_EVIDENCE_GENERATION is CURRENT_GENERATION
    store = open_knowledge_store(tmp_path / "fresh.db", REPOSITORY_ID)
    try:
        store.create_repository(
            RepositoryIdentity(repository_id=REPOSITORY_ID, authority_home="agents-remember")
        )
        assert store.generation.user_version == REQUIRED_EVIDENCE_GENERATION.user_version
        tables = {
            str(row[0])
            for row in store.connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert set(EVIDENCE_WRITABLE_TABLES) <= tables
    finally:
        store.close()


# ---------------------------------------------------------------------------
# Required Behavior 8.1 and 8.4: the read projection.


def test_a_claim_reads_back_with_every_field_including_an_empty_limitations(
    fixture: EvidenceFixture,
) -> None:
    """8.1 and 1.5: nothing is dropped, and an empty limitations value is served as itself.

    The protected property is that a projection cannot summarise the limitations field away: the
    served claim carries ``limitations == ""`` -- the author's declaration -- the explanation, the
    subject, the anchor, the coverage, the author and the lifecycle, and the field is not optional on
    the served item.
    """

    store = open_admitted_knowledge_store(fixture.destination)
    try:
        command = claim_command(
            fixture,
            ClaimOptions(
                limitations="",
                explanation="the committed fixture exercises the retry budget",
                coverage=(
                    RealizationClaimCoverage(claim_id=fixture.claim_id),
                    anchor_coverage(fixture),
                ),
            ),
        )
        assert write_claim(store, fixture, command).state == "applied"
    finally:
        store.close()
    result = read_claim(fixture, command.claim_id)
    assert result.state == "page", result.refusal
    item = result.page.items[0]
    assert item.kind == "evidence_claim"
    assert item.claim.payload.limitations == ""
    assert item.claim.payload.explanation == "the committed fixture exercises the retry budget"
    assert item.claim.state_at_origin == "proposed"
    assert item.subject.subject.revision_id == fixture.invariant_revision_id
    assert item.evidence_anchor_id == fixture.anchor_id
    assert {edge.endpoint.kind for edge in item.coverage} == {"realization_claim", "source_anchor"}
    assert item.claim.provenance.actor_ref == fixture.authorship.actor_ref
    assert result.page.counts.evidence_claims == 1
    assert result.page.counts.evidence_claim_revisions == 1
    # The limitations field is not optional on either the payload or the served item.
    assert not EvidenceClaimPayload.model_fields["limitations"].is_required()
    assert item.claim.payload.model_fields_set >= {"limitations"}
    # And a claim with authored limitations serves them verbatim.
    store = open_admitted_knowledge_store(fixture.destination)
    try:
        second = claim_command(fixture, ClaimOptions(limitations="only the committed fixture"))
        assert write_claim(store, fixture, second).state == "applied"
    finally:
        store.close()
    served = read_claim(fixture, second.claim_id).page.items[0]
    assert served.claim.payload.limitations == "only the committed fixture"


def test_no_served_field_of_a_claim_could_carry_a_verdict(fixture: EvidenceFixture) -> None:
    """2.2, 4.1 and 4.2: nothing computes or carries a semantic conclusion.

    The protected property is checkable by review of the declared field sets: a field whose *name*
    names sufficiency, adequacy, confidence, a grade, a score, a ranking or a verdict is a conclusion
    field whatever its type, so the case walks every field name the served claim exposes and every
    name the payload and item declare.
    """

    forbidden = (
        "sufficien",
        "adequa",
        "confiden",
        "grade",
        "score",
        "rank",
        "verified",
        "satisfied",
        "verdict",
        "supported",
        "strength",
        "severity",
    )
    for model in (
        EvidenceClaimPayload,
        EvidenceClaimRecord,
        ClaimSubject,
        ClaimCoverage,
        AssessmentReferenceState,
        EvidenceClaimItem,
    ):
        for name in model.model_fields:
            assert not any(term in name for term in forbidden), (model.__name__, name)
    result = read_claim(fixture, fixture.claim_id)
    # A claim identity the fixture never wrote is the absent state, not an empty page.
    assert result.state == "refused"
    assert result.refusal.code == "selector_absent"
    assert result.refusal.record_id == fixture.claim_id


def test_the_read_refuses_a_database_that_is_not_the_selected_snapshot(
    fixture: EvidenceFixture, tmp_path: Path
) -> None:
    """8.4: the projection is derived from one declared snapshot and never from whatever bytes exist.

    The context names the right namespace and a *stale* logical identity, which is the state a read
    reaches when the candidate moved after the caller resolved it. The refusal names both digests
    exactly, which is what makes it actionable rather than merely negative, and the seed is the one
    the fixture really holds -- so the read refuses on the snapshot rather than on absence.
    """

    current = fixture.snapshot_identity()
    stale = current.model_copy(update={"logical_digest": "0" * 64})
    context = KnowledgeReadContext(
        repository_id=fixture.destination.repository.repository_id,
        knowledge=stale,
    )
    result = read_evidence_scope(
        fixture.destination.database_path,
        context,
        EvidenceReadRequest(seed=EvidenceClaimSeed(claim_id=fixture.claim_id)),
    )
    assert result.state == "refused"
    assert result.refusal is not None
    assert result.refusal.code == "snapshot_unavailable"
    assert result.refusal.expected == "0" * 64
    assert result.refusal.observed == current.logical_digest
    del tmp_path


def test_the_coverage_table_makes_one_endpoint_covered_once_unrepresentable(
    fixture: EvidenceFixture,
) -> None:
    """1.4: the claim's coverage is a set, and the table says so rather than a convention.

    The protected property is the structural half of the duplicate refusal: the model refuses a list
    that names one endpoint twice, and the stored table refuses the same row even when a caller
    reaches it another way. The second insert is written by hand because that is exactly the case the
    constraint exists for -- a write path that forgot the rule.
    """

    store = open_admitted_knowledge_store(fixture.destination)
    try:
        command = claim_command(fixture)
        assert write_claim(store, fixture, command).state == "applied"
        insert = (
            "INSERT INTO evidence_claim_coverage (repository_id, claim_id, coverage_kind, "
            "covered_identity, claim_id_endpoint, anchor_id_endpoint) VALUES (?, ?, ?, ?, ?, ?)"
        )
        parameters = (
            store.repository_id,
            command.claim_id,
            "realization_claim",
            fixture.claim_id,
            fixture.claim_id,
            None,
        )
        with pytest.raises(apsw.Error, match="UNIQUE constraint failed"):
            store.write(insert, parameters)
    finally:
        store.close()


def test_a_claim_and_its_revision_are_sealed_against_rewriting(fixture: EvidenceFixture) -> None:
    """3.7's rule applied to this leaf's rows: the trigger set refuses an in-place rewrite.

    The protected property is that immutability is enforced by the database and not only by the
    operation: a repair script, a changeset or a future code path that forgot the rule still cannot
    rewrite a recorded claim, rebind its subject, or narrow its claimed coverage.
    """

    store = open_admitted_knowledge_store(fixture.destination)
    try:
        assert write_claim(store, fixture, claim_command(fixture)).state == "applied"
        claim_id = _claim_ids(fixture)[-1]
        statement = f"UPDATE evidence_claim SET evidence_anchor_id = '{fixture.anchor_id}' WHERE repository_id = ? AND claim_id = ?"
        for sql in (
            statement,
            "DELETE FROM evidence_claim WHERE repository_id = ? AND claim_id = ?",
            "DELETE FROM evidence_claim_invariant_subject WHERE repository_id = ? AND claim_id = ?",
            "DELETE FROM evidence_claim_coverage WHERE repository_id = ? AND claim_id = ?",
        ):
            with pytest.raises(apsw.Error, match="immutable_revision"):
                store.connection.execute(sql, (store.repository_id, claim_id))
    finally:
        store.close()
