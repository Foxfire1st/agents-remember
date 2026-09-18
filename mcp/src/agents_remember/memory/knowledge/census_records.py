"""Row codecs and the in-transaction write step for the census record group.

The census stores three envelope records -- an inventory row, an assessable claim and a migration
disposition -- and three relations they resolve through. Every conversion in both directions has an
owner, and the two shared ones are not re-implemented here:

* the generic ``knowledge_record``/``record_revision`` codec is reused from
  :mod:`agents_remember.memory.knowledge.facet_records`, so a stored revision's seal is verified
  against the one digest definition rather than a second copy of it;
* the payload is validated through the **envelope seam**, which is the one place any write path
  decides whether a payload is admissible. This module declares the ``(kind, record_schema)`` triples
  and nothing else about admissibility.

The step is written as an ``apply_*`` function taking the caller's open store, because that is how
every record group in this package participates in the batch: the batch owns the transaction, and a
record group that opened its own would be a second write path.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, NamedTuple, cast

from pydantic import ValidationError

from agents_remember.kernel.canonical_json import decoded_json, sha256_digest
from agents_remember.memory.knowledge import routes
from agents_remember.memory.knowledge.facet_records import (
    RecordRevisionDraft,
    record_revision_row,
)
from agents_remember.memory.knowledge.record_envelope import validate_record_payload
from agents_remember.memory.knowledge.records import encode_authorship, encode_typed_column
from agents_remember.memory.knowledge.refusals import (
    KnowledgeRefused,
    KnowledgeStorageError,
    missing_expected_row_refusal,
    refusal,
)
from agents_remember.memory.knowledge.schema_generations import CURRENT_GENERATION
from agents_remember.memory.knowledge.schema_v9 import (
    CENSUS_APPLICABILITY,
    CENSUS_ARTIFACT_KINDS,
    CENSUS_ASSESSMENT_DISPOSITIONS,
    CENSUS_CLAIM_KINDS,
    CENSUS_DISPOSITION_KINDS,
    CENSUS_DISPOSITION_STATES,
    CENSUS_EVIDENCE_STATES,
    CENSUS_INVENTORY_STATES,
    CENSUS_LINK_KINDS,
    CENSUS_PARSE_OUTCOMES,
    CENSUS_REALIZATION_STATES,
    CENSUS_TARGET_STATES,
)
from agents_remember.memory.knowledge.store import OpenedKnowledgeStore
from agents_remember.models.knowledge.authorship import Authorship
from agents_remember.models.knowledge.base import ACCEPTED_STATE, PROPOSED_STATE
from agents_remember.models.knowledge.candidate import RecordIdentity
from agents_remember.models.knowledge.census import (
    CENSUS_CLAIM_KIND,
    CENSUS_CLAIM_SCHEMA,
    CENSUS_DISPOSITION_KIND,
    CENSUS_DISPOSITION_SCHEMA,
    CENSUS_INVENTORY_ROW_KIND,
    CENSUS_INVENTORY_ROW_SCHEMA,
    CensusApplicability,
    CensusArtifactKind,
    CensusAssessmentDisposition,
    CensusClaim,
    CensusClaimCommand,
    CensusClaimEvidence,
    CensusClaimKind,
    CensusClaimPayload,
    CensusClaimRealization,
    CensusDisposition,
    CensusDispositionCommand,
    CensusDispositionKind,
    CensusDispositionLink,
    CensusDispositionPayload,
    CensusDispositionState,
    CensusEvidenceState,
    CensusInventoryRow,
    CensusInventoryRowCommand,
    CensusInventoryRowPayload,
    CensusInventoryState,
    CensusLinkKind,
    CensusParseOutcome,
    CensusProvenance,
    CensusRealizationState,
    CensusTargetState,
)
from agents_remember.models.knowledge.result import KnowledgeOperation, KnowledgeRefusal

# The one operation this record group serves. Recording a census observation is the candidate batch's
# own operation because the packet requires these rows to be written through that one write path;
# reading the census back is this record group's, and it is named once so a result and a refusal
# cannot disagree about which act ran.
CensusOperation = KnowledgeOperation
CENSUS_OPERATION: KnowledgeOperation = "change_candidate"

CENSUS_RECORD_LIFECYCLE = PROPOSED_STATE


def _written_entry(state: str, table: str, record_id: str, digest: str) -> RecordIdentity:
    """Build one receipt entry without re-validating it: the store computed every field here.

    Declared here rather than imported from the batch module, because the batch module imports *this*
    one to dispatch the census commands: a record group that reached back up for a five-line value
    constructor would close an import cycle to avoid naming a type it already depends on.
    """

    return RecordIdentity.model_construct(
        state=state, table=table, record_id=record_id, digest=digest
    )


# The generation that registers these tables. A dataset whose recorded generation predates it is
# refused rather than migrated, widened or written through.
REQUIRED_CENSUS_GENERATION = CURRENT_GENERATION

# The ``(kind, record_schema)`` triples this record group declares, as one mapping so a fourth kind
# without a schema, or a schema without a kind, is unrepresentable.
# One INSERT per table, declared once beside the record group that owns the rows. The column order is
# each table's declared column order in the generation module, so a column added there without a
# statement here is a storage error rather than a silently mis-ordered write.
EFFECT_RECORD_INSERT = (
    "INSERT INTO knowledge_record (repository_id, record_id, kind, authority_home, lifecycle, "
    "governing_route_id, record_schema, provenance) VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
)
EFFECT_REVISION_INSERT = (
    "INSERT INTO record_revision (repository_id, revision_id, record_id, record_schema, payload, "
    "predecessor_revision_id, content_digest, provenance) VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
)

INVENTORY_ROW_INSERT = (
    "INSERT INTO census_inventory_row (repository_id, inventory_row_id, artifact_path, artifact_kind, "
    "declared_source_path, observed_doc_type, observed_route_path, outcome, unparsed_content, "
    "source_route_path, inventory_state, provenance) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)
CLAIM_INSERT = (
    "INSERT INTO census_claim (repository_id, claim_id, claim_text, claim_location, claim_kind, "
    "applicability, disposition, provenance) VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
)
DISPOSITION_INSERT = (
    "INSERT INTO census_disposition (repository_id, disposition_id, disposition_kind, "
    "disposition_state, rationale, provenance) VALUES (?, ?, ?, ?, ?, ?)"
)
CLAIM_EVIDENCE_INSERT = (
    "INSERT INTO census_claim_evidence (repository_id, claim_id, evidence_ref, evidence_state, "
    "assessment_disposition, provenance) VALUES (?, ?, ?, ?, ?, ?)"
)
CLAIM_REALIZATION_INSERT = (
    "INSERT INTO census_claim_realization (repository_id, claim_id, realization_ref, "
    "attribution_state, provenance) VALUES (?, ?, ?, ?, ?)"
)
DISPOSITION_LINK_INSERT = (
    "INSERT INTO census_disposition_link (repository_id, disposition_id, link_kind, target_ref, "
    "target_state, provenance) VALUES (?, ?, ?, ?, ?, ?)"
)


def _inventory_columns(row: Mapping[str, Any]) -> tuple[Any, ...]:
    """Return the ``census_inventory_row`` columns for one validated payload.

    ``provenance`` is the table's own typed-JSON column and carries the payload's stored
    :class:`…CensusProvenance` -- the artifact, the location and the frozen baseline. The authored
    envelope is not a column here: it already lives on ``knowledge_record``, and storing one fact twice
    is how the two copies start to disagree.
    """

    return (
        row["artifact_path"],
        row["artifact_kind"],
        row["declared_source_path"],
        row["observed_doc_type"],
        row["observed_route_path"],
        row["outcome"],
        None if row["unparsed_content"] is None else encode_typed_column(row["unparsed_content"]),
        row["source_route_path"],
        row["inventory_state"],
        encode_typed_column(row["provenance"]),
    )


def _decode_closed(value: object, admitted: tuple[str, ...], column: str) -> str:
    """Return one stored value that is a member of its declared vocabulary, refusing anything else.

    The vocabularies are the schema's own CHECK constraints, so a stored value outside one means the
    row was altered outside the operation. Coercing it into the nearest member would serve a state the
    vocabulary does not have, which is exactly what a closed vocabulary exists to prevent.
    """

    text = str(value)
    if text not in admitted:
        raise KnowledgeStorageError(
            f"a stored census value {text!r} in {column} is not one of the declared vocabulary "
            f"{admitted}; the row was altered outside the operation"
        )
    return text


def _decode_outcome(value: object) -> CensusParseOutcome:
    return cast(CensusParseOutcome, _decode_closed(value, CENSUS_PARSE_OUTCOMES, "outcome"))


def _decode_artifact_kind(value: object) -> CensusArtifactKind:
    return cast(CensusArtifactKind, _decode_closed(value, CENSUS_ARTIFACT_KINDS, "artifact_kind"))


def _decode_inventory_state(value: object) -> CensusInventoryState:
    return cast(
        CensusInventoryState,
        _decode_closed(value, CENSUS_INVENTORY_STATES, "inventory_state"),
    )


def _decode_claim_kind(value: object) -> CensusClaimKind:
    return cast(CensusClaimKind, _decode_closed(value, CENSUS_CLAIM_KINDS, "claim_kind"))


def _decode_applicability(value: object) -> CensusApplicability:
    return cast(CensusApplicability, _decode_closed(value, CENSUS_APPLICABILITY, "applicability"))


def _decode_disposition(value: object) -> CensusDispositionKind:
    return cast(
        CensusDispositionKind, _decode_closed(value, CENSUS_DISPOSITION_KINDS, "disposition")
    )


def _decode_disposition_state(value: object) -> CensusDispositionState:
    return cast(
        CensusDispositionState,
        _decode_closed(value, CENSUS_DISPOSITION_STATES, "disposition_state"),
    )


def _decode_evidence_state(value: object) -> CensusEvidenceState:
    return cast(
        CensusEvidenceState, _decode_closed(value, CENSUS_EVIDENCE_STATES, "evidence_state")
    )


def _decode_assessment(value: object) -> CensusAssessmentDisposition | None:
    return (
        None
        if value is None
        else cast(
            CensusAssessmentDisposition,
            _decode_closed(value, CENSUS_ASSESSMENT_DISPOSITIONS, "assessment_disposition"),
        )
    )


def _decode_realization_state(value: object) -> CensusRealizationState:
    return cast(
        CensusRealizationState,
        _decode_closed(value, CENSUS_REALIZATION_STATES, "attribution_state"),
    )


def _decode_link_kind(value: object) -> CensusLinkKind:
    return cast(CensusLinkKind, _decode_closed(value, CENSUS_LINK_KINDS, "link_kind"))


def _decode_target_state(value: object) -> CensusTargetState:
    return cast(CensusTargetState, _decode_closed(value, CENSUS_TARGET_STATES, "target_state"))


def _decode_lifecycle(value: object) -> Literal["proposed", "accepted"]:
    return cast(
        Literal["proposed", "accepted"],
        _decode_closed(value, (PROPOSED_STATE, ACCEPTED_STATE), "lifecycle"),
    )


def _decode_text(value: object) -> str | None:
    """Decode one stored opaque string, or return ``None`` for a recorded absence."""

    return None if value is None else str(decoded_json(str(value)))


def inventory_row_row(payload: CensusInventoryRowPayload) -> tuple[Any, ...]:
    """Return the whole ``census_inventory_row`` column tuple.

    The table's typed-JSON ``provenance`` column holds the payload's :class:`…CensusProvenance` --
    which artifact at which frozen baseline. The authored envelope is stored once, on
    ``knowledge_record``, and is not repeated here.
    """

    return _inventory_columns(payload.model_dump(mode="json"))


def claim_row(claim_id: str, payload: CensusClaimPayload) -> tuple[Any, ...]:
    """Return the whole ``census_claim`` column tuple.

    The table's typed-JSON ``provenance`` column carries the payload's
    :class:`…CensusProvenance` -- which artifact at which frozen baseline -- rather than the authored
    envelope: the envelope is one per record and already lives on ``knowledge_record``, and a read
    that wanted it reads it from there. Storing the same fact twice would let the two disagree.
    """

    return (
        claim_id,
        payload.claim_text,
        payload.claim_location,
        payload.claim_kind,
        payload.applicability,
        payload.disposition,
        encode_typed_column(payload.provenance),
    )


def disposition_row(disposition_id: str, payload: CensusDispositionPayload) -> tuple[Any, ...]:
    """Return the whole ``census_disposition`` column tuple, on the same rule as ``claim_row``."""

    return (
        disposition_id,
        payload.disposition_kind,
        payload.disposition_state,
        payload.rationale,
        encode_typed_column(payload.provenance),
    )


def claim_evidence_row(
    claim_id: str, evidence: CensusClaimEvidence, provenance: str
) -> tuple[Any, ...]:
    """Return the ``census_claim_evidence`` column tuple.

    A relation row has no payload of its own, so it inherits the **claim's** stored provenance: the
    relation is an observation about the same artifact at the same baseline, and a relation carrying a
    second, independently supplied baseline would be an observation whose baseline nobody checked.
    """

    return (
        claim_id,
        evidence.evidence_ref,
        evidence.evidence_state,
        evidence.assessment_disposition,
        provenance,
    )


def claim_realization_row(
    claim_id: str, realization: CensusClaimRealization, provenance: str
) -> tuple[Any, ...]:
    """Return the ``census_claim_realization`` column tuple, on the evidence relation's rule."""

    return (
        claim_id,
        realization.realization_ref,
        realization.attribution_state,
        provenance,
    )


def disposition_link_row(
    disposition_id: str, link: CensusDispositionLink, provenance: str
) -> tuple[Any, ...]:
    """Return the ``census_disposition_link`` column tuple, on the evidence relation's rule."""

    return (
        disposition_id,
        link.link_kind,
        link.target_ref,
        link.target_state,
        provenance,
    )


def require_census_generation(store: OpenedKnowledgeStore) -> None:
    """Refuse a census command against a dataset that predates the census's tables.

    The dataset's **own** recorded generation is compared with the one that registers these tables,
    and the refusal carries both numbers as facts. A dataset older than that generation is not
    migrated, not repaired and not extended in place -- the same disposition every earlier record
    group's generation check takes.
    """

    observed = store.generation.user_version
    required = REQUIRED_CENSUS_GENERATION.user_version
    if observed >= required:
        return
    raise KnowledgeRefused(
        refusal(
            "unsupported_schema",
            CENSUS_OPERATION,
            "the census tables are registered by a later schema generation than this dataset "
            "declares, so this command cannot be written through it",
            next_action=(
                "write the census into a dataset created at the current generation; an older "
                "dataset is read through its own generation and is never migrated in place"
            ),
            facts=None,
        )
    )


def apply_census_command(
    store: OpenedKnowledgeStore,
    command: CensusInventoryRowCommand | CensusClaimCommand | CensusDispositionCommand,
    authorship: Authorship,
) -> tuple[RecordIdentity, ...]:
    """Apply one census command inside the caller's open transaction.

    Every refusal below is raised rather than returned, so the transaction that carries the record and
    its revision aborts whole: a refused command leaves no envelope row, no revision and no relation
    behind, and an earlier command's rows in the same batch are rolled back with it.
    """

    require_census_generation(store)
    _require_governing_route(store, command.governing_route_id)
    if isinstance(command, CensusInventoryRowCommand):
        return _apply_inventory_row(store, command, authorship)
    if isinstance(command, CensusClaimCommand):
        return _apply_claim(store, command, authorship)
    return _apply_disposition(store, command, authorship)


def _require_governing_route(store: OpenedKnowledgeStore, route_id: str | None) -> None:
    """Refuse a record whose declared governing route is not authored in this repository.

    An ungoverned record is the explicit ``None`` state and is never refused. A *named* route that
    does not exist is a dangling reference and is refused rather than stored, which is what keeps
    "slices are keyed by recorded routes" true instead of aspirational.
    """

    if route_id is None:
        return
    if routes.route_exists(store.connection, store.repository_id, route_id):
        return
    raise KnowledgeRefused(
        missing_expected_row_refusal(operation=CENSUS_OPERATION, table="route", record_id=route_id)
    )


def _admissible(command: Any, kind: str, schema: str) -> Mapping[str, Any]:
    """Resolve one payload through the envelope seam, raising the seam's own refusal."""

    validated = validate_record_payload(
        kind,
        schema,
        command.payload.model_dump(mode="json"),
        operation=CENSUS_OPERATION,
        record_id=command.revision_id,
    )
    if isinstance(validated, KnowledgeRefusal):
        raise KnowledgeRefused(validated)
    return validated.model_dump(mode="json")


def _write_envelope(
    store: OpenedKnowledgeStore,
    command: Any,
    declaration: tuple[str, str],
    payload: Mapping[str, Any],
    authorship: Authorship,
) -> None:
    """Write the envelope row and its one sealed revision for a census record.

    ``declaration`` is the ``(kind, record_schema)`` pair, passed as the one value the registry keys
    on rather than as two arguments that a caller could transpose.
    """

    kind, schema = declaration
    foundation = _foundation(store, command, kind, schema, authorship)
    store.write(foundation.record_insert, foundation.record_parameters)
    store.write(
        foundation.revision_insert,
        (store.repository_id, *record_revision_row(foundation.draft, payload, authorship)),
    )


@dataclass(frozen=True)
class _Foundation:
    """One census record's envelope write, as the values its two statements need."""

    record_insert: str
    record_parameters: tuple[Any, ...]
    revision_insert: str
    draft: RecordRevisionDraft


def _foundation(
    store: OpenedKnowledgeStore,
    command: Any,
    kind: str,
    schema: str,
    authorship: Authorship,
) -> _Foundation:
    """Return the envelope and revision statements one census record needs, with their values."""

    return _Foundation(
        record_insert=EFFECT_RECORD_INSERT,
        record_parameters=(
            store.repository_id,
            command.record_id,
            kind,
            _authority_home(store),
            CENSUS_RECORD_LIFECYCLE,
            command.governing_route_id,
            schema,
            encode_authorship(authorship),
        ),
        revision_insert=EFFECT_REVISION_INSERT,
        draft=RecordRevisionDraft(
            record_id=command.record_id, revision_id=command.revision_id, record_schema=schema
        ),
    )


def _authority_home(store: OpenedKnowledgeStore) -> str:
    """Return the authority home the bound repository declares.

    A record's ``authority_home`` is a fact about the namespace it was written into rather than a
    field a caller authors: the destination resolved that namespace, so the record inherits it.
    """

    repository = store.get_repository()
    if repository is None:
        raise KnowledgeStorageError(
            "the open store has no repository row, so a record has no authority home to inherit"
        )
    return repository.authority_home


def _apply_inventory_row(
    store: OpenedKnowledgeStore, command: CensusInventoryRowCommand, authorship: Authorship
) -> tuple[RecordIdentity, ...]:
    """Write one inventory row and return its receipt entry."""

    payload = _admissible(command, CENSUS_INVENTORY_ROW_KIND, CENSUS_INVENTORY_ROW_SCHEMA)
    _write_envelope(
        store,
        command,
        declaration=(CENSUS_INVENTORY_ROW_KIND, CENSUS_INVENTORY_ROW_SCHEMA),
        payload=payload,
        authorship=authorship,
    )
    row = inventory_row_row(command.payload)
    store.write(INVENTORY_ROW_INSERT, (store.repository_id, command.record_id, *row))
    return (
        _written_entry(
            "written",
            "census_inventory_row",
            command.record_id,
            _row_digest("census_inventory_row", command.record_id, row),
        ),
    )


def _apply_claim(
    store: OpenedKnowledgeStore, command: CensusClaimCommand, authorship: Authorship
) -> tuple[RecordIdentity, ...]:
    """Write one claim, its evidence and its realization relations, and return the receipt."""

    payload = _admissible(command, CENSUS_CLAIM_KIND, CENSUS_CLAIM_SCHEMA)
    _write_envelope(
        store,
        command,
        declaration=(CENSUS_CLAIM_KIND, CENSUS_CLAIM_SCHEMA),
        payload=payload,
        authorship=authorship,
    )
    stored_provenance = encode_typed_column(command.payload.provenance.model_dump(mode="json"))
    row = claim_row(command.record_id, command.payload)
    store.write(CLAIM_INSERT, (store.repository_id, *row))
    entries = [
        _written_entry(
            "written",
            "census_claim",
            command.record_id,
            _row_digest("census_claim", command.record_id, row),
        )
    ]
    for evidence in command.evidence:
        values = claim_evidence_row(command.record_id, evidence, stored_provenance)
        store.write(CLAIM_EVIDENCE_INSERT, (store.repository_id, *values))
        entries.append(
            _written_entry(
                "written",
                "census_claim_evidence",
                f"{command.record_id}:{evidence.evidence_ref}",
                _row_digest("census_claim_evidence", command.record_id, values),
            )
        )
    for realization in command.realizations:
        values = claim_realization_row(command.record_id, realization, stored_provenance)
        store.write(CLAIM_REALIZATION_INSERT, (store.repository_id, *values))
        entries.append(
            _written_entry(
                "written",
                "census_claim_realization",
                f"{command.record_id}:{realization.realization_ref}",
                _row_digest("census_claim_realization", command.record_id, values),
            )
        )
    return tuple(entries)


def _apply_disposition(
    store: OpenedKnowledgeStore, command: CensusDispositionCommand, authorship: Authorship
) -> tuple[RecordIdentity, ...]:
    """Write one migration disposition, its links, and return the receipt."""

    payload = _admissible(command, CENSUS_DISPOSITION_KIND, CENSUS_DISPOSITION_SCHEMA)
    _write_envelope(
        store,
        command,
        declaration=(CENSUS_DISPOSITION_KIND, CENSUS_DISPOSITION_SCHEMA),
        payload=payload,
        authorship=authorship,
    )
    stored_provenance = encode_typed_column(command.payload.provenance.model_dump(mode="json"))
    row = disposition_row(command.record_id, command.payload)
    store.write(DISPOSITION_INSERT, (store.repository_id, *row))
    entries = [
        _written_entry(
            "written",
            "census_disposition",
            command.record_id,
            _row_digest("census_disposition", command.record_id, row),
        )
    ]
    for link in command.links:
        values = disposition_link_row(command.record_id, link, stored_provenance)
        store.write(DISPOSITION_LINK_INSERT, (store.repository_id, *values))
        entries.append(
            _written_entry(
                "written",
                "census_disposition_link",
                f"{command.record_id}:{link.link_kind}:{link.target_ref}",
                _row_digest("census_disposition_link", command.record_id, values),
            )
        )
    return tuple(entries)


def _row_digest(table: str, record_id: str, values: Sequence[Any]) -> str:
    """Digest one written census row, so an expectation can name it.

    The digest is computed here rather than stored: the census records mint no identity of their own,
    and a stored digest column on a census table would be a second identity authority beside the
    envelope's sealed revision.
    """

    return sha256_digest({"table": table, "record_id": record_id, "values": list(values)})


# -- reading the census back ---------------------------------------------------------------------

_INVENTORY_ROW_SELECT = (
    "SELECT repository_id, inventory_row_id, artifact_path, artifact_kind, declared_source_path, "
    "observed_doc_type, observed_route_path, outcome, unparsed_content, source_route_path, "
    "inventory_state, provenance FROM census_inventory_row WHERE repository_id = ? "
    "ORDER BY inventory_row_id"
)
_CLAIM_SELECT = (
    "SELECT repository_id, claim_id, claim_text, claim_location, claim_kind, applicability, "
    "disposition, provenance FROM census_claim WHERE repository_id = ? ORDER BY claim_id"
)
_DISPOSITION_SELECT = (
    "SELECT repository_id, disposition_id, disposition_kind, disposition_state, rationale, "
    "provenance FROM census_disposition WHERE repository_id = ? ORDER BY disposition_id"
)
_CLAIM_EVIDENCE_SELECT = (
    "SELECT claim_id, evidence_ref, evidence_state, assessment_disposition "
    "FROM census_claim_evidence "
    "WHERE repository_id = ? ORDER BY claim_id, evidence_ref"
)
_CLAIM_REALIZATION_SELECT = (
    "SELECT claim_id, realization_ref, attribution_state FROM census_claim_realization "
    "WHERE repository_id = ? ORDER BY claim_id, realization_ref"
)
_DISPOSITION_LINK_SELECT = (
    "SELECT disposition_id, link_kind, target_ref, target_state FROM census_disposition_link "
    "WHERE repository_id = ? ORDER BY disposition_id, link_kind, target_ref"
)
_ENVELOPE_ROUTES_SELECT = (
    "SELECT record_id, lifecycle, governing_route_id, record_schema FROM knowledge_record "
    "WHERE repository_id = ? AND kind = ?"
)


class _EnvelopeEntry(NamedTuple):
    """One stored envelope row's own facts: its lifecycle, its governing route and its record schema.

    A named tuple rather than a bare tuple, because these three travel together through three
    functions and a positional triple read by index is where the wrong field gets the right name.
    """

    lifecycle: Literal["proposed", "accepted"]
    governing_route_id: str | None
    record_schema: str


def _rows(store: OpenedKnowledgeStore, statement: str, *extra: Any) -> list[Sequence[Any]]:
    """Return every stored row one census read selects, as plain tuples."""

    cursor = store.connection.execute(statement, (store.repository_id, *extra))
    return list(cursor)


def _envelope_of(store: OpenedKnowledgeStore, kind: str) -> dict[str, _EnvelopeEntry]:
    """Return each stored envelope row of one kind, keyed by record id."""

    decoded: dict[str, _EnvelopeEntry] = {}
    for row in _rows(store, _ENVELOPE_ROUTES_SELECT, kind):
        record_id = str(row[0])
        route = None if row[2] is None else str(row[2])
        decoded[record_id] = _EnvelopeEntry(_decode_lifecycle(row[1]), route, str(row[3]))
    return decoded


# The entry a census table row reports when its envelope row is absent. It carries the empty schema so
# the declared-schema check refuses by name rather than serving a row whose envelope was never written.
_UNKNOWN_ENVELOPE = _EnvelopeEntry(PROPOSED_STATE, None, "")


def _require_declared_schema(record_id: str, stored: str, declared: str) -> None:
    """Refuse a stored record whose schema disagrees with the kind's own declaration."""

    if stored == declared:
        return
    raise KnowledgeStorageError(
        f"census record {record_id} stores record_schema {stored!r}, which is not the schema kind "
        f"{declared!r} declares; the row was altered outside the operation"
    )


def _decode_provenance(value: object) -> CensusProvenance:
    """Decode one stored provenance column into the frozen model, refusing a malformed one."""

    decoded = decoded_json(str(value))
    if isinstance(decoded, dict) and isinstance(decoded.get("author"), dict):
        # The provenance is stored as typed JSON, so its nested authorship decodes as a mapping and is
        # reconstructed here rather than left to the model's coercer -- a nested authored value is a
        # value this tree types, never an ambient dictionary.
        decoded = {**decoded, "author": Authorship.model_validate(decoded["author"])}
    try:
        return CensusProvenance.model_validate(decoded)
    except ValidationError as error:
        raise KnowledgeStorageError(
            f"a stored census provenance does not validate against its frozen shape: {error}"
        ) from error


def read_inventory_rows(store: OpenedKnowledgeStore) -> tuple[CensusInventoryRow, ...]:
    """Return every stored inventory row, with its envelope's own facts."""

    envelopes = _envelope_of(store, CENSUS_INVENTORY_ROW_KIND)
    decoded: list[CensusInventoryRow] = []
    for row in _rows(store, _INVENTORY_ROW_SELECT):
        record_id = str(row[1])
        envelope = envelopes.get(record_id, _UNKNOWN_ENVELOPE)
        lifecycle, route = envelope.lifecycle, envelope.governing_route_id
        stored_schema = envelope.record_schema
        _require_declared_schema(record_id, stored_schema, CENSUS_INVENTORY_ROW_SCHEMA)
        payload = CensusInventoryRowPayload(
            artifact_path=str(row[2]),
            artifact_kind=_decode_artifact_kind(row[3]),
            declared_source_path=None if row[4] is None else str(row[4]),
            observed_doc_type=None if row[5] is None else str(row[5]),
            observed_route_path=None if row[6] is None else str(row[6]),
            outcome=_decode_outcome(row[7]),
            unparsed_content=_decode_text(row[8]),
            source_route_path=None if row[9] is None else str(row[9]),
            inventory_state=_decode_inventory_state(row[10]),
            provenance=_decode_provenance(row[11]),
        )
        decoded.append(
            CensusInventoryRow(
                record_id=record_id, lifecycle=lifecycle, governing_route_id=route, payload=payload
            )
        )
    return tuple(decoded)


def read_claims(store: OpenedKnowledgeStore) -> tuple[CensusClaim, ...]:
    """Return every stored census claim, with its evidence and realization relations."""

    envelopes = _envelope_of(store, CENSUS_CLAIM_KIND)
    evidence: dict[str, list[CensusClaimEvidence]] = {}
    for row in _rows(store, _CLAIM_EVIDENCE_SELECT):
        evidence.setdefault(str(row[0]), []).append(
            CensusClaimEvidence(
                claim_id=str(row[0]),
                evidence_ref=str(row[1]),
                evidence_state=_decode_evidence_state(row[2]),
                assessment_disposition=_decode_assessment(row[3]),
            )
        )
    realizations: dict[str, list[CensusClaimRealization]] = {}
    for row in _rows(store, _CLAIM_REALIZATION_SELECT):
        realizations.setdefault(str(row[0]), []).append(
            CensusClaimRealization(
                claim_id=str(row[0]),
                realization_ref=str(row[1]),
                attribution_state=_decode_realization_state(row[2]),
            )
        )
    decoded: list[CensusClaim] = []
    for row in _rows(store, _CLAIM_SELECT):
        record_id = str(row[1])
        envelope = envelopes.get(record_id, _UNKNOWN_ENVELOPE)
        lifecycle, route = envelope.lifecycle, envelope.governing_route_id
        stored_schema = envelope.record_schema
        _require_declared_schema(record_id, stored_schema, CENSUS_CLAIM_SCHEMA)
        payload = CensusClaimPayload(
            claim_text=str(row[2]),
            claim_location=str(row[3]),
            claim_kind=_decode_claim_kind(row[4]),
            applicability=_decode_applicability(row[5]),
            disposition=_decode_disposition(row[6]),
            provenance=_decode_provenance(row[7]),
        )
        decoded.append(
            CensusClaim(
                record_id=record_id,
                lifecycle=lifecycle,
                governing_route_id=route,
                payload=payload,
                evidence=tuple(evidence.get(record_id, ())),
                realizations=tuple(realizations.get(record_id, ())),
            )
        )
    return tuple(decoded)


def read_dispositions(store: OpenedKnowledgeStore) -> tuple[CensusDisposition, ...]:
    """Return every stored migration disposition, with its links."""

    envelopes = _envelope_of(store, CENSUS_DISPOSITION_KIND)
    links: dict[str, list[CensusDispositionLink]] = {}
    for row in _rows(store, _DISPOSITION_LINK_SELECT):
        links.setdefault(str(row[0]), []).append(
            CensusDispositionLink(
                disposition_id=str(row[0]),
                link_kind=_decode_link_kind(row[1]),
                target_ref=str(row[2]),
                target_state=_decode_target_state(row[3]),
            )
        )
    decoded: list[CensusDisposition] = []
    for row in _rows(store, _DISPOSITION_SELECT):
        record_id = str(row[1])
        envelope = envelopes.get(record_id, _UNKNOWN_ENVELOPE)
        lifecycle, route = envelope.lifecycle, envelope.governing_route_id
        stored_schema = envelope.record_schema
        _require_declared_schema(record_id, stored_schema, CENSUS_DISPOSITION_SCHEMA)
        payload = CensusDispositionPayload(
            disposition_kind=_decode_disposition(row[2]),
            disposition_state=_decode_disposition_state(row[3]),
            rationale=None if row[4] is None else str(row[4]),
            provenance=_decode_provenance(row[5]),
        )
        decoded.append(
            CensusDisposition(
                record_id=record_id,
                lifecycle=lifecycle,
                governing_route_id=route,
                payload=payload,
                links=tuple(links.get(record_id, ())),
            )
        )
    return tuple(decoded)


# -- the expectation readers ---------------------------------------------------------------------
#
# One reader per census table, returning the same digest the write path put in its receipt and the
# read path exposes. A caller therefore carries an expectation straight from a read instead of
# deriving a second identity scheme that could disagree with the one it read.

_INVENTORY_ROW_BY_ID = (
    "SELECT repository_id, inventory_row_id, artifact_path, artifact_kind, declared_source_path, "
    "observed_doc_type, observed_route_path, outcome, unparsed_content, source_route_path, "
    "inventory_state, provenance FROM census_inventory_row "
    "WHERE repository_id = ? AND inventory_row_id = ?"
)
_CLAIM_BY_ID = (
    "SELECT repository_id, claim_id, claim_text, claim_location, claim_kind, applicability, "
    "disposition, provenance FROM census_claim WHERE repository_id = ? AND claim_id = ?"
)
_DISPOSITION_BY_ID = (
    "SELECT repository_id, disposition_id, disposition_kind, disposition_state, rationale, "
    "provenance FROM census_disposition WHERE repository_id = ? AND disposition_id = ?"
)


def inventory_row_digest(store: OpenedKnowledgeStore, row_id: str) -> str | None:
    """Return one stored inventory row's digest, or ``None`` when it is not stored."""

    rows = tuple(store.connection.execute(_INVENTORY_ROW_BY_ID, (store.repository_id, row_id)))
    if not rows:
        return None
    row = rows[0]
    values = tuple(row[2:11])
    return _row_digest("census_inventory_row", row_id, values)


def claim_digest(store: OpenedKnowledgeStore, claim_id: str) -> str | None:
    """Return one stored census claim's digest, or ``None`` when it is not stored."""

    rows = tuple(store.connection.execute(_CLAIM_BY_ID, (store.repository_id, claim_id)))
    if not rows:
        return None
    row = rows[0]
    return _row_digest("census_claim", claim_id, tuple(row[2:7]))


def disposition_digest(store: OpenedKnowledgeStore, disposition_id: str) -> str | None:
    """Return one stored migration disposition's digest, or ``None`` when it is not stored."""

    rows = tuple(
        store.connection.execute(_DISPOSITION_BY_ID, (store.repository_id, disposition_id))
    )
    if not rows:
        return None
    row = rows[0]
    return _row_digest("census_disposition", disposition_id, tuple(row[2:5]))
