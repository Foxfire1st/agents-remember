"""The application seam for family composition: one traversal, one projection, no writes.

This is the fifth read-only application seam beside :mod:`knowledge_read`,
:mod:`knowledge_snapshot`, :mod:`knowledge_merge` and :mod:`knowledge_export`. It admits a dataset
path and a repository namespace, opens the database **read-only**, and delegates to the two memory
modules that own the acts:

* :func:`follow_composition_scope` -- the ``Doc13:287`` traversal, under one declared policy version,
  bounded by that version's declared depth bound, inside the §8 registered-review-scope axis;
* :func:`family_view` -- the Family projection (``Doc13:237``), which reports the recorded links,
  the canonical owning route or the explicit ungoverned state, and the authored explanatory context.

Three boundaries this module owns, and each is the reason it exists rather than a caller importing
the memory modules directly:

1. **Read-only, so a refusal persists nothing.** The connection comes from
   ``open_read_only_database``, so the strongest statement available is a ``SELECT``. That is the
   same property :mod:`knowledge_read` states for the retrieval read, and it is what makes "a refused
   traversal changed nothing" a fact about the handle rather than a rollback this code remembers.
2. **The traversal is not the retrieval read.** This module does not touch
   :func:`…knowledge_read.read_knowledge_scope`, its selection policy or its advertised frontier. A
   caller that wants the recorded-scope selection asks for that operation; a caller that wants
   declared composition edges followed asks for this one. Conflating the two axes is the defect
   ``KS-R17@v1`` §7.3 names.
3. **Every modelled failure is a typed refusal, not an exception.** An unknown policy identity or
   version, a not-permitted edge, a step past the declared bound and a family revision this
   namespace does not hold are all returned as ``KnowledgeRefusal`` values inside the result.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from agents_remember.memory.knowledge.composition_traversal import (
    CompositionScope,
    follow_composition_scope,
)
from agents_remember.memory.knowledge.connection import (
    inspect_schema,
    open_read_only_database,
)
from agents_remember.memory.knowledge.family_view import FamilyRevisionView, family_revision_view
from agents_remember.memory.knowledge.refusals import KnowledgeRefused, KnowledgeStorageError
from agents_remember.memory.knowledge.store import OpenedKnowledgeStore
from agents_remember.models.knowledge.result import KnowledgeRefusal

__all__ = [
    "CompositionScope",
    "CompositionTraversalResult",
    "FamilyRevisionView",
    "FamilyViewResult",
    "family_view",
    "follow_family_composition",
]


@dataclass(frozen=True)
class CompositionTraversalResult:
    """The typed outcome of one declared-policy traversal: a bounded scope, or one refusal.

    ``scope`` carries the policy identity and version the traversal executed under, so a scope
    constructed under one version is never readable as one constructed under another.
    """

    state: Literal["scope", "refused"]
    seed_family_revision_id: str
    scope: CompositionScope | None = None
    refusal: KnowledgeRefusal | None = None


@dataclass(frozen=True)
class FamilyViewResult:
    """The typed outcome of one Family projection: the reported view, or one refusal."""

    state: Literal["reported", "refused"]
    family_revision_id: str
    view: FamilyRevisionView | None = None
    refusal: KnowledgeRefusal | None = None


def follow_family_composition(
    database_path: Path,
    repository_id: str,
    seed_family_revision_id: str,
    policy_id: str,
    policy_version_id: str,
) -> CompositionTraversalResult:
    """Follow declared composition edges from one family revision under one declared policy version.

    The database is opened read-only and closed before this returns, so nothing this call does can
    reach the file. A traversal that cannot be reported as a *complete* scope -- an unknown policy,
    a not-permitted edge, a step past the declared bound -- returns the refusal rather than a
    truncated scope, because a truncated traversal reported as a scope would be a false statement
    about what was reached.
    """

    with open_read_only_store(database_path, repository_id) as store:
        try:
            scope = follow_composition_scope(
                store, seed_family_revision_id, policy_id, policy_version_id
            )
        except KnowledgeRefused as refused:
            return CompositionTraversalResult(
                state="refused",
                seed_family_revision_id=seed_family_revision_id,
                refusal=_as_refusal(refused),
            )
        return CompositionTraversalResult(
            state="scope", seed_family_revision_id=seed_family_revision_id, scope=scope
        )


def family_view(
    database_path: Path, repository_id: str, family_revision_id: str
) -> FamilyViewResult:
    """Report one family revision's recorded links, owning route and explanatory context.

    The projection reports and never traverses: it fills no missing link, route or context with an
    inferred value, and it presents no derived summary of "related families" as a recorded
    relationship. A revision this namespace does not hold is refused rather than reported empty.
    """

    with open_read_only_store(database_path, repository_id) as store:
        reported = family_revision_view(store, family_revision_id)
    if isinstance(reported, FamilyRevisionView):
        return FamilyViewResult(
            state="reported", family_revision_id=family_revision_id, view=reported
        )
    return FamilyViewResult(
        state="refused", family_revision_id=family_revision_id, refusal=reported
    )


def open_read_only_store(database_path: Path, repository_id: str) -> OpenedKnowledgeStore:
    """Open one dataset through a connection that cannot write it, as a store the readers accept.

    The connection is the shipped :func:`…connection.open_read_only_database` handle -- the same one
    the retrieval read uses, whose strongest available statement is a ``SELECT`` -- and the schema
    is validated against the generation the *file* declares, exactly as every other open path does.
    A caller of this seam therefore cannot reach a writable handle by accident, which is what makes
    "a refused traversal changed nothing" a property of the handle rather than of this code's care.

    The returned store is closed by the caller; every function in this module uses it inside a
    ``with`` block, so no read leaves a connection open.
    """

    path = Path(database_path)
    if not path.exists():
        raise KnowledgeStorageError(f"knowledge database does not exist: {path}")
    connection = open_read_only_database(path)
    return OpenedKnowledgeStore(
        database_path=path,
        repository_id=repository_id,
        schema=inspect_schema(connection),
        connection=connection,
        resource_lock_path=path,
    )


def _as_refusal(error: Exception) -> KnowledgeRefusal:
    """Return the typed refusal a traversal refusal carries, and let anything else propagate.

    A modelled failure is a :class:`KnowledgeRefused` carrying a typed refusal; anything else is a
    defect rather than an outcome, and converting it would report a bug as an expected state.
    """

    if isinstance(error, KnowledgeRefused):
        return error.refusal
    raise error
