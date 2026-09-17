"""Focused behaviour of the candidate lifecycle: creation, restart, isolation and disposal.

Each case protects one consequential failure: a candidate whose receipt does not bind it to the
admission it is opened under, an occupied destination that must not be re-initialized, two
candidates that must not share a file or a row, a restart that must keep the committed batch and
drop the abandoned one, and a disposal that must not be authorized by a stale identity or by a
publication that carries different knowledge.

Every refusal case measures the stored database before and after, because "the working database
was left exactly as it was" is the property the whole lifecycle exists to provide.
"""

from __future__ import annotations

from contextlib import suppress
from pathlib import Path

import apsw
import pytest
from agents_remember.application.knowledge_snapshot import (
    authorize_knowledge_candidate_disposal,
    open_knowledge_candidate,
)
from agents_remember.memory.knowledge import candidate_workspace
from agents_remember.memory.knowledge.schema_generations import (
    CURRENT_GENERATION,
    GENERATION_1,
)
from agents_remember.models.knowledge.candidate import SnapshotIdentity
from agents_remember.models.knowledge.snapshot import (
    DiscardCandidate,
    PublishedCandidate,
    candidate_receipt_path,
)
from generation_test_support import create_generation_1_store, declared_pair
from snapshot_lifecycle_test_support import (
    SnapshotCase,
    build_case,
    clone_from,
    crash_and_abandon,
    create,
    derive_case,
    file_digest,
    live_identity,
    live_store,
    open_candidate,
    publish,
    read_identity,
    row_counts,
    write_record,
)

pytestmark = pytest.mark.evidence_unit


@pytest.fixture
def candidate(tmp_path: Path) -> SnapshotCase:
    return build_case(tmp_path)


def test_a_created_candidate_reopens_with_its_receipt_and_the_declared_schema(
    candidate: SnapshotCase,
) -> None:
    """Re-scoped by `KS-R10` §Shipped Assertions.

    Both of the generation assertions this case shipped with are falsified by requirement 2.7:
    creation declares the *newest* generation the build supports -- generation 2 after this leaf --
    rather than the build's generation 1. The replacement fact reads the declared generation's own
    ``(schema name, user_version)`` pair and that generation's **recorded** fingerprint constant,
    from its own record, instead of the running build's live ``schema_fingerprint()``. The
    generation-1 fact stays asserted, as its own case, by
    :func:`test_a_version_1_candidate_keeps_the_receipt_it_was_written_with` below.
    """

    created = create(candidate)

    assert created.state == "created"
    assert created.identity is not None
    assert created.receipt is not None
    assert created.identity.repository_id == candidate.repository.repository_id
    assert created.identity.schema_version == CURRENT_GENERATION.schema_name
    assert created.receipt.schema_version == CURRENT_GENERATION.schema_name
    assert created.receipt.schema_fingerprint == CURRENT_GENERATION.fingerprint
    assert created.receipt.candidate_ref == candidate.resolution.candidate_ref
    assert created.receipt.memory.tree_id == candidate.resolution.memory_tree_id

    reopened = open_candidate(candidate)

    assert reopened.state == "resumed"
    assert reopened.identity == created.identity
    assert reopened.receipt == created.receipt
    assert candidate.database_path.exists()
    assert candidate.receipt_path.exists()
    assert row_counts(candidate.database_path)["repository"] == 1


def test_a_version_1_candidate_keeps_the_receipt_it_was_written_with(
    candidate: SnapshotCase,
) -> None:
    """The generation-1 half of the re-scoped assertion, on a dataset that really is generation 1.

    A version-1 store opened by generation-2 code reports generation 1's own pair and generation 1's
    recorded fingerprint, so the receipt written against it carries those facts and the store keeps
    its version instead of being silently upgraded.
    """

    repository_id = candidate.repository.repository_id
    with create_generation_1_store(candidate.database_path, repository_id) as store:
        assert store.schema.schema_name == GENERATION_1.schema_name
        assert store.schema.user_version == GENERATION_1.user_version
        assert store.schema.fingerprint == GENERATION_1.fingerprint
        assert store.snapshot_identity().schema_version == GENERATION_1.schema_name
        assert declared_pair(candidate.database_path) == (
            GENERATION_1.schema_name,
            GENERATION_1.user_version,
        )


def test_two_candidates_cloned_from_one_baseline_diverge_and_share_no_file_or_row(
    tmp_path: Path,
) -> None:
    baseline = build_case(tmp_path, name="baseline")
    create(baseline)
    baseline_invariant, _ = write_record(baseline, "The baseline statement both clones start on.")
    baseline_identity = live_identity(baseline)
    published = publish(baseline)
    assert published.state == "published"

    left = derive_case(baseline, "left")
    right = derive_case(baseline, "right")
    assert clone_from(left, baseline.destination_path(), baseline_identity).state == "created"
    assert clone_from(right, baseline.destination_path(), baseline_identity).state == "created"
    assert left.database_path != right.database_path
    assert left.database_path.stat().st_ino != right.database_path.stat().st_ino

    left_invariant, _ = write_record(left, "Only the left candidate authors this statement.")

    left_store, right_store = live_store(left), live_store(right)
    try:
        assert left_store.get_invariant(left_invariant) is not None
        assert right_store.get_invariant(left_invariant) is None
        assert left_store.get_invariant(baseline_invariant) is not None
        assert right_store.get_invariant(baseline_invariant) is not None
        assert left_store.connection is not right_store.connection
    finally:
        left_store.connection.close()
        right_store.connection.close()
    assert live_identity(left) != live_identity(right)
    assert live_identity(right) == baseline_identity


def test_an_existing_destination_is_a_resume_attempt_not_an_initialization_target(
    candidate: SnapshotCase,
) -> None:
    create(candidate)
    invariant_id, _ = write_record(candidate, "Unpublished work the second create must not touch.")
    database_before = file_digest(candidate.database_path)
    receipt_before = candidate.receipt_path.read_bytes()

    refused = create(candidate)

    assert refused.state == "refused"
    assert refused.refusal is not None
    assert refused.refusal.code == "destination_occupied"
    assert refused.identity is None and refused.receipt is None
    assert file_digest(candidate.database_path) == database_before
    assert candidate.receipt_path.read_bytes() == receipt_before
    store = live_store(candidate)
    try:
        assert store.get_invariant(invariant_id) is not None
    finally:
        store.connection.close()


def test_a_candidate_the_admission_cannot_verify_is_refused_with_its_bytes_intact(
    candidate: SnapshotCase,
) -> None:
    create(candidate)
    write_record(candidate, "Authored work that a refused admission must leave untouched.")
    database_before = file_digest(candidate.database_path)
    rebound = candidate.candidate().model_copy(
        update={"resolution": candidate.resolution.model_copy(update={"memory_tree_id": "e" * 40})}
    )

    refused = open_knowledge_candidate(rebound)

    assert refused.state == "refused"
    assert refused.refusal is not None
    assert refused.refusal.code == "candidate_binding_changed"
    assert "memory" in refused.refusal.detail

    candidate.receipt_path.write_bytes(b'{"receipt_version":"ar-knowledge-candidate-receipt/v1"}')
    unreadable = open_candidate(candidate)

    assert unreadable.state == "refused"
    assert unreadable.refusal is not None
    assert unreadable.refusal.code == "selected_input_unavailable"
    assert file_digest(candidate.database_path) == database_before


def test_a_missing_database_or_receipt_is_an_input_error_that_creates_nothing(
    tmp_path: Path,
) -> None:
    absent = build_case(tmp_path, name="absent")

    refused = open_candidate(absent)

    assert refused.state == "refused"
    assert refused.refusal is not None
    assert refused.refusal.code == "selected_input_unavailable"
    assert not absent.directory().exists()

    missing_baseline = derive_case(absent, "clone-of-nothing")
    cloned = clone_from(
        missing_baseline,
        tmp_path / "never-written.sqlite",
        SnapshotIdentity(
            repository_id=absent.repository.repository_id,
            schema_version="ar-knowledge-sqlite/v1",
            logical_digest="f" * 64,
        ),
    )

    assert cloned.state == "refused"
    assert cloned.refusal is not None
    assert cloned.refusal.code == "selected_input_unavailable"
    assert not missing_baseline.directory().exists()

    candidate = build_case(tmp_path, name="candidate")
    create(candidate)
    write_record(candidate, "Work that must survive a lost receipt without being replaced.")
    database_before = file_digest(candidate.database_path)
    candidate.receipt_path.unlink()

    receiptless = open_candidate(candidate)

    assert receiptless.state == "refused"
    assert receiptless.refusal is not None
    assert receiptless.refusal.code == "selected_input_unavailable"
    assert file_digest(candidate.database_path) == database_before
    assert row_counts(candidate.database_path)["invariant"] == 1


def test_a_live_reader_does_not_let_the_write_boundarys_close_lose_the_commit(
    candidate: SnapshotCase,
) -> None:
    """A closing writer must not unlink journal state a live reader has pinned.

    This is the regression node for the removed close-path unlink. With a read transaction open on
    one connection, SQLite cannot checkpoint the writer's committed frames, so they are still only
    in the WAL when the writing store closes. Unlinking that WAL there destroyed the committed
    batch and left the candidate unreadable; both assertions below fail if the unlink comes back,
    because the batch is authored through the write boundary and that boundary owns the close.
    """

    create(candidate)
    marker = "A batch committed while another connection held a read snapshot."
    reader = live_store(candidate)
    try:
        reader.connection.execute("PRAGMA journal_mode=WAL")
        reader.connection.execute("BEGIN")
        reader.connection.execute("SELECT count(*) FROM invariant")

        invariant_id, result = write_record(candidate, marker)

        assert result.state == "changed"
        journal = candidate.database_path.with_name(candidate.database_path.name + "-wal")
        assert journal.exists() and journal.stat().st_size > 0, (
            "the closing write boundary removed committed journal state that SQLite keeps while "
            "another connection holds a snapshot"
        )
    finally:
        with suppress(apsw.Error):
            reader.connection.execute("ROLLBACK")
        reader.connection.close()

    reopened = open_candidate(candidate)
    assert reopened.state == "resumed"
    store = live_store(candidate)
    try:
        assert store.get_invariant(invariant_id) is not None
    finally:
        store.connection.close()


def test_a_crash_restart_keeps_the_committed_batch_and_drops_the_abandoned_one(
    candidate: SnapshotCase,
) -> None:
    create(candidate)
    marker = "crash-committed"

    outcome = crash_and_abandon(candidate, marker)

    assert outcome.wal_bytes > 0, (
        "the crash case did not leave a journal-resident committed batch, so it would prove "
        "nothing about recovery"
    )
    assert sorted(outcome.peer_names) == sorted(
        [
            f"{candidate.database_path.name}-shm",
            f"{candidate.database_path.name}-wal",
        ]
    )
    reopened = open_candidate(candidate)
    assert reopened.state == "resumed"
    assert reopened.identity == outcome.identity
    store = live_store(candidate)
    try:
        labels = {
            str(row[0]) for row in store.connection.execute("SELECT display_label FROM invariant")
        }
    finally:
        store.connection.close()
    assert marker in labels
    assert f"{marker}-uncommitted" not in labels
    assert not candidate.destination_path().exists()


def test_a_discard_disposition_must_name_the_current_candidate_identity(
    candidate: SnapshotCase,
) -> None:
    create(candidate)
    stale = live_identity(candidate)
    write_record(candidate, "Work authored after the discard authorization was written.")
    current = live_identity(candidate)
    assert current != stale

    refused = authorize_knowledge_candidate_disposal(
        candidate.candidate(),
        DiscardCandidate(candidate=stale, authorization_ref="disposition:stale"),
    )

    assert refused.state == "refused"
    assert refused.refusal is not None
    assert refused.refusal.code == "stale_precondition"
    assert refused.refusal.expected == stale.logical_digest
    assert refused.refusal.observed == current.logical_digest

    allowed = authorize_knowledge_candidate_disposal(
        candidate.candidate(),
        DiscardCandidate(candidate=current, authorization_ref="disposition:current"),
    )

    assert allowed.state == "disposable"
    assert allowed.observed == current
    assert candidate.database_path.exists()
    assert live_identity(candidate) == current


def test_a_published_disposition_requires_a_publication_of_that_exact_dataset(
    candidate: SnapshotCase,
) -> None:
    create(candidate)
    write_record(candidate, "The record the first publication carries.")
    first = live_identity(candidate)
    assert publish(candidate, expected_candidate=first).state == "published"

    write_record(candidate, "A newer record the first publication does not carry.")
    current = live_identity(candidate)
    assert current != first

    stale = authorize_knowledge_candidate_disposal(
        candidate.candidate(),
        PublishedCandidate(candidate=current, published_path=candidate.destination_path()),
    )

    assert stale.state == "refused"
    assert stale.refusal is not None
    assert stale.refusal.code == "stale_precondition"
    assert stale.refusal.expected == current.logical_digest
    assert stale.refusal.observed == first.logical_digest

    republished = publish(
        candidate,
        expected_candidate=current,
        expected_destination=first,
        admitted_absent=False,
    )
    assert republished.state == "published"
    assert read_identity(candidate.destination_path()) == current

    allowed = authorize_knowledge_candidate_disposal(
        candidate.candidate(),
        PublishedCandidate(candidate=current, published_path=candidate.destination_path()),
    )

    assert allowed.state == "disposable"
    assert allowed.observed == current

    missing = authorize_knowledge_candidate_disposal(
        candidate.candidate(),
        PublishedCandidate(
            candidate=current, published_path=candidate.root / "elsewhere" / "nothing.sqlite"
        ),
    )

    assert missing.state == "refused"
    assert missing.refusal is not None
    assert missing.refusal.code == "selected_input_unavailable"
    assert candidate_receipt_path(candidate.directory()).exists()


def test_a_failed_candidate_flush_is_refused_before_the_directory_is_exposed(
    candidate: SnapshotCase, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A candidate whose durability step failed must never be reported as created.

    Two-phase creation exists so a half-durable candidate never appears at an admitted path. The
    flush is that durability step, so a refusing filesystem has to produce a typed refusal and no
    destination directory at all -- reporting success with a full receipt would admit a candidate
    whose bytes never reached stable storage.
    """

    def refuse_the_flush(path: Path) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(candidate_workspace, "fsync_file", refuse_the_flush)
    refused = create(candidate)

    assert refused.state == "refused"
    assert refused.refusal is not None
    assert refused.refusal.code == "snapshot_incomplete"
    assert refused.identity is None and refused.receipt is None
    assert not candidate.directory().exists()
    assert list(candidate.directory().parent.glob(".*.candidate")) == []
