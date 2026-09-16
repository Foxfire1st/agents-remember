"""Focused behaviour of closed-snapshot publication and the read-side publication gate.

Each case protects one consequential failure: a published file that still depends on a journal
beside it, a WAL-resident batch that a main-file copy silently omits, a failed replacement that
damages the destination it was supposed to preserve, a durability report that claims the old file
was restored when it was not, a logical no-op that rewrites bytes anyway, a destination or
candidate that moved after it was admitted, and a read that would answer from a stale snapshot.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from agents_remember.memory.knowledge import closed_snapshot, publication
from agents_remember.memory.knowledge.closed_snapshot import freeze_closed_snapshot
from agents_remember.memory.knowledge.materialization import unpublished_refusal
from agents_remember.memory.knowledge.publication import publish_prepared_snapshot
from agents_remember.memory.knowledge.refusals import KnowledgeStorageError
from agents_remember.models.knowledge.snapshot import SnapshotDestinationRequest
from snapshot_lifecycle_test_support import (
    SnapshotCase,
    add_raw_invariant,
    build_case,
    byte_copy,
    create,
    file_digest,
    journal_peer_names,
    live_identity,
    live_store,
    logical_identity_of,
    publication_state,
    publish,
    read_identity,
    read_journal_mode,
    row_counts,
    statement_present,
    vacuum,
    write_label_on_live_store,
    write_record,
)

pytestmark = pytest.mark.evidence_unit


@pytest.fixture
def candidate(tmp_path: Path) -> SnapshotCase:
    return build_case(tmp_path)


def test_a_published_snapshot_reopens_to_the_candidate_records_and_is_closed(
    candidate: SnapshotCase,
) -> None:
    create(candidate)
    write_record(candidate, "The first statement the published snapshot must carry.")
    write_record(candidate, "The second statement the published snapshot must carry.")
    identity = live_identity(candidate)

    published = publish(candidate, expected_candidate=identity)

    assert published.state == "published"
    assert published.identity == identity
    assert published.previous_identity is None
    assert published.destination_ref == str(candidate.destination_path())
    assert read_identity(candidate.destination_path()) == identity
    assert read_journal_mode(candidate.destination_path()) == "delete"
    assert journal_peer_names(candidate.destination_path()) == []
    assert row_counts(candidate.destination_path()) == row_counts(candidate.database_path)

    copied = byte_copy(candidate.destination_path(), candidate.root / "reader-copy.sqlite")

    assert logical_identity_of(copied) == identity.logical_digest
    assert row_counts(copied) == row_counts(candidate.destination_path())

    add_raw_invariant(candidate.destination_path(), candidate.repository.repository_id, "closure")

    assert journal_peer_names(candidate.destination_path()) == []
    assert row_counts(candidate.destination_path())["invariant"] == 3


def test_a_wal_resident_batch_is_published_whole_while_a_main_file_copy_is_not(
    candidate: SnapshotCase,
) -> None:
    create(candidate)
    marker = "A committed batch that is still only in the write-ahead log."
    store = live_store(candidate)
    try:
        store.connection.execute("PRAGMA journal_mode=WAL")
        write_label_on_live_store(store, candidate, marker)
        identity = store.snapshot_identity()

        assert statement_present(candidate.database_path, marker) is False, (
            "the case needs committed content that a main-file-only copy would omit"
        )
        main_only = byte_copy(candidate.database_path, candidate.root / "main-only.sqlite")
        assert row_counts(main_only)["invariant"] == 0

        published = publish(candidate, expected_candidate=identity)

        assert published.state == "published"
        assert published.identity == identity
        assert statement_present(candidate.destination_path(), marker) is True
        assert row_counts(candidate.destination_path())["invariant"] == 1
        assert read_identity(candidate.destination_path()) == identity
        assert read_journal_mode(candidate.destination_path()) == "delete"
        assert journal_peer_names(candidate.destination_path()) == []
        assert live_identity(candidate) == identity
    finally:
        store.connection.close()


def test_a_failed_replacement_leaves_the_prior_destination_byte_identical(
    candidate: SnapshotCase, monkeypatch: pytest.MonkeyPatch
) -> None:
    create(candidate)
    write_record(candidate, "The record the first publication carries.")
    first = live_identity(candidate)
    assert publish(candidate, expected_candidate=first).state == "published"
    destination_before = file_digest(candidate.destination_path())
    write_record(candidate, "A newer record the failed attempt must not publish.")
    second = live_identity(candidate)

    def refuse_replacement(source: Path, destination: Path) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(publication, "atomic_replace", refuse_replacement)
    failed = publish(
        candidate,
        expected_candidate=second,
        expected_destination=first,
        admitted_absent=False,
    )

    assert failed.state == "refused"
    assert failed.refusal is not None
    assert failed.refusal.code == "publication_failed"
    assert failed.identity is None
    assert file_digest(candidate.destination_path()) == destination_before
    assert read_identity(candidate.destination_path()) == first
    assert list(candidate.destination_path().parent.glob(".*.stage")) == []


def test_a_publication_whose_readback_fails_reports_the_destination_it_actually_left(
    candidate: SnapshotCase, monkeypatch: pytest.MonkeyPatch
) -> None:
    create(candidate)
    write_record(candidate, "The record the first publication carries.")
    first = live_identity(candidate)
    assert publish(candidate, expected_candidate=first).state == "published"
    write_record(candidate, "The record the replacement installs before its readback fails.")
    second = live_identity(candidate)

    readable = publication.dataset_identity
    calls: list[Path] = []

    def fail_after_the_observation(database_path: Path):
        calls.append(database_path)
        if len(calls) > 1:
            raise KnowledgeStorageError("the case refused the post-replacement readback")
        return readable(database_path)

    monkeypatch.setattr(publication, "dataset_identity", fail_after_the_observation)
    unconfirmed = publish(
        candidate,
        expected_candidate=second,
        expected_destination=first,
        admitted_absent=False,
    )

    assert unconfirmed.state == "refused"
    assert unconfirmed.refusal is not None
    assert unconfirmed.refusal.code == "publication_durability_unconfirmed"
    assert unconfirmed.identity is None
    assert len(calls) == 2
    assert read_identity(candidate.destination_path()) == second


def test_a_logical_no_op_retains_the_published_bytes(candidate: SnapshotCase) -> None:
    create(candidate)
    write_record(candidate, "The first statement the retained snapshot carries.")
    write_record(candidate, "The second statement the retained snapshot carries.")
    identity = live_identity(candidate)
    assert publish(candidate, expected_candidate=identity).state == "published"
    published_before = file_digest(candidate.destination_path())
    candidate_before = file_digest(candidate.database_path)

    vacuum(candidate.database_path)

    assert file_digest(candidate.database_path) != candidate_before
    assert live_identity(candidate) == identity
    again = publish(
        candidate,
        expected_candidate=identity,
        expected_destination=identity,
        admitted_absent=False,
    )

    assert again.state == "no_change"
    assert again.identity == identity
    assert again.previous_identity == identity
    assert file_digest(candidate.destination_path()) == published_before


def test_a_destination_that_moved_since_the_admitted_identity_is_refused_untouched(
    candidate: SnapshotCase,
) -> None:
    create(candidate)
    write_record(candidate, "The record the first publication carries.")
    first = live_identity(candidate)
    assert publish(candidate, expected_candidate=first).state == "published"
    destination_before = file_digest(candidate.destination_path())

    admitted_absent = publish(candidate, expected_candidate=first)

    assert admitted_absent.state == "refused"
    assert admitted_absent.refusal is not None
    assert admitted_absent.refusal.code == "destination_stale"
    assert admitted_absent.refusal.observed == first.logical_digest

    other = first.model_copy(update={"logical_digest": "a" * 64})
    wrong_identity = publish(
        candidate,
        expected_candidate=first,
        expected_destination=other,
        admitted_absent=False,
    )

    assert wrong_identity.state == "refused"
    assert wrong_identity.refusal is not None
    assert wrong_identity.refusal.code == "destination_stale"
    assert wrong_identity.refusal.expected == "a" * 64
    assert file_digest(candidate.destination_path()) == destination_before

    write_record(candidate, "A newer record that stays unpublished.")
    second = live_identity(candidate)
    candidate.destination_path().unlink()
    vanished = publish(
        candidate,
        expected_candidate=second,
        expected_destination=first,
        admitted_absent=False,
    )

    assert vanished.state == "refused"
    assert vanished.refusal is not None
    assert vanished.refusal.code == "destination_stale"
    assert vanished.refusal.observed == "<absent>"
    assert not candidate.destination_path().exists()


def test_a_candidate_that_moved_after_admission_is_refused_rather_than_published_fresher(
    candidate: SnapshotCase,
) -> None:
    create(candidate)
    write_record(candidate, "The record the admission selected.")
    admitted = live_identity(candidate)
    write_record(candidate, "A newer record the admission did not select.")
    observed = live_identity(candidate)
    assert observed != admitted

    refused = publish(candidate, expected_candidate=admitted)

    assert refused.state == "refused"
    assert refused.refusal is not None
    assert refused.refusal.code == "stale_precondition"
    assert refused.refusal.expected == admitted.logical_digest
    assert refused.refusal.observed == observed.logical_digest
    assert not candidate.destination_path().exists()


def test_a_candidate_that_moves_between_admission_and_acquisition_is_refused(
    candidate: SnapshotCase, monkeypatch: pytest.MonkeyPatch
) -> None:
    create(candidate)
    write_record(candidate, "The record the admission selected.")
    admitted = live_identity(candidate)
    freeze = publication._freeze_under_candidate_lock

    def move_then_freeze(destination, expected, stage_directory):
        writer = live_store(candidate)
        try:
            write_label_on_live_store(writer, candidate, "A record committed after admission.")
        finally:
            writer.connection.close()
        return freeze(destination, expected, stage_directory)

    monkeypatch.setattr(publication, "_freeze_under_candidate_lock", move_then_freeze)
    refused = publish(candidate, expected_candidate=admitted)

    assert refused.state == "refused"
    assert refused.refusal is not None
    assert refused.refusal.code == "stale_precondition"
    assert refused.refusal.expected == admitted.logical_digest
    assert refused.refusal.observed == live_identity(candidate).logical_digest
    assert not candidate.destination_path().exists()


def test_a_write_that_lands_during_the_freeze_stays_out_of_the_published_snapshot(
    candidate: SnapshotCase, monkeypatch: pytest.MonkeyPatch
) -> None:
    create(candidate)
    write_record(candidate, "The statement the admission pinned and selected.")
    late = "A statement committed while the copy was being taken."
    store = live_store(candidate)
    try:
        store.connection.execute("PRAGMA journal_mode=WAL")
        pinned = store.snapshot_identity()
        copy_the_source = closed_snapshot._run_backup

        def commit_while_the_copy_runs(source, stage: Path) -> None:
            writer = live_store(candidate)
            try:
                write_label_on_live_store(writer, candidate, late)
            finally:
                writer.connection.close()
            copy_the_source(source, stage)

        monkeypatch.setattr(closed_snapshot, "_run_backup", commit_while_the_copy_runs)
        published = publish(candidate, expected_candidate=pinned)
    finally:
        store.connection.close()

    assert published.state == "published"
    assert published.identity == pinned
    assert statement_present(candidate.destination_path(), late) is False
    assert row_counts(candidate.destination_path())["invariant"] == 1
    assert live_identity(candidate) != pinned


def test_a_prepared_stage_that_is_not_the_verified_file_is_not_installed(
    candidate: SnapshotCase,
) -> None:
    create(candidate)
    write_record(candidate, "The record the prepared stage carries.")
    identity = live_identity(candidate)
    stage_directory = candidate.root / "prepared-stage"
    stage_directory.mkdir()
    store = live_store(candidate)
    try:
        prepared = freeze_closed_snapshot(store, identity, stage_directory / "snapshot.sqlite")
    finally:
        store.connection.close()
    request = SnapshotDestinationRequest(
        destination_path=candidate.destination_path(), expected_destination=None
    )

    tampered = prepared.model_copy(update={"file_digest": "b" * 64})
    refused = publish_prepared_snapshot(tampered, request)

    assert refused.state == "refused"
    assert refused.refusal is not None
    assert refused.refusal.code == "snapshot_incomplete"
    assert not candidate.destination_path().exists()

    installed = publish_prepared_snapshot(prepared, request)

    assert installed.state == "published"
    assert installed.identity == identity
    assert read_identity(candidate.destination_path()) == identity
    assert journal_peer_names(candidate.destination_path()) == []


def test_a_newer_runtime_candidate_reports_candidate_snapshot_unpublished_until_published(
    candidate: SnapshotCase,
) -> None:
    create(candidate)
    write_record(candidate, "The record the first publication carries.")
    first = live_identity(candidate)
    assert publish(candidate, expected_candidate=first).state == "published"
    assert publication_state(candidate).state == "current"

    write_record(candidate, "A newer record the published snapshot does not carry.")
    current = live_identity(candidate)
    assert current != first

    stale = publication_state(candidate)

    assert stale.state == "candidate_snapshot_unpublished"
    assert stale.candidate == current
    assert stale.published == first
    refusal = unpublished_refusal(stale, str(candidate.destination_path()))
    assert refusal.code == "candidate_snapshot_unpublished"
    assert refusal.expected == current.logical_digest
    assert refusal.observed == first.logical_digest

    republished = publish(
        candidate,
        expected_candidate=current,
        expected_destination=first,
        admitted_absent=False,
    )

    assert republished.state == "published"
    assert publication_state(candidate).state == "current"

    absent = publication_state(candidate, candidate.root / "nothing.sqlite")

    assert absent.state == "refused"
    assert absent.refusal is not None
    assert absent.refusal.code == "selected_input_unavailable"


def test_a_failed_stage_flush_is_refused_before_the_destination_is_replaced(
    candidate: SnapshotCase, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A durability failure while freezing must arrive as a typed refusal, not as a raw error.

    The freeze step's last act is flushing the staged file. A filesystem that refuses that flush
    still has to leave the caller with a decision it can branch on: the destination untouched, the
    private stage gone, and one named refusal code. An escaping ``OSError`` would report none of
    that, and the caller would have no way to tell this failure from a defect in its own code.
    """

    create(candidate)
    write_record(candidate, "The record the first publication carries.")
    first = live_identity(candidate)
    assert publish(candidate, expected_candidate=first).state == "published"
    destination_before = file_digest(candidate.destination_path())
    write_record(candidate, "A newer record the failed flush must not publish.")
    second = live_identity(candidate)

    def refuse_the_flush(path: Path) -> None:
        raise OSError(5, "Input/output error")

    monkeypatch.setattr(closed_snapshot, "fsync_file", refuse_the_flush)
    refused = publish(
        candidate,
        expected_candidate=second,
        expected_destination=first,
        admitted_absent=False,
    )

    assert refused.state == "refused"
    assert refused.refusal is not None
    assert refused.refusal.code == "snapshot_incomplete"
    assert refused.identity is None
    assert file_digest(candidate.destination_path()) == destination_before
    assert list(candidate.destination_path().parent.glob(".*.stage")) == []
