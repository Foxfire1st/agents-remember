"""Complete logical export and import: the operation, its staging and its publication.

This module is the operation. Export reads one admitted dataset through the *one* canonical logical
encoder and renders it as the portable artifact that encoder's body describes. Import takes such an
artifact, proves it is complete and typed, builds a **fresh private staging database**, loads it in
one transaction with deferred foreign keys, runs the checks a normal store open runs, recomputes the
logical identity, and publishes the stage through the same atomic staging protocol every other
knowledge publication uses -- but only on exact digest equality.

Three decisions shape the whole sequence, and each exists because the step before it cannot see
what it prevents:

1. **Validation before any database work.** The artifact is parsed with duplicate-key detection, its
   envelope and manifest are compared against this build's, its rows are checked for declared column
   order and declared types, and its declared digest is recomputed from the records it carries. An
   artifact that fails any of those never reaches a database, so a malformed document cannot leave
   a half-written stage behind for a later step to mistake for progress.
2. **A private stage that is never the destination.** The imported dataset is built somewhere else
   entirely and is verified there -- graph, sealed payload digests, typed aggregates, closed,
   journalless, re-readable and holding the expected identity -- before the destination is touched
   at all. "Import" is therefore not a write to the destination that might fail halfway; it is a
   verified closed file that either replaces the destination atomically or does not.
3. **Publication on exact digest equality, through the L4 protocol.** The stage is installed by
   :func:`publish_prepared_snapshot`, which takes the destination's lock, compares the identity the
   caller admitted, retains the existing bytes when they already hold this knowledge, and re-reads
   the installed file before reporting success. Import owns no second install path and never patches
   a live database in place.

**What this operation never does.** It does not regenerate or renumber an identifier, infer missing
provenance, manufacture an ``accepted`` state, restore a Git commit, resolve an external source
object, or claim anything about a second backend. ``state_at_origin`` and ``acceptance_ref`` cross
as the stored text they are: importing a row that says ``accepted`` imports that *value*, and the
receiving context grants it no authority. A dataset restored from an artifact is knowledge, not
history.

**Lock policy.** One resource lock at a time, never nested. Export takes no lock: it reads through a
connection that is reopened for the selected dataset. Import takes no lock while it stages or
validates -- the stage is private -- and the destination's lock is taken by the publication
operation and released with it.
"""

from __future__ import annotations

import hashlib
import shutil
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any
from uuid import uuid4

import apsw

from agents_remember.kernel.atomic_write import fsync_file
from agents_remember.memory.knowledge import logical, schema
from agents_remember.memory.knowledge.closed_snapshot import (
    discard_stage,
    require_closed_database,
)
from agents_remember.memory.knowledge.connection import (
    immediate_transaction,
    open_database,
    open_read_only_database,
)
from agents_remember.memory.knowledge.export_portable import (
    EXPORT_FORMAT,
    PORTABLE_NOTES,
    ValidatedExport,
    encode_export,
    export_envelope,
    file_digest,
    parse_export,
    validate_export,
)
from agents_remember.memory.knowledge.export_refusals import (
    destination_absent_refusal,
    destination_occupied_refusal,
    import_validation_failed_refusal,
    invalid_export_refusal,
)
from agents_remember.memory.knowledge.publication import (
    destination_observation,
    publish_prepared_snapshot,
)
from agents_remember.memory.knowledge.records import (
    decode_family_revision_row,
    decode_predecessor_rows,
    decode_revision_row,
    encode_typed_column,
)
from agents_remember.memory.knowledge.refusals import (
    RefusalFacts,
    refusal,
    selected_input_unavailable_refusal,
)
from agents_remember.memory.knowledge.schema_generations import (
    SchemaGeneration,
    create_schema_statements,
    generation_of_database,
)
from agents_remember.memory.knowledge.store import open_existing_knowledge_store
from agents_remember.models.knowledge.candidate import SnapshotIdentity
from agents_remember.models.knowledge.portable import (
    ExportRequest,
    ExportResult,
    ImportRequest,
    ImportResult,
    PortableValidation,
)
from agents_remember.models.knowledge.repository import RepositoryIdentity
from agents_remember.models.knowledge.result import KnowledgeRefusal
from agents_remember.models.knowledge.snapshot import (
    PreparedKnowledgeSnapshot,
    SnapshotDestinationRequest,
    SnapshotPublicationResult,
)

EXPORT_OPERATION = "export_knowledge_dataset"
IMPORT_OPERATION = "import_knowledge_dataset"

# The stage a private import database is written under inside its own directory. One name, so this
# module and the snapshot contract cannot drift apart about which file the stage is.
_STAGE_FILE_NAME = "snapshot.sqlite"

# The two immutable aggregates whose rows seal their own payload: the revision table, the
# predecessor-edge table that holds each revision's parents, and the decoder that recomputes the
# seal. Both shapes are validated by the same loop, and the decoders are the *shared* ones from
# :mod:`records` -- the seal has one owner, and an artifact's claim about it is checked with that
# owner rather than with a second reading of the same column.
_SEALED_AGGREGATES: tuple[tuple[str, str, Callable[[Any, tuple[str, ...]], object]], ...] = (
    ("invariant_revision", "invariant_predecessor", decode_revision_row),
    ("family_revision", "family_predecessor", decode_family_revision_row),
)


def export_knowledge_dataset(request: ExportRequest) -> ExportResult:
    """Encode one admitted dataset as its complete portable artifact, or refuse.

    The identity is re-read before anything is encoded. An export addressed at a dataset that moved
    since the caller admitted it is ``stale_precondition`` rather than an artifact describing a
    dataset nobody selected -- the same rule every other read in this package follows, and the one
    that makes "the artifact I produced is the dataset I admitted" checkable afterwards.
    """

    expected = request.expected_identity
    try:
        store = open_existing_knowledge_store(request.database_path, expected.repository_id)
    except (apsw.Error, OSError, ValueError) as error:
        return ExportResult(
            state="refused",
            refusal=selected_input_unavailable_refusal(
                EXPORT_OPERATION,
                f"the selected dataset could not be opened for export: {error}",
                record_id=str(request.database_path),
            ),
        )
    try:
        repository = store.get_repository()
        if repository is None:
            return ExportResult(
                state="refused",
                refusal=selected_input_unavailable_refusal(
                    EXPORT_OPERATION,
                    "the selected database holds no repository namespace row, so it is not a "
                    "knowledge dataset this code can export",
                    record_id=str(request.database_path),
                ),
            )
        body = logical.logical_body(store.connection, store.schema.schema_name)
        observed = _identity(
            repository.repository_id,
            store.schema.schema_name,
            logical.logical_digest_of_tables(store.schema.schema_name, body["tables"]),
        )
        if observed != expected:
            return ExportResult(state="refused", refusal=_stale_export_refusal(expected, observed))
        artifact = encode_export(
            export_envelope(
                body["tables"],
                repository.repository_id,
                generation=store.generation,
                logical_digest=observed.logical_digest,
                schema_fingerprint=body["schema_fingerprint"],
            )
        )
    finally:
        store.close()
    return ExportResult(
        state="exported",
        artifact=artifact,
        format=EXPORT_FORMAT,
        identity=observed,
        artifact_digest=artifact_digest(artifact),
        row_counts={table: len(body["tables"][table]) for table in store.generation.tables},
        notes=PORTABLE_NOTES,
    )


def import_knowledge_dataset(request: ImportRequest) -> ImportResult:
    """Validate one artifact and install its dataset at an admitted destination, or refuse.

    Every failure returns a typed refusal with the destination untouched. The one refusal this
    function builds beyond the validators is the destination check, which runs *before* a stage
    exists so a caller who addressed the wrong path learns it without any filesystem work at all.
    """

    parsed = parse_export(request.artifact)
    if isinstance(parsed, KnowledgeRefusal):
        return ImportResult(state="refused", refusal=parsed)
    checked = validate_export(parsed, expected_repository_id=request.expected_repository_id)
    if checked.refusal is not None:
        return ImportResult(state="refused", refusal=checked.refusal)
    tables, report = _checked_content(checked)
    repository = RepositoryIdentity(
        repository_id=str(report.repository_id),
        authority_home=str(tables["repository"][0]["authority_home"]),
    )
    identity = _identity(
        repository.repository_id, parsed.generation.schema_name, str(report.logical_digest)
    )
    blocked = _destination_refusal(request)
    if blocked is not None:
        return ImportResult(state="refused", validation=report, refusal=blocked)
    stage_directory = _private_stage_directory()
    try:
        staged = _stage_imported_dataset(
            stage_directory / _STAGE_FILE_NAME, tables, identity, parsed.generation
        )
        if isinstance(staged, KnowledgeRefusal):
            return ImportResult(state="refused", validation=report, refusal=staged)
        publication = publish_prepared_snapshot(
            staged,
            SnapshotDestinationRequest(
                destination_path=request.destination_path,
                expected_destination=request.expected_destination,
            ),
        )
    except (apsw.Error, OSError, ValueError) as error:
        return ImportResult(
            state="refused",
            validation=report,
            refusal=import_validation_failed_refusal(
                IMPORT_OPERATION,
                f"the staged import could not be built: {type(error).__name__}: {error}",
                record_id=str(stage_directory),
            ),
        )
    finally:
        _discard(stage_directory)
    return _import_outcome(publication, identity, report)


def artifact_digest(text: str) -> str:
    """Return the sha256 of one artifact's exact UTF-8 bytes.

    The digest is over bytes rather than over the decoded string because that is what a caller
    compares a file against, and re-encoding the same text is what makes the two the same artifact.
    """

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_artifact(path: Path) -> str | KnowledgeRefusal:
    """Read one artifact file as text, or return the typed refusal that says why it is not one.

    Two inputs fail here and they are different facts, so they get different codes. A path that
    cannot be read at all is ``selected_input_unavailable``: the file the caller selected is absent
    or unreadable, and answering it with an empty document would be the "absent selection reads as
    no knowledge" confusion this package refuses everywhere else. A byte sequence that is not UTF-8
    is ``invalid_export``, because a portable artifact is UTF-8 by construction and a file that is
    not is a malformed artifact rather than a missing one -- decoding strictly is what keeps it from
    being read through a replacement-character guess.

    Both failures are returned rather than raised: this helper's contract is a value or a refusal,
    like every other reader on this boundary, so no ``OSError`` and no ``UnicodeDecodeError``
    escapes it for a caller to catch.
    """

    target = Path(path)
    try:
        payload = target.read_bytes()
    except OSError as error:
        return selected_input_unavailable_refusal(
            IMPORT_OPERATION,
            f"the artifact file could not be read: {error}",
            record_id=str(target),
        )
    try:
        return payload.decode("utf-8")
    except UnicodeDecodeError as error:
        return invalid_export_refusal(
            IMPORT_OPERATION,
            f"the artifact is not UTF-8 text, so it is not a well-formed artifact of this format: "
            f"{error}",
            facts=RefusalFacts(record_id=str(target)),
        )


class KnowledgeExportDefect(RuntimeError):
    """An internal state the sequence itself makes unreachable."""


def _checked_content(
    checked: ValidatedExport,
) -> tuple[dict[str, list[dict[str, object]]], PortableValidation]:
    """Return the rows and report one successful validation carries, or the defect that it did not."""

    if checked.tables is None or checked.validation is None:
        raise KnowledgeExportDefect("a validated export carried neither a report nor its rows")
    return checked.tables, checked.validation


def _identity(repository_id: str, schema_name: str, digest: str) -> SnapshotIdentity:
    """Return one dataset identity from its three declared fields."""

    return SnapshotIdentity(
        repository_id=repository_id, schema_version=schema_name, logical_digest=digest
    )


def _stale_export_refusal(
    expected: SnapshotIdentity, observed: SnapshotIdentity
) -> KnowledgeRefusal:
    """The refusal for a dataset that moved between admission and export."""

    return refusal(
        "stale_precondition",
        EXPORT_OPERATION,
        "the database does not hold the logical dataset the export was admitted for",
        facts=RefusalFacts(
            record_id=observed.repository_id,
            expected=expected.logical_digest,
            observed=observed.logical_digest,
        ),
        next_action=(
            "Re-read the dataset's identity and export the state that is actually there. A fresher "
            "dataset is never exported silently in place of the selected one."
        ),
    )


def _destination_refusal(request: ImportRequest) -> KnowledgeRefusal | None:
    """Refuse an occupied destination with no admitted identity, or an admitted one that is gone."""

    observation = destination_observation(request.destination_path)
    if observation.detail:
        return selected_input_unavailable_refusal(
            IMPORT_OPERATION,
            "the destination exists but does not hold a readable knowledge dataset: "
            f"{observation.detail}",
            record_id=str(request.destination_path),
        )
    if request.expected_destination is None:
        if observation.identity is None:
            return None
        return destination_occupied_refusal(
            IMPORT_OPERATION,
            destination_ref=str(request.destination_path),
            observed=observation.identity.logical_digest,
        )
    if observation.identity is None:
        return destination_absent_refusal(
            IMPORT_OPERATION,
            destination_ref=str(request.destination_path),
            expected=request.expected_destination.logical_digest,
        )
    return None


def _stage_imported_dataset(
    stage: Path,
    tables: dict[str, list[dict[str, object]]],
    identity: SnapshotIdentity,
    generation: SchemaGeneration,
) -> PreparedKnowledgeSnapshot | KnowledgeRefusal:
    """Build, load and verify one private staging database, or refuse what it turned out to be.

    The verification is the same one a normal store open performs and then some: the schema is
    created by the manifest rather than by the artifact, foreign keys are checked against the loaded
    graph, every sealed revision payload is re-derived and compared with the digest it declares,
    every canonical row is read back through the typed decoders, the journal mode is normalised and
    its peers proved absent, and the whole dataset's logical digest is recomputed from the reopened
    file and compared with the artifact's. A stage that fails any of them is removed and never
    published.
    """

    stage.parent.mkdir(parents=True, exist_ok=True)
    connection = open_database(stage)
    try:
        # The stage is created at the generation the ARTIFACT declares, not at the newest this build
        # supports. Creating it at the newest made a genuine version-1 artifact stage as version 2
        # holding only version 1's tables, so it digested differently from the artifact it came from
        # and every version-1 import was refused -- a preservation-boundary break. The artifact's
        # declared pair was already resolved and checked by `validate_export`, so this trusts the
        # same fact the rest of the import does.
        for statement in create_schema_statements(generation):
            connection.execute(statement)
        connection.execute(f"PRAGMA user_version = {generation.user_version}")
        try:
            with immediate_transaction(connection):
                _load_tables(connection, tables, generation)
        except (apsw.Error, ValueError) as error:
            return import_validation_failed_refusal(
                IMPORT_OPERATION,
                f"the database refused the artifact's records: {type(error).__name__}: {error}",
                record_id=str(stage),
            )
    finally:
        connection.close()
    refused = _verify_staged_dataset(stage, identity)
    if refused is not None:
        return refused
    try:
        require_closed_database(stage, identity)
    except (apsw.Error, OSError, ValueError) as error:
        return import_validation_failed_refusal(IMPORT_OPERATION, str(error), record_id=str(stage))
    fsync_file(stage)
    return PreparedKnowledgeSnapshot(
        stage_path=stage, identity=identity, file_digest=file_digest(stage)
    )


def _verify_staged_dataset(stage: Path, identity: SnapshotIdentity) -> KnowledgeRefusal | None:
    """Prove the staged dataset is this schema's knowledge and the identity the artifact declared.

    Four checks on the file the import just wrote, each answering a question the load cannot:

    * **The declared graph.** ``PRAGMA foreign_key_check`` is read directly rather than inferred from
      a successful commit, so a staged database that carries a dangling edge is named as such instead
      of arriving as an engine constraint error with no table or row attached to it.
    * **The sealed aggregates.** Every retained revision is re-derived through the decoders that own
      its payload digest and compared against the digest the artifact declared for that row, so a
      ``payload_digest`` that crosses in the artifact as ordinary text is verified here rather than
      stored and trusted. Without this read the one row-internal identity this schema promotes to a
      stored column would be the one the import publishes unchecked, and the resulting file would be
      one this package's own decoder and its own merge validation call damaged.
    * **The typed aggregates.** Reading the canonical logical body decodes every typed JSON column,
      so a column whose stored text is not unambiguous JSON is refused here rather than by a later
      consumer.
    * **The declared identity.** The whole dataset's logical digest is recomputed from the reopened
      file and must equal the artifact's. This is the exact-digest-equality the requirement asks for,
      and it is what makes a staged database that lost a row or re-spelled a value unpublishable.
    """

    connection = open_read_only_database(stage)
    try:
        violations = [tuple(row) for row in connection.execute("PRAGMA foreign_key_check")]
        if violations:
            return import_validation_failed_refusal(
                IMPORT_OPERATION,
                f"the staged dataset holds {len(violations)} foreign-key violation(s), the first "
                f"naming {violations[0][0]}",
                record_id=str(violations[0]),
            )
        sealed = _sealed_aggregate_refusal(connection)
        if sealed is not None:
            return sealed
        try:
            observed = logical.logical_digest(connection, generation_of_database(connection))
        except (apsw.Error, ValueError) as error:
            return import_validation_failed_refusal(
                IMPORT_OPERATION,
                f"the staged dataset does not read back as its typed aggregates: {error}",
                record_id=str(stage),
            )
    finally:
        connection.close()
    if observed != identity.logical_digest:
        return invalid_export_refusal(
            IMPORT_OPERATION,
            "the staged dataset does not hold the logical identity the artifact declared, so the "
            "import did not reproduce the exported dataset exactly",
            facts=RefusalFacts(expected=identity.logical_digest, observed=observed),
        )
    return None


def _sealed_aggregate_refusal(connection: apsw.Connection) -> KnowledgeRefusal | None:
    """Refuse the first staged revision whose payload contradicts the digest it declares.

    ``payload_digest`` is a canonical column of both revision tables, so it crosses the artifact
    boundary as ordinary text and nothing about parsing the document establishes it. The check is
    the decoder that owns the column: it rebuilds the payload from the stored row and the row's
    predecessor edges and raises when the recomputed digest is not the stored one, which is exactly
    the comparison a later reader would make. Running it over every retained revision -- both
    aggregates, before anything is published -- is what keeps such an artifact from becoming a
    published dataset that this package then reports as damaged.
    """

    for table, edge_table, decode in _SEALED_AGGREGATES:
        for record_id, row in _revision_rows(connection, table):
            predecessors = decode_predecessor_rows(
                list(
                    connection.execute(
                        f"SELECT parent_revision_id FROM {edge_table} WHERE child_revision_id = ?",
                        (record_id,),
                    )
                )
            )
            try:
                decode(row, predecessors)
            except (apsw.Error, ValueError) as error:
                return import_validation_failed_refusal(
                    IMPORT_OPERATION,
                    f"the staged {table} row {record_id} does not match the payload digest it "
                    f"declares: {error}",
                    record_id=record_id,
                )
    return None


def _revision_rows(connection: apsw.Connection, table: str) -> list[tuple[str, tuple[Any, ...]]]:
    """Return each row of one sealed aggregate table, paired with the revision it identifies.

    Both sealed tables declare ``revision_id`` third, so one read serves them; the columns are
    selected in declared order because that is the order the decoders index the row by.
    """

    columns = ", ".join(schema.CANONICAL_COLUMNS[table])
    return [
        (str(row[2]), tuple(row)) for row in connection.execute(f"SELECT {columns} FROM {table}")
    ]


def _load_tables(
    connection: apsw.Connection,
    tables: dict[str, list[dict[str, object]]],
    generation: SchemaGeneration,
) -> None:
    """Insert every canonical row inside the caller's one transaction.

    Rows are inserted in declared manifest order and the declared foreign keys are deferred to
    commit, so a dataset whose rows reference each other in any order loads without a topological
    sort -- and the same deferral is what makes a dangling reference a *commit* failure rather than
    a partially written stage.

    A typed JSON column arrives as a decoded value and is stored through
    :func:`records.encode_typed_column` -- the same encoder every other write in this package uses.
    That is not a convenience: it is what makes "the value that was validated" and "the text that a
    later read decodes" the same value, rather than this module inventing a second spelling that the
    store would then have to accept.
    """

    for table in generation.tables:
        rows = tables.get(table) or []
        if not rows:
            continue
        columns = generation.columns[table]
        json_columns = generation.json_columns.get(table, frozenset())
        placeholders = ", ".join("?" for _ in columns)
        connection.executemany(
            f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})",
            [_row_bindings(row, columns, json_columns) for row in rows],
        )


def _row_bindings(
    row: Mapping[str, object], columns: tuple[str, ...], json_columns: frozenset[str]
) -> tuple[str | int | float | bytes | None, ...]:
    """Return one row's values in declared column order, ready to bind to an INSERT.

    A typed JSON column is encoded through :func:`records.encode_typed_column` so the stored text is
    the same text every other write in this package produces; everything else binds as the exact
    value the artifact carried.
    """

    return tuple(_binding(row, column, json_columns) for column in columns)


def _binding(
    row: Mapping[str, object], column: str, json_columns: frozenset[str]
) -> str | int | float | bytes | None:
    """Return one column's value as a SQLite binding.

    A typed JSON column is stored as its canonical text; every other column is stored as the exact
    scalar the artifact carried. The declared types make this total: text columns hold strings, a
    typed column holds a decoded JSON value, and a nullable column may hold null.
    """

    value = row[column]
    if value is None:
        return None
    if column in json_columns:
        return encode_typed_column(value)
    if isinstance(value, (str, int, float, bytes)):
        return value
    # The row reader refuses every value this branch would refuse, so a weakened mutation of this
    # ``isinstance`` guard leaves the suite green: the same malformed artifact is still refused, one
    # step later and through the staging path's own refusal. It is stated rather than implied -- the
    # guard is defense in depth for a caller that reaches this function with rows the reader never
    # checked, and no black-box case can distinguish the two outcomes.
    raise ValueError(
        f"column {column} holds {type(value).__name__}, which is not a value this schema stores"
    )


def _import_outcome(
    publication: SnapshotPublicationResult, identity: SnapshotIdentity, report: PortableValidation
) -> ImportResult:
    """Render the publication's outcome as the import's own factual result."""

    if publication.state == "refused":
        return ImportResult(state="refused", validation=report, refusal=publication.refusal)
    installed = publication.state == "published"
    return ImportResult(
        state="installed" if installed else "no_change",
        verified_identity=identity,
        destination_identity=publication.identity,
        destination_ref=publication.destination_ref,
        publication="published" if installed else "no_change",
        row_counts=report.row_counts,
        validation=report,
    )


def _private_stage_directory() -> Path:
    """Return a fresh private directory for one import stage.

    The stage is deliberately *not* created inside the destination's own directory: the destination
    directory is a published location, not a workbench, and the install is an atomic replace of one
    already-written, already-fsynced file rather than a write into the destination in place.
    """

    return Path(tempfile.mkdtemp(prefix=f"knowledge-import-{uuid4().hex[:12]}-"))


def _discard(stage_directory: Path) -> None:
    """Remove one private stage directory, its database, and any journal peer of it."""

    discard_stage(stage_directory / _STAGE_FILE_NAME)
    shutil.rmtree(stage_directory, ignore_errors=True)
