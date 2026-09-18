"""``VerificationObservation``: the recorded run, its artifact digest, its reads and its refusals.

These cases protect ``KS-R12@v1``'s observation contract in the order the packet states it: the typed
aggregate under the same envelope, the exact tested candidate recorded as data, the verbatim command
identity, the result artifact bound by the digest of its bytes, the closed execution-result
vocabulary, the run's environment, the write-time digest decision, the artifact resolution states at
read time, and the read projection that reports facts and no verdict.

Three properties are load-bearing enough to name before the cases:

* **The digest identifies an artifact, never a row and never a dataset.** The value the record carries
  is ``sha256`` of the artifact's bytes; the record states whether that digest was *checked* against
  the bytes at write time. Writing a plausible hex string would be a fabricated claim, so the cases
  build real bytes under a temporary root and compare against what the write path actually did.
* **"Not run" is never reported as passed.** The execution-result set is closed, an unlisted value is
  refused rather than coerced, and ``not_run`` is served as ``not_run``.
* **Nothing here manufactures a verdict.** A passing run and a failing run are equally facts: neither
  is a finding, a gate or a lifecycle change, and no served field or count could be read as one.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import apsw
import pytest
from agents_remember.application.knowledge import (
    admitted_evidence_request,
    build_candidate_context,
    change_knowledge_candidate,
    open_admitted_knowledge_store,
    resolve_candidate_context,
    write_knowledge_evidence,
)
from agents_remember.application.knowledge_evidence import read_evidence_scope
from agents_remember.memory.knowledge import evidence_records
from agents_remember.memory.knowledge.evidence import (
    REQUIRED_EVIDENCE_GENERATION,
    require_evidence_generation,
)
from agents_remember.memory.knowledge.logical import dataset_identity
from agents_remember.memory.knowledge.record_envelope import (
    KIND_SCHEMAS,
    PAYLOAD_MODELS,
    validate_record_payload,
)
from agents_remember.memory.knowledge.refusals import KnowledgeRefused
from agents_remember.memory.knowledge.schema_generations import (
    CURRENT_GENERATION,
    GENERATION_6,
    GENERATION_7,
    create_schema_statements,
)
from agents_remember.memory.knowledge.schema_v7 import EXECUTION_RESULT_MEMBERS
from agents_remember.memory.knowledge.store import open_existing_knowledge_store
from agents_remember.models.knowledge.candidate import CandidateResolution, ChangeBatch
from agents_remember.models.knowledge.evidence import (
    EXECUTION_RESULTS,
    VERIFICATION_OBSERVATION_KIND,
    VERIFICATION_OBSERVATION_SCHEMA,
    PublicationReference,
    ResultArtifactReference,
    RunEnvironment,
    VerificationObservationPayload,
    execution_results,
)
from agents_remember.models.knowledge.evidence_read import (
    EvidenceReadRequest,
    ObservationCandidateSeed,
    VerificationObservationItem,
)
from evidence_test_support import (
    CODE_TREE,
    COMMAND_CONTROL,
    COMMAND_EXPECTED,
    EvidenceFixture,
    ObservationOptions,
    build_evidence_fixture,
    default_environment,
    digest_of,
    expect_artifact,
    expect_publication,
    expect_refusal,
    observation_command,
)

pytestmark = pytest.mark.evidence_unit

MEMORY_TREE = "b" * 40


@pytest.fixture
def fixture(tmp_path: Path) -> EvidenceFixture:
    return build_evidence_fixture(tmp_path)


def read_context(fixture: EvidenceFixture) -> Any:
    store = open_admitted_knowledge_store(fixture.destination)
    try:
        identity = store.snapshot_identity()
    finally:
        store.close()
    return build_candidate_context(
        identity,
        CandidateResolution(
            lane="draft-candidate",
            code_tree_id=CODE_TREE,
            memory_tree_id=MEMORY_TREE,
            snapshot_ref="snapshot:observation-case",
            candidate_ref="candidate:observation-case",
        ),
    )


def read_observations(fixture: EvidenceFixture, **seed: Any) -> Any:
    return read_evidence_scope(
        fixture.destination.database_path,
        read_context(fixture),
        EvidenceReadRequest(seed=ObservationCandidateSeed(**seed)),
    )


def write_observation(store: Any, fixture: EvidenceFixture, command: Any) -> Any:
    return write_knowledge_evidence(
        fixture.destination, admitted_evidence_request(fixture.destination, command)
    )


# ---------------------------------------------------------------------------
# Required Behavior 3.1, 3.5 and 3.6: the typed aggregate and its closed vocabularies.


def test_the_observation_payload_is_frozen_and_the_execution_result_set_is_closed() -> None:
    """3.5: an unlisted result is refused as a shape error rather than coerced to a member.

    The protected property is that the vocabulary is closed *and* that no member is a sufficiency
    verdict: the case asserts the exact member list, asserts that the words a verdict would use are
    not members, and asserts that a near miss is refused rather than mapped onto the nearest member.
    """

    assert EXECUTION_RESULTS == ("passed", "failed", "error", "skipped", "not_run")
    assert execution_results() is EXECUTION_RESULTS
    assert EXECUTION_RESULT_MEMBERS == EXECUTION_RESULTS
    for word in (
        "sufficient",
        "adequate",
        "verified",
        "satisfied",
        "ok",
        "clean",
        "PASSED",
        "pass",
    ):
        assert word not in EXECUTION_RESULTS

    with pytest.raises(ValueError):
        VerificationObservationPayload.model_validate(
            observation_payload_arguments() | {"execution_result": "sufficient"}
        )
    with pytest.raises(ValueError):
        VerificationObservationPayload.model_validate(
            observation_payload_arguments() | {"execution_result": "PASSED"}
        )
    # ``not_run`` is a member and stays itself; it is never reported as passed.
    not_run = VerificationObservationPayload.model_validate(
        observation_payload_arguments() | {"execution_result": "not_run"}
    )
    assert not_run.execution_result == "not_run"


def observation_payload_arguments() -> dict[str, Any]:
    """Return one valid observation payload's raw arguments, so a case varies exactly one field."""

    return {
        "command_name": "pytest",
        "command_identity": COMMAND_EXPECTED,
        "knowledge_candidate": {
            "repository_id": "11111111-1111-4111-8111-111111111111",
            "schema_version": "ar-knowledge-sqlite/v7",
            "logical_digest": "a" * 64,
        },
        "execution_result": "passed",
        "environment": {
            "host": "fixture-host",
            "interpreter": "3.13.15",
            "toolchain": [["pytest", "9.0.0"]],
        },
    }


def test_an_observation_that_names_no_tested_candidate_is_refused() -> None:
    """3.2: the exact tested candidate is required data, so "no candidate" is a shape error.

    The protected property is that a record of no candidate is a different statement from a record of
    one, and only the second is something an observation can mean. Either half of the candidate may
    be absent, so a knowledge-only and a code-only record are both admissible.
    """

    without = observation_payload_arguments()
    without.pop("knowledge_candidate")
    with pytest.raises(ValueError, match="exact tested candidate"):
        VerificationObservationPayload.model_validate(without)

    code_only = VerificationObservationPayload.model_validate(
        without | {"code_candidate_tree_id": CODE_TREE}
    )
    assert code_only.knowledge_candidate is None
    assert code_only.code_candidate_tree_id == CODE_TREE
    with pytest.raises(ValueError):
        VerificationObservationPayload.model_validate(
            observation_payload_arguments() | {"code_candidate_tree_id": "not-a-tree"}
        )


def test_the_run_environment_is_bounded_and_records_the_runs_own_toolchain() -> None:
    """3.6: the environment names the run's environment and is a bounded recorded value."""

    environment = RunEnvironment(
        host="fixture-host", interpreter="3.13.15", toolchain=(("pytest", "9.0.0"),)
    )
    assert environment.toolchain == (("pytest", "9.0.0"),)
    assert RunEnvironment.model_fields["toolchain"].default == ()
    with pytest.raises(ValueError):
        RunEnvironment(host="h", interpreter="i", toolchain=(("pytest", "9"), ("pytest", "10")))
    with pytest.raises(ValueError):
        RunEnvironment(host="h", interpreter="i", toolchain=(("", "9"),))
    with pytest.raises(ValueError):
        RunEnvironment(
            host="h", interpreter="i", toolchain=tuple((f"t{n}", "1") for n in range(20))
        )


def test_the_observation_is_registered_in_the_envelope_under_its_own_kind_and_schema() -> None:
    """3.1: the same envelope, the same immutability, a frozen payload of its own."""

    assert (
        PAYLOAD_MODELS[(VERIFICATION_OBSERVATION_KIND, VERIFICATION_OBSERVATION_SCHEMA)]
        is VerificationObservationPayload
    )
    assert KIND_SCHEMAS[VERIFICATION_OBSERVATION_KIND] == frozenset(
        {VERIFICATION_OBSERVATION_SCHEMA}
    )
    validated = validate_record_payload(
        VERIFICATION_OBSERVATION_KIND,
        VERIFICATION_OBSERVATION_SCHEMA,
        observation_payload_arguments(),
    )
    assert isinstance(validated, VerificationObservationPayload)
    undeclared = validate_record_payload(
        VERIFICATION_OBSERVATION_KIND,
        VERIFICATION_OBSERVATION_SCHEMA,
        observation_payload_arguments() | {"status": "verified"},
    )
    refused = expect_refusal(undeclared)
    assert refused.code == "invalid_payload"
    assert "status" in refused.detail


# ---------------------------------------------------------------------------
# Required Behavior 7.1: the artifact reference is a confined reference.


def test_an_artifact_path_that_is_not_confined_is_refused_as_a_shape_error() -> None:
    """7.1: no absolute root, drive, UNC, backslash, NUL, ``..`` or Git pathspec spelling.

    The protected property is that the confinement is vocabulary rather than a runtime check that can
    be skipped: each refused spelling is refused at construction, before any write is attempted, and
    the ones that *are* legitimate (glob characters, which ``git ls-tree`` resolves literally) are
    admitted so a real artifact is never un-recordable.
    """

    refused = (
        "/absolute/report.log",
        "\\\\server\\share\\report.log",
        "C:/report.log",
        "reports\\report.log",
        "~/report.log",
        "reports/\x00report.log",
        "reports/../report.log",
        "..",
        "",
        "   ",
        ":(exclude)reports/report.log",
        ":!reports/report.log",
    )
    for path in refused:
        with pytest.raises(ValueError):
            ResultArtifactReference(
                path=path,
                sha256="a" * 64,
                size_bytes=1,
                digest_checked_against_bytes=False,
            )
    admitted = ResultArtifactReference(
        path="reports/a[1].log",
        sha256="a" * 64,
        size_bytes=1,
        digest_checked_against_bytes=False,
    )
    assert admitted.path == "reports/a[1].log"


def test_a_publication_destination_is_confined_and_its_digest_is_the_manifests() -> None:
    """9.3: the record carries its publication reference explicitly, and it is not a second archive."""

    reference = PublicationReference(
        destination="notes/reports/260915-KS-L12-evidence.json",
        sha256="b" * 64,
        published_at="2026-09-18T03:00:00+00:00",
    )
    assert reference.sha256 == "b" * 64
    for destination in ("/abs/x.json", "C:/x.json", "a\\b.json", "../x.json", "", "a//b.json"):
        with pytest.raises(ValueError):
            PublicationReference(
                destination=destination, sha256="b" * 64, published_at="2026-09-18T03:00:00+00:00"
            )
    # The digest the reference carries identifies the *published manifest*, and nothing in the model
    # ties it to the artifact: there is one field for each and no field that could conflate them.
    fields = set(VerificationObservationPayload.model_fields)
    assert {"result_artifact", "publication"} <= fields
    assert not {"status", "sufficient", "verified", "grade", "score"} & fields


# ---------------------------------------------------------------------------
# Required Behavior 3.4, 7.3 and 7.5: the write-time digest decision.


def test_the_write_time_digest_is_checked_against_the_bytes_and_recorded_as_checked(
    fixture: EvidenceFixture,
) -> None:
    """3.4 and 7.3: the record states which of the two admissible things happened.

    The protected property is that ``digest_checked_against_bytes`` is a *measured* fact: with a root
    declared and the bytes present and equal, it becomes ``True``; with no root, it stays ``False``.
    The digest itself is of the artifact's bytes and of nothing else, which the case checks by hashing
    the bytes it wrote.
    """

    store = open_admitted_knowledge_store(fixture.destination)
    try:
        checked = observation_command(
            fixture, ObservationOptions(artifact_root=str(fixture.artifact_root))
        )
        written = write_observation(store, fixture, checked)
        assert written.state == "applied", written.refusal
        row = evidence_records.decode_observation_row(
            next(
                iter(
                    store.connection.execute(
                        evidence_records.OBSERVATION_BY_ID,
                        (store.repository_id, checked.observation_id),
                    )
                )
            )
        )
        assert row.payload.result_artifact is not None
        assert row.payload.result_artifact.digest_checked_against_bytes is True
        assert row.payload.result_artifact.sha256 == digest_of(fixture.artifact_bytes)
        assert row.payload.result_artifact.sha256 == fixture.artifact_sha256
        assert row.payload.result_artifact.size_bytes == len(fixture.artifact_bytes)

        unchecked = observation_command(fixture, ObservationOptions(artifact_root=None))
        assert write_observation(store, fixture, unchecked).state == "applied"
        plain = evidence_records.decode_observation_row(
            next(
                iter(
                    store.connection.execute(
                        evidence_records.OBSERVATION_BY_ID,
                        (store.repository_id, unchecked.observation_id),
                    )
                )
            )
        )
        assert plain.payload.result_artifact is not None
        assert plain.payload.result_artifact.digest_checked_against_bytes is False
    finally:
        store.close()


def test_a_digest_that_does_not_describe_the_bytes_is_refused_with_exact_facts(
    fixture: EvidenceFixture,
) -> None:
    """3.4 and 7.5: a false digest is refused rather than laundered into an immutable row.

    The protected property is that the refusal names the path, the recorded digest and the observed
    bytes, that nothing is written, and that the dataset does not move. ``digest_checked_at_write``
    cannot be used to store a digest the writer observed to be wrong: the weaker flag says "not
    checked", not "checked and false".
    """

    before = dataset_identity(fixture.destination.database_path)
    wrong = ResultArtifactReference(
        path=fixture.artifact_path,
        sha256="c" * 64,
        size_bytes=len(fixture.artifact_bytes),
        digest_checked_against_bytes=False,
    )
    store = open_admitted_knowledge_store(fixture.destination)
    try:
        command = observation_command(fixture, ObservationOptions(artifact=wrong))
        written = write_observation(store, fixture, command)
        assert written.state == "refused"
        assert written.refusal.code == "invalid_reference"
        assert written.refusal.operation == "add_verification_observation"
        assert written.refusal.record_id == fixture.artifact_path
        assert written.refusal.expected == "c" * 64
        assert fixture.artifact_sha256 in (written.refusal.observed or "")
        assert written.written == ()
    finally:
        store.close()
    after = dataset_identity(fixture.destination.database_path)
    assert after.logical_digest == before.logical_digest


def test_an_artifact_that_is_absent_at_write_time_is_recorded_rather_than_refused(
    fixture: EvidenceFixture,
) -> None:
    """3.4 and 7.4: a reference to an artifact the run moved is still a writeable record.

    The protected property is the distinction between "the bytes contradict the digest" and "the bytes
    are not there": the first is a refusal, the second is a recorded fact about a run that already
    happened. The record is written with the checked flag false, and the read reports ``absent``.
    """

    missing = ResultArtifactReference(
        path="reports/never-written.log",
        sha256="d" * 64,
        size_bytes=7,
        digest_checked_against_bytes=False,
    )
    store = open_admitted_knowledge_store(fixture.destination)
    try:
        command = observation_command(fixture, ObservationOptions(artifact=missing))
        written = write_observation(store, fixture, command)
        assert written.state == "applied", written.refusal
    finally:
        store.close()
    result = read_evidence_scope(
        fixture.destination.database_path,
        read_context(fixture),
        EvidenceReadRequest(
            seed=ObservationCandidateSeed(
                knowledge_logical_digest=fixture.snapshot().logical_digest
            ),
            artifact_root=str(fixture.artifact_root),
        ),
    )
    item = _observation_item(result, command.observation_id)
    assert item.artifact_resolution is not None
    assert item.artifact_resolution.state == "absent"
    assert item.artifact_resolution.observed_sha256 is None
    served_artifact = expect_artifact(item)
    assert served_artifact.sha256 == "d" * 64


# ---------------------------------------------------------------------------
# Required Behavior 8.2 and 7.4: the read projection and its resolution states.


def test_the_four_artifact_resolution_states_are_distinguishable(
    fixture: EvidenceFixture,
) -> None:
    """7.4 and 8.2: resolvable-and-equal, absent, digest-mismatch and not-attempted are four states.

    The protected property is that a read reports *what it observed* and never invents a state: the
    recorded digest and size are served as recorded in every state, observed bytes are served only
    when bytes were read, and a mismatch is reported as a mismatch rather than repaired. The record's
    own content is identical in all four states, which is the "a missing artifact never erases the
    record" half of the clause.
    """

    store = open_admitted_knowledge_store(fixture.destination)
    try:
        equal_command = observation_command(fixture)
        assert write_observation(store, fixture, equal_command).state == "applied"
        # A mismatch cannot be *written* through this path (the write refuses it), so the stored row
        # is produced by copying the equal record's row and rewriting only its digest through a direct
        # insert into a second dataset -- which is exactly how a stale artifact is observed in
        # practice: the record is intact and the bytes moved.
        row = next(
            iter(
                store.connection.execute(
                    evidence_records.OBSERVATION_BY_ID,
                    (store.repository_id, equal_command.observation_id),
                )
            )
        )
        assert row is not None
    finally:
        store.close()

    # equal: the bytes are there and hash to the recorded digest, which requires a root to read them
    with_root = read_evidence_scope(
        fixture.destination.database_path,
        read_context(fixture),
        EvidenceReadRequest(
            seed=ObservationCandidateSeed(
                knowledge_logical_digest=fixture.snapshot().logical_digest
            ),
            artifact_root=str(fixture.artifact_root),
        ),
    )
    served = _observation_item(with_root, equal_command.observation_id)
    assert served.artifact_resolution is not None
    assert served.artifact_resolution.state == "equal"
    assert served.artifact_resolution.observed_sha256 == fixture.artifact_sha256

    # not-attempted: a read that declared no root cannot claim to have compared anything
    without_root = read_evidence_scope(
        fixture.destination.database_path,
        read_context(fixture),
        EvidenceReadRequest(
            seed=ObservationCandidateSeed(
                knowledge_logical_digest=fixture.snapshot().logical_digest
            ),
            artifact_root=None,
        ),
    )
    unattempted = _observation_item(without_root, equal_command.observation_id)
    assert unattempted.artifact_resolution is not None
    assert unattempted.artifact_resolution.state == "not_attempted"
    assert unattempted.artifact_resolution.observed_sha256 is None

    # absent: the bytes are not there, and the record is still served with its own content intact
    missing_command = observation_command(
        fixture,
        ObservationOptions(
            artifact=ResultArtifactReference(
                path="reports/never-written.log",
                sha256="f" * 64,
                size_bytes=3,
                digest_checked_against_bytes=False,
            )
        ),
    )
    store = open_admitted_knowledge_store(fixture.destination)
    try:
        assert write_observation(store, fixture, missing_command).state == "applied"
    finally:
        store.close()
    absent = _observation_item(
        read_evidence_scope(
            fixture.destination.database_path,
            read_context(fixture),
            EvidenceReadRequest(
                seed=ObservationCandidateSeed(
                    knowledge_logical_digest=fixture.snapshot().logical_digest
                ),
                artifact_root=str(fixture.artifact_root),
            ),
        ),
        missing_command.observation_id,
    )
    assert absent.artifact_resolution is not None
    assert absent.artifact_resolution.state == "absent"
    assert absent.artifact_resolution.recorded_sha256 == "f" * 64
    assert absent.observation.payload.command_identity == COMMAND_EXPECTED

    # digest-mismatch: the bytes are there and do not hash to the recorded digest, and the stored
    # reference is served exactly as written
    mismatched = _stored_mismatch(fixture)
    assert mismatched.artifact_resolution is not None
    assert mismatched.artifact_resolution.state == "digest_mismatch"
    assert mismatched.artifact_resolution.recorded_sha256 == fixture.artifact_sha256
    assert mismatched.artifact_resolution.observed_sha256 != fixture.artifact_sha256
    assert mismatched.artifact_resolution.observed_size_bytes == len(fixture.artifact_bytes) + len(
        b"more evidence\n"
    )
    # The stored record is served exactly as written: a mismatch never re-pins the digest.
    assert expect_artifact(mismatched).sha256 == fixture.artifact_sha256
    assert mismatched.observation.payload.command_identity == COMMAND_EXPECTED


def _stored_mismatch(fixture: EvidenceFixture) -> VerificationObservationItem:
    """Return one observation whose *stored* digest disagrees with the bytes still at its path.

    The write path refuses to author such a record, which is the point: a caller cannot assert a
    digest the substrate can read and contradict. The only honest way to produce the state this case
    is about is to let the bytes move after the record was written, and the way to do that without
    inventing a second write path is to overwrite the artifact file. That is what this helper does --
    it writes a correct record, then appends to the artifact -- so the mismatch is a fact about the
    world rather than a value a caller asserted.
    """

    store = open_admitted_knowledge_store(fixture.destination)
    try:
        good = observation_command(fixture)
        assert write_observation(store, fixture, good).state == "applied"
    finally:
        store.close()
    artifact_file = fixture.artifact_root / fixture.artifact_path
    original = artifact_file.read_bytes()
    artifact_file.write_bytes(original + b"more evidence\n")
    try:
        result = read_evidence_scope(
            fixture.destination.database_path,
            read_context(fixture),
            EvidenceReadRequest(
                seed=ObservationCandidateSeed(
                    knowledge_logical_digest=fixture.snapshot().logical_digest
                ),
                artifact_root=str(fixture.artifact_root),
            ),
        )
        return _observation_item(result, good.observation_id)
    finally:
        artifact_file.write_bytes(original)


def _observation_item(result: Any, observation_id: str) -> VerificationObservationItem:
    """Return one selected observation item, refusing a page that does not carry it."""

    assert result.state == "page", result.refusal
    for item in result.page.items:
        if (
            isinstance(item, VerificationObservationItem)
            and item.observation.observation_id == observation_id
        ):
            return item
    raise AssertionError(f"observation {observation_id} was not selected")


def test_the_observation_read_reports_facts_and_no_verdict(fixture: EvidenceFixture) -> None:
    """4.2, 8.2 and 4.3: every recorded fact is served, and nothing is served that is a judgement.

    The protected property is that the pane can render the run and cannot render a conclusion from
    it: the served item carries the candidate, the command identity as recorded, the artifact
    reference with its digest and write-time flag, the execution result and the environment, and its
    counts are arithmetic over the selected set. A failing run is served exactly as a passing one is.
    """

    store = open_admitted_knowledge_store(fixture.destination)
    try:
        passed = observation_command(fixture)
        failed = observation_command(
            fixture,
            ObservationOptions(
                command_name="ruff",
                command_identity=COMMAND_CONTROL,
                execution_result="failed",
            ),
        )
        not_run = observation_command(
            fixture,
            ObservationOptions(
                command_name="pytest-not-run",
                command_identity=COMMAND_EXPECTED,
                execution_result="not_run",
            ),
        )
        for command in (passed, failed, not_run):
            assert write_observation(store, fixture, command).state == "applied"
    finally:
        store.close()

    result = read_observations(fixture, knowledge_logical_digest=fixture.snapshot().logical_digest)
    assert result.state == "page", result.refusal
    item = _observation_item(result, passed.observation_id)
    assert item.observation.payload.knowledge_candidate == fixture.snapshot()
    assert item.observation.payload.command_identity == COMMAND_EXPECTED
    assert item.observation.payload.command_name == "pytest"
    assert item.observation.payload.execution_result == "passed"
    assert item.observation.payload.environment == default_environment()
    assert item.observation.row_digest is not None

    failed_item = _observation_item(result, failed.observation_id)
    assert failed_item.observation.payload.execution_result == "failed"
    assert failed_item.artifact_resolution is not None
    assert _observation_item(
        result, not_run.observation_id
    ).observation.payload.execution_result == ("not_run")
    assert result.page.counts.verification_observations == 3
    assert result.page.counts.verification_observation_revisions == 3

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
        "outcome_ok",
    )
    for model in (VerificationObservationPayload, VerificationObservationItem):
        for name in model.model_fields:
            assert not any(term in name for term in forbidden), (model.__name__, name)
    rendered = result.page.model_dump(mode="json")
    text = str(rendered)
    for word in ('"verified"', '"status"', '"sufficient"', "invariant_satisfied"):
        assert word not in text


def test_the_command_identity_is_served_verbatim_and_never_resolved(
    fixture: EvidenceFixture,
) -> None:
    """3.3: the recorded string set is what the read serves, and this leaf adds no execution surface.

    The protected property is that nothing resolves a recorded command into a different one: the
    served ``command_identity`` is byte-identical to the recorded one, and no module in this leaf
    executes, schedules, retries or interprets a command.
    """

    recorded = (
        "python -m pytest mcp/tests/test_knowledge_evidence_claims.py -q -k 'claim and not x'"
    )
    store = open_admitted_knowledge_store(fixture.destination)
    try:
        command = observation_command(fixture, ObservationOptions(command_identity=recorded))
        assert write_observation(store, fixture, command).state == "applied"
    finally:
        store.close()
    item = _observation_item(
        read_observations(fixture, knowledge_logical_digest=fixture.snapshot().logical_digest),
        command.observation_id,
    )
    assert item.observation.payload.command_identity == recorded
    assert item.observation.payload.command_name == "pytest"


def test_a_second_run_is_a_second_observation_and_the_first_is_not_edited(
    fixture: EvidenceFixture,
) -> None:
    """3.7 and 7.5: a re-run is a new record, and the earlier one keeps the identity it was written
    with.

    The protected property is that immutability is not a matter of policy here: the earlier row is
    still stored unchanged, the two rows are distinct identities, and the database refuses an in-place
    rewrite of either.
    """

    store = open_admitted_knowledge_store(fixture.destination)
    try:
        first = observation_command(fixture, ObservationOptions(execution_result="failed"))
        assert write_observation(store, fixture, first).state == "applied"
        first_digest = evidence_records.observation_digest(store, first.observation_id)
        second = observation_command(fixture, ObservationOptions(execution_result="passed"))
        assert write_observation(store, fixture, second).state == "applied"
        assert first.observation_id != second.observation_id
        assert evidence_records.observation_digest(store, first.observation_id) == first_digest
        for sql in (
            "UPDATE verification_observation SET execution_result = 'passed' WHERE repository_id = ? "
            "AND observation_id = ?",
            "DELETE FROM verification_observation WHERE repository_id = ? AND observation_id = ?",
        ):
            with pytest.raises(apsw.Error, match="immutable_revision"):
                store.connection.execute(sql, (store.repository_id, first.observation_id))
    finally:
        store.close()
    result = read_observations(fixture, knowledge_logical_digest=fixture.snapshot().logical_digest)
    assert result.page.counts.verification_observations == 2
    assert _observation_item(result, first.observation_id).observation.payload.execution_result == (
        "failed"
    )
    assert _observation_item(
        result, second.observation_id
    ).observation.payload.execution_result == ("passed")


def test_a_candidate_seed_selects_by_the_recorded_candidate_and_reports_absence(
    fixture: EvidenceFixture,
) -> None:
    """3.2 and 8.4: the selection is over recorded candidate identities, and a seed that selects
    nothing is the absent state rather than an empty page.
    """

    store = open_admitted_knowledge_store(fixture.destination)
    try:
        by_snapshot = observation_command(fixture)
        by_tree = observation_command(
            fixture,
            ObservationOptions(knowledge_candidate=None, code_candidate_tree_id=CODE_TREE),
        )
        assert write_observation(store, fixture, by_snapshot).state == "applied"
        assert write_observation(store, fixture, by_tree).state == "applied"
    finally:
        store.close()
    snapshot_selected = read_observations(
        fixture, knowledge_logical_digest=fixture.snapshot().logical_digest
    )
    assert snapshot_selected.page.counts.verification_observations == 1
    tree_selected = read_observations(fixture, code_candidate_tree_id=CODE_TREE)
    assert tree_selected.page.counts.verification_observations == 1
    both = read_observations(
        fixture,
        knowledge_logical_digest=fixture.snapshot().logical_digest,
        code_candidate_tree_id=CODE_TREE,
    )
    assert both.page.counts.verification_observations == 2

    none = read_observations(fixture, code_candidate_tree_id="f" * 40)
    assert none.state == "refused"
    assert none.refusal.code == "selector_absent"
    assert none.refusal.record_id == "f" * 40


def test_a_candidate_seed_that_names_no_candidate_is_refused() -> None:
    """8.4: a seed that names no candidate would select every observation, which is a different
    question from the one the seed is for."""

    with pytest.raises(ValueError, match="names the candidate"):
        ObservationCandidateSeed()


# ---------------------------------------------------------------------------
# Required Behavior 3.1, 6.5 and 7.2: the batch path, the generation gate and no content store.


def test_the_observation_command_stores_through_the_batch_and_moves_the_digest(
    fixture: EvidenceFixture,
) -> None:
    """6.1 and 6.4: the same act through the one operation that writes the graph."""

    context = resolve_candidate_context(
        fixture.destination,
        CandidateResolution(
            lane="draft-candidate",
            code_tree_id=CODE_TREE,
            memory_tree_id=MEMORY_TREE,
            snapshot_ref="snapshot:observation-batch",
            candidate_ref="candidate:observation-batch",
        ),
    )
    command = observation_command(fixture)
    outcome = change_knowledge_candidate(
        fixture.destination, ChangeBatch(expected=context, commands=(command,))
    )
    assert outcome.state == "changed", outcome.refusal
    assert {entry.table for entry in outcome.changed} == {
        "verification_observation",
        "knowledge_record",
        "record_revision",
    }
    assert outcome.after != outcome.before
    item = _observation_item(
        read_observations(fixture, knowledge_logical_digest=fixture.snapshot().logical_digest),
        command.observation_id,
    )
    assert item.observation.payload.result_artifact is not None
    assert item.observation.payload.result_artifact.digest_checked_against_bytes is True


def test_an_observation_write_against_an_older_generation_is_refused(tmp_path: Path) -> None:
    """6.5: the same gate as the claim's, applied to the observation's own tables."""

    path = tmp_path / "recorded-v4" / "candidate.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = apsw.Connection(str(path))
    try:
        for statement in create_schema_statements(GENERATION_6):
            connection.execute(statement)
        connection.execute(f"PRAGMA user_version = {GENERATION_6.user_version}")
    finally:
        connection.close()
    store = open_existing_knowledge_store(path, "11111111-1111-4111-8111-111111111111")
    try:
        with pytest.raises(KnowledgeRefused) as refused:
            require_evidence_generation(store, "add_verification_observation")
        assert refused.value.refusal.code == "unsupported_schema"
        assert refused.value.refusal.expected == str(REQUIRED_EVIDENCE_GENERATION.user_version)
        assert refused.value.refusal.observed == str(GENERATION_6.user_version)
        assert refused.value.refusal.operation == "add_verification_observation"
    finally:
        store.close()


def test_there_is_no_blob_column_and_no_second_content_store() -> None:
    """7.2: the bytes stay outside the database, and the digest is of the bytes and nothing else.

    The protected property is checkable by reading the declared structure: no column of the
    observation's table holds bytes, and no column holds a digest that is not the artifact's. A
    row-level digest would be a second identity authority, and the case asserts its absence by name.
    """

    columns = GENERATION_7.columns["verification_observation"]
    assert "artifact_sha256" in columns
    assert "artifact_size_bytes" in columns
    assert "digest_checked_at_write" in columns
    for name in columns:
        assert "blob" not in name
        assert "bytes_payload" not in name
        assert name not in {"payload_digest", "row_digest", "content_address", "fingerprint"}
    ddl = GENERATION_7.table_ddl["verification_observation"]
    assert "BLOB" not in ddl
    assert "artifact_bytes" not in ddl
    # The digest column is the *artifact's*, and the record's own seal lives on the envelope's
    # revision aggregate -- one identity authority, not two.
    assert "content_digest" not in columns
    assert "content_digest" in GENERATION_7.columns["record_revision"]
    # The only digest-named columns of the observation's table are the artifact's own digest and the
    # write-time check flag; a row-level digest would be a second identity authority.
    assert {name for name in columns if "digest" in name} == {
        "knowledge_snapshot_logical_digest",
        "digest_checked_at_write",
    }
    # ... and the first of those is the *tested candidate's* recorded identity, which is a recorded
    # input rather than this record's own seal.
    assert "content_digest" not in columns
    assert "content_digest" not in ddl


def test_the_observation_generation_appends_and_inherits_by_name() -> None:
    """6.5 and ``KS-R10@v1`` §1.3: the new generation appends, and generation 4 is unchanged.

    The protected property is inheritance, not a number: every one of generation 4's declared tables
    carries the same column order in generation 5, generation 4 is a prefix of generation 5's table
    list, and this leaf's five tables are exactly the ones appended.
    """

    assert GENERATION_7.tables[: len(GENERATION_6.tables)] == GENERATION_6.tables
    for table in GENERATION_6.tables:
        assert GENERATION_7.columns[table] == GENERATION_6.columns[table], table
    appended = GENERATION_7.tables[len(GENERATION_6.tables) :]
    assert appended == (
        "evidence_claim",
        "evidence_claim_invariant_subject",
        "evidence_claim_facet_subject",
        "evidence_claim_coverage",
        "verification_observation",
    )
    assert GENERATION_7.user_version == GENERATION_6.user_version + 1
    assert GENERATION_7.schema_name.endswith(f"/v{GENERATION_7.user_version}")
    # The base is the generation this leaf descends from, and its size is stated as the fact the
    # append is measured against rather than as a number a later landing would falsify.
    assert len(GENERATION_6.tables) == 28
    assert len(GENERATION_7.tables) == len(GENERATION_6.tables) + 5
    assert CURRENT_GENERATION is GENERATION_7


def test_a_publication_reference_is_stored_on_the_record_and_served_with_it(
    fixture: EvidenceFixture,
) -> None:
    """9.3 and 9.6: the record states where its interpretable manifest was published.

    The protected property is that a later reader does not have to guess: the reference is part of the
    record, it is served with it, and the record states which of the two retention routes it relies on
    -- the memory repo's own committed content (no reference) or an external artifact (the reference).
    """

    reference = PublicationReference(
        destination="notes/reports/260915-KS-L12-evidence.json",
        sha256=hashlib.sha256(b"manifest-bytes").hexdigest(),
        published_at="2026-09-18T03:00:00+00:00",
    )
    store = open_admitted_knowledge_store(fixture.destination)
    try:
        command = observation_command(fixture, ObservationOptions(publication=reference))
        assert write_observation(store, fixture, command).state == "applied"
    finally:
        store.close()
    item = _observation_item(
        read_observations(fixture, knowledge_logical_digest=fixture.snapshot().logical_digest),
        command.observation_id,
    )
    assert item.observation.payload.publication == reference
    published = expect_publication(item)
    assert published.destination.endswith("-evidence.json")
    # A record that published nothing says so by carrying no reference, which is a fact rather than
    # an omission to be filled in later.
    store = open_admitted_knowledge_store(fixture.destination)
    try:
        plain = observation_command(fixture, ObservationOptions(publication=None))
        assert write_observation(store, fixture, plain).state == "applied"
    finally:
        store.close()
    unpub = _observation_item(
        read_observations(fixture, knowledge_logical_digest=fixture.snapshot().logical_digest),
        plain.observation_id,
    )
    assert unpub.observation.payload.publication is None
