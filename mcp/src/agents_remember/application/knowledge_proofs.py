"""Test proofs as first-class knowledge: what the views and the curator checklist show (MIK-R28).

A test that proves an invariant is a ``proves`` entry in the test file's sidecar (MIK-R21 rule 5),
written by the curator writer from the hand-off (MIK-R12, rule 2 here). This module reads them back
for two consumers, both from the derived index (MIK-R23) of one converted memory tree:

* **Views (rule 4).** :func:`view_proofs` gives ``knowledge_read``'s ``invariant`` and ``family``
  responses the proof entries of the invariant, or of every member of the family: entry ID,
  invariant, test path, anchor and the curator's facet. A proof says what the test demonstrates;
  nothing here states whether the test passes, and a proof is never presented as the invariant
  being satisfied (Doc13). Test execution results are not stored.
* **Without proof (rule 5).** :func:`invariants_without_proof` lists the live invariants no proof
  entry names, for the curator checklist. It is information, never a gate: the admission rule
  (MIK-R27) accepts other criteria. Beside each one it names the tests the invariant's recorded
  ``origin.handoff.evidence`` mentions -- the migrated "Evidence: ..." text (MIK-R24) that a curator
  pass turns into proofs through the writer, authoring each facet (rule 6). Naming is textual only
  (:func:`...knowledge_writer.handoff.tests_named_in`); whether a test resolves is the writer's to
  establish at C.

Both are answered only for a converted tree (one that holds the layout marker). An unconverted tree
has no proof entries to read, and every consumer keeps its output unchanged for it.
"""

from __future__ import annotations

import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import apsw

from agents_remember.application.knowledge_writer.handoff import tests_named_in
from agents_remember.memory.knowledge_index import (
    Answer,
    Entry,
    IndexMismatchError,
    KnowledgeIndex,
    KnowledgeIndexCache,
    MemoryTreeError,
    Record,
    build_index,
    default_cache_directory,
    directory_snapshot,
)
from agents_remember.models.knowledge_files.documents import LAYOUT_MARKER_PATH

__all__ = [
    "ProofCoverage",
    "UnprovenInvariant",
    "invariants_without_proof",
    "tree_view_proofs",
    "view_proofs",
]

_PROOF_VIEWS = frozenset({"invariant", "family"})


class _ViewSubject(Protocol):
    @property
    def view(self) -> str: ...
    @property
    def invariant_revision_id(self) -> str | None: ...
    @property
    def family_revision_id(self) -> str | None: ...


def tree_view_proofs(
    index_path: Path, tree_key: str, request: _ViewSubject
) -> list[dict[str, Any]] | None:
    """The ``proofs`` of one tool read from a memory tree's index at ``index_path``, or ``None``.

    The index is opened only for the ``invariant`` and ``family`` views, the two that carry proofs.
    Opening it for a key it was not built for raises :class:`IndexMismatchError`, which the tool
    turns into its ordinary refusal.
    """

    if request.view not in _PROOF_VIEWS:
        return None
    with KnowledgeIndex(index_path, expected_key=tree_key) as index:
        return view_proofs(
            index,
            request.view,
            invariant_revision_id=request.invariant_revision_id,
            family_revision_id=request.family_revision_id,
        )


def view_proofs(
    index: KnowledgeIndex,
    view: str,
    *,
    invariant_revision_id: str | None,
    family_revision_id: str | None,
) -> list[dict[str, Any]] | None:
    """The proof entries an ``invariant`` or ``family`` view of ``index`` shows, or ``None``.

    The view names its subject by the projected revision UUID (MIK-R23 rule 6); the index maps it
    back to ``<ID>@<revision>``. A family's proofs are those of its members. Any other view, or a
    subject the index does not hold, has no proof section (``None``), which is distinct from a
    subject with no proof (``[]``).
    """

    if view not in _PROOF_VIEWS:
        return None
    subject = invariant_revision_id if view == "invariant" else family_revision_id
    text_id = None if subject is None else index.text_id(subject)
    if text_id is None:
        return None
    record_id = text_id.split("@", 1)[0]
    if view == "invariant":
        invariants: tuple[str, ...] = (record_id,)
    else:
        invariants = index.family(record_id).value.members
    return [_proof_document(entry) for entry in index.proofs_of(invariants).value]


def _proof_document(entry: Entry) -> dict[str, Any]:
    return {
        "id": entry.id,
        "invariant": entry.invariant,
        "path": entry.path,
        "anchor": entry.document.get("anchor"),
        "facet": entry.document.get("facet"),
        "sidecar": entry.sidecar,
    }


@dataclass(frozen=True)
class UnprovenInvariant:
    """One live invariant no proof entry names, and the tests its recorded evidence mentions."""

    id: str
    status: str
    path: str
    evidence_tests: tuple[str, ...]

    def as_row(self) -> dict[str, Any]:
        return {
            "invariant": self.id,
            "status": self.status,
            "path": self.path,
            "evidenceTests": list(self.evidence_tests),
        }


@dataclass(frozen=True)
class ProofCoverage:
    """The "without proof" list of one converted memory tree, or why it could not be computed."""

    unproven: tuple[UnprovenInvariant, ...]
    index_state: str
    problem: str | None = None


def invariants_without_proof(
    memory_root: Path, *, coordination_root: Path | None = None
) -> ProofCoverage | None:
    """List the live invariants of ``memory_root``'s working tree that no proof entry names.

    ``None`` for an unconverted tree. With ``coordination_root`` the tree's index comes from the
    coordination index cache (MIK-R23 rule 4), reused when a file for the tree's key exists;
    without one it is built into a temporary file outside every working tree and discarded. The
    memory tree is only read (its key capture writes nothing). A tree, index or cache that cannot be
    read yields a coverage with its ``problem`` and no rows, never an exception, because the list is
    informational and must not break the run that shows it.
    """

    if not (memory_root / LAYOUT_MARKER_PATH).is_file():
        return None
    try:
        answer = _unproven(memory_root, coordination_root)
    except (MemoryTreeError, IndexMismatchError, apsw.Error, OSError) as error:
        return ProofCoverage(unproven=(), index_state="unreadable", problem=str(error))
    return ProofCoverage(
        unproven=tuple(
            UnprovenInvariant(
                id=record.id,
                status=record.status,
                path=record.path,
                evidence_tests=_evidence_tests(record.document),
            )
            for record in answer.value
        ),
        index_state=answer.index.state,
        problem=None
        if answer.index.complete
        else "the index is partial: " + "; ".join(path for path, _ in answer.index.problems),
    )


def _unproven(memory_root: Path, coordination_root: Path | None) -> Answer[tuple[Record, ...]]:
    if coordination_root is not None:
        cache = KnowledgeIndexCache(default_cache_directory(coordination_root))
        with cache.for_directory(memory_root) as index:
            return index.invariants_without_proof()
    snapshot = directory_snapshot(memory_root)
    with tempfile.TemporaryDirectory(prefix="ar-proof-coverage-") as scratch:
        destination = Path(scratch) / "index.sqlite"
        build_index(snapshot, destination)
        with KnowledgeIndex(destination, expected_key=snapshot.key) as index:
            return index.invariants_without_proof()


def _evidence_tests(document: Mapping[str, Any]) -> tuple[str, ...]:
    origin = document.get("origin")
    handoff = origin.get("handoff") if isinstance(origin, Mapping) else None
    evidence = handoff.get("evidence") if isinstance(handoff, Mapping) else None
    if not isinstance(evidence, Sequence) or isinstance(evidence, str):
        return ()
    texts = [text for text in evidence if isinstance(text, str)]
    return tuple(test.spelling for test in tests_named_in(texts))
