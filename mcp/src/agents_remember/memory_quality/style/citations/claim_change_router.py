"""Cheap exact-path routing before citation claims pay for structural history reads.

The router is deliberately only an I/O optimization.  It can prove that every local
evidence path is byte-object unchanged from one verified commit through ``HEAD`` and the
current working tree.  Every other shape goes through the semantic fingerprint comparison
owned by :mod:`claim_reopen`; a Git failure is provenance failure, never an unchanged vote.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from agents_remember.kernel.git_command import GIT_METADATA_TIMEOUT_SECONDS, run_git
from agents_remember.memory_quality.style.citations import model, provenance
from agents_remember.memory_quality.style.citations.resolution import Trees

RepositoryKind = Literal["code", "memory"]
RouteStatus = Literal["proven-unchanged", "semantic-required", "error"]


@dataclass(frozen=True)
class LocalCitation:
    citation: model.Citation
    repository: RepositoryKind
    target: Path | None


@dataclass(frozen=True)
class CitationPartition:
    local: tuple[LocalCitation, ...]
    dependencies: tuple[model.Citation, ...]
    error: str | None = None


@dataclass(frozen=True)
class ClaimRoute:
    local: tuple[LocalCitation, ...]
    dependencies: tuple[model.Citation, ...]
    status: RouteStatus
    error: str | None = None


@dataclass(frozen=True)
class PathRead:
    paths: frozenset[str] = frozenset()
    error: str | None = None


@dataclass
class RepositoryRouteMetrics:
    working_tree_censuses: int = 0
    head_tree_reads: int = 0
    historical_comparisons: int = 0
    working_delta_paths: int = 0
    historical_delta_paths: int = 0

    def telemetry(self) -> dict[str, int]:
        return {
            "workingTreeCensuses": self.working_tree_censuses,
            "headTreeReads": self.head_tree_reads,
            "historicalComparisons": self.historical_comparisons,
            "workingDeltaPaths": self.working_delta_paths,
            "historicalDeltaPaths": self.historical_delta_paths,
        }


@dataclass
class RepositoryChanges:
    """One working census and one object comparison per distinct resolved commit."""

    root: Path
    name: RepositoryKind
    metrics: RepositoryRouteMetrics = field(default_factory=RepositoryRouteMetrics)
    _working: PathRead | None = None
    _head: PathRead | None = None
    _historical: dict[str, PathRead] = field(default_factory=dict)

    def route(self, commit: str, path: str) -> tuple[bool, str | None]:
        """Return ``(proven_unchanged, error)`` for one repository-relative path."""
        working = self._working_paths()
        if working.error is not None:
            return False, working.error
        if path in working.paths:
            return False, None
        head = self._head_paths()
        if head.error is not None:
            return False, head.error
        if path not in head.paths:
            return False, None
        historical = self._historical_paths(commit)
        if historical.error is not None or path in historical.paths:
            return False, historical.error
        return True, None

    def _working_paths(self) -> PathRead:
        if self._working is None:
            self.metrics.working_tree_censuses += 1
            completed = run_git(
                self.root,
                [
                    "status",
                    "--porcelain=v1",
                    "-z",
                    "--untracked-files=all",
                    "--ignore-submodules=none",
                    "--no-renames",
                ],
                timeout=GIT_METADATA_TIMEOUT_SECONDS,
            )
            if completed.returncode != 0:
                self._working = PathRead(error=_git_error(completed.stderr, self.name, "status"))
            else:
                try:
                    paths = _status_paths(completed.stdout)
                except ValueError as error:
                    self._working = PathRead(error=f"{self.name} Git status is invalid: {error}")
                else:
                    self.metrics.working_delta_paths = len(paths)
                    self._working = PathRead(frozenset(paths))
        return self._working

    def _head_paths(self) -> PathRead:
        if self._head is None:
            self.metrics.head_tree_reads += 1
            completed = run_git(
                self.root,
                ["ls-tree", "-r", "--name-only", "-z", "HEAD"],
                timeout=GIT_METADATA_TIMEOUT_SECONDS,
            )
            if completed.returncode != 0:
                self._head = PathRead(error=_git_error(completed.stderr, self.name, "HEAD tree"))
            else:
                self._head = PathRead(frozenset(_nul_fields(completed.stdout)))
        return self._head

    def _historical_paths(self, commit: str) -> PathRead:
        if commit not in self._historical:
            self.metrics.historical_comparisons += 1
            completed = run_git(
                self.root,
                [
                    "diff-tree",
                    "--no-commit-id",
                    "--name-status",
                    "-r",
                    "-z",
                    "--no-renames",
                    commit,
                    "HEAD",
                ],
                timeout=GIT_METADATA_TIMEOUT_SECONDS,
            )
            if completed.returncode != 0:
                self._historical[commit] = PathRead(
                    error=_git_error(completed.stderr, self.name, f"{commit}-to-HEAD tree")
                )
            else:
                try:
                    paths = _name_status_paths(completed.stdout)
                except ValueError as error:
                    self._historical[commit] = PathRead(
                        error=f"{self.name} historical Git delta is invalid: {error}"
                    )
                else:
                    self.metrics.historical_delta_paths += len(paths)
                    self._historical[commit] = PathRead(frozenset(paths))
        return self._historical[commit]


@dataclass
class ClaimChangeRouter:
    trees: Trees
    histories: provenance.Histories
    code: RepositoryChanges = field(init=False)
    memory: RepositoryChanges = field(init=False)
    local_claims_proven_unchanged: int = 0
    local_claims_semantic_required: int = 0
    routing_errors: int = 0
    local_sources_skipped: int = 0
    _memory_commits: dict[str, provenance.Read] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.code = RepositoryChanges(self.trees.code_root, "code")
        self.memory = RepositoryChanges(self.trees.memory_root, "memory")

    def route_claim(self, citations: tuple[model.Citation, ...], code_commit: str) -> ClaimRoute:
        partition = partition_citations(self.trees, citations)
        if partition.error is not None:
            return self._error(partition, partition.error)
        semantic_required = False
        errors: list[str] = []
        for source in partition.local:
            unchanged, error = self._route_source(source, code_commit)
            if error is not None:
                errors.append(error)
            elif not unchanged:
                semantic_required = True
        if errors:
            return self._error(partition, "; ".join(dict.fromkeys(errors)))
        if semantic_required:
            self.local_claims_semantic_required += 1
            return ClaimRoute(partition.local, partition.dependencies, "semantic-required")
        if partition.local:
            self.local_claims_proven_unchanged += 1
        self.local_sources_skipped += len(partition.local)
        return ClaimRoute(partition.local, partition.dependencies, "proven-unchanged")

    def _route_source(self, source: LocalCitation, code_commit: str) -> tuple[bool, str | None]:
        if source.repository == "code":
            return self.code.route(code_commit, source.citation.path)
        memory_commit = self.memory_commit(code_commit)
        if memory_commit.text is None:
            return False, memory_commit.error or "memory history mapping is unavailable"
        return self.memory.route(memory_commit.text, source.citation.path)

    def memory_commit(self, code_commit: str) -> provenance.Read:
        if code_commit not in self._memory_commits:
            self._memory_commits[code_commit] = self.histories.memory_commit(code_commit)
        return self._memory_commits[code_commit]

    def _error(self, partition: CitationPartition, error: str) -> ClaimRoute:
        self.routing_errors += 1
        return ClaimRoute(partition.local, partition.dependencies, "error", error)

    def telemetry(self) -> dict[str, object]:
        return {
            "localClaimsProvenUnchanged": self.local_claims_proven_unchanged,
            "localClaimsSemanticRequired": self.local_claims_semantic_required,
            "routingErrors": self.routing_errors,
            "localHistoricalBlobReadsSkipped": self.local_sources_skipped,
            "repositories": {
                "code": self.code.metrics.telemetry(),
                "memory": self.memory.metrics.telemetry(),
            },
        }


def partition_citations(trees: Trees, citations: tuple[model.Citation, ...]) -> CitationPartition:
    local: list[LocalCitation] = []
    dependencies: list[model.Citation] = []
    for citation in citations:
        source, error = classify_citation(trees, citation)
        if error is not None:
            return CitationPartition(tuple(local), tuple(dependencies), error)
        if source is None:
            dependencies.append(citation)
        else:
            local.append(source)
    return CitationPartition(tuple(local), tuple(dependencies))


def classify_citation(
    trees: Trees, citation: model.Citation
) -> tuple[LocalCitation | None, str | None]:
    target = trees.resolve(citation.path)
    if target is not None:
        repository: RepositoryKind = "code" if _under(target, trees.code_root) else "memory"
        return LocalCitation(citation, repository, target), None
    first = citation.path.split("/", maxsplit=1)[0]
    code_owned = (trees.code_root / first).exists()
    memory_owned = (trees.memory_root / first).exists()
    if code_owned and not memory_owned:
        return LocalCitation(citation, "code", None), None
    if memory_owned and not code_owned:
        return LocalCitation(citation, "memory", None), None
    if code_owned and memory_owned:
        return None, (
            f"{citation.path} is absent now and its top-level path exists in both code and "
            "memory, so its provenance kind is ambiguous"
        )
    return None, None


def _status_paths(raw: str) -> set[str]:
    fields = _nul_fields(raw)
    paths: set[str] = set()
    index = 0
    while index < len(fields):
        record = fields[index]
        if len(record) < 4 or record[2] != " ":
            raise ValueError(f"malformed porcelain record {record!r}")
        status = record[:2]
        paths.add(record[3:])
        index += 1
        if "R" in status or "C" in status:
            if index >= len(fields):
                raise ValueError("rename/copy record has no source path")
            paths.add(fields[index])
            index += 1
    return paths


def _name_status_paths(raw: str) -> set[str]:
    fields = _nul_fields(raw)
    paths: set[str] = set()
    index = 0
    while index < len(fields):
        field = fields[index]
        if "\t" in field:
            status, path = field.split("\t", maxsplit=1)
            index += 1
        else:
            status = field
            index += 1
            if index >= len(fields):
                raise ValueError(f"status {status!r} has no path")
            path = fields[index]
            index += 1
        paths.add(path)
        if status.startswith(("R", "C")):
            if index >= len(fields):
                raise ValueError(f"status {status!r} has no second path")
            paths.add(fields[index])
            index += 1
    return paths


def _nul_fields(raw: str) -> list[str]:
    fields = raw.split("\0")
    if fields and fields[-1] == "":
        fields.pop()
    if any(not field for field in fields):
        raise ValueError("empty NUL-delimited field")
    return fields


def _under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _git_error(stderr: str, repository: str, operation: str) -> str:
    first = next((line.strip() for line in stderr.splitlines() if line.strip()), "")
    return first or f"{repository} Git {operation} failed"
