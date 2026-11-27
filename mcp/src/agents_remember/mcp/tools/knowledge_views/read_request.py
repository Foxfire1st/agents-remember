"""What one ``knowledge_read`` call names, and the code source it observes.

:class:`ReadToolRequest` is the call's arguments as one value; :func:`_observe_source` resolves the
code tree the read answers at (capturing a working tree when it differs from its commit) and
:class:`_ObservedSource` holds that capture until the read has answered. :func:`_refused_read` is
the one shape of a read's typed refusal, shared by every view's adapter.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agents_remember.application.worktree_code_state import (
    CODE_STATE_COMMIT,
    CODE_STATE_WORKING_TREE,
    WORKING_TREE_SELECTOR,
    CodeState,
    WorktreeCapture,
    capture_worktree,
    hold_observed_code_tree,
)
from agents_remember.memory.knowledge_index import (
    INDEX_REPOSITORY_ID,
)


@dataclass(frozen=True)
class ReadToolRequest:
    """One ``knowledge_read`` call's arguments as one value.

    The tool signature stays flat because MCPServer derives the published input schema from it; this
    value is what the builder consumes.

    ``memory_root`` is the *selection*, and the caller owns it: the root directory of a converted
    memory tree (``memoryTree.memoryRoot`` of a published-intent block, a leaf's memory worktree, or
    the repository's memory root). ``repository_id`` is not a wire argument: a memory tree's index
    has one constant namespace (:data:`INDEX_REPOSITORY_ID`), which the server supplies and every
    response states.

    ``record_id`` is a record's text ID (``INV-…``, ``FAM-…``), accepted by the ``invariant`` and
    ``family`` views beside the revision UUIDs (MIK-R73 rule 3): the UUIDs are derived from the
    text IDs and are stored nowhere, so a caller may name either.
    """

    memory_root: str
    view: str
    ordering_input: str | None = None
    limit: int = 32
    continuation: str | None = None
    invariant_revision_id: str | None = None
    family_revision_id: str | None = None
    record_id: str | None = None
    source_path: str | None = None
    repository_root: str | None = None
    code_tree_id: str | None = None
    repository_id: str = INDEX_REPOSITORY_ID
    weakness_kind: str | None = None
    include_closed: bool = False
    stale_only: bool = False
    stands_on: str | None = None
    repo_id: str | None = None
    contract_path: str | None = None
    files: tuple[str, ...] | None = None
    folder: str | None = None
    query: str | None = None
    kinds: tuple[str, ...] | None = None
    record_ids: tuple[str, ...] | None = None
    compact: bool = False
    master_id: str | None = None


def _refused_read(view: str, repository_id: str, code: str, detail: str) -> dict[str, Any]:
    return {
        "ok": True,
        "state": "refused",
        "view": view,
        "repositoryId": repository_id,
        "refusalCode": code,
        "refusalDetail": detail,
    }


class _NoCodeTreeError(ValueError):
    """A named ``repositoryRoot`` resolves no tree: it has no commit, or it is not a repository.

    The read context refuses a root without a tree, so this was a raw ``ValidationError`` out of the
    tool (carried to L01 from L02); it is now the named refusal below.
    """

    def __init__(self, repository_root: str) -> None:
        super().__init__(
            f"repositoryRoot {repository_root} resolves no code tree to read anchors at (it has no "
            "commit, or it is not a Git repository); name a repository with a commit, add "
            "codeTreeId, or omit repositoryRoot"
        )


@dataclass
class _ObservedSource:
    """The code source one read observes, and any working-tree capture it must hold until it answers.

    A capture is a scratch store holding the working tree's blobs: it has to outlive the whole read
    (the page's anchors and currentness are observed while the answer is built) and be removed
    afterwards, so the object that observed it owns its removal.
    """

    repository_root: str | None
    code_tree_id: str | None
    code_state: CodeState | None
    capture: WorktreeCapture | None = None

    def close(self) -> None:
        if self.capture is not None:
            self.capture.close()
            self.capture = None


def _observe_source(request: ReadToolRequest, workspace_root: str | None) -> _ObservedSource:
    """Resolve the code source one read observes, capturing its working tree when it differs.

    Three shapes, one rule -- the read answers at the bytes the caller sees (MIK-R73 rule 7):

    * ``codeTreeId: "working-tree"`` with ``repositoryRoot``: capture that checkout and read it;
    * a named tree and root: read exactly that tree, as before;
    * a root alone (or the mount's workspace default): the commit when the checkout is clean, else
      its captured working tree.

    The capture is returned, not closed: the read that asked for it holds it while it observes.
    """

    named_tree = request.code_tree_id
    root = request.repository_root
    if named_tree == WORKING_TREE_SELECTOR:
        target = root if root is not None else workspace_root
        if target is None:
            raise _NoCodeTreeError("the working-tree selector with no repository root")
        capture = capture_worktree(Path(target))
        return _ObservedSource(target, capture.tree_id, CODE_STATE_WORKING_TREE, capture)
    if named_tree is not None and root is not None:
        return _ObservedSource(root, named_tree, CODE_STATE_COMMIT)
    if root is None:
        if named_tree is not None or workspace_root is None:
            return _ObservedSource(None, None, None)
        # The mount's own default: it supplies a repository when it has one, and resolves nothing
        # when it has none -- a workspace that is not a checkout is not a caller's mistake.
        observed, capture = hold_observed_code_tree(Path(workspace_root))
        if observed is None:
            return _ObservedSource(None, None, None)
        return _ObservedSource(workspace_root, observed.tree_id, observed.code_state, capture)
    observed, capture = hold_observed_code_tree(Path(root))
    if observed is None:
        raise _NoCodeTreeError(root)
    return _ObservedSource(root, observed.tree_id, observed.code_state, capture)
