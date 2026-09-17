"""The application seam for the facet-specific selection (``KS-R11@v1`` §9.3).

This is the narrow API a caller uses: one explicit context, one seed, one complete page. It is the
sixth application seam beside :mod:`knowledge`, :mod:`knowledge_snapshot`, :mod:`knowledge_merge`,
:mod:`knowledge_export` and :mod:`knowledge_read`, and like them it decides no authority and holds
no durable state. It admits a context, delegates selection to
:mod:`agents_remember.memory.knowledge.facet_read`, and returns the typed result unchanged.

Three boundaries this module owns, each because getting it wrong is a different kind of wrong:

1. **The read is read-only, and that is how a refusal persists nothing.** The connection is opened
   through ``open_read_only_database``, so the strongest statement available to this operation is a
   ``SELECT``. "A refused facet read left the file byte-identical" is therefore a property of the
   handle rather than a rollback this code has to remember.
2. **The declared snapshot is verified before anything is selected.** The file must be bound to the
   requested namespace, must implement the schema generation the context declares, and must hold the
   declared logical dataset -- the same three comparisons the recorded-scope read makes, for the
   same reason: a context describing another dataset is refused by name rather than answered from
   whatever bytes the path happens to hold.
3. **The selection is complete or it refuses.** A selection that reaches its declared bound is
   ``selection_incomplete``; there is no cursor, so a caller never receives a page it could read as
   the whole aggregate when it is not.

This operation does not touch ``KS-R07@v1``'s selection in any way: it does not call it, does not
share a policy name with it, and does not appear in its response. That is what makes the shipped
seed pages byte-identical across this leaf.
"""

from __future__ import annotations

from pathlib import Path

import apsw

from agents_remember.memory.knowledge import facet_read
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
from agents_remember.models.knowledge.facet import subject_identity
from agents_remember.models.knowledge.facet_read import (
    FACET_SELECTION_POLICY_VERSION,
    FacetReadPage,
    FacetReadRequest,
    FacetReadResult,
    FacetRecordSeed,
    facet_seed_digest,
)
from agents_remember.models.knowledge.read import (
    KnowledgeReadContext,
    read_context_digest,
    snapshot_of_context,
)
from agents_remember.models.knowledge.result import KnowledgeRefusal

__all__ = ["read_facet_scope"]

_OPERATION = "read_facet_scope"


def read_facet_scope(
    database_path: Path,
    context: KnowledgeReadContext,
    request: FacetReadRequest,
) -> FacetReadResult:
    """Read one complete page of the facet aggregate a seed names, at one declared snapshot.

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
    except facet_read.FacetSelectionIncomplete as error:
        return _refused(
            context,
            request,
            selection_incomplete_refusal(
                item_count=error.item_count, bound=error.bound, operation=_OPERATION
            ),
        )
    except KnowledgeStorageError as error:
        # A schema this build does not implement, a database bound elsewhere, a missing canonical
        # table, a typed JSON column that does not decode: each means the file is not the snapshot
        # the caller selected.
        return _unusable_snapshot(context, request, path, str(error))
    except apsw.Error as error:
        detail = f"the database could not be read through SQLite: {error}"
        return _unusable_snapshot(context, request, path, detail)
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
    path: Path, context: KnowledgeReadContext, request: FacetReadRequest
) -> FacetReadResult:
    """Run one facet selection inside one read-only connection, then close it."""

    connection = open_read_only_database(path)
    try:
        unusable = _snapshot_identity_refusal(connection, context, path)
        if unusable is not None:
            return _refused(context, request, unusable)
        selection = facet_read.select_facet_scope(
            connection,
            facet_read.FacetSelectionQuery(repository_id=context.repository_id, seed=request.seed),
        )
        absence = _absence_refusal(request, selection)
        if absence is not None:
            return _refused(context, request, absence)
        return FacetReadResult(
            state="page",
            repository_id=context.repository_id,
            snapshot=snapshot_of_context(context),
            context_digest=read_context_digest(context),
            seed=request.seed,
            seed_digest=facet_seed_digest(request.seed),
            manifest_digest=selection.manifest_digest,
            policy_version=FACET_SELECTION_POLICY_VERSION,
            page=_page(selection),
            refusal=None,
        )
    finally:
        connection.close()


def _page(selection: facet_read.FacetSelection) -> FacetReadPage:
    """Return the page one selected aggregate serves, in the selection's declared order."""

    return FacetReadPage(items=selection.items, counts=selection.counts)


def _absence_refusal(
    request: FacetReadRequest, selection: facet_read.FacetSelection
) -> KnowledgeRefusal | None:
    """Return the typed absence a seed earns, or ``None`` when the seed selected something.

    A seed naming nothing recorded is ``selector_absent`` -- the caller asked for a record the
    snapshot does not hold. A *recorded* facet record with no attachments and no supersession edges
    is not absence: the page is served with the counts it really has, so the caller sees the record
    rather than a claim that nothing exists.
    """

    if selection.seed_recorded:
        return None
    if isinstance(request.seed, FacetRecordSeed):
        return selector_absent_refusal(
            record_id=request.seed.record_id,
            kind="facet record",
            operation=_OPERATION,
        )
    subject = request.seed.subject
    identity, revision_id = subject_identity(subject)
    return selector_absent_refusal(
        record_id=f"{identity}/{revision_id}",
        kind=f"{subject.kind} statement revision",
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
    context: KnowledgeReadContext, request: FacetReadRequest, path: Path, detail: str
) -> FacetReadResult:
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
    context: KnowledgeReadContext, request: FacetReadRequest, refusal_value: KnowledgeRefusal
) -> FacetReadResult:
    return FacetReadResult(
        state="refused",
        repository_id=context.repository_id,
        snapshot=snapshot_of_context(context),
        context_digest=read_context_digest(context),
        seed=request.seed,
        seed_digest=facet_seed_digest(request.seed),
        page=None,
        refusal=refusal_value,
    )
