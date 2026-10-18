"""Relationship investigation at a bound memory side (MIK-R102 rules 3, 5, 12).

The walk follows the owners that actually hold each relationship rather than guessing from a generic
key list: a family owns its ``members``; a decision or an invariant owns its ``links`` and
``supersedes``; a file sidecar owns its ``realizes`` and ``proves``; a history file owns its rows. An
edge another owner records is reported ``recorded-by``, so it is never read as a field the inspected
file carries. Each owned edge also **resolves its target at the same bound tree**: the target's own
revision and origin leaf travel with the edge, and a filename is never trusted as the record it names.

Two cuts are recoverable, because a page a consumer cannot continue is a page it cannot use:

* an edge cut names ``v1:after=<n>`` and the reader accepts it, continuing after the edges already
  returned instead of restarting the roster;
* a listing cut names ``v1:names-after=<path>`` and the reader accepts it too, resuming the name
  stream from that path.

The listing is streamed with a byte and name budget rather than materialized whole, lookups go
through one id→path index built from that listing, and the reverse-owner scan stops as soon as the
edge budget is spent. Records are read through :func:`read_bound_source`, so the same exact-tree
operand, byte bound and measured counters apply here as anywhere else.
"""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass
from itertools import islice
from pathlib import Path

from agents_remember.application.review_evidence_packet import comparison_reference
from agents_remember.application.review_evidence_read import (
    DEFAULT_READ_LIMITS,
    SideOperand,
    read_bound_source,
    side_operand,
)
from agents_remember.kernel.git_command import git_environment
from agents_remember.models.knowledge.review_evidence import EvidenceLimits
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

_MAX_LISTED_PATHS = 5000
_MAX_LISTING_BYTES = 4 * 1024 * 1024
_CHUNK = 65536
_RECORD_FILE = re.compile(r"^(?P<id>[A-Z]{2,4}-[A-Z0-9]{4,})-[^/]*\.json$")
_RECORD_ID = re.compile(r"^[A-Z]{2,4}-[A-Z0-9]{4,}$")
_EDGE_CURSOR = re.compile(r"^v1:after=(?P<after>[0-9]+)$")
_NAME_CURSOR = re.compile(r"^v1:names-after=(?P<after>.+)$")


def read_bound_relationships(  # noqa: PLR0913 - one read states its operands, bounds and cursor
    record: ReviewTreeComparisonRecord,
    side: str,
    path: str,
    *,
    depth: int = 1,
    limits: EvidenceLimits = DEFAULT_READ_LIMITS,
    continuation: str | None = None,
    listing_cursor: str | None = None,
) -> EvidenceRelationshipRead:
    """Follow the recorded relationships of one memory file at one bound side (rules 3, 5)."""

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
    if depth < 0 or depth > limits.relationship_depth:
        return _refusal(
            record,
            side,
            "read-beyond-limit",
            (
                f"the requested relationship depth {depth} is beyond the declared bound "
                f"{limits.relationship_depth}; nothing was read"
            ),
        )
    after = _edge_after(continuation, record, side)
    if isinstance(after, EvidenceRelationshipRead):
        return after
    if after >= limits.records_per_collection * limits.pages_per_read:
        return _refusal(
            record,
            side,
            "read-beyond-limit",
            "the continuation exceeds the admitted relationship pages; select a narrower scope",
        )
    named_after = _name_after(listing_cursor)
    operand = side_operand(record, side)
    if isinstance(operand, EvidenceReadReceipt):
        return _refusal(record, side, "unknown-side", operand.detail)
    return _Relations(record, operand, limits).read(path, depth, after, named_after)


def _edge_after(
    cursor: str | None, record: ReviewTreeComparisonRecord, side: str
) -> int | EvidenceRelationshipRead:
    if cursor is None:
        return 0
    matched = _EDGE_CURSOR.match(cursor)
    if matched is None:
        return _refusal(
            record,
            side,
            "unknown-continuation",
            f"the continuation {cursor!r} is not one this reader issued; nothing was read",
        )
    return int(matched.group("after"))


def _name_after(cursor: str | None) -> str | None:
    if cursor is None:
        return None
    matched = _NAME_CURSOR.match(cursor)
    return matched.group("after") if matched else None


class _Relations:
    """One bounded relationship walk over a bound memory tree."""

    def __init__(
        self,
        record: ReviewTreeComparisonRecord,
        operand: SideOperand,
        limits: EvidenceLimits,
    ) -> None:
        self.record = record
        self.operand = operand
        self.limits = limits
        self.examined = 0
        self.resolved = 0
        self._names: list[str] | None = None
        self._index: dict[str, str] | None = None
        self._listing_error: str | None = None
        self._listing_truncated = False
        self._listing_cursor: str | None = None

    # -- the walk ----------------------------------------------------------------------------

    def read(
        self, path: str, depth: int, after: int, named_after: str | None
    ) -> EvidenceRelationshipRead:
        document = self._document(path)
        if document is None:
            return self._refuse(
                "record-unreadable",
                f"the entry at {path} is not a knowledge file this build reads, so its recorded "
                "relationships were not followed",
            )
        edges, cut = self._walk(document, depth, after, named_after)
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
            listing_truncated=self._listing_truncated,
            listing_cursor=self._listing_cursor,
            examined_bytes=self.examined,
            continuation=f"v1:after={after + len(edges)}" if cut else None,
            limit="records_per_collection" if cut else None,
            resolved_targets=self.resolved,
            detail=(
                f"{len(edges)} edge(s) of {path} at the {self.operand.side} tree "
                f"{self.operand.tree}, followed to depth {depth}"
                + (f" from edge {after}" if after else "")
                + (
                    f"; the listing was cut at {len(self._names or ())} path(s) and continues "
                    f"after {self._listing_cursor}"
                    if self._listing_truncated
                    else ""
                )
                + ("; the record bound cut the population" if cut else "")
            ),
        )

    def _walk(
        self, document: object, depth: int, after: int, named_after: str | None
    ) -> tuple[list[RelationshipEdge], bool]:
        if not self._list_names(named_after):
            return [], False
        budget = self.limits.records_per_collection
        page = list(islice(self._edges(document, depth), after, after + budget + 1))
        return page[:budget], len(page) > budget

    def _edges(self, document: object, depth: int) -> Iterator[RelationshipEdge]:
        """One stable owned/nested/reverse edge space; paging skips edges, never owners."""

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
        receipt = read_bound_source(self.record, self.operand.side, path, limits=self.limits)
        if not receipt.source_read:
            return None
        self.examined += receipt.examined_bytes
        return _parse_memory_file(path, receipt.text or "")

    def _list_names(self, after: str | None = None) -> list[str] | None:
        """The memory tree's names, streamed under a name and byte budget (rules 4, 5)."""

        if self._names is not None and after is None:
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
            names, cursor, scanned = _stream_names(process, after)
        finally:
            _stop(process)
        if not names and process.returncode not in (0, -15, None):
            self._listing_error = (
                f"the memory tree listing did not answer (git ls-tree exited {process.returncode})"
            )
            return None
        self.examined += scanned
        if cursor is not None:
            self._listing_truncated = True
            self._listing_cursor = cursor
        if after is None:
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


def _stream_names(
    process: subprocess.Popen[bytes], after: str | None
) -> tuple[list[str], str | None, int]:
    """Read ``ls-tree -z`` output until the name or byte budget is spent.

    Nothing is materialized beyond the budget: the child is stopped as soon as the cap is reached,
    and a cut names the cursor a consumer resumes from.
    """

    names: list[str] = []
    seen_after = after is None
    cursor: str | None = None
    scanned = 0
    pending = b""
    assert process.stdout is not None
    while len(names) <= _MAX_LISTED_PATHS and scanned < _MAX_LISTING_BYTES:
        chunk = process.stdout.read(_CHUNK)
        if not chunk:
            break
        scanned += len(chunk)
        pending += chunk
        parts = pending.split(b"\0")
        pending = parts.pop()
        for raw in parts:
            name = raw.decode("utf-8", "replace")
            if not name:
                continue
            if not seen_after:
                seen_after = name == after
                continue
            names.append(name)
            if len(names) > _MAX_LISTED_PATHS:
                cursor = f"v1:names-after={names[-2]}"
                names = names[:_MAX_LISTED_PATHS]
                return names, cursor, scanned
    return names, cursor, scanned


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
