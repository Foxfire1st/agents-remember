"""Relationship investigation at a bound memory side (MIK-R102 rules 3, 5, 12).

The walk follows the owners that actually hold each relationship rather than guessing from a generic
key list: a family owns its ``members``; a decision or an invariant owns its ``links`` and
``supersedes``; a file sidecar owns its ``realizes`` and ``proves``; a history file owns its rows. An
edge another owner records is reported ``recorded-by``, so it is never read as a field the inspected
file carries. Each owned edge also **resolves its target at the same bound tree**: the target's own
revision and origin leaf travel with the edge, and a filename is never trusted as the record it names.

The read answers in full (MIK-R211 v4 rules 1-3): the tree listing names every path under
``knowledge`` and ``onboarding``, every owned, nested and reverse edge is returned, and no edge
budget, name or byte cap, depth bound or cursor cuts the roster. Lookups go through one id→path
index built from that listing. Records are read through :func:`read_bound_source`, so the same
exact-tree operand and measured counters apply here as anywhere else.
"""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from agents_remember.application.review_evidence_packet import comparison_reference
from agents_remember.application.review_evidence_read import (
    SideOperand,
    read_bound_source,
    side_operand,
)
from agents_remember.kernel.git_command import git_environment
from agents_remember.models.knowledge.review_evidence_read import (
    EvidenceReadReceipt,
    EvidenceRelationshipRead,
    RelationshipEdge,
)
from agents_remember.models.knowledge.review_trees import ReviewTreeComparisonRecord
from agents_remember.models.knowledge_files.documents import parse_document_text
from agents_remember.models.knowledge_files.records import (
    DecisionRecord,
    FamilyRecord,
    InvariantRecord,
)
from agents_remember.models.knowledge_files.sidecars import FileSidecar

__all__ = ["read_bound_relationships"]

# How much of the listing stream one pipe read takes: its granularity, not a bound.
_CHUNK = 65536
_RECORD_FILE = re.compile(r"^(?P<id>[A-Z]{2,4}-[A-Z0-9]{4,})-[^/]*\.json$")
_RECORD_ID = re.compile(r"^[A-Z]{2,4}-[A-Z0-9]{4,}$")


def read_bound_relationships(
    record: ReviewTreeComparisonRecord,
    side: str,
    path: str,
    *,
    depth: int = 1,
) -> EvidenceRelationshipRead:
    """Follow every recorded relationship of one memory file at one bound side (rules 3, 5)."""

    if side not in {"memory-base", "memory-candidate"}:
        return _refusal(
            record,
            side,
            "code-side-has-no-knowledge",
            (
                f"the {side!r} side holds code, not knowledge: records, sidecars and history rows "
                "are read from the comparison's named memory side"
            ),
        )
    if depth < 0:
        return _refusal(
            record,
            side,
            "range-invalid",
            f"the requested relationship depth {depth} is negative; nothing was read",
        )
    operand = side_operand(record, side)
    if isinstance(operand, EvidenceReadReceipt):
        return _refusal(record, side, "unknown-side", operand.detail)
    return _Relations(record, operand).read(path, depth)


class _Relations:
    """One relationship walk over a bound memory tree, returning every edge it finds."""

    def __init__(self, record: ReviewTreeComparisonRecord, operand: SideOperand) -> None:
        self.record = record
        self.operand = operand
        self.examined = 0
        self.resolved = 0
        self._names: list[str] | None = None
        self._index: dict[str, str] | None = None
        self._listing_error: str | None = None

    # -- the walk ----------------------------------------------------------------------------

    def read(self, path: str, depth: int) -> EvidenceRelationshipRead:
        document = self._document(path)
        if document is None:
            return self._refuse(
                "record-unreadable",
                f"the entry at {path} is not a knowledge file this build reads, so its recorded "
                "relationships were not followed",
            )
        edges = list(self._edges(document, depth)) if self._list_names() else []
        return EvidenceRelationshipRead(
            state="records",
            side=self.operand.side,
            comparison_number=self.record.number,
            comparison_reference=comparison_reference(self.record),
            repository=str(self.operand.repository),
            tree=self.operand.tree,
            stored_tree=self.operand.stored_tree,
            from_path=path,
            from_record=_identifier(document),
            from_revision=_revision(document),
            edges=tuple(edges),
            depth=depth,
            listed_paths=len(self._names or ()),
            examined_bytes=self.examined,
            resolved_targets=self.resolved,
            detail=(
                f"{len(edges)} edge(s) of {path} at the {self.operand.side} tree "
                f"{self.operand.tree}, followed to depth {depth}, from a listing of "
                f"{len(self._names or ())} path(s)"
            ),
        )

    def _edges(self, document: object, depth: int) -> Iterator[RelationshipEdge]:
        """The owned, nested and reverse edges, in one stable order."""

        for relation, target in _owned_targets(document):
            edge = self._owned_edge(document, relation, target)
            yield edge
            if depth < 1 or edge.lookup_state != "resolved":
                continue
            nested_path = self._locate(target)
            nested = self._document(nested_path) if nested_path else None
            if nested is None:
                continue
            for nested_relation, nested_target in _owned_targets(nested):
                yield self._owned_edge(nested, nested_relation, nested_target, depth=1)
        yield from self._reverse_edges(document)

    def _owned_edge(
        self, source: object, relation: str, target: str, *, depth: int = 0
    ) -> RelationshipEdge:
        """One edge the inspected file records, with its target resolved at this tree."""

        found = self._resolve(target)
        return _edge(
            target,
            relation,
            "owns",
            depth=depth,
            state="present",
            leaf=_origin_leaf(source),
            revision=_revision(source),
            target_revision=found[0],
            target_origin=found[1],
            lookup_state=found[2],
        )

    def _resolve(self, identifier: str) -> tuple[int | None, str | None, str]:
        """What the target itself says: its revision and origin, or why it did not resolve."""

        path = self._locate(identifier)
        if path is None:
            return None, None, "absent"
        document = self._document(path)
        if document is None or _identifier(document) != identifier:
            return None, None, "unreadable"
        self.resolved += 1
        return _revision(document), _origin_leaf(document), "resolved"

    # -- the owners that record an edge to this file -----------------------------------------

    def _reverse_edges(self, source: object) -> Iterator[RelationshipEdge]:
        identifier = _identifier(source)
        if not identifier:
            return
        names = self._list_names()
        if names is None:
            yield _edge(
                identifier,
                "recorded-by",
                "recorded-by",
                depth=0,
                state="unavailable",
                detail=self._listing_error or "the memory tree listing did not answer",
            )
            return
        for candidate in _related_owner_paths(names):
            document = self._document(candidate)
            if document is None:
                yield _edge(
                    identifier,
                    "recorded-by",
                    "recorded-by",
                    path=candidate,
                    depth=0,
                    state="invalid",
                    detail=(
                        f"the owner file {candidate} could not be read as a knowledge file, so "
                        "what it records about this id stays unexamined"
                    ),
                )
                continue
            for relation, row_id, attempt in _references_to(document, identifier):
                yield _edge(
                    identifier,
                    relation,
                    "recorded-by",
                    path=candidate,
                    depth=0,
                    state="present",
                    revision=_revision(document),
                    leaf=_origin_leaf(document),
                    attempt=attempt,
                    row_id=row_id,
                )

    # -- reading the tree --------------------------------------------------------------------

    def _document(self, path: str) -> object | None:
        receipt = read_bound_source(self.record, self.operand.side, path)
        if not receipt.source_read:
            return None
        self.examined += receipt.examined_bytes
        return _parse_memory_file(path, receipt.text or "")

    def _list_names(self) -> list[str] | None:
        """Every name of the memory tree's ``knowledge`` and ``onboarding`` paths (rule 5)."""

        if self._names is not None:
            return self._names
        try:
            process = subprocess.Popen(
                [
                    "git",
                    "ls-tree",
                    "-r",
                    "--name-only",
                    "-z",
                    self.operand.tree,
                    "--",
                    "knowledge",
                    "onboarding",
                ],
                cwd=self.operand.repository,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                env=git_environment(),
            )
        except OSError as failure:
            self._listing_error = f"the memory tree listing could not run ({failure})"
            return None
        try:
            names, scanned = _stream_names(process)
        finally:
            _stop(process)
        if not names and process.returncode not in (0, -15, None):
            self._listing_error = (
                f"the memory tree listing did not answer (git ls-tree exited {process.returncode})"
            )
            return None
        self.examined += scanned
        self._names = names
        self._index = {
            matched.group("id"): name
            for name in names
            if (matched := _RECORD_FILE.match(Path(name).name))
        }
        return names

    def _locate(self, identifier: str) -> str | None:
        """The path of one record id, through the index built from the one listing (rule 4)."""

        self._list_names()
        return (self._index or {}).get(identifier)

    def _refuse(self, code: str, detail: str) -> EvidenceRelationshipRead:
        return _refusal(self.record, self.operand.side, code, detail)


def _stream_names(process: subprocess.Popen[bytes]) -> tuple[list[str], int]:
    """Every name ``ls-tree -z`` writes, and the bytes the stream held."""

    names: list[str] = []
    scanned = 0
    pending = b""
    assert process.stdout is not None
    while chunk := process.stdout.read(_CHUNK):
        scanned += len(chunk)
        pending += chunk
        parts = pending.split(b"\0")
        pending = parts.pop()
        names.extend(name for raw in parts if (name := raw.decode("utf-8", "replace")))
    return names, scanned


def _stop(process: subprocess.Popen[bytes]) -> None:
    """Stop the listing child and close the pipe this read opened."""

    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:  # pragma: no cover - defensive
        process.kill()
        process.wait(timeout=5)
    if process.stdout is not None:
        process.stdout.close()


# --- the landed schemas -------------------------------------------------------------------------


def _identifier(document: object) -> str | None:
    value = getattr(document, "id", "")
    return str(value) if value else None


def _revision(document: object) -> int | None:
    value = getattr(document, "revision", None)
    return value if isinstance(value, int) else None


def _origin_leaf(document: object) -> str | None:
    origin = getattr(document, "origin", None)
    leaf = getattr(origin, "leaf", None)
    return str(leaf) if leaf else None


def _parse_memory_file(path: str, text: str) -> object | None:
    """The landed model one memory file holds, or ``None`` when this build does not read it."""

    if "/history/" in path:
        return _parse_history(text)
    try:
        return parse_document_text(text)
    except ValueError:
        return None


def _parse_history(text: str) -> object | None:
    try:
        document = json.loads(text)
    except ValueError:
        return None
    return document if isinstance(document, dict) else None


def _owned_targets(document: object) -> list[tuple[str, str]]:
    """The ``(relation, target id)`` pairs this file itself records."""

    found: list[tuple[str, str]] = []
    if isinstance(document, FamilyRecord):
        found += [("members", member) for member in document.members]
    if isinstance(document, (InvariantRecord, DecisionRecord)):
        found += [("supersedes", target) for target in document.supersedes]
    for link in getattr(document, "links", ()) or ():
        target = getattr(link, "target", None)
        if isinstance(target, str) and _RECORD_ID.match(target):
            found.append((f"links:{getattr(link, 'relation', '')}", target))
    if isinstance(document, FileSidecar):
        found += [("realizes", entry.invariant) for entry in document.realizes]
        found += [("proves", entry.invariant) for entry in (document.proves or ())]
    return found


def _references_to(document: object, identifier: str) -> list[tuple[str, str | None, int | None]]:
    """How this file's own owner records a relationship *to* ``identifier``."""

    found: list[tuple[str, str | None, int | None]] = []
    if isinstance(document, FamilyRecord) and identifier in document.members:
        found.append(("members", None, None))
    if isinstance(document, FileSidecar):
        if any(entry.invariant == identifier for entry in document.realizes):
            found.append(("realizes", None, None))
        if any(entry.invariant == identifier for entry in (document.proves or ())):
            found.append(("proves", None, None))
    if isinstance(document, dict) and isinstance(document.get("rows"), list):
        attempt = document.get("attempt")
        attempt = attempt if isinstance(attempt, int) else 1
        for row in document["rows"]:
            if isinstance(row, dict) and row.get("subject") == identifier:
                row_id = row.get("id")
                found.append(("history", str(row_id) if row_id else None, attempt))
    return found


def _related_owner_paths(names: list[str]) -> list[str]:
    """The bounded set of owner files that could record an edge to a memory record."""

    return [
        name
        for name in names
        if name.endswith(".json")
        and ("/families/" in name or "/history/" in name or name.startswith("onboarding/"))
    ]


@dataclass(frozen=True)
class _Found:
    """One resolved target's own facts, when the walk could read it."""

    revision: int | None
    origin_leaf: str | None
    state: str


def _edge(  # noqa: PLR0913 - one edge's own identity and provenance
    record_id: str,
    relation: str,
    direction: str,
    *,
    path: str | None = None,
    depth: int = 0,
    state: str = "present",
    revision: int | None = None,
    leaf: str | None = None,
    attempt: int | None = None,
    row_id: str | None = None,
    target_revision: int | None = None,
    target_origin: str | None = None,
    lookup_state: str = "not-followed",
    detail: str | None = None,
) -> RelationshipEdge:
    return RelationshipEdge(
        record_id=record_id,
        relation=relation,
        direction=direction,  # type: ignore[arg-type]
        path=path,
        depth=depth,
        state=state,  # type: ignore[arg-type]
        record_revision=revision,
        origin_leaf=leaf,
        attempt=attempt,
        row_id=row_id,
        target_revision=target_revision,
        target_origin_leaf=target_origin,
        lookup_state=lookup_state,  # type: ignore[arg-type]
        detail=detail,
    )


def _refusal(
    record: ReviewTreeComparisonRecord, side: str, code: str, detail: str
) -> EvidenceRelationshipRead:
    return EvidenceRelationshipRead(
        state="refused",
        side=side,
        comparison_number=record.number,
        comparison_reference=comparison_reference(record),
        detail=detail,
        refusal_code=code,
    )
