"""The application seam for the evidence-specific selection (``KS-R12@v1`` §8).

This is the narrow API a caller uses: one explicit context, one seed and one complete page. It is the
seventh application seam beside :mod:`knowledge`, :mod:`knowledge_snapshot`, :mod:`knowledge_merge`,
:mod:`knowledge_export`, :mod:`knowledge_read`, :mod:`knowledge_facets` and the detection surface,
and like them it decides no authority and holds no durable state. It admits a context, delegates
selection to :mod:`agents_remember.memory.knowledge.evidence_read`, and returns the typed result
unchanged.

Three boundaries this module owns, each because getting it wrong is a different kind of wrong:

1. **The read is read-only, and that is how a refusal persists nothing.** The connection is opened
   through ``open_read_only_database``, so the strongest statement available to this operation is a
   ``SELECT``. "A refused evidence read left the file byte-identical" is therefore a property of the
   handle rather than a rollback this code has to remember.
2. **The declared snapshot is verified before anything is selected.** The file must be bound to the
   requested namespace, must implement the schema generation the context declares, and must hold the
   declared logical dataset -- the same three comparisons the recorded-scope read and the facet read
   make, for the same reason: a context describing another dataset is refused by name rather than
   answered from whatever bytes the path happens to hold.
3. **The artifact resolution is a read-time fact and never a rewrite.** A caller may declare a local
   artifact root so a recorded repository-relative path can be resolved against real bytes; the
   resolution is reported as its own state and the stored record is served exactly as it was written,
   whatever the resolution says. Nothing here re-pins a digest, drops a reference, or converts a
   missing artifact into a statement about the record.

This operation does not touch ``KS-R07@v1``'s selection or ``KS-R11@v1``'s facet selection in any
way: it does not call either, shares no policy name with either, and appears in neither's response.
"""

from __future__ import annotations

from pathlib import Path

import apsw

from agents_remember.memory.knowledge import evidence_read
from agents_remember.memory.knowledge.connection import (
    inspect_schema,
    open_read_only_database,
)
from agents_remember.memory.knowledge.logical import bound_repository, logical_digest
from agents_remember.memory.knowledge.read_refusals import (
    selection_incomplete_refusal,
    selector_absent_refusal,
    snapshot_unavailable_refusal,
)
from agents_remember.memory.knowledge.refusals import (
    KnowledgeStorageError,
    selected_input_unavailable_refusal,
)
from agents_remember.models.knowledge.evidence_read import (
    EVIDENCE_SELECTION_POLICY_VERSION,
    EvidenceClaimSeed,
    EvidenceReadPage,
    EvidenceReadRequest,
    EvidenceReadResult,
    evidence_seed_digest,
)
from agents_remember.models.knowledge.read import (
    KnowledgeReadContext,
    read_context_digest,
    snapshot_of_context,
)
from agents_remember.models.knowledge.result import KnowledgeRefusal

__all__ = ["read_evidence_scope"]

_OPERATION = "read_evidence_scope"


def read_evidence_scope(
    database_path: Path,
    context: KnowledgeReadContext,
    request: EvidenceReadRequest,
) -> EvidenceReadResult:
    """Read one complete page of the evidence aggregate a seed names, at one declared snapshot.

    ``context`` is the whole admission this operation has, and it is the *same* resolved context
    model the recorded-scope read uses -- one definition of "which snapshot am I reading", not two.
    Everything is verified against the file that was actually opened.
    """

    path = Path(database_path)
    if not path.is_file():
        return _refused(
            context,
            request,
            selected_input_unavailable_refusal(
                _OPERATION,
                f"the selected knowledge database is absent or is not a file: {path}",
                record_id=str(path),
            ),
        )
    try:
        return _read_inside_snapshot(path, context, request)
    except evidence_read.EvidenceSelectionIncomplete as error:
        return _refused(
            context,
            request,
            selection_incomplete_refusal(
                item_count=error.item_count, bound=error.bound, operation=_OPERATION
            ),
        )
    except KnowledgeStorageError as error:
        # A schema this build does not implement, a database bound elsewhere, a missing canonical
        # table, a typed JSON column that does not decode, a revision whose stored seal no longer
        # holds: each means the file is not the snapshot the caller selected.
        return _unusable_snapshot(context, request, path, str(error))
    except apsw.Error as error:
        return _unusable_snapshot(
            context, request, path, f"the database could not be read through SQLite: {error}"
        )
    except OSError as error:
        return _refused(
            context,
            request,
            selected_input_unavailable_refusal(
                _OPERATION,
                f"the selected knowledge database could not be read: {error}",
                record_id=str(path),
            ),
        )


def _read_inside_snapshot(
    path: Path, context: KnowledgeReadContext, request: EvidenceReadRequest
) -> EvidenceReadResult:
    """Run one evidence selection inside one read-only connection, then close it."""

    connection = open_read_only_database(path)
    try:
        unusable = _snapshot_identity_refusal(connection, context, path)
        if unusable is not None:
            return _refused(context, request, unusable)
        selection = evidence_read.select_evidence_scope(
            connection,
            evidence_read.EvidenceSelectionQuery(
                repository_id=context.repository_id,
                seed=request.seed,
                artifact_root=request.artifact_root,
            ),
        )
        absence = _absence_refusal(request, selection)
        if absence is not None:
            return _refused(context, request, absence)
        return EvidenceReadResult(
            state="page",
            repository_id=context.repository_id,
            snapshot=snapshot_of_context(context),
            context_digest=read_context_digest(context),
            seed=request.seed,
            seed_digest=evidence_seed_digest(request.seed),
            manifest_digest=selection.manifest_digest,
            policy_version=EVIDENCE_SELECTION_POLICY_VERSION,
            page=EvidenceReadPage(items=selection.items, counts=selection.counts),
            refusal=None,
        )
    finally:
        connection.close()


def _absence_refusal(
    request: EvidenceReadRequest, selection: evidence_read.EvidenceSelection
) -> KnowledgeRefusal | None:
    """Return the typed absence a seed earns, or ``None`` when the seed selected something.

    A seed naming nothing recorded is ``selector_absent`` -- the caller asked for a record, or for the
    observations of a candidate, that the snapshot does not hold. A *recorded* claim whose assessment
    references are empty, or whose limitations field is empty, is not absence: the page is served with
    the state it really has, so the caller sees the record rather than a claim that nothing exists.
    """

    if selection.seed_recorded:
        return None
    if isinstance(request.seed, EvidenceClaimSeed):
        return selector_absent_refusal(
            record_id=request.seed.claim_id,
            kind="evidence claim",
            operation=_OPERATION,
        )
    seed = request.seed
    named = seed.knowledge_logical_digest or seed.code_candidate_tree_id or "<no candidate>"
    return selector_absent_refusal(
        record_id=named,
        kind="verification observation of the named tested candidate",
        operation=_OPERATION,
    )


def _snapshot_identity_refusal(
    connection: apsw.Connection, context: KnowledgeReadContext, path: Path
) -> KnowledgeRefusal | None:
    """Return the refusal the opened file earns against the declared snapshot, or ``None``."""

    declared = context.knowledge
    repository = bound_repository(connection)
    if repository is None or repository.repository_id != context.repository_id:
        return snapshot_unavailable_refusal(
            detail="the selected database is not bound to the requested repository namespace",
            expected=context.repository_id,
            observed=("<no namespace row>" if repository is None else repository.repository_id),
            operation=_OPERATION,
        )
    identity = inspect_schema(connection)
    if identity.schema_name != declared.schema_version:
        return snapshot_unavailable_refusal(
            detail="the selected database declares another schema generation",
            expected=declared.schema_version,
            observed=identity.schema_name,
            operation=_OPERATION,
        )
    observed_digest = logical_digest(connection, identity.schema_name)
    if observed_digest != declared.logical_digest:
        return snapshot_unavailable_refusal(
            detail=(
                "the database holds another logical dataset than the one this read selected "
                f"(at {path})"
            ),
            expected=declared.logical_digest,
            observed=observed_digest,
            operation=_OPERATION,
        )
    return None


def _unusable_snapshot(
    context: KnowledgeReadContext, request: EvidenceReadRequest, path: Path, detail: str
) -> EvidenceReadResult:
    """Refuse a file that is not the selected snapshot, naming both identities exactly."""

    return _refused(
        context,
        request,
        snapshot_unavailable_refusal(
            detail=f"{detail} (at {path})",
            expected=context.knowledge.logical_digest,
            observed=str(path),
            operation=_OPERATION,
        ),
    )


def _refused(
    context: KnowledgeReadContext, request: EvidenceReadRequest, refusal_value: KnowledgeRefusal
) -> EvidenceReadResult:
    return EvidenceReadResult(
        state="refused",
        repository_id=context.repository_id,
        snapshot=snapshot_of_context(context),
        context_digest=read_context_digest(context),
        seed=request.seed,
        seed_digest=evidence_seed_digest(request.seed),
        page=None,
        refusal=refusal_value,
    )
