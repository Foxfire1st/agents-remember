"""Resolve canonical task-document references and their structural containment.

The task tree is the hierarchy authority.  Spawn ancestry is retained as provenance,
but it never decides who a seat's parent or child is.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks.document import (
    LeafPlacement,
    SprintExecutionGraph,
    SprintExecutionNode,
    TaskDocument,
    derived_leaf_placement,
)
from agents_remember.tasks.store import read_task_doc

TaskAltitude = Literal["sprint", "master", "leaf"]

SPRINT_ROLES = frozenset(
    {"architect", "orchestrator", "strategist", "designer", "system-specialist"}
)
MASTER_ROLES = frozenset({"manager"})
LEAF_ROLES = frozenset({"worker", "reviewer", "curator"})


class TaskDocumentRefError(ValueError):
    """A task document or containment relation could not resolve exactly once."""

    def __init__(self, status: str, detail: str) -> None:
        self.status = status
        super().__init__(detail)


@dataclass(frozen=True)
class ResolvedTaskDocument:
    ref: TaskDocumentRef
    path: Path
    document: TaskDocument


@dataclass(frozen=True)
class MasterLeafPlacement:
    """One commanded master's live leaf-to-segment placement report (L11-R2/R6).

    Read paths surface ``placement.unknown_leaf_ids`` and
    ``placement.unplaced_leaf_ids`` as facts; only the graph-authoring write path
    refuses an incomplete partition.
    """

    master: ResolvedTaskDocument
    placement: LeafPlacement


class TaskDocumentTopology:
    """Read-only resolver for one coordination root's canonical task hierarchy."""

    def __init__(self, coordination_root: Path) -> None:
        self.coordination_root = coordination_root.resolve()

    def resolve(self, ref: TaskDocumentRef) -> ResolvedTaskDocument:
        root = self._repo_root(ref.repository)
        path = (root / ref.path).resolve(strict=False)
        if not path.is_relative_to(root):
            raise TaskDocumentRefError(
                "task-document-outside-root",
                f"task document {ref.path!r} escapes tasks/{ref.repository}",
            )
        if not path.is_file():
            raise TaskDocumentRefError(
                "task-document-not-found", f"task document does not exist: {path}"
            )
        try:
            document = read_task_doc(path)
        except (OSError, ValueError) as exc:
            raise TaskDocumentRefError(
                "task-document-invalid", f"cannot read task document {path}: {exc}"
            ) from exc
        if document.repo != ref.repository:
            raise TaskDocumentRefError(
                "task-document-repo-mismatch",
                f"task document {path} declares repo {document.repo!r}, expected {ref.repository!r}",
            )
        return ResolvedTaskDocument(ref=ref, path=path, document=document)

    def canonical_ref(self, repository: str, path: str | Path) -> TaskDocumentRef:
        """Confine an absolute or repository-relative path and return its canonical reference."""

        root = self._repo_root(repository)
        supplied = Path(path)
        resolved = (supplied if supplied.is_absolute() else root / supplied).resolve(strict=False)
        if not resolved.is_relative_to(root):
            raise TaskDocumentRefError(
                "task-document-outside-root",
                f"task document {str(path)!r} is outside tasks/{repository}",
            )
        ref = TaskDocumentRef(repository=repository, path=resolved.relative_to(root).as_posix())
        return self.resolve(ref).ref

    def ref_for_id(
        self, repository: str, directory: str | Path, document_id: str
    ) -> TaskDocumentRef:
        """Resolve exactly one JSON-primary task document by its declared id."""

        root = self._repo_root(repository)
        supplied = Path(directory)
        resolved_directory = (supplied if supplied.is_absolute() else root / supplied).resolve(
            strict=False
        )
        if not resolved_directory.is_relative_to(root):
            raise TaskDocumentRefError(
                "task-document-outside-root",
                f"task directory {str(directory)!r} is outside tasks/{repository}",
            )
        matches: list[TaskDocumentRef] = []
        for path in sorted(resolved_directory.glob("*.json")):
            ref = self.canonical_ref(repository, path)
            if self.resolve(ref).document.id == document_id:
                matches.append(ref)
        if len(matches) != 1:
            raise TaskDocumentRefError(
                "task-document-id-ambiguous" if matches else "task-document-not-found",
                f"task id {document_id!r} resolved to {len(matches)} documents in "
                f"{resolved_directory}",
            )
        return matches[0]

    def altitude(self, ref: TaskDocumentRef) -> TaskAltitude:
        resolved = self.resolve(ref)
        if resolved.document.kind != "master":
            self.parent(ref)
            return "leaf"
        parents = self._sprint_parents(resolved)
        if resolved.document.orchestrates:
            if parents:
                raise TaskDocumentRefError(
                    "task-document-altitude-ambiguous",
                    f"task document {ref.key} both commands masters and is commanded by a sprint",
                )
            return "sprint"
        if len(parents) == 1:
            return "master"
        if not parents and resolved.document.executionNature == "atomic":
            return "master"
        if not parents:
            raise TaskDocumentRefError(
                "task-document-parent-missing",
                f"master task document {ref.key} is not commanded by a sprint document",
            )
        raise TaskDocumentRefError(
            "task-document-parent-ambiguous",
            f"master task document {ref.key} is commanded by multiple sprint documents",
        )

    def parent(self, ref: TaskDocumentRef) -> TaskDocumentRef | None:
        resolved = self.resolve(ref)
        if resolved.document.kind != "master":
            return self._leaf_parent(resolved).ref
        parents = self._sprint_parents(resolved)
        if (
            resolved.document.orchestrates or resolved.document.executionNature == "atomic"
        ) and not parents:
            return None
        if len(parents) == 1:
            return parents[0].ref
        status = "task-document-parent-missing" if not parents else "task-document-parent-ambiguous"
        raise TaskDocumentRefError(status, f"cannot resolve one parent for {ref.key}")

    def validate_role(self, ref: TaskDocumentRef, role: str) -> TaskAltitude:
        expected = (
            "sprint"
            if role in SPRINT_ROLES
            else "master"
            if role in MASTER_ROLES
            else "leaf"
            if role in LEAF_ROLES
            else None
        )
        if expected is None:
            raise TaskDocumentRefError(
                "seat-role-unsupported", f"role {role!r} has no structural task altitude"
            )
        actual = self.altitude(ref)
        if actual != expected:
            raise TaskDocumentRefError(
                "seat-role-altitude-mismatch",
                f"role {role!r} requires a {expected} document, got {actual}: {ref.key}",
            )
        return actual

    def children(self, ref: TaskDocumentRef) -> tuple[TaskDocumentRef, ...]:
        resolved = self.resolve(ref)
        altitude = self.altitude(ref)
        if altitude == "leaf":
            return ()
        if altitude == "master":
            children: list[TaskDocumentRef] = []
            for item in resolved.document.subTasks:
                if not item.file:
                    continue
                candidate = (resolved.path.parent / item.file).with_suffix(".json")
                if candidate.is_file():
                    children.append(self.canonical_ref(ref.repository, candidate))
            return tuple(dict.fromkeys(children))
        return tuple(parent.ref for parent in self._commanded_masters(resolved))

    def validate_execution_topology(
        self,
        sprint_ref: TaskDocumentRef,
        *,
        overrides: Mapping[TaskDocumentRef, TaskDocument] | None = None,
    ) -> tuple[ResolvedTaskDocument, ...]:
        """Validate one sprint's exact commanded membership and execution contract.

        Legacy documents remain parseable so the explicit migration operation can inspect
        them. They never acquire inferred meaning: the first topology consumer reports a
        migration-required status until both the sprint graph and every commanded master's
        execution nature exist.
        """

        candidates = overrides or {}
        sprint = self._resolve_with_overrides(sprint_ref, candidates)
        if sprint.document.kind != "master" or not sprint.document.orchestrates:
            raise TaskDocumentRefError(
                "task-execution-graph-sprint-required",
                f"execution graph requires an orchestration sprint: {sprint_ref.key}",
            )
        graph = sprint.document.executionGraph
        if graph is None:
            raise TaskDocumentRefError(
                "task-execution-topology-migration-required",
                f"orchestration sprint {sprint_ref.key} has no executionGraph; "
                "run task_doc.migrate_execution_topology",
            )
        commanded = self._commanded_masters_exact(sprint, candidates)
        commanded_refs = {master.ref for master in commanded}
        graph_refs = set(graph.master_refs())
        if graph_refs != commanded_refs:
            missing = sorted(ref.key for ref in commanded_refs - graph_refs)
            extra = sorted(ref.key for ref in graph_refs - commanded_refs)
            raise TaskDocumentRefError(
                "task-execution-graph-membership-invalid",
                f"executionGraph membership must exactly match orchestrates; "
                f"missing={missing!r}, extra={extra!r}",
            )
        nature_by_ref: dict[TaskDocumentRef, str | None] = {}
        for master in commanded:
            if master.document.executionNature is None:
                raise TaskDocumentRefError(
                    "task-execution-topology-migration-required",
                    f"commanded master {master.ref.key} has no executionNature; "
                    "run task_doc.migrate_execution_topology",
                )
            nature_by_ref[master.ref] = master.document.executionNature
        for node in graph.nodes:
            if node.kind == "segment" and nature_by_ref[node.ref] == "atomic":
                raise TaskDocumentRefError(
                    "task-execution-graph-node-kind-invalid",
                    f"atomic master {node.ref.key} admits lump nodes only; refused segment "
                    f"node with leafIds={node.leafIds!r}",
                )
        return commanded

    def execution_leaf_placement(
        self,
        sprint_ref: TaskDocumentRef,
        *,
        overrides: Mapping[TaskDocumentRef, TaskDocument] | None = None,
    ) -> tuple[MasterLeafPlacement, ...]:
        """Return each commanded master's live leaf placement after topology validation.

        The re-validation hook (L11-R6): computed against the master's *live* subTasks
        rows, so a leaf set that changed after graph authoring shows up as unknown or
        unplaced facts. Lump-only masters report an empty placement.
        """

        candidates = overrides or {}
        masters = self.validate_execution_topology(sprint_ref, overrides=overrides)
        sprint = self._resolve_with_overrides(sprint_ref, candidates)
        graph = sprint.document.executionGraph
        if graph is None:  # pragma: no cover - validate_execution_topology already refused
            raise TaskDocumentRefError(
                "task-execution-topology-migration-required",
                f"orchestration sprint {sprint_ref.key} has no executionGraph",
            )
        completed = {master.ref for master in masters if master.document.status == "Completed"}
        return tuple(
            MasterLeafPlacement(
                master=master,
                placement=derived_leaf_placement(
                    graph,
                    master.ref,
                    [row.number for row in master.document.subTasks],
                    completed,
                ),
            )
            for master in masters
        )

    def execution_waves(self, sprint_ref: TaskDocumentRef) -> list[list[SprintExecutionNode]]:
        """Return the graph-derived waves after exact cross-document validation."""

        sprint = self.resolve(sprint_ref)
        self.validate_execution_topology(sprint_ref, overrides={sprint_ref: sprint.document})
        graph = cast(SprintExecutionGraph, sprint.document.executionGraph)
        return graph.derived_waves()

    def execution_sprints_affected_by_master(
        self,
        master_ref: TaskDocumentRef,
        *,
        original: TaskDocument | None,
        candidate: TaskDocument,
    ) -> tuple[ResolvedTaskDocument, ...]:
        """Return every sprint whose alias-based command may change under a master edit."""

        aliases = {Path(master_ref.path).parent.name, candidate.id, candidate.title}
        if original is not None:
            aliases.update({original.id, original.title})
        return tuple(
            sprint
            for sprint in self._master_documents(master_ref.repository)
            if sprint.ref != master_ref
            and sprint.document.orchestrates
            and aliases.intersection(sprint.document.orchestrates)
        )

    def repository_masters(self, repository: str) -> tuple[ResolvedTaskDocument, ...]:
        """Return every canonical master document in one repository task tree.

        Branch authority is repository-global: an ordinary leaf under one sprint must not be
        able to claim another sprint's super or atomic integration ref. Callers that census
        those refs therefore need the complete canonical master set, not only the current
        leaf's ancestry.
        """

        return self._master_documents(repository)

    def resolve_candidate(
        self,
        ref: TaskDocumentRef,
        overrides: Mapping[TaskDocumentRef, TaskDocument],
    ) -> ResolvedTaskDocument:
        """Resolve one published-or-proposed document through the canonical authority."""

        return self._resolve_with_overrides(ref, overrides)

    def _repo_root(self, repository: str) -> Path:
        return (self.coordination_root / "tasks" / repository).resolve(strict=False)

    def _resolve_with_overrides(
        self,
        ref: TaskDocumentRef,
        overrides: Mapping[TaskDocumentRef, TaskDocument],
    ) -> ResolvedTaskDocument:
        document = overrides.get(ref)
        if document is None:
            return self.resolve(ref)
        root = self._repo_root(ref.repository)
        path = (root / ref.path).resolve(strict=False)
        if not path.is_relative_to(root):
            raise TaskDocumentRefError(
                "task-document-outside-root",
                f"task document {ref.path!r} escapes tasks/{ref.repository}",
            )
        if document.repo != ref.repository:
            raise TaskDocumentRefError(
                "task-document-repo-mismatch",
                f"task document {ref.key} declares repo {document.repo!r}",
            )
        return ResolvedTaskDocument(ref=ref, path=path, document=document)

    def _leaf_parent(self, leaf: ResolvedTaskDocument) -> ResolvedTaskDocument:
        parent_path = leaf.path.parent / "task.json"
        parent_ref = self.canonical_ref(leaf.ref.repository, parent_path)
        parent = self.resolve(parent_ref)
        if parent.document.kind != "master":
            raise TaskDocumentRefError(
                "task-document-parent-invalid", f"leaf parent is not a master: {parent_path}"
            )
        names = {leaf.path.name, leaf.path.stem, leaf.document.id}
        declared = any(
            item.number == leaf.document.id
            or (item.file and (Path(item.file).name in names or Path(item.file).stem in names))
            for item in parent.document.subTasks
        )
        if not declared:
            raise TaskDocumentRefError(
                "task-document-parent-missing",
                f"leaf {leaf.ref.key} is not declared by {parent.ref.key}",
            )
        return parent

    def _master_documents(self, repository: str) -> tuple[ResolvedTaskDocument, ...]:
        root = self._repo_root(repository)
        if not root.is_dir():
            return ()
        documents: list[ResolvedTaskDocument] = []
        for path in sorted(root.rglob("task.json")):
            relative = path.relative_to(root)
            if "0_archive" in relative.parts or "enclosures" in relative.parts:
                continue
            resolved = self.resolve(
                TaskDocumentRef(repository=repository, path=relative.as_posix())
            )
            if resolved.document.kind == "master":
                documents.append(resolved)
        return tuple(documents)

    def _sprint_parents(self, master: ResolvedTaskDocument) -> tuple[ResolvedTaskDocument, ...]:
        names = {
            master.path.parent.name,
            master.document.id,
            master.document.title,
        }
        return tuple(
            candidate
            for candidate in self._master_documents(master.ref.repository)
            if candidate.ref != master.ref
            and candidate.document.orchestrates
            and any(name in names for name in candidate.document.orchestrates)
        )

    def _commanded_masters(self, sprint: ResolvedTaskDocument) -> tuple[ResolvedTaskDocument, ...]:
        commanded = set(sprint.document.orchestrates)
        matches: list[ResolvedTaskDocument] = []
        for candidate in self._master_documents(sprint.ref.repository):
            if candidate.ref == sprint.ref:
                continue
            names = {candidate.path.parent.name, candidate.document.id, candidate.document.title}
            if commanded.intersection(names):
                matches.append(candidate)
        return tuple(matches)

    def _commanded_masters_exact(
        self,
        sprint: ResolvedTaskDocument,
        overrides: Mapping[TaskDocumentRef, TaskDocument],
    ) -> tuple[ResolvedTaskDocument, ...]:
        available = {
            candidate.ref: self._resolve_with_overrides(candidate.ref, overrides)
            for candidate in self._master_documents(sprint.ref.repository)
            if candidate.ref != sprint.ref
        }
        for ref, document in overrides.items():
            if (
                ref.repository == sprint.ref.repository
                and ref != sprint.ref
                and document.kind == "master"
            ):
                available[ref] = self._resolve_with_overrides(ref, overrides)
        resolved: list[ResolvedTaskDocument] = []
        for commanded_name in sprint.document.orchestrates:
            matches = [
                candidate
                for candidate in available.values()
                if commanded_name
                in {
                    candidate.path.parent.name,
                    candidate.document.id,
                    candidate.document.title,
                }
            ]
            if len(matches) != 1:
                raise TaskDocumentRefError(
                    "task-execution-graph-membership-invalid",
                    f"orchestrates entry {commanded_name!r} resolves to {len(matches)} masters",
                )
            resolved.append(matches[0])
        refs = [master.ref for master in resolved]
        if len(refs) != len(set(refs)):
            raise TaskDocumentRefError(
                "task-execution-graph-membership-invalid",
                "orchestrates contains multiple aliases for the same commanded master",
            )
        return tuple(resolved)
