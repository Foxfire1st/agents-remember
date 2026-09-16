"""Shape the operator-facing report for a completed terminal reclamation.

Reclamation itself is run by ``lifecycle_finalize_task``. It calls
:func:`agents_remember.worktrees.modules.cleanup.cleanup_result`, which proves terminal
evidence, archives it and reads it back, and refuses when authority is live or ambiguous. This
module owns only the report an operator reads afterwards: the inventory of what was removed and
what was left in place, and the one sentence that names both. Reclamation stays automatic and
unprompted -- what moved is which procedure reaches it.

The shaping is pure. :func:`cleanup_report` reads the cleanup payload plus the contract's own
declared targets and returns the report; it mutates nothing and refuses nothing. A refused or
failed cleanup never reaches it: that payload is passed through unchanged, so its ``blockers``,
its partial removal inventory and its citation-cache facts stay visible instead of being
flattened into a sentence.
"""

from __future__ import annotations

from pathlib import Path

from agents_remember.worktrees.worktree_contract import WorktreeContract

ALREADY_CLEAN = "already-clean"

RemovalInventory = dict[str, list[dict[str, object]]]


def cleanup_report(contract: WorktreeContract, payload: dict[str, object]) -> dict[str, object]:
    """Report what a completed terminal reclamation removed and what it left in place."""

    state = str(payload.get("state"))
    if state == ALREADY_CLEAN:
        # The terminal archive already proves this enclosure was reclaimed: nothing remained
        # to remove, and reporting the contract's targets as "still in place" would state the
        # opposite of what the terminal proof says.
        return {
            "automatic": True,
            "state": state,
            "summary": (
                "Automatic cleanup found the enclosure already reclaimed; no worktrees, "
                "merged local branches, reports or enclosure root remained."
            ),
            "removed": _empty_inventory(),
            "notRemoved": _empty_inventory(),
        }
    removed, not_removed = _inventory(contract, payload)
    return {
        "automatic": True,
        "state": state,
        "summary": _summary(removed, not_removed),
        "removed": removed,
        "notRemoved": not_removed,
    }


def _empty_inventory() -> RemovalInventory:
    return {"worktrees": [], "localBranches": [], "reports": [], "enclosureRoot": []}


def _inventory(
    contract: WorktreeContract,
    payload: dict[str, object],
) -> tuple[RemovalInventory, RemovalInventory]:
    reports = _directory(contract.worktree_group / "reports", payload, "reports")
    root = _directory(contract.worktree_group, payload, "worktree_group")
    removed: RemovalInventory = {
        "worktrees": _removed_worktrees(payload),
        "localBranches": _removed_branches(payload),
        "reports": _presented(reports),
        "enclosureRoot": _presented(root),
    }
    not_removed: RemovalInventory = {
        "worktrees": _kept_worktrees(contract, payload),
        "localBranches": _kept_branches(contract, payload),
        "reports": _absent(reports),
        "enclosureRoot": _absent(root),
    }
    return removed, not_removed


def _removed_worktrees(payload: dict[str, object]) -> list[dict[str, object]]:
    return [
        {"side": side, "path": item.get("path")}
        for side, item in _entries(payload.get("removed_worktrees")).items()
        if item.get("removed")
    ]


def _kept_worktrees(
    contract: WorktreeContract, payload: dict[str, object]
) -> list[dict[str, object]]:
    observed = _entries(payload.get("removed_worktrees"))
    kept: list[dict[str, object]] = []
    for side, path in _contract_worktrees(contract):
        item = observed.get(side)
        if item is not None and item.get("removed"):
            continue
        kept.append({"side": side, "path": path, "reason": _reason(item)})
    return kept


def _removed_branches(payload: dict[str, object]) -> list[dict[str, object]]:
    return [
        {"side": side, "branch": item.get("branch")}
        for side, item in _entries(payload.get("branches")).items()
        if item.get("deleted")
    ]


def _kept_branches(
    contract: WorktreeContract, payload: dict[str, object]
) -> list[dict[str, object]]:
    observed = _entries(payload.get("branches"))
    kept: list[dict[str, object]] = []
    for side, branch in _contract_branches(contract):
        item = observed.get(side)
        if item is not None and item.get("deleted"):
            continue
        kept.append({"side": side, "branch": branch, "reason": _reason(item)})
    return kept


def _directory(path: Path, payload: dict[str, object], name: str) -> dict[str, object]:
    item = _entries(payload.get("directories")).get(name)
    return {
        "path": path.as_posix(),
        "removed": bool(item and item.get("removed")),
        "reason": _reason(item),
    }


def _presented(directory: dict[str, object]) -> list[dict[str, object]]:
    if not directory["removed"]:
        return []
    return [{"path": directory["path"], "removed": True}]


def _absent(directory: dict[str, object]) -> list[dict[str, object]]:
    if directory["removed"]:
        return []
    return [{"path": directory["path"], "removed": False, "reason": directory["reason"]}]


def _entries(value: object) -> dict[str, dict[str, object]]:
    if not isinstance(value, dict):
        return {}
    return {str(key): item for key, item in value.items() if isinstance(item, dict)}


def _reason(item: dict[str, object] | None) -> str:
    if item is None:
        return "cleanup-did-not-run"
    return str(item.get("reason") or "not-removed")


def _contract_worktrees(contract: WorktreeContract) -> list[tuple[str, str]]:
    worktrees = [("code", contract.code_worktree.as_posix())]
    if contract.memory_mode == "external" and contract.memory_worktree is not None:
        worktrees.append(("memory", contract.memory_worktree.as_posix()))
    return worktrees


def _contract_branches(contract: WorktreeContract) -> list[tuple[str, str]]:
    branches = [("code", contract.code_work_branch)]
    if contract.memory_mode == "external":
        branches.append(("memory", contract.memory_work_branch))
    return branches


def _summary(removed: RemovalInventory, not_removed: RemovalInventory) -> str:
    reclaimed = _inventory_phrase(removed) or "nothing"
    left = _inventory_phrase(not_removed)
    if not left:
        return f"Automatic cleanup removed {reclaimed}; nothing was left in place."
    return f"Automatic cleanup removed {reclaimed}; left in place {left}."


def _inventory_phrase(inventory: RemovalInventory) -> str:
    parts = [
        _counted(inventory["worktrees"], "worktree", "worktrees"),
        _counted(inventory["localBranches"], "local branch", "local branches"),
        _counted(inventory["reports"], "reports directory", "reports directories"),
        _counted(inventory["enclosureRoot"], "enclosure root", "enclosure roots"),
    ]
    return ", ".join(part for part in parts if part)


def _counted(items: list[dict[str, object]], singular: str, plural: str) -> str:
    if not items:
        return ""
    return f"{len(items)} {singular if len(items) == 1 else plural}"
