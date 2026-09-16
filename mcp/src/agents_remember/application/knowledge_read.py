"""The application seam for the selective recorded-scope read (KS-R07).

This is the narrow API a caller uses: one explicit context, one seed, one bounded page. It is the
fourth application seam beside :mod:`knowledge`, :mod:`knowledge_snapshot`, :mod:`knowledge_merge`
and :mod:`knowledge_export`, and like them it decides no authority and holds no durable state. It
admits a context, delegates selection to :mod:`agents_remember.memory.knowledge.read`, and returns
the typed result unchanged.

Three boundaries this module owns, each because getting it wrong is a different kind of wrong:

1. **The read is read-only, and that is how a refusal persists nothing.** The connection is opened
   through ``open_read_only_database``, so the strongest statement available to this operation is a
   ``SELECT``. "A refused read left the file byte-identical" is therefore a property of the handle
   rather than a rollback this code has to remember, and the evidence proves it by measuring the
   per-table row counts and the logical digest across the refusal.
2. **A baseline read needs no task.** A context with ``task_ref=None`` is served: this operation
   never resolves a leaf contract, never asks an enclosure owner for one and never fabricates a
   task to satisfy a check. Planning has to be able to read recorded knowledge before a leaf
   exists, and the ordinary baseline read is exactly the case that must not become a prerequisite.
3. **A continuation is a binding, not a position.** The cursor is verified against the snapshot,
   the context, the selector, the policy and schema it names before any page is built from it, and
   against the selected set's manifest once that set exists; a cursor that binds something else --
   including one whose position lies past the end of the selection -- is refused with no partial
   page. A continuation carries the seed forward rather than replacing it: the caller re-sends the
   selector and the cursor's own ``seed_digest`` is what proves it is the same one, so a
   continuation cannot silently re-point a position at a different selection.

Every failure on the way out is a typed ``KnowledgeRefusal`` inside the result. Neither ``OSError``,
``apsw.Error``, nor ``ValueError`` escapes this boundary for an input this seam models: an unreadable
file, a database this build does not implement, a malformed cursor, a cursor position outside the
selection and an unusable selector are all named outcomes a caller branches on. The one class that
does not reach it is a caller that passes an object which is not one of the two typed models at all;
that is a programming error at the call site rather than a modeled read failure.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

import apsw

from agents_remember.memory.knowledge.connection import (
    inspect_schema,
    open_read_only_database,
)
from agents_remember.memory.knowledge.logical import (
    bound_repository,
    dataset_identity,
    logical_digest,
)
from agents_remember.memory.knowledge.read import (
    PageRequest,
    SelectedScope,
    SelectionIncomplete,
    SelectionQuery,
    page_of_scope,
    select_recorded_scope,
)
from agents_remember.memory.knowledge.read_anchors import anchor_resolver_for
from agents_remember.memory.knowledge.read_refusals import (
    continuation_binding_mismatch_refusal,
    page_budget_too_small_refusal,
    registration_absent_refusal,
    selection_incomplete_refusal,
    selector_absent_refusal,
    snapshot_unavailable_refusal,
)
from agents_remember.memory.knowledge.refusals import (
    KnowledgeStorageError,
    selected_input_unavailable_refusal,
)
from agents_remember.memory.knowledge.schema import CANONICAL_TABLES
from agents_remember.models.knowledge.context import KnowledgeSchemaIdentity
from agents_remember.models.knowledge.read import (
    KNOWLEDGE_READ_POLICY_VERSION,
    FamilyIdentitySeed,
    FamilyRevisionSeed,
    InvariantIdentitySeed,
    InvariantRevisionSeed,
    KnowledgeReadContext,
    KnowledgeReadCursor,
    KnowledgeReadPage,
    KnowledgeReadRequest,
    KnowledgeReadResult,
    KnowledgeReadSeed,
    PathSeed,
    continue_from_cursor,
    read_context_digest,
    seed_digest,
    snapshot_of_context,
)
from agents_remember.models.knowledge.result import KnowledgeRefusal

__all__ = ["open_read_context", "read_knowledge_scope", "read_row_counts"]

# The one statement shape this module runs that changes nothing: it counts a canonical table so a
# caller (and the evidence) can prove a refused read persisted nothing.
ROW_COUNT_TEMPLATE = "SELECT count(*) FROM {table}"


def open_read_context(
    database_path: Path,
    repository_id: str,
    *,
    repository_root: Path | None = None,
    code_tree_id: str | None = None,
    task_ref: str | None = None,
) -> KnowledgeReadContext:
    """Resolve one read context from the identity the dataset at ``database_path`` actually holds.

    This is the constructor a caller uses to *build* a read context: it opens the file read-only,
    reads the logical identity it holds, and returns a context naming that exact snapshot. A caller
    therefore cannot hand-write the snapshot a read will be verified against -- it can only resolve
    one, and the read compares that resolution again against the file it opens.

    ``task_ref`` is carried, not decided here, and defaults to ``None``: a baseline read during
    planning is addressed at recorded knowledge with no leaf, no enclosure and no fabricated task.
    Supplying a task reference is how a caller states that this read belongs to an admitted
    candidate; this function does not resolve one and never asks a contract owner for one.
    """

    identity = dataset_identity(Path(database_path))
    if identity.repository_id != repository_id:
        raise KnowledgeStorageError(
            f"the dataset at {database_path} is bound to {identity.repository_id}, not to the "
            f"requested repository namespace {repository_id}"
        )
    return KnowledgeReadContext(
        repository_id=repository_id,
        knowledge=identity,
        repository_root=None if repository_root is None else str(repository_root),
        code_tree_id=code_tree_id,
        task_ref=task_ref,
    )


def read_knowledge_scope(
    database_path: Path,
    context: KnowledgeReadContext,
    request: KnowledgeReadRequest,
) -> KnowledgeReadResult:
    """Read one bounded page of the recorded scope a seed names, at one declared snapshot.

    ``context`` is the whole admission this operation has: a namespace, an exact logical snapshot
    and an optional exact code tree. Everything is verified against the file that was actually
    opened, so a context describing another dataset is refused by name rather than answered from
    whatever bytes the path happens to hold.

    ``context`` and ``request`` are the typed models and not ``Any``, so this seam's own signature
    is what the boundary claim rests on: a caller supplies a resolved :class:`KnowledgeReadContext`
    and a :class:`KnowledgeReadRequest`, and every input class this operation answers is a field of
    one of those two. Reaching it with an arbitrary object is a programming error in the caller,
    not a modeled failure, and the docstring below says so rather than implying a generality the
    signature does not have.
    """

    path = Path(database_path)
    if not path.is_file():
        return _refused(
            context,
            selected_input_unavailable_refusal(
                "read_knowledge_scope",
                f"the selected knowledge database is absent or is not a file: {path}",
                record_id=str(path),
            ),
        )
    try:
        return _read_inside_snapshot(path, context, request)
    except SelectionIncomplete as error:
        return _refused(
            context, selection_incomplete_refusal(item_count=error.item_count, bound=error.bound)
        )
    except KnowledgeStorageError as error:
        # A schema this build does not implement, a database bound elsewhere, a missing canonical
        # table, a typed JSON column that does not decode: each means the file is not the snapshot
        # the caller selected, and none of them is an exception a caller should have to catch.
        return _unusable_snapshot(context, path, str(error))
    except apsw.Error as error:
        return _unusable_snapshot(
            context, path, f"the database could not be read through SQLite: {error}"
        )
    except OSError as error:
        return _refused(
            context,
            selected_input_unavailable_refusal(
                "read_knowledge_scope",
                f"the selected knowledge database could not be read: {error}",
                record_id=str(path),
            ),
        )


def _read_inside_snapshot(
    path: Path, context: KnowledgeReadContext, request: KnowledgeReadRequest
) -> KnowledgeReadResult:
    """Run one read inside one read-only connection, then close it."""

    cursor = None
    if request.continuation is not None:
        cursor = continue_from_cursor(request.continuation)
        if cursor is None:
            return _refused(
                context,
                continuation_binding_mismatch_refusal(
                    detail="the continuation is not a cursor of this format",
                    expected="knowledge-read-cursor/v1",
                    observed="<unreadable>",
                ),
            )
    # The cursor's request-level binding is decided before the file is opened, because a
    # continuation that binds another selection is a defect of the request and not a fact about the
    # bytes: reporting it as a snapshot problem would send the caller to re-select a dataset they
    # selected correctly. It is checked once, here; the scope-dependent half of the binding (the
    # manifest) is checked where the selection it names exists.
    mismatch = _cursor_mismatch(cursor, context, request.seed)
    if mismatch is not None:
        return _refused(context, mismatch)

    connection = open_read_only_database(path)
    try:
        return _select_and_page(connection, path, context, request, cursor)
    finally:
        connection.close()


def _select_and_page(
    connection: apsw.Connection,
    path: Path,
    context: KnowledgeReadContext,
    request: KnowledgeReadRequest,
    cursor: KnowledgeReadCursor | None,
) -> KnowledgeReadResult:
    """Verify the declared snapshot, select, page it, and assemble the typed result."""

    unusable = _snapshot_identity_refusal(connection, context, path, inspect_schema(connection))
    if unusable is not None:
        return _refused(context, unusable)

    scope = select_recorded_scope(
        connection,
        SelectionQuery(
            repository_id=context.repository_id,
            seed=request.seed,
            resolve_anchor=anchor_resolver_for(context),
        ),
    )
    absence = _absence_refusal(connection, context.repository_id, request.seed, scope)
    if absence is not None:
        return _refused(context, absence)

    position = 0 if cursor is None else cursor.position
    continuation_refusal = _continuation_refusal(cursor, position, scope)
    if continuation_refusal is not None:
        return _refused(context, continuation_refusal)

    page = page_of_scope(
        scope,
        PageRequest(
            context=context,
            seed=request.seed,
            max_items=request.budget.max_items,
            max_utf8_bytes=request.budget.max_utf8_bytes,
            position=position,
        ),
    )
    small_budget = _small_budget_refusal(page, scope, request, position)
    if small_budget is not None:
        return _refused(context, small_budget)
    return _page_result(context, request.seed, scope, page)


def _snapshot_identity_refusal(
    connection: apsw.Connection,
    context: KnowledgeReadContext,
    path: Path,
    identity: KnowledgeSchemaIdentity,
) -> KnowledgeRefusal | None:
    """Return the refusal the opened file earns against the declared snapshot, or ``None``.

    Three comparisons, each a different fact and each checked before anything is selected: the
    file must be bound to the requested namespace, it must implement the *schema generation* the
    context declares, and it must hold the declared logical dataset. The schema comparison is its
    own statement rather than a corollary of the digest: a context keeps the file's real digest
    while declaring another generation reaches it directly, and only it.
    """

    declared = context.knowledge
    repository = bound_repository(connection)
    if repository is None or repository.repository_id != context.repository_id:
        return snapshot_unavailable_refusal(
            detail="the selected database is not bound to the requested repository namespace",
            expected=context.repository_id,
            observed=("<no namespace row>" if repository is None else repository.repository_id),
        )
    if identity.schema_name != declared.schema_version:
        return snapshot_unavailable_refusal(
            detail="the selected database declares another schema generation",
            expected=declared.schema_version,
            observed=identity.schema_name,
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
        )
    return None


def _continuation_refusal(
    cursor: KnowledgeReadCursor | None, position: int, scope: SelectedScope
) -> KnowledgeRefusal | None:
    """Return the refusal a continuation earns against the selection it names, or ``None``.

    The two checks here are the halves of the cursor binding that need the selected set to exist:
    its position must be a position *in* that set, and its manifest must be that set's manifest.
    Both are decided before a page is built, so a cursor that describes another selection, or one
    whose position was hand-edited past the end, is refused rather than sliced or handed to a
    model constraint -- where a caller-edited position would have left the boundary as a raw
    exception instead of a named outcome.
    """

    if cursor is None:
        return None
    if position > len(scope.items):
        return continuation_binding_mismatch_refusal(
            detail="it names a position past the end of the selected set",
            expected=f"a position within {len(scope.items)} selected items",
            observed=str(position),
        )
    return _manifest_binding_mismatch(cursor, scope)


def _small_budget_refusal(
    page: KnowledgeReadPage,
    scope: SelectedScope,
    request: KnowledgeReadRequest,
    position: int,
) -> KnowledgeRefusal | None:
    """Return the refusal a page too small to hold its next item earns, or ``None``."""

    if page.minimum_utf8_bytes is None:
        return None
    return page_budget_too_small_refusal(
        minimum_utf8_bytes=page.minimum_utf8_bytes,
        requested_utf8_bytes=request.budget.max_utf8_bytes,
        item_id=scope.items[position].item_id,
    )


def _absence_refusal(
    connection: apsw.Connection,
    repository_id: str,
    seed: KnowledgeReadSeed,
    scope: SelectedScope,
) -> KnowledgeRefusal | None:
    """Return the typed absence a seed earns, or ``None`` when the seed selected something.

    The two absence codes are separated by what the caller got wrong. A path that is not recorded
    is a question the snapshot can answer in the negative, so it is ``registration_absent``; an
    identity or revision the snapshot does not hold is a selector that names nothing, so it is
    ``selector_absent``. Neither is a statement about what the path or obligation *means*, and
    neither reads a working tree or a Markdown document to fill the gap.
    """

    if scope.counts.primary_items_total > 0:
        return None
    if isinstance(seed, PathSeed):
        return registration_absent_refusal(path=str(seed.path))
    recorded = (
        _invariant_identity_is_recorded(connection, repository_id, seed)
        if isinstance(seed, (InvariantIdentitySeed, InvariantRevisionSeed))
        else _family_identity_is_recorded(connection, repository_id, seed)
    )
    if not recorded:
        return selector_absent_refusal(
            record_id=_selector_id(seed),
            kind=_selector_kind(seed),
        )
    # A recorded identity whose selected revisions carry no memberships and no claims is an empty
    # but real selection: the page is served with its zero counts so the caller sees the recorded
    # record rather than a claim that nothing exists.
    return None


def _invariant_identity_is_recorded(
    connection: apsw.Connection,
    repository_id: str,
    seed: InvariantIdentitySeed | InvariantRevisionSeed,
) -> bool:
    if isinstance(seed, InvariantRevisionSeed):
        return (
            _count(
                connection,
                "SELECT count(*) FROM invariant_revision WHERE repository_id = ? "
                "AND invariant_id = ? AND revision_id = ?",
                (repository_id, seed.invariant_id, seed.revision_id),
            )
            > 0
        )
    return (
        _count(
            connection,
            "SELECT count(*) FROM invariant WHERE repository_id = ? AND invariant_id = ?",
            (repository_id, seed.invariant_id),
        )
        > 0
    )


def _family_identity_is_recorded(
    connection: apsw.Connection,
    repository_id: str,
    seed: FamilyIdentitySeed | FamilyRevisionSeed,
) -> bool:
    if isinstance(seed, FamilyRevisionSeed):
        return (
            _count(
                connection,
                "SELECT count(*) FROM family_revision WHERE repository_id = ? "
                "AND family_id = ? AND revision_id = ?",
                (repository_id, seed.family_id, seed.revision_id),
            )
            > 0
        )
    return (
        _count(
            connection,
            "SELECT count(*) FROM family WHERE repository_id = ? AND family_id = ?",
            (repository_id, seed.family_id),
        )
        > 0
    )


def _count(connection: apsw.Connection, statement: str, parameters: tuple[Any, ...]) -> int:
    return int(next(iter(connection.execute(statement, parameters)))[0])


def _page_result(
    context: KnowledgeReadContext,
    seed: KnowledgeReadSeed,
    scope: SelectedScope,
    page: KnowledgeReadPage,
) -> KnowledgeReadResult:
    return KnowledgeReadResult(
        state="page",
        repository_id=context.repository_id,
        snapshot=snapshot_of_context(context),
        context_digest=read_context_digest(context),
        seed=seed,
        seed_digest=seed_digest(seed),
        manifest_digest=scope.manifest_digest,
        policy_version=KNOWLEDGE_READ_POLICY_VERSION,
        directly_containing_families=scope.directly_containing_families,
        revision_groups=scope.revision_groups,
        page=page,
        refusal=None,
    )


def _cursor_mismatch(
    cursor: KnowledgeReadCursor | None,
    context: KnowledgeReadContext,
    seed: KnowledgeReadSeed,
) -> KnowledgeRefusal | None:
    """Return the refusal a cursor earns, or ``None`` when it binds exactly this read."""

    if cursor is None:
        return None
    checks = (
        (
            cursor.logical_digest,
            context.knowledge.logical_digest,
            "it binds another logical snapshot",
        ),
        (cursor.context_digest, read_context_digest(context), "it binds another resolved context"),
        (cursor.seed_digest, seed_digest(seed), "it binds another selector"),
        (cursor.policy_version, KNOWLEDGE_READ_POLICY_VERSION, "it binds another selection policy"),
        (cursor.schema_version, context.knowledge.schema_version, "it binds another schema"),
    )
    for observed, expected, detail in checks:
        if observed != expected:
            return continuation_binding_mismatch_refusal(
                detail=detail, expected=str(expected), observed=str(observed)
            )
    return None


def _manifest_binding_mismatch(
    cursor: KnowledgeReadCursor, scope: SelectedScope
) -> KnowledgeRefusal | None:
    """Return the refusal a cursor whose manifest is not this selection's earns, or ``None``.

    The cursor's own ``manifest_digest`` names the *selected set* it is a position in, so it is
    checkable only once that set has been selected -- which is why this half of the binding runs
    here rather than beside the request-level checks. It is verified rather than dropped because
    the field is what makes "a position in one selection" a fact this read can confirm: it is a
    second, independent statement of the same selection the other five bindings describe from the
    request's side, and a cursor carrying another selection's manifest is refused instead of being
    continued into this one.
    """

    if cursor.manifest_digest == scope.manifest_digest:
        return None
    return continuation_binding_mismatch_refusal(
        detail="it binds another selected set",
        expected=scope.manifest_digest,
        observed=cursor.manifest_digest,
    )


# The word each seed kind is named by in a refusal. The fallback is the discriminator itself, so a
# seed kind added later is reported by its own name rather than as a neighbouring one.
_SELECTOR_KINDS: Mapping[str, str] = {
    "invariant": "invariant identity",
    "invariant_revision": "invariant revision",
    "family": "family identity",
    "family_revision": "family revision",
    "path": "path",
}


def _selector_kind(seed: KnowledgeReadSeed) -> str:
    kind: Literal["path", "invariant", "invariant_revision", "family", "family_revision"] = (
        seed.kind
    )
    return _SELECTOR_KINDS.get(kind, kind)


def _selector_id(seed: KnowledgeReadSeed) -> str:
    """Name the record one absent selector asked for, in the spelling its own seed carries.

    A revision seed is named by its identity and its exact revision, an identity seed by its identity
    alone, and a path seed by its path. The narrowing is written out rather than reached through
    ``getattr`` so the spelling a caller is shown is a field of the seed that actually carries it.
    """

    if isinstance(seed, InvariantRevisionSeed):
        return f"{seed.invariant_id}/{seed.revision_id}"
    if isinstance(seed, FamilyRevisionSeed):
        return f"{seed.family_id}/{seed.revision_id}"
    if isinstance(seed, InvariantIdentitySeed):
        return seed.invariant_id
    if isinstance(seed, FamilyIdentitySeed):
        return seed.family_id
    return seed.path


def _unusable_snapshot(
    context: KnowledgeReadContext,
    path: Path,
    detail: str,
    *,
    expected: str | None = None,
    observed: str | None = None,
) -> KnowledgeReadResult:
    """Refuse a file that is not the selected snapshot, naming both identities exactly."""

    return _refused(
        context,
        snapshot_unavailable_refusal(
            detail=f"{detail} (at {path})",
            expected=expected or context.knowledge.logical_digest,
            observed=observed or str(path),
        ),
    )


def _refused(context: KnowledgeReadContext, refusal_value: KnowledgeRefusal) -> KnowledgeReadResult:
    return KnowledgeReadResult(
        state="refused",
        repository_id=context.repository_id,
        snapshot=snapshot_of_context(context),
        context_digest=read_context_digest(context),
        page=None,
        refusal=refusal_value,
    )


def read_row_counts(database_path: Path) -> dict[str, int]:
    """Return one row count per canonical table for one database, reading it without writing.

    This is the measurement half of "a refused read persisted nothing": a caller takes it before
    and after a refusal and compares. It is a real read of the real file through the same
    read-only handle the operation uses, not an assertion about the operation's intent.
    """

    connection = open_read_only_database(Path(database_path))
    try:
        return {
            table: _count(connection, ROW_COUNT_TEMPLATE.format(table=table), ())
            for table in CANONICAL_TABLES
        }
    finally:
        connection.close()
