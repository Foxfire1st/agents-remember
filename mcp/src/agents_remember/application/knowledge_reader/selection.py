"""Which memory tree the reader shows, and the code tree its states are measured at (MIK-R29 rule 1).

A reader selection names one repository and one memory tree:

* ``published`` (the default) -- the published memory-tree selection of MIK-R23 rule 6: the
  repository's own memory root, read in its current captured state through
  :func:`~agents_remember.application.published_intent.select_knowledge_dataset`;
* a memory **commit** (its full or abbreviated hexadecimal name) or **branch** -- resolved to the
  commit's tree, read through Git objects with nothing checked out;
* ``leaf:<scope>`` -- a live leaf's candidate tree: the memory worktree of the active enclosure
  whose worktree group is ``<scope>``, in its current captured state.

The reader needs no task: a task is only one of the ways to name a tree.

**The code tree** each MIK-R03 state is measured at, and whose paths the explorer lists: for a
commit, the code commit its ``Code-Commit`` trailer pairs it with (read from the code repository's
object store); for ``published`` and a leaf, ``HEAD`` of the scope's code checkout -- the tree the
published-intent block already measures at (L03 gap 1). No working tree is read for code, and
when no code tree can be named the selection says why and every entry is ``unverifiable``.

**Converted trees only.** A tree without the layout marker is answered ``not-converted`` before any
index is built: the reader serves the text layout, and unconverted memory is unchanged.

Everything here reads: the derived index is built into the coordination runtime's cache, never into
a repository.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Literal

import apsw

from agents_remember.application.knowledge_currentness import CodeTree
from agents_remember.application.published_intent import (
    converted_memory_tree,
    select_knowledge_dataset,
)
from agents_remember.application.worktree_code_state import (
    WorktreeCapture,
    hold_observed_code_tree,
)
from agents_remember.errors import AuthorityError
from agents_remember.kernel.git_command import (
    GIT_METADATA_TIMEOUT_SECONDS,
    GitRunnerOptions,
    run_git,
)
from agents_remember.kernel.git_preparation import GitPreparationError
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.memory.conversion.base import own_paired_code_commit
from agents_remember.memory.knowledge_index import (
    IndexMismatchError,
    KnowledgeIndex,
    KnowledgeIndexCache,
    MemoryTreeError,
    default_cache_directory,
)
from agents_remember.models.knowledge_files.documents import LAYOUT_MARKER_PATH
from agents_remember.serving.scope import FileScope, resolve_scope
from agents_remember.worktrees.task_resolver import (
    is_foreign_enclosure_contract,
    iter_leaf_enclosure_contracts,
)
from agents_remember.worktrees.worktree_contract import ContractError, load_contract

logger = logging.getLogger(__name__)

__all__ = [
    "PUBLISHED",
    "READ_FAILURES",
    "ReaderReadError",
    "ReaderSelection",
    "ReaderUnavailable",
    "open_selection",
    "selection_options",
]

PUBLISHED: Final = "published"
LEAF_PREFIX: Final = "leaf:"
SelectionKind = Literal["published", "commit", "leaf"]
_RECENT_COMMITS: Final = 40
CodeSource = Literal["paired-code-commit", "checkout-head", "checkout-working-tree", "none"]
_ACCEPTED_NAMES: Final = (
    "accepted forms are a full or abbreviated memory commit name, a memory branch name, "
    "'published', or leaf:<scope>"
)


class ReaderReadError(RuntimeError):
    """A Git read the reader needs failed: the part of the view it feeds is ``unavailable``."""


# Everything that can go wrong reading a tree, named: Git, the file system, the index, the scope.
# Programming errors (a ``KeyError``, a ``TypeError``) are not here: they surface as errors rather
# than as an ``unavailable`` answer.
READ_FAILURES: Final = (
    apsw.Error,
    OSError,
    subprocess.SubprocessError,
    AuthorityError,
    MemoryTreeError,
    IndexMismatchError,
    GitPreparationError,
    UnicodeDecodeError,
    ReaderReadError,
)


@dataclass(frozen=True)
class ReaderUnavailable:
    """Why no view can be served for a selection: named, never an empty view."""

    state: Literal["not-converted", "unavailable", "invalid-request"]
    detail: str

    def to_document(self, repository_id: str, commit: str) -> dict[str, Any]:
        return {
            "state": self.state,
            "selection": {"repo": repository_id, "commit": commit},
            "detail": self.detail,
        }


@dataclass
class ReaderSelection:
    """One opened memory tree: its index, where its files are read from, and its code tree."""

    repository_id: str
    commit: str
    kind: SelectionKind
    memory_repository: Path
    memory_root: Path | None
    revision: str | None
    index: KnowledgeIndex
    code_repository: Path
    code_tree: CodeTree | None
    code_note: str
    code_source: CodeSource = "none"
    pinned_commit: str | None = None
    code_state: str | None = None
    """``commit`` or ``working-tree``: the state the code tree was observed in (MIK-R73 rule 7)."""
    code_capture: WorktreeCapture | None = None
    """A held working-tree capture, alive until this selection is closed."""
    coordination_root: Path | None = None
    """The task plane whose requirement owner resolves record links, when available."""

    def close(self) -> None:
        self.index.close()
        if self.code_capture is not None:
            self.code_capture.close()
            self.code_capture = None

    def __enter__(self) -> ReaderSelection:
        return self

    def __exit__(self, *exception: object) -> None:
        self.close()

    @property
    def reads_directory(self) -> bool:
        """Whether the tree's files are read from a working directory (``published``, a leaf)."""

        return self.memory_root is not None

    def to_document(self) -> dict[str, Any]:
        state = self.index.state
        return {
            "repo": self.repository_id,
            "commit": self.commit,
            "kind": self.kind,
            "memoryRevision": self.revision,
            "treeKey": state.key,
            "indexState": state.state,
            "problems": [{"path": path, "detail": detail} for path, detail in state.problems],
            "codeTree": None if self.code_tree is None else self.code_tree.to_document(),
            "codeSource": self.code_source,
            "codeNote": self.code_note,
            # N2: a working-tree selection whose state equals its HEAD commit can be shared pinned.
            "pinnedCommit": self.pinned_commit,
        }


def selection_for_dataset(  # noqa: PLR0913 - dataset binding and requirement-owner context
    *,
    repository_id: str,
    memory_root: Path,
    database_path: Path,
    tree_key: str,
    code_tree: CodeTree | None = None,
    coordination_root: Path | None = None,
) -> ReaderSelection:
    """Open the reader's selection over a dataset a caller already resolved (MIK-R73 rule 2).

    ``knowledge_read`` selects its dataset from the caller's ``memoryRoot`` and verifies it against
    that file; the reader's own ``open_selection`` resolves the tree from the process settings
    instead, which is the dashboard's question and not this one. This constructor therefore takes
    the dataset and the tree key the read already holds, so a mounted record view reads exactly the
    tree the caller named. The caller closes it (``ReaderSelection.close`` closes the index).
    """

    root = memory_root.resolve()
    # The commit the working tree is at, so a record's history rows have an epoch to be read at
    # (MIK-R73 rule 2); a tree with no commit names none and the timeline says so.
    revision = _rev_parse(root, "HEAD")
    return ReaderSelection(
        repository_id=repository_id,
        commit=tree_key,
        kind="published",
        memory_repository=root,
        memory_root=root,
        revision=revision,
        index=KnowledgeIndex(Path(database_path), expected_key=tree_key),
        code_repository=root if code_tree is None else code_tree.repository,
        code_tree=code_tree,
        code_note=(
            "no code tree was resolved for this read; anchor states are unverifiable"
            if code_tree is None
            else "the code tree this read resolved, observed for the caller"
        ),
        code_source="none" if code_tree is None else "checkout-head",
        pinned_commit=None,
        coordination_root=coordination_root,
    )


def open_selection(
    config: McpRuntimeConfig, repository_id: str, commit: str | None
) -> ReaderSelection | ReaderUnavailable:
    """Open the memory tree ``commit`` names in ``repository_id`` (``published`` when ``None``)."""

    spelled = commit or PUBLISHED
    # NUL cannot be serialized as a subprocess argument; other names remain Git's decision.
    if "\x00" in spelled:
        return ReaderUnavailable(
            "invalid-request", f"memory names cannot contain NUL; {_ACCEPTED_NAMES}"
        )
    try:
        if spelled == PUBLISHED:
            scope = resolve_scope(config, repository_id, "mainline")
            return _directory_selection(config, repository_id, spelled, scope)
        if spelled.startswith(LEAF_PREFIX):
            return _leaf_selection(config, repository_id, spelled)
        scope = resolve_scope(config, repository_id, "mainline")
        return _commit_selection(config, repository_id, spelled, scope)
    except (*READ_FAILURES, MemoryTreeError, IndexMismatchError) as error:
        return ReaderUnavailable("unavailable", f"{type(error).__name__}: {error}")


def _leaf_selection(
    config: McpRuntimeConfig, repository_id: str, spelled: str
) -> ReaderSelection | ReaderUnavailable:
    scope_id = spelled[len(LEAF_PREFIX) :]
    if not scope_id or scope_id == "mainline":
        return ReaderUnavailable("invalid-request", "a leaf selection names its scope")
    if spelled not in {leaf["commit"] for leaf in _leaves(config, repository_id)}:
        return ReaderUnavailable(
            "unavailable", f"no active enclosure of {repository_id} has the scope {scope_id}"
        )
    scope = resolve_scope(config, repository_id, scope_id)
    return _directory_selection(config, repository_id, spelled, scope, kind="leaf")


def _directory_selection(
    config: McpRuntimeConfig,
    repository_id: str,
    spelled: str,
    scope: FileScope,
    *,
    kind: SelectionKind = "published",
) -> ReaderSelection | ReaderUnavailable:
    memory_root = scope.memory_root
    if memory_root is None:
        return ReaderUnavailable("unavailable", f"{repository_id} declares no memory layer")
    if converted_memory_tree(memory_root) is None:
        return ReaderUnavailable(
            "not-converted",
            f"the memory tree at {memory_root} holds no {LAYOUT_MARKER_PATH}; the reader serves "
            "converted trees, and this tree keeps its database reads",
        )
    selected = select_knowledge_dataset(memory_root, coordination_root=config.coordination_root)
    tree = selected.memory_tree
    if tree is None:  # pragma: no cover - a converted root always selects a tree
        return ReaderUnavailable("unavailable", f"{memory_root} selected no memory tree")
    index = KnowledgeIndex(selected.database_path, expected_key=tree.tree_key)
    head = _rev_parse(memory_root, "HEAD")
    clean = head is not None and _rev_parse(memory_root, "HEAD^{tree}") == tree.tree_key
    code_tree, note, code_state, capture = _checkout_code_tree(scope.code_root, kind)
    return ReaderSelection(
        repository_id=repository_id,
        commit=spelled,
        kind=kind,
        memory_repository=memory_root,
        memory_root=memory_root,
        revision=head,
        index=index,
        code_repository=scope.code_root,
        code_tree=code_tree,
        code_note=note,
        code_source=(
            "none"
            if code_tree is None
            else "checkout-working-tree"
            if code_state == "working-tree"
            else "checkout-head"
        ),
        pinned_commit=head if clean else None,
        code_state=code_state,
        code_capture=capture,
        coordination_root=config.coordination_root,
    )


def _commit_selection(
    config: McpRuntimeConfig, repository_id: str, spelled: str, scope: FileScope
) -> ReaderSelection | ReaderUnavailable:
    repository = scope.memory_root
    if repository is None:
        return ReaderUnavailable("unavailable", f"{repository_id} declares no memory layer")
    commit = _rev_parse(repository, f"{spelled}^{{commit}}")
    if commit is None:
        return ReaderUnavailable(
            "invalid-request",
            f"{spelled!r} names no commit in the memory repository; {_ACCEPTED_NAMES}",
        )
    if not _holds(repository, commit, LAYOUT_MARKER_PATH):
        return ReaderUnavailable(
            "not-converted",
            f"memory commit {commit} holds no {LAYOUT_MARKER_PATH}; the reader serves converted "
            "trees",
        )
    cache = KnowledgeIndexCache(default_cache_directory(config.coordination_root))
    index = cache.for_git_tree(repository, commit)
    code_tree, note = _paired_code_tree(repository, commit, scope.code_root)
    return ReaderSelection(
        repository_id=repository_id,
        commit=commit,
        kind="commit",
        memory_repository=repository,
        memory_root=None,
        revision=commit,
        index=index,
        code_repository=scope.code_root,
        code_tree=code_tree,
        code_note=note,
        code_source="none" if code_tree is None else "paired-code-commit",
        pinned_commit=commit,
        coordination_root=config.coordination_root,
    )


def _checkout_code_tree(
    code_root: Path, kind: SelectionKind
) -> tuple[CodeTree | None, str, str | None, WorktreeCapture | None]:
    """The code tree the caller sees, observed once and held for the read (MIK-R73 rule 7).

    The commit when the checkout is clean; otherwise the working tree, captured with Git's
    isolated mechanism into a scratch store that the selection holds until it is closed, so the
    captured blobs are readable for exactly as long as the read that asked for them.
    """

    observed, capture = hold_observed_code_tree(code_root)
    if observed is None:
        return (
            None,
            f"the code checkout {code_root} has no HEAD tree; states are unverifiable",
            None,
            None,
        )
    tree = CodeTree(code_root, observed.tree_id, observed.object_directory)
    where = (
        "the working tree of the leaf's code worktree, captured for this read"
        if observed.code_state == "working-tree" and kind == "leaf"
        else "the working tree of the repository's code checkout, captured for this read"
        if observed.code_state == "working-tree"
        else "HEAD of the leaf's code worktree"
        if kind == "leaf"
        else "HEAD of the repository's code checkout"
    )
    return tree, where, observed.code_state, capture


def _paired_code_tree(
    memory_repository: Path, commit: str, code_root: Path
) -> tuple[CodeTree | None, str]:
    code_commit = own_paired_code_commit(memory_repository, commit)
    if code_commit is None:
        return None, (
            f"memory commit {commit[:12]} carries no Code-Commit trailer; states are unverifiable"
        )
    tree = _rev_parse(code_root, f"{code_commit}^{{tree}}")
    if tree is None:
        return None, (
            f"the paired code commit {code_commit[:12]} is not in {code_root}; states are "
            "unverifiable"
        )
    return CodeTree(code_root, tree), f"the paired code commit {code_commit}"


def _rev_parse(repository: Path, revision: str) -> str | None:
    result = run_git(
        repository,
        ["rev-parse", "--verify", "--quiet", "--end-of-options", revision],
        GitRunnerOptions(timeout=GIT_METADATA_TIMEOUT_SECONDS),
    )
    value = result.stdout.strip()
    return value if result.returncode == 0 and value else None


def _holds(repository: Path, commit: str, path: str) -> bool:
    result = run_git(
        repository,
        ["cat-file", "-e", f"{commit}:{path}"],
        GitRunnerOptions(timeout=GIT_METADATA_TIMEOUT_SECONDS),
    )
    return result.returncode == 0


# --------------------------------------------------------------------------------------------------
# The selector's choices
# --------------------------------------------------------------------------------------------------


def selection_options(config: McpRuntimeConfig, repository_id: str) -> dict[str, Any]:
    """What the commit selector offers: the published tree, recent memory commits, live leaves."""

    try:
        scope = resolve_scope(config, repository_id, "mainline")
    except AuthorityError as error:
        return {"state": "unavailable", "repo": repository_id, "detail": str(error)}
    memory = scope.memory_root
    document: dict[str, Any] = {
        "state": "options",
        "repo": repository_id,
        "default": PUBLISHED,
        "published": {
            "commit": PUBLISHED,
            "memoryRoot": None if memory is None else str(memory),
            "converted": memory is not None and converted_memory_tree(memory) is not None,
        },
        **_commit_choices(memory),
        "leaves": _leaves(config, repository_id),
    }
    return document


def _commit_choices(memory: Path | None) -> dict[str, Any]:
    """The recent memory commits, or why they could not be listed (never an empty list instead)."""

    if memory is None:
        return {"commits": [], "commitsState": {"state": "absent", "detail": "no memory layer"}}
    try:
        return {"commits": _recent_commits(memory), "commitsState": {"state": "listed"}}
    except READ_FAILURES as error:
        detail = f"{type(error).__name__}: {error}"
        return {"commits": [], "commitsState": {"state": "unavailable", "detail": detail}}


def _recent_commits(repository: Path) -> list[dict[str, Any]]:
    result = run_git(
        repository,
        ["log", f"-{_RECENT_COMMITS}", "--format=%H%x1f%cI%x1f%s", "HEAD"],
        GitRunnerOptions(timeout=GIT_METADATA_TIMEOUT_SECONDS),
    )
    if result.returncode != 0:
        raise ReaderReadError(result.stderr.strip() or "git log failed")
    rows = [line.split("\x1f") for line in result.stdout.splitlines() if line.count("\x1f") == 2]
    probe = "".join(f"{sha}:{LAYOUT_MARKER_PATH}\n" for sha, _date, _subject in rows)
    checked = run_git(
        repository,
        ["cat-file", "--batch-check"],
        GitRunnerOptions(input_text=probe, timeout=GIT_METADATA_TIMEOUT_SECONDS),
    )
    if checked.returncode != 0:
        raise ReaderReadError(checked.stderr.strip() or "git cat-file --batch-check failed")
    marks = checked.stdout.splitlines()
    return [
        {
            "commit": sha,
            "date": date,
            "subject": subject,
            "converted": index < len(marks) and not marks[index].endswith(" missing"),
        }
        for index, (sha, date, subject) in enumerate(rows)
    ]


def _leaves(config: McpRuntimeConfig, repository_id: str) -> list[dict[str, Any]]:
    """The active leaf enclosures of ``repository_id``, read now (the files catalog's filter)."""

    leaves = []
    for path in iter_leaf_enclosure_contracts(config.coordination_root / "tasks"):
        try:
            contract = load_contract(path)
        except (ContractError, OSError):
            continue
        if is_foreign_enclosure_contract(path, contract.task_artifact):
            logger.warning(
                "knowledge selection: skipping enclosure contract whose task "
                "document lies outside its repository tasks: %s",
                path.as_posix(),
            )
            continue
        if (
            contract.repo_name != repository_id
            or contract.cleanup == "abandoned"
            or not contract.code_worktree.exists()
        ):
            continue
        leaves.append(
            {
                "commit": f"{LEAF_PREFIX}{contract.worktree_group.name}",
                "leafId": contract.leaf_id,
                "taskName": contract.task_name,
                "branch": contract.code_work_branch,
            }
        )
    return leaves
