"""Rule 13 and rule 14 landing-publication policy, apart from the chain proof.

MIK-R202@v2 adds a second way for the master line to move: a leaf lands by AR integration and is
then published as a pull request whose merge brings the integrated commit onto the master line. The
closeout proof has to admit that merge without admitting a contributor's merge, a squash, or a merge
that carries content of its own.

This module holds that policy as a predicate over facts the caller has already read, plus the one
reader that turns a merge commit into those facts. It sits beside ``series_closeout`` rather than
inside it so the chain proof keeps its size while the rule stays legible on its own; the proof
imports these names and keeps its public seam.
"""

from __future__ import annotations

from pathlib import Path

from agents_remember.kernel.coordination_context.models import (
    LANDING_WORKFLOW_LOCAL,
    LANDING_WORKFLOW_PULL_REQUEST,
)
from agents_remember.kernel.coordination_context.settings import parse_landing_settings
from agents_remember.models.lifecycles.operation import LifecycleOperationRecord
from agents_remember.worktrees.integration.lifecycle.lifecycle_enclosure_terminal import (
    terminal_cleanup_contract_authority,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_location import (
    inspect_lifecycle_operation_locator,
    located_lifecycle_operation_store,
)
from agents_remember.worktrees.modules.git import require_git

__all__ = [
    "LANDING_WORKFLOW_LOCAL",
    "LANDING_WORKFLOW_PULL_REQUEST",
    "PublicationMergeUnreadable",
    "_archived_authority_range",
    "landing_workflow",
    "leaf_authority_range",
    "publication_merge_admitted",
    "publication_merge_is_admitted",
    "publication_ranges",
]


class PublicationMergeUnreadable(RuntimeError):
    """A merge that cannot be read is refused as an input, never admitted or condemned."""


def landing_workflow(contract: object) -> str:
    """The landing workflow this series lands under (MIK-R202@v2 rule 13).

    The one value governs the repository's code and memory pair, so it is read once from the pair's
    memory settings. A series whose memory worktree is unknown, absent or still on markdown settings
    lands locally: a missing key means ``local``, and this proof must never invent a workflow.
    """

    worktree = getattr(contract, "memory_worktree", None)
    if worktree is None:
        return LANDING_WORKFLOW_LOCAL
    settings_path = Path(worktree) / "system" / "settings.json"
    return parse_landing_settings(settings_path).workflow


def publication_merge_is_admitted(
    *,
    workflow: str,
    parents: tuple[str, ...],
    merge_tree: str,
    second_parent_tree: str,
    authority_ranges: tuple[tuple[str, str, str], ...],
) -> bool:
    """Whether one merge is the publication of a leaf's landing (MIK-R202@v2 rule 14).

    The merge is admitted when the repository lands by pull request, it has exactly two parents,
    those parents are the recorded position B and the integrated landing commit H of a leaf this
    proof already admits on this side, and its tree equals H's tree. Anything else -- a contributor's
    merge, a squash, a merge with content of its own, a merge of the wrong pair -- is not admitted,
    so it falls through to the refusal this proof has always made.
    """

    if workflow != LANDING_WORKFLOW_PULL_REQUEST:
        return False
    if len(parents) != 2:
        return False
    first, second = parents
    if second_parent_tree != merge_tree:
        return False
    return any(
        base == first and integrated == second for _leaf_id, base, integrated in authority_ranges
    )


def publication_merge_admitted(
    repository: Path,
    merge: str,
    *,
    workflow: str,
    authority_ranges: tuple[tuple[str, str, str], ...],
) -> bool:
    """Read one merge's parents and tree, then judge it by the publication rule above."""

    if workflow != LANDING_WORKFLOW_PULL_REQUEST:
        return False
    try:
        listed = require_git(repository, ["rev-list", "--parents", "-n", "1", merge]).split()
    except RuntimeError as error:
        raise PublicationMergeUnreadable(
            f"cannot read the parents of merge {merge} ({error})"
        ) from error
    parents = tuple(listed[1:])
    if len(parents) != 2:
        return False
    try:
        merge_tree = require_git(repository, ["rev-parse", f"{merge}^{{tree}}"])
        second_tree = require_git(repository, ["rev-parse", f"{parents[1]}^{{tree}}"])
    except RuntimeError as error:
        raise PublicationMergeUnreadable(
            f"cannot read the tree of merge {merge} ({error})"
        ) from error
    return publication_merge_is_admitted(
        workflow=workflow,
        parents=parents,
        merge_tree=merge_tree,
        second_parent_tree=second_tree,
        authority_ranges=authority_ranges,
    )


def _pair_from_authority(authority: object, side: str, leaf_id: str) -> tuple[str, str, str] | None:
    """One side's (leaf id, B, H) out of an accepted integration authority, or nothing."""

    if authority is None:
        return None
    if side == "code":
        base, head = authority.codeSourceCommit, authority.codeCandidateCommit  # type: ignore[attr-defined]
    elif side == "memory":
        base, head = authority.memorySourceCommit, authority.memoryContentCommit  # type: ignore[attr-defined]
    else:
        return None
    if not leaf_id or not base or not head or base == head:
        return None
    return (leaf_id, base, head)


def leaf_authority_range(leaf: object, side: str) -> tuple[str, str, str] | None:
    """One leaf's own accepted B/H for one side, from its immutable integration authority.

    L194-V2-001: the expected pair must come from the authority the integration recorded, not from
    the contract's editable base cell. The live journal is opened through the canonical locator,
    which cross-checks the contract against the locator and manifest before reading.

    A leaf whose enclosure has already been archived has no live journal left to read, so the same
    authority is taken from the terminal archive through its own owners - locator inspection, the
    cleanup-contract authority, and the archived ``integrate-operation.json`` entry, whose bytes the
    archive has already digest- and size-validated, parsed by the operation model.

    ``None`` means this leaf grants no publication admission on this side - no authority, no side, an
    archived record that is absent, a refusal the store raised, or a side whose integrated commit
    equals its source position.

    Attribution, stated as the code behaves: a store refusal is **swallowed here**, not propagated.
    The live read is attempted, its refusal falls through to the archive, and an archive that cannot
    answer leaves ``None``. This function therefore reports *absence of admission*, never the store's
    typed reason - that reason belongs to whoever can act on it, and the proof's own refusal for a
    merge no admission explains is where the caller still lands. Nothing is re-read by a second route
    and no pair is invented to fill the gap.
    """

    leaf_id = str(getattr(leaf, "leaf_id", "") or "")
    if not leaf_id:
        return None
    try:
        record = located_lifecycle_operation_store(leaf, "integrate").read()  # type: ignore[arg-type]
    except Exception:
        record = None
    found = _pair_from_authority(
        None if record is None else record.integrationAuthority, side, leaf_id
    )
    if found is not None:
        return found
    return _archived_authority_range(leaf, side, leaf_id)


def _archived_authority_range(leaf: object, side: str, leaf_id: str) -> tuple[str, str, str] | None:
    """The same authority, read from the terminal archive, only when the archive proves it."""

    coordination_root = getattr(leaf, "coordination_root", None)
    contract_path = getattr(leaf, "contract_path", None)
    if coordination_root is None or contract_path is None:
        return None
    observation = inspect_lifecycle_operation_locator(coordination_root, contract_path)
    if observation.state != "terminal-archived" or observation.locator is None:
        return None
    authority = terminal_cleanup_contract_authority(
        coordination_root,
        leaf,  # type: ignore[arg-type]
        observation.locator,
    )
    for entry in authority.archive.canonicalEntries:
        if entry.relativePath.rsplit("/", 1)[-1] == "integrate-operation.json":
            record = LifecycleOperationRecord.model_validate_json(entry.content)
            return _pair_from_authority(record.integrationAuthority, side, leaf_id)
    return None


def publication_ranges(leaves, side: str) -> tuple[tuple[str, str, str], ...]:
    """The authority pairs of the leaves this proof already admits, for one side.

    A leaf whose authority cannot be read contributes nothing: it grants no new admission, and a
    merge only it could have explained keeps falling through to the refusal this proof has always
    made. That is the difference between "no authority" and "a merge no admission explains".

    The store's typed reason is **not** propagated from here: the broad catches below turn an
    unreadable authority into an absence of admission, so the caller keeps its own refusal instead of
    receiving a substituted pair or a re-raised store error.
    """

    ranges: list[tuple[str, str, str]] = []
    for leaf in leaves:
        try:
            found = leaf_authority_range(leaf, side)
        except Exception:
            continue
        if found is not None:
            ranges.append(found)
    return tuple(ranges)
