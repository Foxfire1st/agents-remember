"""The gate at direct landing (MIK-R09 rule 3): the branch-addressed leaf closeout.

A direct landing publishes a leaf implemented on the series branch itself, without its own worktree
enclosure, so its sides are read from the series lines:

* **K_B** is the series memory line's head, the commit this landing's memory commit will follow;
* **B** is the code commit K_B's ``Code-Commit`` trailer names -- the code the line last published
  -- which must be an ancestor of the landed code commit;
* **C** is the verified code commit's tree, and **K_C** the memory checkout's exact candidate tree.

**The leaf.** The route names no leaf, so the leaf is the owner of the one open leaf history file in
K_C (``knowledge/history/<leaf-id>.json`` with ``closed: false``). None, or more than one, refuses:
a direct-mode leaf of a converted repository records its rows -- or an empty ``rows`` -- in its own
file, which this landing then closes. This is a choice the packet leaves open (see the L09 report).

A replay of an already published landing (the checkout is clean at a head whose trailer names this
code commit) commits nothing, so it is not gated again.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agents_remember.application.knowledge_gate.gate import GateTrees, judge
from agents_remember.application.knowledge_worklist.base_cache import (
    default_base_cache_directory,
)
from agents_remember.application.knowledge_worklist.compute import (
    Incomplete,
    git_failure,
    incomplete_worklist,
)
from agents_remember.application.knowledge_worklist.leaf import ExplicitSides, worklist_over
from agents_remember.application.knowledge_worklist.planned_effects import declarations_from
from agents_remember.kernel.git_command import (
    GIT_METADATA_TIMEOUT_SECONDS,
    GitRunnerOptions,
    run_git,
)
from agents_remember.memory.conversion.base import own_paired_code_commit
from agents_remember.memory_quality.knowledge_validator.trees import knowledge_tree_from_git
from agents_remember.models.knowledge_files.canonical import (
    CanonicalFormatError,
    canonical_text,
    parse_json,
)
from agents_remember.models.knowledge_files.history import HISTORY_SCHEMA
from agents_remember.tasks.leaf_decisions import LeafDocumentUnresolved, strict_leaf_doc
from agents_remember.worktrees.services import DirectGateSource, DirectGateVerdict
from agents_remember.worktrees.worktree_contract import WorktreeContract

__all__ = ["direct_source", "direct_verdict"]

_HISTORY_PREFIX = "knowledge/history/"


def _git(repository: Path, *args: str) -> str | None:
    result = run_git(repository, list(args), GitRunnerOptions(timeout=GIT_METADATA_TIMEOUT_SECONDS))
    value = result.stdout.strip()
    return value if result.returncode == 0 and value else None


def _open_histories(memory_repository: Path, memory_tree: str) -> list[tuple[str, str, bytes]]:
    tree = knowledge_tree_from_git(memory_repository, memory_tree)
    histories = []
    for path, data in sorted(tree.files.items()):
        if not (path.startswith(_HISTORY_PREFIX) and path.endswith(".json")):
            continue
        try:
            document: Any = parse_json(data.decode("utf-8"))
        except (UnicodeDecodeError, CanonicalFormatError):
            continue  # the validator names a file that does not parse
        if (
            isinstance(document, dict)
            and document.get("schema") == HISTORY_SCHEMA
            and document.get("closed") is False
            and isinstance(document.get("leaf"), str)
        ):
            histories.append((document["leaf"], path, data))
    return histories


def direct_source(
    contract: WorktreeContract, *, code_commit: str, memory_tree: str
) -> DirectGateVerdict:
    """Bind the unique open history source before the route closes it; run no worklist gate."""
    memory = contract.memory_repo_path
    if memory is None:
        return DirectGateVerdict(True, None, "direct landing has no memory repository to gate")
    try:
        return _source(memory, code_commit, memory_tree)
    except subprocess.SubprocessError as error:
        detail = git_failure(error).detail
        return DirectGateVerdict(True, None, f"the mandatory invariant gate (MIK-R09): {detail}")


def direct_verdict(
    contract: WorktreeContract,
    *,
    code_commit: str,
    memory_tree: str,
    source: DirectGateSource | None = None,
) -> DirectGateVerdict:
    """The gate's verdict over a direct landing's exact candidate (the caller probed the marker)."""

    memory = contract.memory_repo_path
    if memory is None:
        return DirectGateVerdict(True, None, "direct landing has no memory repository to gate")
    try:
        selected = _source(
            memory, code_commit, memory_tree if source is None else source.memory_tree
        )
        if selected.refusal is not None:
            return selected
        if source is not None:
            refusal = _closed_source_refusal(memory, memory_tree, selected.source, source)
            if refusal is not None:
                return DirectGateVerdict(True, None, refusal)
        elif not selected.applies:
            return selected
        assert selected.source is not None
        return _verdict(contract, memory, code_commit, memory_tree, selected.source)
    except subprocess.SubprocessError as error:
        detail = git_failure(error).detail
        return DirectGateVerdict(True, None, f"the mandatory invariant gate (MIK-R09): {detail}")


def _source(memory: Path, code_commit: str, memory_tree: str) -> DirectGateVerdict:
    head = _git(memory, "rev-parse", "--verify", "--quiet", "HEAD^{commit}")
    if head is None:
        return DirectGateVerdict(True, None, "the series memory line has no head to land on")
    published = own_paired_code_commit(memory, head)
    if published == code_commit and _git(memory, "rev-parse", f"{head}^{{tree}}") == memory_tree:
        return DirectGateVerdict(False, None, None)  # a replay commits nothing
    histories = _open_histories(memory, memory_tree)
    if len(histories) != 1:
        owners = [owner for owner, _path, _data in histories]
        return DirectGateVerdict(
            True,
            None,
            "the mandatory invariant gate (MIK-R09) refuses this direct landing: it names no leaf, "
            f"and the memory candidate holds {len(owners)} open leaf history file(s) "
            f"({', '.join(owners) or 'none'}); exactly one, knowledge/history/<leaf-id>.json with "
            "closed: false, names the leaf whose rows this landing publishes and closes",
        )
    owner, path, data = histories[0]
    return DirectGateVerdict(
        True,
        owner,
        None,
        DirectGateSource(memory.resolve(), head, memory_tree, code_commit, owner, path, data),
    )


def _closed_source_refusal(
    memory: Path,
    memory_tree: str,
    selected: DirectGateSource | None,
    source: DirectGateSource,
) -> str | None:
    """Require the same source namespace and rows, with only its closing flag changed."""
    if selected != source:
        return "the direct landing's selected history source or memory head changed; retry the gate"
    original = parse_json(source.history_bytes.decode("utf-8"))
    assert isinstance(original, dict)
    closed = canonical_text({**original, "closed": True}).encode("utf-8")
    files = knowledge_tree_from_git(memory, memory_tree).files
    if files.get(source.history_path) != closed:
        return (
            f"the direct landing's closed candidate changed the selected history source "
            f"{source.history_path}; only its closing flag may change before the gate"
        )
    if _open_histories(memory, memory_tree):
        return (
            "the closed direct-landing candidate contains another open leaf history; retry the gate"
        )
    return None


def _verdict(
    contract: WorktreeContract,
    memory: Path,
    code_commit: str,
    memory_tree: str,
    source: DirectGateSource,
) -> DirectGateVerdict:
    owner, head = source.owner, source.memory_head
    published = own_paired_code_commit(memory, head)
    document = _worklist(contract, _DirectSides(owner, head, published, code_commit, memory_tree))
    trees = GateTrees(
        code_repository=contract.code_repo_path,
        code_tree=code_commit,
        memory_repository=memory,
        memory_tree=memory_tree,
        validation_bases=(head,),
        base_code_commit=published or code_commit,
        cache_directory=default_base_cache_directory(contract.coordination_root),
    )
    result = judge(document, None, trees, owner)
    return DirectGateVerdict(True, owner, result.refusal())


@dataclass(frozen=True)
class _DirectSides:
    owner: str
    head: str
    published: str | None
    code_commit: str
    memory_tree: str


def _worklist(contract: WorktreeContract, direct: _DirectSides) -> dict[str, Any]:
    owner, head, published = direct.owner, direct.head, direct.published
    code_commit, memory_tree = direct.code_commit, direct.memory_tree
    if published is None or not _is_ancestor(contract.code_repo_path, published, code_commit):
        return incomplete_worklist(
            Incomplete(
                "pairing",
                f"the series memory head {head} names no code commit ({published}) that is an "
                f"ancestor of the landed code commit {code_commit}",
            ),
            owner=owner,
            pairing=None,
        )
    try:
        found = strict_leaf_doc(contract.task_root, owner)
        sides = ExplicitSides(
            code_repository=contract.code_repo_path,
            base=published,
            memory_repository=contract.memory_repo_path or contract.code_repo_path,
            memory_base=head,
            memory_candidate=memory_tree,
            code_candidate=code_commit,
            maintenance_scope=bool(found is not None and found[1].knowledgeMaintenanceScope),
            owner=owner,
            cache_directory=default_base_cache_directory(contract.coordination_root),
            expected_effects=None
            if found is None
            else declarations_from(found[1].expectedKnowledgeEffects),
            coordination_root=contract.coordination_root,
        )
        document = worklist_over(contract, sides)
    except LeafDocumentUnresolved as error:
        return incomplete_worklist(
            Incomplete("leaf task document", str(error)), owner=owner, pairing=None
        )
    except subprocess.SubprocessError as error:
        return incomplete_worklist(git_failure(error), owner=owner, pairing=None)
    except Exception as error:  # the run's own failure blocks, named
        return incomplete_worklist(
            Incomplete("worklist run", f"{type(error).__name__}: {error}"),
            owner=owner,
            pairing=None,
        )
    if document is None:  # both sides unconverted: the caller's probe said otherwise
        return incomplete_worklist(
            Incomplete("K_C", "neither memory side holds the layout marker"),
            owner=owner,
            pairing=None,
        )
    return document


def _is_ancestor(repository: Path, ancestor: str, commit: str) -> bool:
    result = run_git(
        repository,
        ["merge-base", "--is-ancestor", ancestor, commit],
        GitRunnerOptions(timeout=GIT_METADATA_TIMEOUT_SECONDS),
    )
    return result.returncode == 0
