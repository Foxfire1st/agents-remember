"""A leaf's worklist: its four sides, its run, and the persisted ``knowledge-worklist/v1`` file.

**Sides (MIK-R07 rule 0).**

* **B** -- the contract's code base commit: the official-line commit the leaf last synced from (a
  managed sync advances it), or its fork point before any sync.
* **K_B** -- the memory tree of the most recent commit of the official memory line
  (``memory_source_branch``) whose ``Code-Commit`` trailer names B or an ancestor of B, read through
  ``kernel/memory_attribution.py``. When K_B is unconverted and K_C is converted, K_B is its
  conversion: MIK-R24 rule 7's converted base, at the base's own paired code commit
  (:class:`GitBaseConverter`).
* **C** -- the code worktree captured as a tree, uncommitted changes included, through the shipped
  private-index capture.
* **K_C** -- the memory worktree captured the same way (the index's directory capture).

The worklist records the pairing it used. A side that cannot be read, or a K_B that no memory commit
pairs with, makes the run ``incomplete`` naming it (MIK-R08 rule 4).

**Applicability.** The worklist exists where K_B or K_C holds the layout marker (MIK-R09 rule 6). A
leaf whose two memory sides are both unconverted gets no worklist: before the cutover (MIK-R37) that
is every production leaf, so nothing changes for them.

**Persistence (rule 7).** The document is written to ``knowledge-worklist.json`` in the leaf's
enclosure directory under the task root -- the leaf's durable task-artifact location, beside its
series contract -- and never into the memory repository. Each run replaces it, so the file is the
latest worklist; :func:`read_leaf_worklist` is what ``knowledge_integrity_check`` returns.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Final

from agents_remember.application.knowledge_worklist.base_cache import (
    converted_base_files,
    default_base_cache_directory,
)
from agents_remember.application.knowledge_worklist.code import CodeReadError, CodeTrees
from agents_remember.application.knowledge_worklist.compute import (
    Incomplete,
    WorklistInputs,
    compute_worklist,
    incomplete_worklist,
)
from agents_remember.application.knowledge_worklist.knowledge import (
    KnowledgeSide,
    KnowledgeSideUnreadable,
)
from agents_remember.application.knowledge_worklist.onboarding_trace import (
    TraceSideRequest,
    onboarding_trace_sides,
    worklist_onboarding,
)
from agents_remember.kernel.atomic_write import atomic_write_text
from agents_remember.kernel.git_command import (
    GIT_METADATA_TIMEOUT_SECONDS,
    GitRunnerOptions,
    run_git,
)
from agents_remember.kernel.memory_attribution import MemoryAttributionError, attributed_commits
from agents_remember.memory.conversion.base import (
    pinned_version,
)
from agents_remember.memory.knowledge_index import (
    MemoryTreeError,
    MemoryTreeSnapshot,
    directory_snapshot,
    git_tree_snapshot,
)
from agents_remember.memory_quality.knowledge_validator.trees import KnowledgeTree
from agents_remember.memory_quality.knowledge_worklist_section import worklist_summary
from agents_remember.models.knowledge_files.documents import LAYOUT_MARKER_PATH
from agents_remember.tasks.leaf_doc import find_leaf_doc
from agents_remember.worktrees.modules.git import worktree_candidate_tree
from agents_remember.worktrees.modules.onboarding_trace import OnboardingTraceSides
from agents_remember.worktrees.worktree_contract import WorktreeContract

__all__ = [
    "WORKLIST_FILE_NAME",
    "ExplicitSides",
    "LeafWorklistRecompute",
    "leaf_maintenance_scope",
    "leaf_onboarding_trace_sides",
    "leaf_worklist",
    "persist_worklist",
    "read_leaf_worklist",
    "recompute_leaf_worklist",
    "worklist_for_sides",
    "worklist_path",
]

WORKLIST_FILE_NAME: Final = "knowledge-worklist.json"


class _Unreadable(Exception):
    def __init__(self, name: str, detail: str) -> None:
        super().__init__(detail)
        self.missing = Incomplete(name, detail)


def worklist_path(contract: WorktreeContract) -> Path | None:
    """Where a leaf's latest worklist lives: beside its series contract, in its enclosure."""

    if contract.kind != "leaf":
        return None
    return contract.contract_path.parent / WORKLIST_FILE_NAME


def persist_worklist(path: Path, document: dict[str, Any]) -> None:
    atomic_write_text(
        path, json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    )


def read_leaf_worklist(contract_path: Path) -> dict[str, Any] | None:
    """The latest persisted worklist beside the contract at ``contract_path``, or ``None``."""

    path = contract_path.parent / WORKLIST_FILE_NAME
    if not path.is_file():
        return None
    loaded = json.loads(path.read_text(encoding="utf-8"))
    return loaded if isinstance(loaded, dict) else None


def leaf_maintenance_scope(contract: WorktreeContract) -> bool:
    """Whether the leaf's task document sets ``knowledgeMaintenanceScope: true``."""

    found = find_leaf_doc(contract.task_root, contract.leaf_id or contract.task_name)
    return bool(found is not None and found[1].knowledgeMaintenanceScope)


@dataclass(frozen=True)
class ExplicitSides:
    """The four sides named directly (the command line, and evidence runs on scratch copies).

    ``memory_candidate`` is a memory working tree (a directory) or a Git revision of
    ``memory_repository``; ``code_candidate`` is a Git revision of ``code_repository`` or ``None`` to
    capture ``code_worktree``.
    """

    code_repository: Path
    base: str
    memory_repository: Path
    memory_base: str
    memory_candidate: str | Path
    code_candidate: str | None = None
    code_worktree: Path | None = None
    maintenance_scope: bool = False
    owner: str | None = None
    cache_directory: Path | None = None
    """Where converted bases are cached (:mod:`.base_cache`); ``None`` converts on every run."""


def _git(repository: Path, *args: str) -> str | None:
    result = run_git(repository, list(args), GitRunnerOptions(timeout=GIT_METADATA_TIMEOUT_SECONDS))
    value = result.stdout.strip()
    return value if result.returncode == 0 and value else None


def _tree(repository: Path, revision: str, name: str) -> str:
    tree = _git(repository, "rev-parse", "--verify", "--quiet", f"{revision}^{{tree}}")
    if tree is None:
        raise _Unreadable(name, f"{revision!r} names no tree in {repository}")
    return tree


def _commit(repository: Path, revision: str, name: str) -> str:
    commit = _git(repository, "rev-parse", "--verify", "--quiet", f"{revision}^{{commit}}")
    if commit is None:
        raise _Unreadable(name, f"{revision!r} names no commit in {repository}")
    return commit


def _captured_code(worktree: Path) -> str:
    try:
        with TemporaryDirectory(prefix="ar-knowledge-worklist-") as scratch:
            return worktree_candidate_tree(worktree, Path(scratch) / "index")
    except (RuntimeError, OSError) as error:
        raise _Unreadable(
            "C", f"the code worktree {worktree} cannot be captured: {error}"
        ) from error


def _memory_snapshot(repository: Path, where: str | Path, name: str) -> MemoryTreeSnapshot:
    try:
        if isinstance(where, Path):
            return directory_snapshot(where)
        return git_tree_snapshot(repository, where)
    except (MemoryTreeError, OSError, ValueError) as error:
        raise _Unreadable(name, f"the memory tree {where} cannot be read: {error}") from error


def paired_memory_commit(
    memory_repository: Path, official_line: str, code_repository: Path, base: str
) -> str:
    """K_B's commit: the newest official-line memory commit whose trailer names B or an ancestor."""

    ancestors = _git(code_repository, "rev-list", base)
    reachable = set((ancestors or "").split())
    try:
        history = attributed_commits(memory_repository, tip=official_line)
    except MemoryAttributionError as error:
        raise _Unreadable("pairing", str(error)) from error
    for commit in history:
        named = commit.code_commit
        if named is None:
            continue
        full = (
            named
            if named in reachable
            else _git(code_repository, "rev-parse", "--verify", "--quiet", f"{named}^{{commit}}")
        )
        if full is not None and full in reachable:
            return commit.memory_commit
    raise _Unreadable(
        "pairing",
        f"no commit of the official memory line {official_line!r} carries a Code-Commit trailer "
        f"naming the code base {base} or one of its ancestors",
    )


@dataclass(frozen=True)
class _Resolved:
    code: CodeTrees
    base: KnowledgeSide
    candidate: KnowledgeSide
    pairing: dict[str, Any]


def _sides(
    sides: ExplicitSides, memory_base_commit: str, candidate_snapshot: MemoryTreeSnapshot
) -> _Resolved | None:
    base_commit = _commit(sides.code_repository, sides.base, "B")
    base_tree = _tree(sides.code_repository, base_commit, "B")
    if sides.code_candidate is not None:
        candidate_tree = _tree(sides.code_repository, sides.code_candidate, "C")
    elif sides.code_worktree is not None:
        candidate_tree = _captured_code(sides.code_worktree)
    else:
        raise _Unreadable("C", "no code candidate was named")
    base_snapshot = _memory_snapshot(sides.memory_repository, memory_base_commit, "K_B")
    candidate_tree_files = KnowledgeTree(label="K_C", files=candidate_snapshot.files)
    base_files = KnowledgeTree(label="K_B", files=base_snapshot.files)
    if not base_files.converted and not candidate_tree_files.converted:
        return None
    converted_base = not base_files.converted
    try:
        if converted_base:
            base = _converted_base_side(
                sides, memory_base_commit, base_commit, candidate_tree_files, base_snapshot.key
            )
        else:
            base = KnowledgeSide.from_snapshot("K_B", base_snapshot)
        candidate = KnowledgeSide.from_snapshot("K_C", candidate_snapshot)
    except KnowledgeSideUnreadable as error:
        raise _Unreadable(error.label, str(error)) from error
    except (ValueError, OSError) as error:
        raise _Unreadable("K_B", f"the converted base cannot be produced: {error}") from error
    pairing = {
        "codeRepository": str(sides.code_repository),
        "base": {"commit": base_commit, "tree": base_tree},
        "candidate": {"tree": candidate_tree},
        "memoryRepository": str(sides.memory_repository),
        "memoryBase": {
            "commit": memory_base_commit,
            "tree": base_snapshot.key,
            "convertedBase": converted_base,
            "conversion": pinned_version(candidate_tree_files) if converted_base else None,
        },
        "memoryCandidate": {
            "tree": candidate_snapshot.key,
            "location": candidate_snapshot.location,
        },
    }
    code = CodeTrees.open(sides.code_repository, base_tree, candidate_tree)
    return _Resolved(code, base, candidate, pairing)


def _converted_base_side(
    sides: ExplicitSides,
    memory_base_commit: str,
    base_commit: str,
    candidate: KnowledgeTree,
    base_key: str,
) -> KnowledgeSide:
    """K_B as its conversion (MIK-R24 rule 7), read from the converted-base cache when present.

    :func:`converted_base_files` chooses the code commit (K_B's own ``Code-Commit`` trailer when the
    code store holds it, B otherwise) and keys the cache on (K_B commit, version, that commit).
    """

    version = pinned_version(candidate)
    if version is None:
        raise ValueError("the candidate is not converted, so there is no version to pin")
    files = converted_base_files(
        sides.memory_repository,
        memory_base_commit,
        code=(sides.code_repository, base_commit),
        version=version,
        cache_directory=sides.cache_directory,
    )
    label = f"converted:{base_key}"
    return KnowledgeSide.from_tree("K_B", label, KnowledgeTree(label=label, files=files))


def worklist_for_sides(sides: ExplicitSides) -> dict[str, Any] | None:
    """The worklist over explicitly named sides; ``None`` when both memory sides are unconverted."""

    try:
        candidate = _memory_snapshot(sides.memory_repository, sides.memory_candidate, "K_C")
        resolved = _sides(
            sides, _commit(sides.memory_repository, sides.memory_base, "K_B"), candidate
        )
    except _Unreadable as error:
        return incomplete_worklist(error.missing, owner=sides.owner, pairing=None)
    except CodeReadError as error:
        return incomplete_worklist(Incomplete("C", str(error)), owner=sides.owner, pairing=None)
    if resolved is None:
        return None
    return compute_worklist(
        WorklistInputs(
            code=resolved.code,
            base=resolved.base,
            candidate=resolved.candidate,
            pairing=resolved.pairing,
            maintenance_scope=sides.maintenance_scope,
            owner=sides.owner,
        )
    )


def leaf_worklist(contract: WorktreeContract, *, persist: bool = True) -> dict[str, Any] | None:
    """Compute (and persist) a leaf's worklist from its contract; ``None`` where it does not apply.

    It does not apply to a non-leaf contract, to a leaf without its own memory worktree, or to a leaf
    whose two memory sides are both unconverted.
    """

    if contract.kind != "leaf" or contract.memory_worktree is None:
        return None
    memory_repository = contract.memory_repo_path or contract.memory_worktree
    owner = contract.leaf_id or None
    if not (contract.memory_worktree / LAYOUT_MARKER_PATH).is_file() and not _official_converted(
        memory_repository, contract.memory_source_branch
    ):
        return None  # cheap applicability probe before any capture: nothing is converted
    try:
        memory_base = paired_memory_commit(
            memory_repository,
            contract.memory_source_branch,
            contract.code_repo_path,
            contract.code_base_commit,
        )
    except _Unreadable as error:
        document: dict[str, Any] | None = incomplete_worklist(
            error.missing, owner=owner, pairing=None
        )
    else:
        cache = default_base_cache_directory(contract.coordination_root)
        document = worklist_for_sides(
            ExplicitSides(
                code_repository=contract.code_repo_path,
                base=contract.code_base_commit,
                memory_repository=memory_repository,
                memory_base=memory_base,
                memory_candidate=contract.memory_worktree,
                code_worktree=contract.code_worktree,
                maintenance_scope=leaf_maintenance_scope(contract),
                owner=owner,
                cache_directory=cache,
            )
        )
        if document is not None:
            # MIK-R30's items join the one list (ruling Q2), over the worklist's own B..C paths.
            document = worklist_onboarding(
                document, contract, _trace_request(contract, memory_repository, memory_base)
            )
    path = worklist_path(contract)
    if persist and document is not None and path is not None:
        persist_worklist(path, document)
    return document


def _trace_request(
    contract: WorktreeContract,
    memory_repository: Path,
    memory_base: str,
    memory_tree: Path | None = None,
) -> TraceSideRequest:
    candidate = memory_tree if memory_tree is not None else contract.memory_worktree
    assert candidate is not None
    return TraceSideRequest(
        owner=contract.leaf_id or contract.task_name,
        memory_repository=memory_repository,
        memory_base=memory_base,
        memory_candidate=Path(candidate),
        code_repository=contract.code_repo_path,
        code_base=contract.code_base_commit,
        cache_directory=default_base_cache_directory(contract.coordination_root),
    )


def leaf_onboarding_trace_sides(
    contract: WorktreeContract, *, memory_tree: Path | None = None
) -> OnboardingTraceSides | None:
    """A leaf's MIK-R30 gate sides from its contract; ``None`` where today's gate still applies.

    Today's gate applies to a non-leaf contract, a leaf without its own memory worktree, and a leaf
    whose K_B and K_C are both unconverted (every production leaf before MIK-R37). K_B pairs exactly
    as the worklist's does. A side that cannot be established is an ``incomplete`` side, which the
    gate reports as a finding, never a pass.
    """

    candidate = memory_tree if memory_tree is not None else contract.memory_worktree
    if contract.kind != "leaf" or candidate is None:
        return None
    memory_repository = contract.memory_repo_path or contract.memory_worktree
    if memory_repository is None:
        return None
    if not (Path(candidate) / LAYOUT_MARKER_PATH).is_file() and not _official_converted(
        memory_repository, contract.memory_source_branch
    ):
        return None  # cheap probe before any read: nothing is converted, today's gate applies
    owner = contract.leaf_id or contract.task_name
    try:
        memory_base = paired_memory_commit(
            memory_repository,
            contract.memory_source_branch,
            contract.code_repo_path,
            contract.code_base_commit,
        )
        return onboarding_trace_sides(
            _trace_request(contract, memory_repository, memory_base, Path(candidate))
        )
    except Exception as error:  # an unestablished side is a finding, never a lapsed gate
        return OnboardingTraceSides(owner=owner, incomplete=f"{type(error).__name__}: {error}")


def _official_converted(memory_repository: Path, official_line: str) -> bool:
    """Whether the official memory line's tip holds the layout marker (K_B may then be converted)."""

    if not official_line:
        return False
    return _git(memory_repository, "cat-file", "-t", f"{official_line}:{LAYOUT_MARKER_PATH}") == (
        "blob"
    )


def recompute_leaf_worklist(
    contract: WorktreeContract,
) -> tuple[dict[str, Any], str | None] | None:
    """The one recompute entry point (MIK-R08 rule 8): compute, persist, and never raise.

    Every trigger calls this: the curator's memory-quality run, managed-sync completion, and the
    gate's closeout and landing evaluations (MIK-R09). It returns the document and where it was
    persisted (``None`` when the enclosure cannot be written), or ``None`` where no worklist
    applies. A failure the run did not anticipate is still a
    worklist -- the ``incomplete`` one naming the run itself, persisted -- never a silently missing
    list (rule 4) and never a failure of the route that triggered it.
    """

    if contract.kind != "leaf":
        return None
    try:
        document = leaf_worklist(contract, persist=False)
    except Exception as error:  # the run's own failure is an incomplete worklist, named
        document = incomplete_worklist(
            Incomplete("worklist run", f"{type(error).__name__}: {error}"),
            owner=contract.leaf_id or None,
            pairing=None,
        )
    if document is None:
        return None
    path = worklist_path(contract)
    if path is not None:
        try:
            persist_worklist(path, document)
        except OSError:
            path = None  # an unwritable enclosure: the worklist is returned, not persisted
    return document, None if path is None else path.as_posix()


@dataclass(frozen=True)
class LeafWorklistRecompute:
    """The composition-bound :class:`KnowledgeWorklistPort` of the worktree layer."""

    def recompute(self, contract: WorktreeContract) -> dict[str, Any] | None:
        found = recompute_leaf_worklist(contract)
        if found is None:
            return None
        document, path = found
        return worklist_summary(document, path)
