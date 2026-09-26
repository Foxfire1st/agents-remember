"""Advance only an admitted candidate's code binding against an exact predecessor.

Normal candidate open remains immutable. This explicit admission operation replaces the sealed
receipt, under the same candidate lock as row writers, only when the caller's predecessor receipt
and logical dataset still match. It does not modify knowledge, allocation files, or a before half.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import apsw

from agents_remember.memory.knowledge.candidate_receipt import (
    build_receipt_for_candidate,
    read_candidate_receipt,
    receipt_binding_refusal,
    resolution_from_receipt,
    write_candidate_receipt,
)
from agents_remember.memory.knowledge.connection import inspect_schema, open_read_only_database
from agents_remember.memory.knowledge.logical import dataset_identity
from agents_remember.memory.knowledge.refusals import (
    KnowledgeStorageError,
    candidate_binding_changed_refusal,
)
from agents_remember.memory.knowledge.store import OpenedKnowledgeStore
from agents_remember.models.knowledge.result import KnowledgeRefusal
from agents_remember.models.knowledge.snapshot import (
    AdmittedCandidateDestination,
    CandidateReceipt,
    CandidateResult,
    candidate_database_path,
    candidate_receipt_path,
)


@dataclass(frozen=True)
class CandidateCodeProgression:
    predecessor: CandidateResult
    source_check: Callable[[], KnowledgeRefusal | None]


def read_candidate_predecessor(directory: Path) -> CandidateResult:
    """Read an existing candidate without changing it; the progression rechecks both identities."""

    try:
        receipt = read_candidate_receipt(candidate_receipt_path(directory))
        identity = dataset_identity(candidate_database_path(directory))
        return CandidateResult(state="resumed", receipt=receipt, identity=identity)
    except (KnowledgeStorageError, apsw.Error, OSError) as error:
        return _refused(f"the candidate predecessor cannot be read: {error}")


def progress_candidate_code(
    destination: AdmittedCandidateDestination,
    progression: CandidateCodeProgression,
) -> CandidateResult:
    """Compare-and-swap the code binding, keeping all other admission facts and dataset rows."""

    predecessor = progression.predecessor
    if predecessor.receipt is None or predecessor.identity is None:
        return _refused("code progression requires an exact predecessor receipt and dataset")
    try:
        with (
            _candidate_store(destination) as store,
            store.exclusive_candidate_lock("open_candidate") as denied,
        ):
            if denied is not None:
                return CandidateResult(state="refused", refusal=denied)
            checked = _validate_progression(destination, predecessor, store)
            if checked.refusal is not None:
                return checked
            source_denied = progression.source_check()
            if source_denied is not None:
                return CandidateResult(state="refused", refusal=source_denied)
            assert checked.receipt is not None
            if checked.receipt != predecessor.receipt:
                write_candidate_receipt(destination.receipt_path, checked.receipt)
            return checked
    except (KnowledgeStorageError, apsw.Error, OSError) as error:
        return _refused(f"candidate code progression could not complete: {error}")


def plan_candidate_code(
    destination: AdmittedCandidateDestination, predecessor: CandidateResult
) -> CandidateResult:
    """The same predecessor and scope checks without a lockfile or receipt write."""

    if predecessor.receipt is None or predecessor.identity is None:
        return predecessor if predecessor.refusal is not None else _refused("no exact predecessor")
    try:
        with _candidate_store(destination) as store:
            return _validate_progression(destination, predecessor, store)
    except (KnowledgeStorageError, apsw.Error, OSError) as error:
        return _refused(f"candidate code progression cannot be planned: {error}")


@contextmanager
def _candidate_store(destination: AdmittedCandidateDestination) -> Iterator[OpenedKnowledgeStore]:
    connection = open_read_only_database(destination.database_path)
    try:
        yield OpenedKnowledgeStore(
            database_path=destination.database_path,
            repository_id=destination.repository.repository_id,
            schema=inspect_schema(connection),
            connection=connection,
            resource_lock_path=destination.database_path,
        )
    finally:
        connection.close()


def _validate_progression(
    destination: AdmittedCandidateDestination,
    predecessor: CandidateResult,
    store: OpenedKnowledgeStore,
) -> CandidateResult:
    previous = predecessor.receipt
    assert previous is not None and predecessor.identity is not None
    if read_candidate_receipt(destination.receipt_path) != previous:
        return _refused("the candidate predecessor receipt moved before code progression")
    bound = store.get_repository()
    if bound is None or bound != destination.repository:
        return _refused(
            "the candidate repository namespace or authority home differs from admission"
        )
    prior = destination.model_copy(update={"resolution": resolution_from_receipt(previous)})
    denied = receipt_binding_refusal(previous, prior, bound_repository=bound, schema=store.schema)
    if denied is not None:
        return CandidateResult(state="refused", refusal=denied)
    current = store.snapshot_identity()
    if current != predecessor.identity:
        return _refused("the candidate predecessor dataset moved before code progression")
    successor = build_receipt_for_candidate(destination, store.schema)
    if not _same_scope(previous, successor):
        return _refused(
            "only the code tree may progress within the same repository, lane, task and memory scope"
        )
    return CandidateResult(state="resumed", receipt=successor, identity=current)


def _same_scope(before: CandidateReceipt, after: CandidateReceipt) -> bool:
    return (
        before.lane == "draft-candidate"
        and before.task_ref is not None
        and before.code.commit_id == after.code.commit_id
        and before.model_dump(exclude={"code", "receipt_digest"})
        == after.model_dump(exclude={"code", "receipt_digest"})
    )


def _refused(detail: str) -> CandidateResult:
    return CandidateResult(
        state="refused",
        refusal=candidate_binding_changed_refusal(
            "open_candidate",
            detail,
            expected="the exact predecessor and unchanged non-code admission scope",
        ),
    )
