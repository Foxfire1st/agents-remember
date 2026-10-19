"""Name the exact inputs a recorded comparison's answers are a function of (MIK-R179 rule 3).

``serving`` ranks below ``application``, so the layer that can read a leaf's contract is the
composition root. This module binds that reading once and keeps it out of the app builder: the
reviewer's reuse cache is given, per read, the record, the two directories its generation lives in
and the object stores the recorded trees are read from -- never a working view and never a live
``HEAD``, which is why a closed leaf's answers can be served again unchanged.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from agents_remember.application.review_candidate_resolution import recorded_leaf_contract
from agents_remember.application.review_comparison_generation import leaf_generation_root
from agents_remember.application.review_tree_comparison import (
    comparison_directory,
    comparison_records,
)
from agents_remember.kernel.git_command import GitRunnerOptions, run_git, run_git_bytes
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.knowledge.review import ReviewSurfaceRequest
from agents_remember.serving.changeset_cache import (
    ContentRead,
    DirectoryRead,
    ObjectStoreRead,
    ReadInput,
)
from agents_remember.serving.review_answer_reuse import RecordedAnswerReuse
from agents_remember.serving.review_trees import ReviewTreesQuery
from agents_remember.worktrees.worktree_contract import WorktreeContract

__all__ = ["RecordedReviewAnswers", "recorded_answer_sources"]


def recorded_answer_sources(
    config: McpRuntimeConfig, repository_id: str, master: str, leaf_id: str
) -> tuple[ReadInput, ...] | None:
    """The inputs one recorded answer is a function of, or ``None`` when this leaf has no record."""

    contract = _recorded_contract(config, repository_id, master, leaf_id)
    if contract is None:
        return None
    return _sources_of(config, contract, repository_id, master, leaf_id)


def _recorded_contract(
    config: McpRuntimeConfig, repository_id: str, master: str, leaf_id: str
) -> WorktreeContract | None:
    """The leaf's own enclosure contract, or ``None`` for a name that cannot address one."""

    for value in (repository_id, master, leaf_id):
        if not value or "/" in value or "\\" in value or value.startswith("."):
            return None
    contract = recorded_leaf_contract(config, repository_id, master, leaf_id)
    return contract if contract is not None and contract.kind == "leaf" else None


def _sources_of(
    config: McpRuntimeConfig,
    contract: WorktreeContract,
    repository_id: str,
    master: str,
    leaf_id: str,
) -> tuple[ReadInput, ...]:
    """The record, the directories its generation lives in, and the stores it is read from.

    A successor record is another answer, so the comparison directory's own listing is an input; a
    generation's listing is one too. The object census is an additional invalidator; direct
    recorded-closure checks below detect a pruned dependency even when an unrelated new object
    compensates for its count.
    """

    task_root = config.coordination_root / "tasks" / repository_id / master
    worklist = contract.contract_path.parent / "knowledge-worklist.json"
    sources: list[ReadInput] = [
        ContentRead(contract.contract_path),
        DirectoryRead(comparison_directory(task_root, leaf_id)),
        DirectoryRead(leaf_generation_root(task_root, leaf_id)),
        ContentRead(worklist) if worklist.exists() else worklist,
    ]
    for root in (contract.code_repo_path, contract.memory_repo_path):
        if root is not None and Path(root).is_dir():
            sources.append(ObjectStoreRead(Path(root)))
    return tuple(dict.fromkeys(sources))


def _recorded_sides(task_root: Path, leaf_id: str) -> tuple[tuple[Path, str], ...]:
    """The trees the recorded answers are computed from, as (repository, tree id), deduplicated.

    A kept answer is only good while the objects it was computed from can still be read. Object
    counts cannot say that: one removed tree and one added blob leave the census the same size.
    """

    sides: list[tuple[Path, str]] = []
    for record in comparison_records(task_root, leaf_id):
        for side in (
            record.code_base,
            record.code_candidate,
            record.memory_base,
            record.memory_candidate,
        ):
            sides.append((Path(side.repository), side.tree))
    return tuple(dict.fromkeys(sides))


def _trees_still_readable(sides: tuple[tuple[Path, str], ...]) -> bool:
    """Check the recorded closures, including descendant presence and object readability."""

    roots: dict[Path, list[str]] = {}
    for repository, tree in sides:
        roots.setdefault(repository, []).append(tree)
    for repository, trees in roots.items():
        # A root can survive the loss of a subtree or blob. Traverse just these recorded
        # closures; Git marks missing descendants with '?', including compensated removals
        # which an object count cannot distinguish (L177-R1-05). Gitlinks are not traversed.
        result = run_git(
            repository,
            [
                "rev-list",
                "--objects",
                "--no-object-names",
                "--missing=print",
                *(f"{tree}^{{tree}}" for tree in trees),
            ],
        )
        if result.returncode or any(line.startswith("?") for line in result.stdout.splitlines()):
            return False
        objects = result.stdout.splitlines()
        if not _object_bodies_readable(repository, objects):
            return False
    return True


def _object_bodies_readable(repository: Path, objects: list[str]) -> bool:
    """Make Git consume each recorded body; readable headers alone do not prove this.

    Batch framing also detects an object lost between the traversal and this read. Bodies
    stay bytes: neither newline conversion nor a previously held blob can hide unreadability.
    """

    result = run_git_bytes(
        repository,
        ["cat-file", "--batch"],
        GitRunnerOptions(input_text="\n".join(objects) + "\n"),
    )
    if result.returncode or result.stderr:
        return False
    offset = 0
    for object_id in objects:
        end = result.stdout.find(b"\n", offset)
        if end < 0:
            return False
        header = result.stdout[offset:end].split()
        if len(header) != 3 or header[0] != object_id.encode("ascii") or not header[2].isdigit():
            return False
        end += 1 + int(header[2])
        if result.stdout[end : end + 1] != b"\n":
            return False
        offset = end + 1
    return offset == len(result.stdout)


def _reads_from_its_record(contract: WorktreeContract | None) -> bool:
    """Whether a leaf is answered from a durable record rather than from a live enclosure.

    A leaf whose enclosure still names a capture worktree is a live comparison: its answers depend
    on a working view this module never observes, so they are computed on every read. The entry
    catalogue has no ``history`` selector of its own, so this is how its recordedness is decided.
    """

    if contract is None:
        return False
    worktree = contract.code_worktree
    return worktree is None or not worktree.exists()


class RecordedReviewAnswers:
    """Serve a recorded comparison's answers from the bounded reuse of their exact inputs.

    One place decides, per read, whether the answer is a function of a record (kept while its
    inputs hold) or of a working view (computed every time), so the three routes cannot disagree
    about which reads are reusable. The keys are the exact questions: the whole intent request, the
    task context of an entry catalogue, and every field that selects a tree view.
    """

    def __init__(self, config: McpRuntimeConfig, reuse: RecordedAnswerReuse) -> None:
        self._config = config
        self._reuse = reuse

    def intent[T](self, request: ReviewSurfaceRequest, produce: Callable[[], T]) -> T:
        """The review answer: reused only when the caller asked for the leaf's record."""

        if request.history != "recorded":
            return produce()
        return self._answer(
            ("intent", request.model_dump_json()),
            (request.repository_id, request.master, request.leaf_id),
            produce,
            must_be_closed=False,
        )

    def entries[T](
        self, repository_id: str, master: str, leaf_id: str, produce: Callable[[], T]
    ) -> T:
        """The subject catalogue: reused only for a leaf answered from its record."""

        return self._answer(
            ("entries", repository_id, master, leaf_id), (repository_id, master, leaf_id), produce
        )

    def trees[T](self, query: ReviewTreesQuery, produce: Callable[[], T]) -> T:
        """The tree view: reused only when the query names a recorded comparison."""

        if not (query.recorded or query.number is not None):
            return produce()
        return self._answer(
            ("trees", _tree_key(query)),
            (query.repository_id, query.master, query.leaf_id),
            produce,
        )

    def _answer[T](
        self,
        key: tuple[object, ...],
        context: tuple[str, str, str],
        produce: Callable[[], T],
        *,
        must_be_closed: bool = True,
    ) -> T:
        """The answer to one recorded question: kept while its inputs hold, else computed."""

        repository_id, master, leaf_id = context
        contract = _recorded_contract(self._config, repository_id, master, leaf_id)
        if contract is None or (must_be_closed and not _reads_from_its_record(contract)):
            return produce()
        task_root = self._config.coordination_root / "tasks" / repository_id / master
        if not _trees_still_readable(_recorded_sides(task_root, leaf_id)):
            # A tree the answer was computed from is gone: the answer is not the same answer, and
            # the kept one must never stand in for the fresh read that reports the loss.
            return produce()
        sources = _sources_of(self._config, contract, repository_id, master, leaf_id)
        return self._reuse.answer(key, sources, produce)


def _tree_key(query: ReviewTreesQuery) -> tuple[object, ...]:
    """Every field of a tree-view query that selects its answer."""

    return (
        "trees",
        query.repository_id,
        query.master,
        query.leaf_id,
        query.number,
        query.recorded,
        query.invariants,
        query.lane,
        query.file,
    )
