"""The ``verification_observation`` row codec: one observation's own row in both directions.

This is the observation half of :mod:`agents_remember.memory.knowledge.evidence_records`, which
re-exports every public name here and keeps the claim, subject, coverage and envelope codecs plus the
stored-value readers. An observation row is the only evidence row whose columns are a flat
projection of a frozen payload rather than a ledger edge, so its column order, its three
all-or-nothing column groups and its toolchain decoding form one unit with no dependency on the
claim codecs.

The row is not a sealed revision: an operation that names one by digest computes that digest from the
row as it stands, with :func:`observation_row_digest`, and the read path exposes the same value.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from agents_remember.kernel.canonical_json import sha256_digest
from agents_remember.memory.knowledge.records import decode_typed_column, encode_typed_column
from agents_remember.memory.knowledge.refusals import KnowledgeStorageError
from agents_remember.models.knowledge.candidate import SnapshotIdentity
from agents_remember.models.knowledge.evidence import (
    PublicationReference,
    ResultArtifactReference,
    RunEnvironment,
    VerificationObservationPayload,
)
from agents_remember.models.knowledge.evidence_read import VerificationObservationRecord

__all__ = [
    "OBSERVATION_COLUMNS",
    "decode_observation_row",
    "observation_cells",
    "observation_row",
    "observation_row_digest",
]


def observation_cells(payload: VerificationObservationPayload) -> dict[str, Any]:
    """Return one observation's stored cells, keyed by the column that holds each.

    The mapping is keyed rather than positional on purpose. The table's column order is declared in
    the generation, the INSERT statement names its columns, and this is the third place the same
    order would have had to be repeated -- so it is not repeated. The write path builds its statement
    from :data:`OBSERVATION_COLUMNS` and its parameters from this mapping, which means a mismatch is
    not expressible rather than merely unlikely.

    Every cell is derived from the frozen payload, so the row and the sealed revision cannot disagree
    about what the run recorded. The knowledge snapshot's three columns are ``NULL`` together when the
    run tested no knowledge dataset, which is the table's own constraint rather than a convention the
    writer remembers.
    """

    snapshot = payload.knowledge_candidate
    artifact = payload.result_artifact
    publication = payload.publication
    return {
        "knowledge_snapshot_repository_id": None if snapshot is None else snapshot.repository_id,
        "knowledge_snapshot_schema_version": None if snapshot is None else snapshot.schema_version,
        "knowledge_snapshot_logical_digest": None if snapshot is None else snapshot.logical_digest,
        "code_candidate_tree_id": payload.code_candidate_tree_id,
        "command_name": payload.command_name,
        "command_identity": payload.command_identity,
        "artifact_path": None if artifact is None else artifact.path,
        "artifact_sha256": None if artifact is None else artifact.sha256,
        "artifact_size_bytes": None if artifact is None else artifact.size_bytes,
        "digest_checked_at_write": (
            1 if (artifact is not None and artifact.digest_checked_against_bytes) else 0
        ),
        "execution_result": payload.execution_result,
        "environment_host": payload.environment.host,
        "environment_interpreter": payload.environment.interpreter,
        "environment_toolchain": encode_typed_column(list(payload.environment.toolchain)),
        "publication_destination": None if publication is None else publication.destination,
        "publication_sha256": None if publication is None else publication.sha256,
        "publication_recorded_at": None if publication is None else publication.published_at,
    }


# The observation's own columns, in the order the generation declares them after the two key
# columns the write path supplies. Declared here, next to the cells, so the builder and the statement
# read the same list; a case asserts it equals the generation's declared order minus the key.
OBSERVATION_COLUMNS: tuple[str, ...] = (
    "knowledge_snapshot_repository_id",
    "knowledge_snapshot_schema_version",
    "knowledge_snapshot_logical_digest",
    "code_candidate_tree_id",
    "command_name",
    "command_identity",
    "artifact_path",
    "artifact_sha256",
    "artifact_size_bytes",
    "digest_checked_at_write",
    "execution_result",
    "environment_host",
    "environment_interpreter",
    "environment_toolchain",
    "publication_destination",
    "publication_sha256",
    "publication_recorded_at",
)


def observation_row(payload: VerificationObservationPayload) -> tuple[Any, ...]:
    """Return the ``verification_observation`` column tuple for one authored observation."""

    cells = observation_cells(payload)
    return tuple(cells[column] for column in OBSERVATION_COLUMNS)


def observation_row_digest(
    repository_id: str, observation_id: str, payload: Mapping[str, Any]
) -> str:
    """Digest one observation row, so an expectation can name it.

    The digest covers the row's own stored facts -- the namespace, the observation identity and the
    exact payload the columns were projected from -- and nothing derived from them. It is not a
    digest of the artifact's bytes and does not stand in for one: the artifact's identity is
    ``artifact_sha256``.
    """

    return sha256_digest(
        {
            "table": "verification_observation",
            "repository_id": repository_id,
            "observation_id": observation_id,
            "payload": dict(payload),
        }
    )


def _snapshot_of_columns(
    repository_id: object, schema_version: object, logical_digest: object, observation_id: str
) -> SnapshotIdentity | None:
    """Rebuild one stored knowledge snapshot identity, refusing a partially populated group."""

    populated = [value is not None for value in (repository_id, schema_version, logical_digest)]
    if not any(populated):
        return None
    if not all(populated):
        raise KnowledgeStorageError(
            f"stored observation {observation_id} populates part of its knowledge snapshot identity; "
            "the table's own constraint was bypassed"
        )
    return SnapshotIdentity.model_construct(
        repository_id=str(repository_id),
        schema_version=str(schema_version),
        logical_digest=str(logical_digest),
    )


def _artifact_of_columns(
    observation_id: str,
    path: object,
    sha256: object,
    size_bytes: object,
    digest_checked_at_write: object,
) -> ResultArtifactReference | None:
    """Rebuild one stored result-artifact reference, refusing a partially populated group."""

    populated = [value is not None for value in (path, sha256, size_bytes)]
    if not any(populated):
        return None
    if not all(populated):
        raise KnowledgeStorageError(
            f"stored observation {observation_id} populates part of its result-artifact reference; "
            "the table's own constraint was bypassed"
        )
    return ResultArtifactReference.model_construct(
        path=str(path),
        sha256=str(sha256),
        size_bytes=int(str(size_bytes)),
        digest_checked_against_bytes=bool(digest_checked_at_write),
    )


def _publication_of_columns(
    observation_id: str, destination: object, sha256: object, published_at: object
) -> PublicationReference | None:
    """Rebuild one stored publication reference, refusing a partially populated group."""

    populated = [value is not None for value in (destination, sha256, published_at)]
    if not any(populated):
        return None
    if not all(populated):
        raise KnowledgeStorageError(
            f"stored observation {observation_id} populates part of its publication reference; "
            "the table's own constraint was bypassed"
        )
    return PublicationReference.model_construct(
        destination=str(destination),
        sha256=str(sha256),
        published_at=str(published_at),
    )


def decode_observation_row(row: Sequence[Any]) -> VerificationObservationRecord:
    """Decode one ``verification_observation`` row into its stored value."""

    repository_id, observation_id = str(row[0]), str(row[1])
    payload = VerificationObservationPayload.model_construct(
        command_name=str(row[6]),
        command_identity=str(row[7]),
        knowledge_candidate=_snapshot_of_columns(row[2], row[3], row[4], observation_id),
        code_candidate_tree_id=None if row[5] is None else str(row[5]),
        result_artifact=_artifact_of_columns(observation_id, row[8], row[9], row[10], row[11]),
        execution_result=str(row[12]),
        environment=RunEnvironment.model_construct(
            host=str(row[13]),
            interpreter=str(row[14]),
            toolchain=_toolchain_of_column(row[15], observation_id),
        ),
        publication=_publication_of_columns(observation_id, row[16], row[17], row[18]),
        state_at_origin="proposed",
        acceptance_ref=None,
    )
    return VerificationObservationRecord(
        repository_id=repository_id,
        observation_id=observation_id,
        payload=payload,
        row_digest=observation_row_digest(
            repository_id, observation_id, payload.model_dump(mode="json")
        ),
    )


def _toolchain_of_column(value: object, observation_id: str) -> tuple[tuple[str, str], ...]:
    """Decode the stored toolchain column, refusing anything that is not a list of pairs."""

    decoded = decode_typed_column(str(value))
    if not isinstance(decoded, list):
        raise KnowledgeStorageError(
            f"stored observation {observation_id} carries a toolchain that is not a JSON array"
        )
    pairs: list[tuple[str, str]] = []
    for component in decoded:
        if not isinstance(component, list) or len(component) != 2:
            raise KnowledgeStorageError(
                f"stored observation {observation_id} carries a malformed toolchain component"
            )
        pairs.append((str(component[0]), str(component[1])))
    return tuple(pairs)
