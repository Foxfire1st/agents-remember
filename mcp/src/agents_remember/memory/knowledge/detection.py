"""Mechanical detection's records: the run's assembly, its write path, its read path and its versions.

This module owns the **record** half of ``KS-R14@v1`` -- what a detection run and its ordered signals
are written as, read back as, and compared against the versions now in force. The walk that decides
*which* recorded facts match which declared condition is
:mod:`agents_remember.memory.knowledge.detection_walk`; the two are split along the property each one
protects (the classification, and the record's own refusals) rather than along a call boundary, and
nothing here re-selects anything: the selection is R07's and the union is R08's.

Four properties are enforced here rather than documented:

* **Nothing here publishes, archives or deletes.** A signal records its manifest reference, its
  retention state and the destination that was checked;
  :func:`~agents_remember.models.knowledge.detection.DetectionScopeManifest.resolve` reports a
  reference it cannot resolve as unresolved, naming what would resolve it, and never as an empty
  manifest. The durable publication route belongs to ``KS-R12@v1``.
* **A detection write never enters the assessed dataset's measurement transaction.** Requirement 7.1
  is enforced as a refusal with its own code (``detection_self_reference``): the request names the
  databases it measured, and a detection store that *is* one of them is refused before any row is
  written, so a detector cannot move the identity of the dataset it just digested.
* **A recorded run's order is sealed.** The signals are written into ``detection_run_signal`` under
  generation 4's composite keys, and generation 4's triggers refuse reordering or shortening them, so
  requirement 3.4's "never overwrites the recorded one" holds against a later code path that forgot it
  as well as against this one.
* **A read is verified against its recorded seal.** The stored revision's own content digest is
  recomputed on the way out, so a payload altered behind its identity is reported as a damaged store
  rather than served as a recorded run.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import NAMESPACE_URL, uuid5

from agents_remember.kernel.canonical_json import decoded_json, sha256_digest
from agents_remember.memory.knowledge.facet_records import (
    RecordRevisionDraft,
    record_revision_digest,
    record_revision_row,
)
from agents_remember.memory.knowledge.record_envelope import PAYLOAD_MODELS, validate_record_payload
from agents_remember.memory.knowledge.records import encode_authorship
from agents_remember.memory.knowledge.refusals import (
    KnowledgeRefused,
    KnowledgeStorageError,
    RefusalFacts,
    SqliteFailureContext,
    refusal,
    scope_refusal,
)
from agents_remember.memory.knowledge.schema_generations import GENERATION_4
from agents_remember.models.knowledge.authorship import Authorship
from agents_remember.models.knowledge.detection import (
    CONDITION_VOCABULARY_VERSION,
    DECLARED_INPUT_SETS,
    DETECTION_EXTRACTOR_VERSION,
    DETECTION_POLICY_VERSION,
    DETECTION_RUN_KIND,
    DETECTION_RUN_SCHEMA,
    DETECTION_SIGNAL_KIND,
    DETECTION_SIGNAL_SCHEMA,
    NO_SEMANTIC_ASSESSMENT_LIMITATION,
    DetectionInputSide,
    DetectionLimitation,
    DetectionManifestResolution,
    DetectionRunCurrentness,
    DetectionRunInputDifference,
    DetectionRunPayload,
    DetectionRunReproduction,
    DetectionRunRequest,
    DetectionRunResult,
    DetectionScopeManifest,
    DetectionSignalPayload,
    ManifestDestinationObservation,
)
from agents_remember.models.knowledge.result import KnowledgeOperation, KnowledgeRefusal

if TYPE_CHECKING:
    from agents_remember.memory.knowledge.store import OpenedKnowledgeStore

# The generation whose table a detection write needs. A dataset that predates it is refused, never
# migrated or widened -- the same rule the facet write applies to its own generation.
REQUIRED_DETECTION_GENERATION = GENERATION_4

_RECORD_INSERT = (
    "INSERT INTO knowledge_record (repository_id, record_id, kind, authority_home, lifecycle, "
    "governing_route_id, record_schema, provenance) VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
)
_REVISION_INSERT = (
    "INSERT INTO record_revision (repository_id, revision_id, record_id, record_schema, payload, "
    "predecessor_revision_id, content_digest, provenance) VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
)
_SEQUENCE_INSERT = "INSERT INTO detection_run_signal (repository_id, run_id, ordinal, signal_id) VALUES (?, ?, ?, ?)"
_RUN_ROW = (
    "SELECT record_id, kind, governing_route_id, record_schema FROM knowledge_record "
    "WHERE repository_id = ? AND record_id = ? AND kind = ?"
)
_RUN_REVISION = (
    "SELECT revision_id, record_schema, payload FROM record_revision "
    "WHERE repository_id = ? AND record_id = ? ORDER BY revision_id"
)
_SEQUENCE_ROWS = (
    "SELECT ordinal, signal_id FROM detection_run_signal "
    "WHERE repository_id = ? AND run_id = ? ORDER BY ordinal"
)
_SIGNAL_REVISION = (
    "SELECT revision_id, record_schema, payload FROM record_revision "
    "WHERE repository_id = ? AND record_id = ? ORDER BY revision_id"
)

# The lifecycle every detection record is written under. A detection signal is a *recorded
# measurement*, not a proposal awaiting acceptance: it is not accepted origin data and it carries no
# acceptance reference, so it declares the proposed lifecycle exactly as an authored facet does.
DETECTION_RECORD_LIFECYCLE = "proposed"


# ---------------------------------------------------------------------------
# The run: assembly, the write path and the read path.


@dataclass(frozen=True)
class DetectionRunAssembly:
    """The identities one run is assembled under, as a value rather than an argument list.

    They travel together because they are the run's *binding*: the identity it is recorded under, the
    namespace it is recorded into, the namespace it measured, the route that governs it, the sides it
    read and the versions it ran under. A builder handed them separately could be handed one run's
    identity with another run's assessed repository.
    """

    run_id: str
    repository_id: str
    assessed_repository_id: str
    governing_route_id: str
    input_sides: tuple[DetectionInputSide, ...]
    policy_version: str = DETECTION_POLICY_VERSION
    extractor_version: str = DETECTION_EXTRACTOR_VERSION
    limitations: tuple[DetectionLimitation, ...] = (NO_SEMANTIC_ASSESSMENT_LIMITATION,)


def build_detection_run(
    assembly: DetectionRunAssembly, signals: Sequence[DetectionSignalPayload]
) -> DetectionRunPayload:
    """Assemble one run from the signals a walk produced, in the order it produced them.

    The declared order is the sequence the walk emitted, and the run's recorded conditions and
    declared input sets are read off that sequence rather than supplied separately, so requirement
    2.4's "the per-signal values are not collapsed into a run-level default" holds by construction:
    a run over two signals with different declared input sets records both because both signals'
    values are carried.
    """

    ordered = tuple(signals)
    return DetectionRunPayload(
        run_id=assembly.run_id,
        repository_id=assembly.repository_id,
        assessed_repository_id=assembly.assessed_repository_id,
        governing_route_id=assembly.governing_route_id,
        policy_version=assembly.policy_version,
        extractor_version=assembly.extractor_version,
        condition_vocabulary_version=CONDITION_VOCABULARY_VERSION,
        input_sides=assembly.input_sides,
        declared_input_sets=tuple(dict.fromkeys(signal.input_set.declared for signal in ordered)),
        recorded_conditions=tuple(signal.condition for signal in ordered),
        signal_order=tuple(signal.signal_id for signal in ordered),
        limitations=assembly.limitations,
        detail=_run_detail(
            assembly.policy_version, assembly.extractor_version, ordered, assembly.limitations
        ),
    )


def _run_detail(
    policy_version: str,
    extractor_version: str,
    signals: tuple[DetectionSignalPayload, ...],
    limitations: tuple[DetectionLimitation, ...],
) -> str:
    members = " | ".join(dict.fromkeys(signal.input_set.declared for signal in signals)) or "<none>"
    declared = " | ".join(limitations) or "<none>"
    return (
        f"policy={policy_version}; extractor={extractor_version}; "
        f"conditions={CONDITION_VOCABULARY_VERSION}; declared_input_sets={members}; "
        f"ordered_signals={len(signals)}; limitations={declared}"
    )


def require_detection_generation(
    store: OpenedKnowledgeStore, operation: KnowledgeOperation
) -> KnowledgeRefusal | None:
    """Refuse a detection write against a dataset whose recorded generation predates its table.

    The dataset's own generation is read from the open store, so this compares against what the file
    declares rather than against what the build supports, and the refusal carries both numbers as
    facts. Nothing is migrated, widened or written through.
    """

    observed = store.generation.user_version
    required = REQUIRED_DETECTION_GENERATION.user_version
    if observed >= required:
        return None
    return refusal(
        "unsupported_schema",
        operation,
        "the detection sequence table is registered by generation "
        f"{REQUIRED_DETECTION_GENERATION.schema_name} (user_version {required})",
        facts=RefusalFacts(
            table="detection_run_signal",
            expected=str(required),
            observed=str(observed),
        ),
        next_action=(
            "Record detection runs in a store whose declared generation carries the detection "
            "tables. A dataset that predates them is not migrated, repaired or written through."
        ),
    )


def require_separate_from_assessed(
    store: OpenedKnowledgeStore,
    assessed_database_paths: Sequence[str],
    operation: KnowledgeOperation,
) -> KnowledgeRefusal | None:
    """Refuse a detection write whose target is one of the datasets the run measured.

    Requirement 7.1: a signal and its run are measurements of an already-existing dataset, and a
    detector whose own output changes the logical digest of the dataset it just digested has
    invalidated its own signal. The check is the dataset's own file identity rather than a promise,
    so the refusal is structural: the detection store and every assessed database are resolved to
    their real paths and an overlap is refused with the code that names the fact.
    """

    target = _resolved(store.database_path)
    for candidate in assessed_database_paths:
        if _resolved(candidate) == target:
            return refusal(
                "detection_self_reference",
                operation,
                "the detection store is one of the databases this run measured, so writing the "
                "measurement into it would move the logical digest of the dataset it just digested "
                "and invalidate its own signal",
                facts=RefusalFacts(
                    table="detection_run_signal",
                    record_id=str(store.database_path),
                    expected="a detection store outside every assessed database",
                    observed=str(candidate),
                ),
                next_action=(
                    "Open a detection store that is a different database from every assessed "
                    "snapshot and submit the run again. Nothing was written."
                ),
            )
    return None


def _resolved(path: object) -> str:
    return str(Path(str(path)).resolve())


def record_detection_run(
    store: OpenedKnowledgeStore, request: DetectionRunRequest
) -> DetectionRunResult:
    """Record one detection run and its ordered signals, or return one typed refusal."""

    operation: KnowledgeOperation = "record_detection_run"
    denied = scope_refusal(operation, store.repository_id, request.repository_id)
    if denied is None:
        denied = require_detection_generation(store, operation)
    if denied is None:
        denied = require_separate_from_assessed(store, request.assessed_database_paths, operation)
    if denied is None:
        denied = require_run_signal_agreement(request)
    if denied is not None:
        return _refused(request.repository_id, denied, operation)
    with store.exclusive_candidate_lock(operation) as lock_refusal:
        if lock_refusal is not None:
            return _refused(request.repository_id, lock_refusal, operation)
        return store.within_immediate(
            lambda: _write_run(store, request),
            on_refusal=lambda refused_value: _refused(
                request.repository_id, refused_value, operation
            ),
            failure=SqliteFailureContext(
                operation=operation, table="knowledge_record", record_id=request.run.run_id
            ),
        )


@dataclass(frozen=True)
class _EnvelopeWrite:
    """One detection record's envelope, as the value the writer stores.

    ``kind`` and ``record_schema`` travel together with the payload because the envelope's registry
    resolves on the *pair*: a payload admitted for one kind is not automatically admitted for
    another, so a writer handed them separately could store a pair the registry would have refused.

    The envelope's own ``governing_route_id`` association is deliberately left unset. A signal's
    governing route names a route in the **assessed** repository's namespace, and
    :mod:`…detection`'s chosen home for these records is a store that is not the assessed dataset
    (requirement 7.2). Binding the column would require copying the assessed route into the detection
    store, and a second copy of one fact is a second authority -- which is the thing
    ``design/retrieval-review-design.md:368`` forbids for a shared knowledge view. The route is
    therefore recorded where it belongs and is still required: as the validated ``governing_route_id``
    field on the run and on every signal, both of which fail construction without it.
    """

    record_id: str
    kind: str
    record_schema: str
    payload: Mapping[str, Any]


def _write_run(store: OpenedKnowledgeStore, request: DetectionRunRequest) -> DetectionRunResult:
    """Write one run, its signals and their recorded order inside the caller's transaction."""

    authorship = request.provenance
    authority_home = _authority_home(store)
    _write_envelope(
        store,
        _EnvelopeWrite(
            record_id=request.run.run_id,
            kind=DETECTION_RUN_KIND,
            record_schema=DETECTION_RUN_SCHEMA,
            payload=request.run.model_dump(mode="json"),
        ),
        authority_home=authority_home,
        authorship=authorship,
    )
    for signal in request.signals:
        _write_envelope(
            store,
            _EnvelopeWrite(
                record_id=signal.signal_id,
                kind=DETECTION_SIGNAL_KIND,
                record_schema=DETECTION_SIGNAL_SCHEMA,
                payload=signal.model_dump(mode="json"),
            ),
            authority_home=authority_home,
            authorship=authorship,
        )
    for ordinal, signal in enumerate(request.signals):
        store.write(
            _SEQUENCE_INSERT,
            (store.repository_id, request.run.run_id, ordinal, signal.signal_id),
        )
    return DetectionRunResult(
        state="created",
        operation="record_detection_run",
        repository_id=request.repository_id,
        run=request.run,
        signals=tuple(request.signals),
    )


def _write_envelope(
    store: OpenedKnowledgeStore,
    envelope: _EnvelopeWrite,
    *,
    authority_home: str,
    authorship: Authorship,
) -> None:
    """Validate one payload through the envelope seam and write its record and first revision.

    The payload is validated through :func:`validate_record_payload` even though the typed request
    already produced it, because that function is the **only** place a write path decides whether a
    payload is admissible and a second write path that skipped it would be a second decision.
    """

    record_id, kind, record_schema = envelope.record_id, envelope.kind, envelope.record_schema
    validated = validate_record_payload(
        kind, record_schema, envelope.payload, operation="record_detection_run", record_id=record_id
    )
    if isinstance(validated, KnowledgeRefusal):  # pragma: no cover - the request already validated
        raise KnowledgeRefused(validated)
    frozen = validated.model_dump(mode="json")
    store.write(
        _RECORD_INSERT,
        (
            store.repository_id,
            record_id,
            kind,
            authority_home,
            DETECTION_RECORD_LIFECYCLE,
            # The envelope association is unset for the reason ``_EnvelopeWrite`` states; the route
            # itself is a required field of both payloads and is not optional anywhere.
            None,
            record_schema,
            _encoded_authorship(authorship),
        ),
    )
    store.write(
        _REVISION_INSERT,
        (
            store.repository_id,
            *record_revision_row(
                RecordRevisionDraft(
                    record_id=record_id,
                    revision_id=_revision_id_of(record_id, kind),
                    record_schema=record_schema,
                    predecessor_revision_id=None,
                ),
                frozen,
                authorship,
            ),
        ),
    )


def _revision_id_of(record_id: str, kind: str) -> str:
    """Return the one revision identity a detection record's first revision is stored under."""

    return str(uuid5(NAMESPACE_URL, f"ar-detection-revision/v1/{kind}/{record_id}"))


def _authority_home(store: OpenedKnowledgeStore) -> str:
    """Return the authority home the bound namespace declares.

    A detection envelope's ``authority_home`` is a fact about the namespace it was written into, not
    a value the caller supplies, so it is read from the store's own repository row rather than
    accepted as input.
    """

    repository = store.get_repository()
    if repository is None:  # pragma: no cover - an opened store always has its row
        raise KnowledgeStorageError(
            "the store holds no repository row, so no authority home describes it"
        )
    return repository.authority_home


def _encoded_authorship(authorship: Authorship) -> str:
    return encode_authorship(authorship)


def require_run_signal_agreement(request: DetectionRunRequest) -> KnowledgeRefusal | None:
    """Refuse a run whose recorded versions, members or order disagree with its signals.

    Requirement 3.5's two-place version publication is enforced here rather than documented: the run
    carries the versions and so does every signal, and the two must be the same values. Requirement
    2.4 is enforced the same way -- the run's declared set is the set its signals declared -- and
    requirement 3.3's order is checked against the signals actually presented, so a run cannot
    declare an order over signals it did not record.
    """

    operation: KnowledgeOperation = "record_detection_run"
    expected_order = tuple(signal.signal_id for signal in request.signals)
    if request.run.repository_id != request.repository_id:
        return _agreement_refusal(
            operation,
            "the run's repository is not the namespace it is being written into",
            request.run.run_id,
            expected=request.repository_id,
            observed=request.run.repository_id,
        )
    if request.run.signal_order != expected_order:
        return _agreement_refusal(
            operation,
            "the run's declared deterministic total order over signal identity names exactly the "
            "signals it recorded, in the order they were produced",
            request.run.run_id,
            expected=" | ".join(expected_order) or "<none>",
            observed=" | ".join(request.run.signal_order) or "<none>",
        )
    for signal in request.signals:
        denial = _signal_disagreement(request, signal)
        if denial is not None:
            return denial
    return None


def _signal_disagreement(
    request: DetectionRunRequest, signal: DetectionSignalPayload
) -> KnowledgeRefusal | None:
    operation: KnowledgeOperation = "record_detection_run"
    if signal.repository_id != request.repository_id:
        return _agreement_refusal(
            operation,
            "a signal's repository is not the namespace the run is written into",
            signal.signal_id,
            expected=request.repository_id,
            observed=signal.repository_id,
        )
    if signal.policy_version != request.run.policy_version:
        return _agreement_refusal(
            operation,
            "a signal must carry the policy version its run executed under: the version is "
            "published in two places, and a signal that disagreed with its run could be read as "
            "current under a policy its run did not run",
            signal.signal_id,
            expected=request.run.policy_version,
            observed=signal.policy_version,
        )
    if signal.extractor_version != request.run.extractor_version:
        return _agreement_refusal(
            operation,
            "a signal must carry the extractor version its run resolved under",
            signal.signal_id,
            expected=request.run.extractor_version,
            observed=signal.extractor_version,
        )
    if signal.input_set.declared not in request.run.declared_input_sets:
        return _agreement_refusal(
            operation,
            "the run's declared input sets are the members its own signals declared: a run-level "
            "default that a signal does not agree with is the collapse requirement 2.4 forbids",
            signal.signal_id,
            expected=" | ".join(request.run.declared_input_sets) or "<none>",
            observed=signal.input_set.declared,
        )
    if signal.condition not in request.run.recorded_conditions:
        return _agreement_refusal(
            operation,
            "the run's recorded conditions are the conditions its own signals matched",
            signal.signal_id,
            expected=" | ".join(request.run.recorded_conditions) or "<none>",
            observed=signal.condition,
        )
    return None


def _agreement_refusal(
    operation: KnowledgeOperation,
    detail: str,
    record_id: str,
    *,
    expected: str,
    observed: str,
) -> KnowledgeRefusal:
    return refusal(
        "invalid_reference",
        operation,
        detail,
        facts=RefusalFacts(
            table="knowledge_record",
            record_id=record_id,
            expected=expected,
            observed=observed,
        ),
        next_action=(
            "Correct the run or the signal so the two agree, and submit the run again. Nothing "
            "was written."
        ),
    )


def _refused(
    repository_id: str, refusal_value: KnowledgeRefusal, operation: KnowledgeOperation
) -> DetectionRunResult:
    return DetectionRunResult(
        state="refused", operation=operation, repository_id=repository_id, refusal=refusal_value
    )


def read_detection_run(store: OpenedKnowledgeStore, run_id: str) -> DetectionRunResult:
    """Read one recorded run and its signals, in the run's own recorded order.

    The order comes from ``detection_run_signal`` rather than from whatever order rows return in,
    which is what makes the read answer the same sequence a reproduction compares. The stored
    revision's own content digest is recomputed and checked, so a payload altered behind its identity
    is reported as a damaged store instead of being served as a recorded run.
    """

    operation: KnowledgeOperation = "read_detection_run"
    denied = require_detection_generation(store, operation)
    if denied is not None:
        return _refused(store.repository_id, denied, operation)
    rows = tuple(
        store.connection.execute(_RUN_ROW, (store.repository_id, run_id, DETECTION_RUN_KIND))
    )
    if not rows:
        return _refused(
            store.repository_id,
            refusal(
                "missing_expected_row",
                operation,
                "no detection run is recorded under this identity in this namespace",
                facts=RefusalFacts(table="knowledge_record", record_id=run_id, expected=run_id),
                next_action="Record the run first, or read the identity that was recorded.",
            ),
            operation,
        )
    run = _decode_payload(store, run_id, DETECTION_RUN_SCHEMA, DetectionRunPayload)
    order = tuple(
        str(row[1])
        for row in store.connection.execute(_SEQUENCE_ROWS, (store.repository_id, run_id))
    )
    signals = tuple(
        _decode_payload(store, signal_id, DETECTION_SIGNAL_SCHEMA, DetectionSignalPayload)
        for signal_id in order
    )
    return DetectionRunResult(
        state="read",
        operation=operation,
        repository_id=store.repository_id,
        run=run,
        signals=signals,
    )


def _decode_payload(
    store: OpenedKnowledgeStore,
    record_id: str,
    record_schema: str,
    model: type[Any],
) -> Any:
    """Decode one detection record's stored revision, verifying that its own seal still holds.

    The stored ``content_digest`` is recomputed from the payload that was read, so a payload altered
    behind its identity is reported as a damaged store rather than served as a recorded run. That is
    the envelope's digest on ``record_revision`` -- not a digest of the signal's own, which
    requirement 3.7 refuses -- and it is what makes the read answer what was recorded.
    """

    rows = tuple(store.connection.execute(_SIGNAL_REVISION, (store.repository_id, record_id)))
    if not rows:
        raise KnowledgeStorageError(f"detection record {record_id} holds no stored revision")
    revision_id = str(rows[0][0])
    stored_schema, payload_text = str(rows[0][1]), str(rows[0][2])
    if stored_schema != record_schema:
        raise KnowledgeStorageError(
            f"detection record {record_id} stores record_schema {stored_schema!r} rather than "
            f"{record_schema!r}; the row was altered outside the operation"
        )
    payload = decoded_json(payload_text)
    if not isinstance(payload, Mapping):  # pragma: no cover - written only from a mapping
        raise KnowledgeStorageError(f"detection record {record_id} holds a non-object payload")
    _require_intact_revision(store, record_id, revision_id, record_schema, payload)
    kind = DETECTION_SIGNAL_KIND if record_schema == DETECTION_SIGNAL_SCHEMA else DETECTION_RUN_KIND
    expected = PAYLOAD_MODELS[(kind, record_schema)]
    if expected is not model:  # pragma: no cover - the registry is the only source of these
        raise KnowledgeStorageError(
            f"detection record {record_id} resolved to another payload model"
        )
    return model.model_validate(dict(payload))


def _require_intact_revision(
    store: OpenedKnowledgeStore,
    record_id: str,
    revision_id: str,
    record_schema: str,
    payload: Mapping[str, Any],
) -> None:
    """Fail loudly when one stored detection revision no longer matches its recorded seal."""

    rows = tuple(
        store.connection.execute(
            "SELECT content_digest FROM record_revision "
            "WHERE repository_id = ? AND revision_id = ?",
            (store.repository_id, revision_id),
        )
    )
    if not rows:  # pragma: no cover - the row was read a moment ago
        raise KnowledgeStorageError(f"detection revision {revision_id} disappeared during the read")
    stored = str(rows[0][0])
    recomputed = record_revision_digest(
        RecordRevisionDraft(
            record_id=record_id,
            revision_id=revision_id,
            record_schema=record_schema,
            predecessor_revision_id=None,
        ),
        payload,
    )
    if recomputed != stored:
        raise KnowledgeStorageError(
            f"detection revision {revision_id} does not match its content digest: stored {stored}, "
            f"recomputed {recomputed}. The row was altered behind its identity; treat the store as "
            "damaged and recover the revision from an intact snapshot."
        )


# ---------------------------------------------------------------------------
# Reproducibility, currentness and the manifest's own retention answer.


def compare_detection_runs(
    recorded: DetectionRunPayload, reexecuted: DetectionRunPayload
) -> DetectionRunReproduction:
    """Compare a recorded run with its re-execution, and name every input or version that differs.

    Requirement 3.3 makes reproducibility a comparison of two ordered sequences, and requirement 3.4
    makes any difference a fact about *which* input or version moved. Nothing here writes: a
    re-execution that differs is reported as two distinct runs, never as "the same run", and the
    recorded run is not touched.
    """

    differences = _run_differences(recorded, reexecuted)
    ordered_equal = recorded.signal_order == reexecuted.signal_order
    return DetectionRunReproduction(
        recorded_run_id=recorded.run_id,
        reexecuted_run_id=reexecuted.run_id,
        policy_version=recorded.policy_version,
        reproduced=ordered_equal and not differences,
        ordered_signals_equal=ordered_equal,
        recorded_signal_order=recorded.signal_order,
        reexecuted_signal_order=reexecuted.signal_order,
        differences=differences,
    )


def _run_differences(
    recorded: DetectionRunPayload, reexecuted: DetectionRunPayload
) -> tuple[DetectionRunInputDifference, ...]:
    found: list[DetectionRunInputDifference] = []
    for axis in ("policy_version", "extractor_version", "condition_vocabulary_version"):
        left, right = getattr(recorded, axis), getattr(reexecuted, axis)
        if left != right:
            found.append(
                DetectionRunInputDifference(
                    kind=_version_kind(axis), recorded=left, reexecuted=right
                )
            )
    if recorded.declared_input_sets != reexecuted.declared_input_sets:
        found.append(
            DetectionRunInputDifference(
                kind="declared_input_set",
                recorded=" | ".join(recorded.declared_input_sets) or "<none>",
                reexecuted=" | ".join(reexecuted.declared_input_sets) or "<none>",
            )
        )
    if recorded.recorded_conditions != reexecuted.recorded_conditions:
        found.append(
            DetectionRunInputDifference(
                kind="condition",
                recorded=" | ".join(recorded.recorded_conditions) or "<none>",
                reexecuted=" | ".join(reexecuted.recorded_conditions) or "<none>",
            )
        )
    found.extend(_side_differences(recorded.input_sides, reexecuted.input_sides))
    return tuple(found)


def _version_kind(axis: str) -> str:
    return "policy_version" if axis == "policy_version" else "extractor_version"


def _side_differences(
    recorded: Sequence[DetectionInputSide], reexecuted: Sequence[DetectionInputSide]
) -> tuple[DetectionRunInputDifference, ...]:
    by_side = {side.side: side for side in reexecuted}
    found: list[DetectionRunInputDifference] = []
    for side in recorded:
        other = by_side.get(side.side)
        if other is None:
            found.append(
                DetectionRunInputDifference(
                    kind="snapshot_identity",
                    side=side.side,
                    recorded=side.context.knowledge.logical_digest,
                    reexecuted="<no such side>",
                )
            )
            continue
        if side.context.knowledge.logical_digest != other.context.knowledge.logical_digest:
            found.append(
                DetectionRunInputDifference(
                    kind="snapshot_identity",
                    side=side.side,
                    recorded=side.context.knowledge.logical_digest,
                    reexecuted=other.context.knowledge.logical_digest,
                )
            )
        if side.context.code_tree_id != other.context.code_tree_id:
            found.append(
                DetectionRunInputDifference(
                    kind="code_tree",
                    side=side.side,
                    recorded=side.context.code_tree_id or "<none>",
                    reexecuted=other.context.code_tree_id or "<none>",
                )
            )
        if side.selector_digest != other.selector_digest:
            found.append(
                DetectionRunInputDifference(
                    kind="selector",
                    side=side.side,
                    recorded=side.selector_digest,
                    reexecuted=other.selector_digest,
                )
            )
    return tuple(found)


def run_currentness(
    run: DetectionRunPayload,
    *,
    current_policy_version: str = DETECTION_POLICY_VERSION,
    current_extractor_version: str = DETECTION_EXTRACTOR_VERSION,
) -> DetectionRunCurrentness:
    """Mark one recorded run current or stale by comparing its versions with the ones in force.

    Requirement 3.6: code may mark a run stale on this comparison and may not reinterpret the run's
    signals for the new versions, re-label them current, or silently re-run and present the new
    result as the old. The answer carries the recorded and current versions beside the state, and it
    carries no signal at all -- its ``signals_unchanged`` field is the literal ``True``, which is the
    type saying that this operation cannot rewrite what it read.
    """

    differing = tuple(
        name
        for name, recorded, current in (
            ("policy_version", run.policy_version, current_policy_version),
            ("extractor_version", run.extractor_version, current_extractor_version),
        )
        if recorded != current
    )
    return DetectionRunCurrentness(
        run_id=run.run_id,
        recorded_policy_version=run.policy_version,
        current_policy_version=current_policy_version,
        recorded_extractor_version=run.extractor_version,
        current_extractor_version=current_extractor_version,
        binding_state="stale" if differing else "current",
        differing_versions=differing,
    )


def resolve_manifest_reference(
    manifest: DetectionScopeManifest, observation: ManifestDestinationObservation
) -> DetectionManifestResolution:
    """Report one signal's manifest reference as retained at a verified destination, or unresolved.

    Requirement 4.5: this leaf records the reference, its retention state and the durable
    destination's identity, and it refuses to report a manifest retained when the destination cannot
    be resolved. A manifest whose only home is enclosure-local, worktree-local or a regenerable
    worklist is reported as unresolved naming what would resolve it -- never as an empty manifest,
    and never silently dropped.
    """

    return manifest.resolve(observation)


def declared_input_set_members() -> tuple[str, ...]:
    """Return the closed declared-input-set vocabulary, for a caller that needs it as a value."""

    return DECLARED_INPUT_SETS


def detection_payload_digest(payload: Mapping[str, Any]) -> str:
    """Return the digest of one detection payload, for a caller comparing two recorded payloads."""

    return sha256_digest(dict(payload))
