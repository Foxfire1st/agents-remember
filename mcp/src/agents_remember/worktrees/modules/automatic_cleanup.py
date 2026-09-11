"""Run the existing cleanup procedure automatically after a completed integration.

The developer ruling is that a completed leaf is reclaimed without another prompt: cleanup is
an automatic procedure that follows integration. This module owns only the wiring and the
operator-facing report. The destructive work stays in
:func:`agents_remember.worktrees.modules.cleanup.cleanup_result`, which already proves
terminal evidence, archives it and reads it back, and refuses when authority is live or
ambiguous.

A refused or failed cleanup is reported, never raised. It runs after a successful landing, and
turning that landing into a reported failure would hide a real integration; the failure stays
loud through this payload and through the contract's own ``cleanup`` cell, which the phase
machine turns into ``cleanup-pending``.
"""

from __future__ import annotations

from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.cleanup import cleanup_result
from agents_remember.worktrees.worktree_contract import WorktreeContract

# The states in which the cleanup procedure reports that reclamation is finished.
_TERMINAL_CLEANUP_STATES = {"cleanup-completed", "already-clean"}

RemovalInventory = dict[str, list[dict[str, object]]]


def run_automatic_cleanup(contract: WorktreeContract) -> dict[str, object]:
    """Reclaim one integrated enclosure and report, in operator language, what happened.

    ``approved`` is the integration approval: the developer ruling removes the separate
    cleanup prompt, so the landing that just completed is the authorization for its own
    terminal reclamation. A refusal or a partial mutation changes only what this report
    says -- it never changes the integration result that carries it.
    """

    try:
        result = cleanup_result(
            WorktreeArgs(
                contract_path=contract.contract_path,
                approved=True,
                dry_run=False,
                teardown_providers=True,
            )
        )
    except Exception as error:
        # A cleanup refusal or crash is the step after a real landing: report it, never
        # convert a completed integration into a failure.
        return _refused(contract, f"{type(error).__name__}: {error}")
    state = result.payload.get("state")
    if result.returncode != 0 or state not in _TERMINAL_CLEANUP_STATES:
        return _refused(contract, _refusal_reason(result.payload), result.payload)
    return _completed(contract, result.payload)


def _refusal_reason(payload: dict[str, object]) -> str:
    reason = str(payload.get("summary") or payload.get("state") or "cleanup refused")
    blockers = payload.get("blockers")
    if not isinstance(blockers, list) or not blockers:
        return reason
    return f"{reason} ({blockers!r})"


def _completed(contract: WorktreeContract, payload: dict[str, object]) -> dict[str, object]:
    state = str(payload.get("state"))
    if state == "already-clean":
        # The terminal archive already proves this enclosure was reclaimed: nothing
        # remained to remove, and reporting the contract's targets as "still in place"
        # would state the opposite of what the terminal proof says.
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
        "summary": _summary(state, "", removed, not_removed),
        "removed": removed,
        "notRemoved": not_removed,
    }


def _empty_inventory() -> RemovalInventory:
    return {"worktrees": [], "localBranches": [], "reports": [], "enclosureRoot": []}


def _refused(
    contract: WorktreeContract,
    reason: str,
    payload: dict[str, object] | None = None,
) -> dict[str, object]:
    observed = payload or {}
    removed, not_removed = _inventory(contract, observed)
    report: dict[str, object] = {
        "automatic": True,
        "state": "refused",
        "summary": _summary("refused", reason, removed, not_removed),
        "removed": removed,
        "notRemoved": not_removed,
        "refusal": {
            "reason": reason,
            "cleanupState": observed.get("state"),
            "blockers": observed.get("blockers") or [],
        },
    }
    return report


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


def _directory(path, payload: dict[str, object], name: str) -> dict[str, object]:
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


def _summary(
    state: str,
    reason: str,
    removed: RemovalInventory,
    not_removed: RemovalInventory,
) -> str:
    if state == "refused":
        reclaimed = _inventory_phrase(removed)
        if reclaimed:
            standing = _inventory_phrase(not_removed) or "nothing that was reported"
            return (
                f"Automatic cleanup was refused after removing {reclaimed}; "
                f"still in place {standing}. Reason: {reason}."
            )
        return (
            f"Automatic cleanup was refused and removed nothing: {reason}. "
            "The worktrees, local task branches, reports and enclosure root are still in place."
        )
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
