"""Dependent-document staleness for incremental landings and a master's end.

An ordinary landing or checkpoint reads only the dependents whose links sit at a changed code path
or changed record, and refuses nothing for them. A master's end inspects every dependent at the
judged tip against the master's standing base and refuses only documents this master's own work
made stale (since that base, or in one of this master's own integrated leaves without a later
confirmation) unless already repaired on the parent line after landing.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path

from agents_remember.application.knowledge_currentness.observe import (
    CodeTree,
    OpenedCodeTree,
    open_code_tree,
)
from agents_remember.application.knowledge_gate.change_reach import dependent_touches, record_id_of
from agents_remember.application.knowledge_reader.marks import dependent_movement
from agents_remember.application.knowledge_worklist.knowledge import KnowledgeSideUnreadable
from agents_remember.kernel.git_command import (
    GIT_METADATA_TIMEOUT_SECONDS,
    GitRunnerOptions,
    read_git_blobs_bytes,
    run_git,
    run_git_bytes,
)
from agents_remember.kernel.git_preparation import GitPreparationError
from agents_remember.models.knowledge_files.documents import (
    KNOWLEDGE_ROOT,
    LAYOUT_MARKER_PATH,
    RECORD_DIRECTORIES,
    parse_document_text,
)
from agents_remember.models.knowledge_files.records import DependentRecord
from agents_remember.models.knowledge_files.sidecars import LayoutMarker
from agents_remember.tasks.document_refs import TaskDocumentRefError
from agents_remember.worktrees.modules.git import branch_commit, is_ancestor
from agents_remember.worktrees.queue.closeout_queue import CloseoutQueueError
from agents_remember.worktrees.series_leaf_contracts import exact_atomic_leaf_contracts
from agents_remember.worktrees.services import LandingGateRequest
from agents_remember.worktrees.worktree_contract import (
    ContractError,
    WorktreeContract,
    load_contract,
)

__all__ = [
    "DocumentStaleness",
    "document_staleness",
]

_LANDING_READ: dict[str, object] = {
    "view": "dependent",
    "staleOnly": True,
    "memoryRoot": "the landing's memory tree",
}
_PARENT_READ: dict[str, object] = {
    "view": "dependent",
    "memoryRoot": "the parent line's present memory tip",
}
_REMEDY_SINCE_BASE = (
    "Run a task of this master that changes or confirms each document (MIK-R124 rules 5 and 8)."
)
_REMEDY_BY_LEAF = (
    "Run a task of this master that changes or confirms each document so "
    "confirmed.commit descends from the leaf's landed code commit."
)


def _git(repository: Path, *args: str) -> str | None:
    result = run_git_bytes(
        repository, list(args), GitRunnerOptions(timeout=GIT_METADATA_TIMEOUT_SECONDS)
    )
    return (
        result.stdout.decode("utf-8", errors="surrogateescape") if result.returncode == 0 else None
    )


def _require_commit(repository: Path, commit: str, *, what: str) -> None:
    """Verify ``commit`` resolves to a readable commit object, or raise ValueError."""

    checked = run_git(
        repository,
        ["rev-parse", "--verify", "--quiet", f"{commit}^{{commit}}"],
        GitRunnerOptions(timeout=GIT_METADATA_TIMEOUT_SECONDS),
    )
    if checked.returncode != 0:
        detail = (checked.stderr or "").strip()
        raise ValueError(f"{what} ({commit}) cannot be read" + (f": {detail}" if detail else ""))


def _strict_is_ancestor(repository: Path, ancestor: str, descendant: str, *, what: str) -> bool:
    """Non-strict Git ancestry (equal commits are True); any unreadable commit raises ValueError."""

    result = run_git(
        repository,
        ["merge-base", "--is-ancestor", ancestor, descendant],
        GitRunnerOptions(timeout=GIT_METADATA_TIMEOUT_SECONDS),
    )
    if result.returncode in (0, 1):
        return result.returncode == 0
    detail = (result.stderr or "").strip()
    raise ValueError(
        f"{what} ancestry cannot be read (git merge-base rc {result.returncode}"
        + (f": {detail}" if detail else "")
        + ")"
    )


@dataclass(frozen=True)
class DocumentStaleness:
    """The documents one landing made stale, split by ownership and repair state.

    ``documents`` holds every stale document that refuses a master's end (or moved on an ordinary
    landing). On a master's end it is the union of ``made_stale_since_base`` and
    ``made_stale_by_leaf``; ``repaired_on_parent`` names documents already current at the parent
    line's present tip after landing; ``already_stale`` names all other stale documents (refusing
    nothing).
    """

    documents: tuple[str, ...] = ()
    already_stale: tuple[str, ...] = ()
    made_stale_since_base: tuple[str, ...] = ()
    made_stale_by_leaf: tuple[str, ...] = ()
    made_stale_by_leaf_ids: tuple[tuple[str, tuple[str, ...]], ...] = ()
    repaired_on_parent: tuple[str, ...] = ()
    uncomputable: str | None = None
    unverifiable: tuple[str, ...] = ()
    examined: tuple[str, ...] = ()
    dependents_read: int = 0

    @property
    def count(self) -> int:
        return len(self.documents)

    def answer(self, *, name_already_stale: bool = False) -> dict[str, object] | None:
        """The landing answer's ``staleDocuments`` field, or ``None`` when nothing is named."""

        if self.uncomputable is not None:
            return {"state": "uncomputable", "detail": self.uncomputable}
        if self.unverifiable:
            return {
                "state": "unverifiable",
                "detail": "; ".join(self.unverifiable),
                "count": self.count,
                "documents": list(self.documents),
            }
        has_non_refusing = bool(self.already_stale) or bool(self.repaired_on_parent)
        if not (bool(self.documents) or (name_already_stale and has_non_refusing)):
            return None
        answer: dict[str, object] = {
            "count": self.count,
            "documents": list(self.documents),
            "read": dict(_LANDING_READ),
        }
        if self.already_stale:
            answer["alreadyStale"] = list(self.already_stale)
        self._add_kind_sections(answer, name_already_stale=name_already_stale)
        return answer

    def _add_kind_sections(self, answer: dict[str, object], *, name_already_stale: bool) -> None:
        """Attach per-kind blocks with their reads and refusal remedies."""

        if self.made_stale_since_base:
            answer["refusedSinceBase"] = {
                "documents": list(self.made_stale_since_base),
                "read": dict(_LANDING_READ),
                "remedy": _REMEDY_SINCE_BASE,
            }
        if self.made_stale_by_leaf:
            by_leaf: dict[str, object] = {
                "documents": list(self.made_stale_by_leaf),
                "read": dict(_LANDING_READ),
                "remedy": _REMEDY_BY_LEAF,
            }
            if self.made_stale_by_leaf_ids:
                by_leaf["leaves"] = {
                    doc: list(leaves) for doc, leaves in self.made_stale_by_leaf_ids
                }
            answer["refusedByOwnLeaf"] = by_leaf
        if self.repaired_on_parent:
            answer["repairedOnParent"] = {
                "documents": list(self.repaired_on_parent),
                "read": dict(_PARENT_READ),
            }
        if name_already_stale and self.already_stale:
            answer["namedOnly"] = {
                "documents": list(self.already_stale),
                "read": dict(_LANDING_READ),
            }

    def refusal_message(self) -> str:
        """Why a master's end is refused, naming each kind apart with its read and remedy."""

        named = ", ".join(self.documents)
        if not self.made_stale_since_base and not self.made_stale_by_leaf:
            parts = [
                f"{self.count} document(s) are stale that this master's own tasks made stale: "
                f"{named}",
            ]
            if self.repaired_on_parent:
                parts.append(
                    "repaired on the parent line and refusing nothing: "
                    + ", ".join(self.repaired_on_parent)
                    + " (read with knowledge_read view=dependent, "
                    "memoryRoot=<the parent line's present memory tip>)"
                )
            if self.already_stale:
                parts.append(
                    "already stale at the base and refusing nothing: "
                    + ", ".join(self.already_stale)
                )
            return (
                "; ".join(parts)
                + ". Read them with knowledge_read view=dependent, staleOnly=true, "
                "memoryRoot=<this master's memory tree>. The remedy is a task of this master "
                "that changes the document or confirms it (MIK-R124 rules 5 and 8)"
            )
        sentences = [
            f"{self.count} document(s) are stale that this master's own tasks made stale: {named}."
        ]
        if self.made_stale_since_base:
            sentences.append(
                f"Made stale since the base: {', '.join(self.made_stale_since_base)}. "
                "Read them with knowledge_read view=dependent, staleOnly=true, "
                "memoryRoot=<this master's memory tree>. "
                "The remedy is a task of this master that changes the document or confirms it "
                "(MIK-R124 rules 5 and 8)."
            )
        if self.made_stale_by_leaf:
            leaves_by_doc = dict(self.made_stale_by_leaf_ids)
            labelled = ", ".join(
                f"{doc} ({', '.join(leaves_by_doc[doc])})" if doc in leaves_by_doc else doc
                for doc in self.made_stale_by_leaf
            )
            sentences.append(
                f"Made stale by this master's own leaf: {labelled}. "
                "Read them with knowledge_read view=dependent, staleOnly=true, "
                "memoryRoot=<this master's memory tree>. "
                "The remedy is a task of this master that changes or confirms each document "
                "so confirmed.commit descends from the leaf's landed code commit."
            )
        if self.repaired_on_parent:
            sentences.append(
                f"Repaired on the parent line and refusing nothing: "
                f"{', '.join(self.repaired_on_parent)} "
                "(read with knowledge_read view=dependent, "
                "memoryRoot=<the parent line's present memory tip>)."
            )
        if self.already_stale:
            sentences.append(
                f"Already stale at the base and refusing nothing: "
                f"{', '.join(self.already_stale)} "
                "(read with knowledge_read view=dependent, staleOnly=true, "
                "memoryRoot=<this master's memory tree>)."
            )
        return " ".join(sentences)


@dataclass(frozen=True)
class _IntegratedLeaf:
    """One integrated leaf of a master, with its base and landed code/memory pairs."""

    leaf_id: str
    code_base: str
    memory_base: str
    code_landed: str
    memory_landed: str


@dataclass(frozen=True)
class _LandingMemory:
    """One memory commit's dependent records, and the record paths needed to read a revision."""

    repository: Path
    revision: str
    paths: Mapping[str, str]
    records: Mapping[str, DependentRecord]
    examined: tuple[str, ...] = ()
    bodies_read: int = 0
    _revisions: dict[str, int] = field(default_factory=dict, compare=False)

    @property
    def dependents(self) -> Mapping[str, DependentRecord]:
        """The dependents this read holds, by the document each names."""

        return {record.document: record for record in self.records.values()}

    def revision_of(self, record_id: str) -> int | None:
        """A record's stored revision, read on demand and once: only the links read need it."""

        if record_id in self._revisions:
            return self._revisions[record_id]
        path = self.paths.get(record_id)
        if path is None:
            return None
        blob = _git(self.repository, "rev-parse", "--verify", "--quiet", f"{self.revision}:{path}")
        if blob is None or not blob.strip():
            raise ValueError(f"the linked record {path} at {self.revision} cannot be read")
        data = read_git_blobs_bytes(self.repository, [blob.strip()]).get(blob.strip())
        if data is None:
            raise ValueError(f"the linked record {path} at {self.revision} returned no bytes")
        document = json.loads(data.decode("utf-8"))
        revision = document.get("revision") if isinstance(document, dict) else None
        if isinstance(revision, int):
            self._revisions[record_id] = revision
            return revision
        raise ValueError(f"the linked record {path} at {self.revision} has no integer revision")


@dataclass(frozen=True)
class _ObservedSides:
    """Observed base and tip dependent records, stale sets, and unverifiable maps."""

    base: _LandingMemory
    tip: _LandingMemory
    base_stale: set[str]
    base_unverifiable: dict[str, str]
    tip_stale: set[str]
    tip_unverifiable: dict[str, str]


@dataclass
class _LeafLookupCache:
    """Per-call cache of memory records and opened code trees shared across leaf pairs."""

    request: LandingGateRequest
    candidates: set[str]
    memory: dict[str, _LandingMemory] = field(default_factory=dict)
    code: dict[str, OpenedCodeTree] = field(default_factory=dict)

    def stale_at(self, code_commit: str, memory_commit: str) -> set[str]:
        memory = self.memory.get(memory_commit)
        if memory is None:
            memory = _dependents_at(self.request.memory_repository, memory_commit, wanted=None)
            self.memory[memory_commit] = memory
        tree_ref = CodeTree(self.request.code_repository, code_commit)
        opened = self.code.get(code_commit)
        if opened is None:
            opened = open_code_tree(tree_ref)
            self.code[code_commit] = opened
        stale: set[str] = set()
        for doc in self.candidates:
            record = memory.dependents.get(doc)
            if record is None:
                continue
            state, _moved = dependent_movement(
                record, revision_of=memory.revision_of, code=opened, tree=tree_ref
            )
            if state == "stale":
                stale.add(doc)
        return stale


def document_staleness(request: LandingGateRequest, *, full: bool = False) -> DocumentStaleness:
    """The dependents stale at the landing's tip, classified for an ordinary landing or master end.

    An incremental landing (``full=False``) reads only dependents linked to a changed code path or
    changed record. A master's end (``full=True``) examines every dependent at the tip and base,
    attributes own-leaf staleness from integrated leaf enclosures, exempts documents already
    repaired on the parent line after landing, and refuses unverifiable links only when unreadable
    at the tip while readable at the base.
    """

    try:
        effective = _effective_request(request, full=full)
        observed = _observe_pair(effective, full=full)
        if not full:
            return DocumentStaleness(
                documents=tuple(sorted(observed.tip_stale - observed.base_stale)),
                already_stale=tuple(sorted(observed.tip_stale & observed.base_stale)),
                unverifiable=tuple(
                    sorted(
                        {
                            *observed.base_unverifiable.values(),
                            *observed.tip_unverifiable.values(),
                        }
                    )
                ),
                examined=tuple(sorted(set(observed.base.examined) | set(observed.tip.examined))),
                dependents_read=observed.base.bodies_read + observed.tip.bodies_read,
            )
        return _classify_master_end(effective, observed)
    except (
        KnowledgeSideUnreadable,
        GitPreparationError,
        CloseoutQueueError,
        ContractError,
        TaskDocumentRefError,
        ValueError,
        OSError,
        subprocess.SubprocessError,
    ) as error:
        return DocumentStaleness(uncomputable=f"{type(error).__name__}: {error}")


def _effective_request(request: LandingGateRequest, *, full: bool) -> LandingGateRequest:
    if full and request.document_base is not None:
        code_base, memory_base = request.document_base
        return replace(request, code_base=code_base, memory_bases=(memory_base,))
    return request


def _observe_pair(request: LandingGateRequest, *, full: bool) -> _ObservedSides:
    changed = _changed_paths(request)
    moved_records = _changed_record_ids(
        request.memory_repository, request.memory_bases[0], request.memory_commit
    )
    wanted: frozenset[str] | None = None if full else changed
    base = _dependents_at(
        request.memory_repository,
        request.memory_bases[0],
        wanted=wanted,
        wanted_records=moved_records,
    )
    tip = _dependents_at(
        request.memory_repository,
        request.memory_commit,
        wanted=wanted,
        wanted_records=moved_records,
    )
    base_view = base if full else _touched_at(base, changed, moved_records)
    tip_view = tip if full else _touched_at(tip, changed, moved_records)
    base_stale, base_unverifiable = _stale_documents(
        base_view,
        None if request.code_base is None else CodeTree(request.code_repository, request.code_base),
    )
    tip_stale, tip_unverifiable = _stale_documents(
        tip_view, CodeTree(request.code_repository, request.code_commit)
    )
    return _ObservedSides(
        base=base,
        tip=tip,
        base_stale=base_stale,
        base_unverifiable=base_unverifiable,
        tip_stale=tip_stale,
        tip_unverifiable=tip_unverifiable,
    )


def _classify_master_end(
    request: LandingGateRequest, observed: _ObservedSides
) -> DocumentStaleness:
    """Apply master-end staleness, own-leaf attribution, parent repair and unreadable filtering."""

    since_base = set(observed.tip_stale - observed.base_stale)
    candidates = set(observed.tip_stale & observed.base_stale)
    by_leaf_map: dict[str, tuple[str, ...]] = {}
    repaired: set[str] = set()
    if request.series_contract is not None:
        series = load_contract(Path(request.series_contract))
        leaves = _master_integrated_leaves(series, request)
        by_leaf_map = _documents_made_stale_by_leaves(request, observed.tip, candidates, leaves)
        repaired = _repaired_on_parent(series, request, since_base | by_leaf_map.keys())
    refused_since_base = tuple(sorted(since_base - repaired))
    refused_by_leaf = tuple(sorted(by_leaf_map.keys() - repaired))
    refused_by_leaf_ids = tuple(
        (doc, by_leaf_map[doc]) for doc in refused_by_leaf if doc in by_leaf_map
    )
    named_only = tuple(sorted(candidates - by_leaf_map.keys()))
    tip_only_unverifiable = tuple(
        sorted(
            msg
            for doc, msg in observed.tip_unverifiable.items()
            if doc not in observed.base_unverifiable
        )
    )
    return DocumentStaleness(
        documents=tuple(sorted((*refused_since_base, *refused_by_leaf))),
        already_stale=named_only,
        made_stale_since_base=refused_since_base,
        made_stale_by_leaf=refused_by_leaf,
        made_stale_by_leaf_ids=refused_by_leaf_ids,
        repaired_on_parent=tuple(sorted(repaired)),
        unverifiable=tip_only_unverifiable,
        examined=tuple(sorted(set(observed.base.examined) | set(observed.tip.examined))),
        dependents_read=observed.base.bodies_read + observed.tip.bodies_read,
    )


def _master_integrated_leaves(
    series: WorktreeContract, request: LandingGateRequest
) -> list[_IntegratedLeaf]:
    """Every integrated leaf of ``series`` whose landed commits are both ancestors of the tip.

    An integrated leaf with unreadable pairs, or with only one side an ancestor of the judged tip,
    raises ValueError so the master's end fails closed as uncomputable.
    """

    if series.kind != "series":
        return []
    contracts = exact_atomic_leaf_contracts(series, require_all=False)
    selected: list[_IntegratedLeaf] = []
    for leaf_id, leaf in contracts.items():
        if leaf.integration_status != "completed":
            continue
        parsed = _validated_leaf_pairs(leaf_id, leaf, request)
        code_anc = _strict_is_ancestor(
            request.code_repository,
            parsed.code_landed,
            request.code_commit,
            what=f"leaf {leaf_id!r} landed code commit {parsed.code_landed}",
        )
        mem_anc = _strict_is_ancestor(
            request.memory_repository,
            parsed.memory_landed,
            request.memory_commit,
            what=f"leaf {leaf_id!r} landed memory commit {parsed.memory_landed}",
        )
        if code_anc != mem_anc:
            present = "code" if code_anc else "memory"
            raise ValueError(
                f"integrated leaf {leaf_id!r} has only its {present} side as an ancestor "
                "of the judged tip"
            )
        if code_anc and mem_anc:
            selected.append(parsed)
    return selected


def _validated_leaf_pairs(
    leaf_id: str, leaf: WorktreeContract, request: LandingGateRequest
) -> _IntegratedLeaf:
    code_base = leaf.code_base_commit
    memory_base = leaf.memory_base_commit
    code_landed = leaf.integrated_code_commit
    memory_landed = leaf.integrated_memory_content_commit
    if not (code_base and memory_base and code_landed and memory_landed):
        raise ValueError(f"integrated leaf {leaf_id!r} is missing a base or landed commit pair")
    _require_commit(request.code_repository, code_base, what=f"leaf {leaf_id!r} code base")
    _require_commit(request.code_repository, code_landed, what=f"leaf {leaf_id!r} landed code")
    _require_commit(request.memory_repository, memory_base, what=f"leaf {leaf_id!r} memory base")
    _require_commit(
        request.memory_repository, memory_landed, what=f"leaf {leaf_id!r} landed memory"
    )
    return _IntegratedLeaf(
        leaf_id=leaf_id,
        code_base=code_base,
        memory_base=memory_base,
        code_landed=code_landed,
        memory_landed=memory_landed,
    )


def _documents_made_stale_by_leaves(
    request: LandingGateRequest,
    tip: _LandingMemory,
    candidates: set[str],
    leaves: list[_IntegratedLeaf],
) -> dict[str, tuple[str, ...]]:
    """Map each candidate document made stale by an own leaf (and unconfirmed since) to its leaves."""

    if not candidates or not leaves:
        return {}
    cache = _LeafLookupCache(request=request, candidates=candidates)
    by_doc: dict[str, list[str]] = {}
    for leaf in leaves:
        if leaf.code_base == leaf.code_landed and leaf.memory_base == leaf.memory_landed:
            continue
        base_stale = cache.stale_at(leaf.code_base, leaf.memory_base)
        landed_stale = cache.stale_at(leaf.code_landed, leaf.memory_landed)
        for doc in sorted(landed_stale - base_stale):
            if not _confirmed_after_leaf(request.code_repository, tip, doc, leaf):
                by_doc.setdefault(doc, []).append(leaf.leaf_id)
    return {doc: tuple(sorted(set(ids))) for doc, ids in by_doc.items()}


def _confirmed_after_leaf(
    code_repository: Path, tip: _LandingMemory, document: str, leaf: _IntegratedLeaf
) -> bool:
    """Whether ``document``'s tip confirmation descends from (or equals) ``leaf``'s landed code."""

    record = tip.dependents.get(document)
    confirmed_commit = record.confirmed.commit if record is not None else ""
    if not confirmed_commit:
        return False
    return _strict_is_ancestor(
        code_repository,
        leaf.code_landed,
        confirmed_commit,
        what=f"confirmed.commit {confirmed_commit} of {document}",
    )


def _repaired_on_parent(
    series: WorktreeContract, request: LandingGateRequest, refusing: set[str]
) -> set[str]:
    """Documents already current at the parent's present tip, only after both commits landed."""

    if not refusing or not series.code_source_branch or not series.memory_source_branch:
        return set()
    try:
        parent_code = branch_commit(request.code_repository, series.code_source_branch)
        parent_memory = branch_commit(request.memory_repository, series.memory_source_branch)
    except (RuntimeError, subprocess.SubprocessError, OSError):
        return set()
    if not (
        is_ancestor(request.code_repository, request.code_commit, parent_code)
        and is_ancestor(request.memory_repository, request.memory_commit, parent_memory)
    ):
        return set()
    try:
        parent_mem = _dependents_at(request.memory_repository, parent_memory, wanted=None)
        parent_tree = CodeTree(request.code_repository, parent_code)
        opened = open_code_tree(parent_tree)
        repaired: set[str] = set()
        for doc in refusing:
            record = parent_mem.dependents.get(doc)
            if record is None:
                continue
            state, moved = dependent_movement(
                record, revision_of=parent_mem.revision_of, code=opened, tree=parent_tree
            )
            if state == "current" and not moved:
                repaired.add(doc)
        return repaired
    except (
        KnowledgeSideUnreadable,
        GitPreparationError,
        ValueError,
        OSError,
        subprocess.SubprocessError,
    ):
        return set()


def _changed_paths(request: LandingGateRequest) -> frozenset[str]:
    """The paths the landing's code range changed, or none when it names no base."""

    if not request.code_base:
        return frozenset()
    listed = _git(
        request.code_repository,
        "diff",
        "--name-only",
        "--no-renames",
        "-z",
        request.code_base,
        request.code_commit,
    )
    if listed is None:
        raise ValueError(
            f"the code range {request.code_base}..{request.code_commit} could not be read"
        )
    return frozenset(path for path in listed.split("\0") if path.strip())


def _changed_record_ids(repository: Path, base: str, tip: str) -> frozenset[str]:
    """The record IDs whose files differ between two memory commits, from the listing alone."""

    listed = _git(
        repository,
        "diff",
        "--name-only",
        "-z",
        "--no-renames",
        base,
        tip,
        "--",
        f"{KNOWLEDGE_ROOT}/",
    )
    if listed is None:
        raise ValueError(f"the memory range {base}..{tip} could not be read")
    found = {
        record_id
        for path in listed.split("\0")
        if path.strip() and (record_id := record_id_of(path)) is not None
    }
    return frozenset(found)


def _touched_at(
    memory: _LandingMemory, changed_paths: frozenset[str], changed_records: frozenset[str]
) -> _LandingMemory:
    """The same memory, holding only the dependents a change can move."""

    return replace(
        memory,
        records={
            record_id: record
            for record_id, record in memory.records.items()
            if dependent_touches(record, changed_paths, changed_records)
        },
    )


def _dependents_at(
    repository: Path,
    revision: str,
    *,
    wanted: frozenset[str] | None = None,
    wanted_records: frozenset[str] = frozenset(),
) -> _LandingMemory:
    """The dependent records a memory commit holds, fetched by what a change can move."""

    paths = _record_paths_at(repository, revision)
    selected = (
        None
        if wanted is None
        else _dependent_paths_saying(repository, revision, wanted, wanted_records, paths)
    )
    if selected is not None and not selected:
        return _LandingMemory(repository=repository, revision=revision, paths=paths, records={})
    wanted_paths = {
        path
        for path in paths.values()
        if path.startswith(f"{KNOWLEDGE_ROOT}/{RECORD_DIRECTORIES['dependent']}/")
        and (selected is None or path in selected)
    }
    records: dict[str, DependentRecord] = {}
    if wanted_paths:
        blobs = _blobs_at(repository, revision, sorted(wanted_paths))
        for path, data in blobs.items():
            record = DependentRecord.model_validate(json.loads(data.decode("utf-8")))
            if paths.get(record.id) != path:
                raise ValueError(f"the dependent {path} does not hold its named record")
            records[record.id] = record
    return _LandingMemory(
        repository=repository,
        revision=revision,
        paths=paths,
        records=records,
        examined=tuple(sorted(record.document for record in records.values())),
        bodies_read=len(wanted_paths),
    )


def _record_paths_at(repository: Path, revision: str) -> dict[str, str]:
    """Every record file of one memory commit, by record ID: a listing, never a body read."""

    listed = _git(
        repository, "ls-tree", "-r", "--name-only", "-z", revision, "--", f"{KNOWLEDGE_ROOT}/"
    )
    if listed is None:
        raise ValueError(f"the record files of {revision} cannot be listed")
    paths: dict[str, str] = {}
    for path in listed.split("\0"):
        if not path.strip():
            continue
        record_id = record_id_of(path)
        if record_id is not None:
            paths[record_id] = path
    return paths


def _dependent_paths_saying(
    repository: Path,
    revision: str,
    wanted: frozenset[str],
    wanted_records: frozenset[str],
    paths: Mapping[str, str],
) -> set[str]:
    """Resolve changed targets from the writer-owned lookup without reading unrelated records."""

    if not wanted and not wanted_records:
        return set()
    data = _blobs_at(repository, revision, [LAYOUT_MARKER_PATH])[LAYOUT_MARKER_PATH]
    marker = parse_document_text(data.decode("utf-8"))
    if not isinstance(marker, LayoutMarker):
        raise ValueError(f"{revision} holds no valid dependent lookup")
    indexed = {path for group in (marker.dependent_links or {}).values() for path in group}
    present = {path for path in paths.values() if path.startswith("knowledge/dependents/")}
    if indexed != present:
        raise ValueError(
            f"the dependent lookup of {revision} does not name exactly its record files; "
            "write them through knowledge_write to rebuild it"
        )
    targets = [*(f"file:{path}" for path in wanted), *(f"record:{key}" for key in wanted_records)]
    return {path for target in targets for path in (marker.dependent_links or {}).get(target, ())}


def _blobs_at(repository: Path, revision: str, paths: list[str]) -> dict[str, bytes]:
    """The bytes of named paths at one memory commit, read in one batch."""

    ids: list[str] = []
    by_id: dict[str, str] = {}
    for path in paths:
        blob = _git(repository, "rev-parse", "--verify", "--quiet", f"{revision}:{path}")
        if blob is None or not blob.strip():
            raise ValueError(f"{path} at {revision} cannot be read")
        by_id[path] = blob.strip()
        ids.append(blob.strip())
    if not ids:
        return {}
    read = read_git_blobs_bytes(repository, ids)
    missing = [path for path, blob in by_id.items() if blob not in read]
    if missing:
        raise ValueError(f"{revision} returned no bytes for {', '.join(missing)}")
    return {path: read[blob] for path, blob in by_id.items()}


def _stale_documents(
    memory: _LandingMemory, code: CodeTree | None
) -> tuple[set[str], dict[str, str]]:
    """The stale documents of ``memory`` at ``code``, and unverifiable ones keyed by document."""

    code_tree = open_code_tree(code)
    stale: set[str] = set()
    unverifiable: dict[str, str] = {}
    for document, record in memory.dependents.items():
        state, moved = dependent_movement(
            record,
            revision_of=memory.revision_of,
            code=code_tree,
            tree=code,
        )
        if state == "stale":
            stale.add(document)
        unreadable = [one for one in moved if one.get("state") == "unverifiable"]
        if state == "unverifiable" or unreadable:
            reason = (unreadable or moved or [{}])[0].get("reason", "a link cannot be observed")
            unverifiable[document] = f"{document}: {reason}"
    return stale, unverifiable
