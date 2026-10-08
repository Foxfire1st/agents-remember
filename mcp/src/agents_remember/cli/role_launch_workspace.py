"""Prepare the admitted execution folder and one idempotent leaf enclosure."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from agents_remember.application.role_launch_context import LEAF_ROLES, RoleLaunchContext
from agents_remember.application.task_docs.task_ref import TaskRef
from agents_remember.application.worktree_tool_requests import TaskIdentity
from agents_remember.application.worktree_tools import worktree_status_tool
from agents_remember.cli.leaf_enclosure_start import start_leaf_enclosure_in_child
from agents_remember.cli.role_launch_receipts import _bind_task_report_access
from agents_remember.errors import RolePreparationError
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.tasks.document_refs import ResolvedTaskDocument
from agents_remember.tasks.task_paths import leaf_enclosure_path, slugify
from agents_remember.worktrees.task_resolver import series_contract_path
from agents_remember.worktrees.worktree_contract import load_contract


def _recorded_parent(context: RoleLaunchContext) -> str:
    assert context.master is not None and context.sprint is not None
    path = series_contract_path(context.master.path.parent)
    return (
        load_contract(path).parent_task_name if path.exists() else context.sprint.path.parent.name
    )


def _preparation_refusal(
    result: dict[str, Any], contract_path: Path, repo_id: str
) -> RolePreparationError:
    status = str(result.get("state") or result.get("status") or "leaf-enclosure-unavailable")
    detail = str(
        result.get("summary") or result.get("detail") or "AR could not prepare the leaf enclosure"
    )
    tool = result.get("nextTool")
    args = result.get("nextArgs")
    if (
        result.get("nextOperation") == "choose_stale_base_recovery"
        or status == "choose_stale_base_recovery"
    ):
        remedy = "Advance the source branch through its repository landing route, then repeat this same role start. A stale-base override requires the owner's explicit stale_base_choice; this start supplies no choice."
    elif tool and isinstance(args, dict):
        remedy = f"{tool}({json.dumps(args, sort_keys=True)})"
    elif status == "reopen-required":
        remedy = f"task_reopen(contract_path={contract_path.as_posix()!r}, dry_run=True)"
    elif status == "atomic-series-contract-edge-mismatch":
        remedy = "Ask the master owner to reconcile the recorded series edge with its commanding sprint; no contract was replaced."
    elif "integrationBranch" in detail:
        remedy = "Ask the sprint owner to declare its integrationBranch in the sprint task document, then repeat this start."
    else:
        remedy = f"Inspect worktree_status(repo_id={repo_id!r}, contract_path={contract_path.as_posix()!r}) before repairing or abandoning the enclosure. Repeat the same request id afterwards."
    return RolePreparationError(status, detail, remedy)


def _leaf_contract_path(leaf: ResolvedTaskDocument) -> Path:
    """The contract path of a leaf's enclosure, derived from the leaf document alone.

    Nothing is created here: a launch goes on to open the enclosure, a revive only compares.
    """

    if leaf.document.kind != "subTask":
        raise ValueError("Only a canonical leaf can open a leaf enclosure.")
    expected = leaf_enclosure_path(leaf.path.parent, leaf.document.id).resolve()
    if not leaf.document.enclosures:
        return expected
    enclosure = leaf.document.enclosures[0]
    if len(leaf.document.enclosures) != 1 or enclosure.leafId != leaf.document.id:
        raise RolePreparationError(
            "leaf-enclosure-binding-conflict",
            "The selected leaf has conflicting enclosure bindings",
            "Ask the leaf owner to reconcile its canonical enclosure bindings; no enclosure was created.",
        )
    contract_path = Path(enclosure.enclosurePath).resolve()
    if contract_path != expected:
        raise RolePreparationError(
            "leaf-enclosure-binding-conflict",
            "The selected leaf enclosure does not match its canonical task binding",
            "Ask the leaf owner to reconcile the canonical enclosure binding; no enclosure was created.",
        )
    return contract_path


def _resolve_workspace(config: McpRuntimeConfig, context: RoleLaunchContext) -> dict[str, str]:
    """The folder the role class is entitled to: Projects, or the leaf's enclosure group folder.

    Only the folder is resolved here. The Paseo workspace of that folder is obtained from the
    runtime when the saved launch call runs; master/task placement is separate from this cwd.
    """

    if context.role not in LEAF_ROLES:
        return workspace_folder(config.workspace_root)
    assert context.task is not None and context.sprint is not None
    contract_path, status = _ensure_leaf_enclosure(
        config,
        context.task,
        parent_task=_recorded_parent(context),
    )
    try:
        group = _require_directory(status, "worktree_group")
        code = _require_directory(status, "code_worktree")
        memory = _require_directory(status, "memory_worktree")
    except ValueError as error:
        raise RolePreparationError(
            "leaf-enclosure-directory-unavailable",
            str(error),
            f"Inspect worktree_status(repo_id={context.task.ref.repository!r}, contract_path={contract_path.as_posix()!r}) and repair the contract-owned roots before repeating this same request.",
        ) from error
    workspace = workspace_folder(group)
    task_reports = context.task.path.parent / "notes" / "reports"
    task_reports.mkdir(parents=True, exist_ok=True)
    report_access = _bind_task_report_access(group, task_reports)
    workspace.update(
        enclosurePreparation=str(status["enclosurePreparation"]),
        contractPath=contract_path.as_posix(),
        codeRoot=code.as_posix(),
        memoryRoot=memory.as_posix(),
        taskReportRoot=task_reports.resolve().as_posix(),
        taskReportAccessRoot=report_access.as_posix(),
    )
    return workspace


def _ensure_leaf_enclosure(
    config: McpRuntimeConfig,
    leaf: ResolvedTaskDocument,
    *,
    parent_task: str,
) -> tuple[Path, dict[str, Any]]:
    contract_path = _leaf_contract_path(leaf)
    task_root = leaf.path.parent

    preparation = "found"
    status = worktree_status_tool(
        config,
        TaskRef(repo_id=leaf.ref.repository, contract_path=contract_path.as_posix()),
    )
    if str(status.get("state") or status.get("status") or "") in (
        "terminal-cleanup-completed",
        "terminal-archive-ready",
    ):
        raise RolePreparationError(
            "reopen-required",
            str(status.get("summary") or "The leaf's enclosure generation is terminal.")
            + " A role cannot run in this terminal generation; preview and apply task_reopen before repeating this start.",
            f"task_reopen(contract_path={contract_path.as_posix()!r}, dry_run=True)",
        )
    if status.get("ok") is not True and not contract_path.exists():
        created = _start_leaf_enclosure(
            config,
            TaskIdentity(
                repo_id=leaf.ref.repository,
                task_name=task_root.name,
                worktree_name=(
                    f"{slugify(leaf.document.slug)[:40]}-"
                    f"{hashlib.sha256(leaf.ref.key.encode('utf-8')).hexdigest()[:10]}"
                ),
                leaf_id=leaf.document.id,
                parent_task=parent_task,
            ),
        )
        if created.get("ok") is not True:
            raise _preparation_refusal(created, contract_path, leaf.ref.repository)
        preparation = "created"
        status = worktree_status_tool(
            config,
            TaskRef(repo_id=leaf.ref.repository, contract_path=contract_path.as_posix()),
        )
    if status.get("ok") is not True:
        raise _preparation_refusal(status, contract_path, leaf.ref.repository)
    status = {**status, "enclosurePreparation": preparation}
    return contract_path, status


def _start_leaf_enclosure(config: McpRuntimeConfig, identity: TaskIdentity) -> dict[str, Any]:
    """Use the existing preparation child for both role-start entry points.

    Its lifecycle belongs to this one operation, so preparing a leaf neither depends on nor
    changes the calling agent's persistent lifecycle. The child retains its root checks, writer
    admission and cut-off policy; the public worktree_start tool keeps its caller semantics.
    """
    return start_leaf_enclosure_in_child(config, identity)


def workspace_folder(path: Path) -> dict[str, str]:
    # Directory placement and native membership both use this resolved execution folder.
    root = path.resolve()
    root.mkdir(parents=True, exist_ok=True)
    return {"path": root.as_posix()}


def _require_directory(payload: dict[str, Any], key: str) -> Path:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"AR worktree status did not return {key}.")
    path = Path(value).resolve()
    if not path.is_dir():
        raise ValueError(f"The selected AR enclosure {key} directory is unavailable.")
    return path
