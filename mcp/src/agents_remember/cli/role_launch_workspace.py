"""Prepare the admitted execution folder and one idempotent leaf enclosure."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from agents_remember.application.direct_execution_grants import refuse_open_direct_sibling
from agents_remember.application.role_launch_context import (
    RoleLaunchContext,
)
from agents_remember.application.task_docs.task_ref import TaskRef
from agents_remember.application.worktree_tool_requests import TaskIdentity
from agents_remember.application.worktree_tools import worktree_status_tool
from agents_remember.cli.leaf_enclosure_start import start_leaf_enclosure_in_child
from agents_remember.cli.role_message_files import (
    MESSAGES_LINK,
    ensure_messages_link,
    leaf_messages_folder,
)
from agents_remember.errors import MemoryModeUnsupportedError, RolePreparationError
from agents_remember.kernel.direct_execution_grants import DirectGrantError, read_grant
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.role_identity import LEAF_ROLES
from agents_remember.tasks.document import INTENT_REVIEW_REFUSAL, intent_review_refusal
from agents_remember.tasks.document_refs import ResolvedTaskDocument
from agents_remember.tasks.store import read_task_doc
from agents_remember.tasks.task_paths import leaf_enclosure_path, slugify
from agents_remember.worktrees import outside_scratch
from agents_remember.worktrees.start_task_publication import publish_start_task_facts
from agents_remember.worktrees.task_document_copies import prepare_leaf_workspace_reference
from agents_remember.worktrees.task_leaf_binding import TaskLeafBindingError
from agents_remember.worktrees.task_resolver import series_contract_path
from agents_remember.worktrees.worktree_contract import (
    ContractError,
    WorktreeContract,
    load_contract,
)


def _recorded_parent(context: RoleLaunchContext) -> str:
    """The task folder that commands the leaf's master: its sprint.

    A master's parent is its sprint. A series contract written before 2026-09-29 records the
    master's *own* name in ``parent_task_name``, and feeding that back as the nested task folder
    resolves the leaf under ``<master>/<master>`` -- a folder that does not exist. The recorded
    value is therefore used only when it names a folder other than the master's own, and the
    sprint's folder is the answer otherwise.
    """

    assert context.master is not None and context.sprint is not None
    master_folder = context.master.path.parent.name
    path = series_contract_path(context.master.path.parent)
    if path.exists():
        recorded = load_contract(path).parent_task_name
        if recorded and recorded != master_folder:
            return recorded
    return context.sprint.path.parent.name


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


def link_leaf_workspace_messages(group: Path, task_dir: Path, leaf_key: str) -> str | None:
    """Carry a new leaf workspace's ``messages`` link to its leaf folder (F02).

    The real workspace owner calls this while the enclosure group folder is
    prepared, so the link exists before any message. An occupied non-link or
    a divergent link is preserved and returned as a warning; the launch goes
    on, and the first message ensures the link again.
    """
    folder = leaf_messages_folder(task_dir, leaf_key)
    return ensure_messages_link(group, folder)


def _direct_leaf_grant(config: McpRuntimeConfig, leaf: ResolvedTaskDocument) -> bool:
    """Whether the leaf is direct: it holds a readable grant (MIK-R94 rule 12).

    A grant file that cannot be read refuses the start: a leaf is never taken
    as direct without a readable grant, and never given an enclosure instead.
    """
    if leaf.document.kind != "subTask":
        return False
    try:
        return read_grant(config.coordination_root, leaf.ref) is not None
    except DirectGrantError as error:
        raise RolePreparationError(
            error.status,
            error.detail,
            "Repair or remove the unreadable grant file before repeating this same role start.",
        ) from error


def _resolve_direct_workspace(
    config: McpRuntimeConfig, context: RoleLaunchContext
) -> dict[str, str]:
    """The workspace of a direct leaf's role: no enclosure is created.

    The agent gets the checkouts of the master's branch as its code and memory
    roots, bound by the canonical leaf document and the master's series
    contract. Its own folders (reports, scratch, document copies) lie in the
    series worktree group, apart from the master's checkouts.
    """
    assert context.task is not None
    ref = context.task.ref
    try:
        refuse_open_direct_sibling(config, ref, "role_start")
    except DirectGrantError as error:
        raise RolePreparationError(
            error.status,
            error.detail,
            "Finish the in-work direct leaf named in the detail, or abandon it and plan "
            "a new one, then repeat this same role start.",
        ) from error
    contract_path = series_contract_path(context.task.path.parent)
    if not contract_path.is_file():
        raise RolePreparationError(
            "direct-series-contract-missing",
            f"The direct leaf {ref.repository}:{ref.path} has no series contract at "
            f"{contract_path.as_posix()}: a direct role works in the checkouts of its "
            "master's branch.",
            "Land the master's series contract before starting a role on its direct leaf.",
        )
    try:
        series = load_contract(contract_path)
    except (ContractError, MemoryModeUnsupportedError, OSError, UnicodeError, ValueError) as error:
        raise RolePreparationError(
            "direct-series-contract-unreadable",
            f"The series contract of the direct leaf {ref.repository}:{ref.path} cannot be "
            f"read: {error}",
            f"Inspect worktree_status(repo_id={ref.repository!r}, contract_path={contract_path.as_posix()!r}) and repair the series contract before repeating this same request.",
        ) from error
    if series.kind != "series":
        raise RolePreparationError(
            "direct-series-contract-mismatch",
            f"The direct leaf {ref.repository}:{ref.path} is not bound by a series contract: "
            f"{contract_path.as_posix()} is a {series.kind} contract.",
            "Ask the master owner to reconcile the task's series contract before repeating "
            "this same role start.",
        )
    try:
        group = _require_directory_of(series.worktree_group, "worktree group")
        code = _require_directory_of(series.code_worktree, "code checkout")
    except ValueError as error:
        raise RolePreparationError(
            "direct-checkout-unavailable",
            str(error),
            f"Inspect worktree_status(repo_id={ref.repository!r}, contract_path={contract_path.as_posix()!r}) and repair the series checkouts before repeating this same request.",
        ) from error
    workspace = workspace_folder(group)
    task_reports = context.task.path.parent / "notes" / "reports"
    task_reports.mkdir(parents=True, exist_ok=True)
    report_access = group / "role-reports"
    report_access.mkdir(exist_ok=True)
    if report_access.is_symlink():
        raise ValueError(
            "The new role-reports folder must be a real directory inside its working folder."
        )
    workspace.update(
        enclosurePreparation="direct",
        contractPath=contract_path.as_posix(),
        codeRoot=code.as_posix(),
        taskReportRoot=task_reports.resolve().as_posix(),
        taskReportAccessRoot=report_access.as_posix(),
        **_outside_scratch_field(series),
    )
    if series.memory_worktree is not None:
        workspace["memoryRoot"] = _require_directory_of(
            series.memory_worktree, "memory checkout"
        ).as_posix()
    return workspace


def _refuse_open_intent_review(leaf: ResolvedTaskDocument) -> None:
    """MIK-R189 rule 2: a leaf whose intent review is open refuses every leaf role's start.

    Answered before the enclosure is ensured, so a refused start creates nothing: no enclosure,
    no contract, no seat.
    """

    refusal = intent_review_refusal(leaf.document)
    if refusal is None:
        return
    raise RolePreparationError(
        INTENT_REVIEW_REFUSAL,
        refusal,
        "The architect records the developer's answer as `go` in `intentReview` through "
        "task_doc, then this same role start is admitted. A start refused this way is never "
        "retried on its own.",
    )


def _move_found_enclosure_to_in_progress(
    context: RoleLaunchContext,
    contract_path: Path,
    status: dict[str, Any],
) -> None:
    """MIK-R189 v2: a found live enclosure makes the leaf's stage move here.

    It is the last act of the resolution -- after the intent-review gate and the folder checks --
    so a refused start moves nothing and the first seat's copies already read ``inProgress``. A
    created enclosure moved in ``worktree_start``; a terminal or reopened contract and a leaf
    that is not ``planning`` are untouched, and a retry writes nothing once the facts agree.
    """

    if str(status.get("enclosurePreparation")) != "found":
        return
    try:
        contract = load_contract(contract_path)
    except (ContractError, MemoryModeUnsupportedError, OSError, UnicodeError, ValueError):
        return
    if contract.cleanup in ("abandoned", "reopened", "completed"):
        return
    if context.task is None:
        return
    try:
        document = read_task_doc(context.task.path)
    except (OSError, UnicodeError, ValueError):
        return
    if document.status != "planning":
        return
    try:
        publish_start_task_facts(contract, started=True)
    except TaskLeafBindingError as error:
        raise RolePreparationError(
            error.status,
            error.detail,
            "The leaf's enclosure was found, but its start facts could not be written. Inspect "
            "worktree_status and repair the leaf/enclosure binding, then repeat this same role "
            "start. No seat was made.",
        ) from error


def _resolve_workspace(config: McpRuntimeConfig, context: RoleLaunchContext) -> dict[str, str]:
    """The folder the role class is entitled to: Projects, or the leaf's enclosure group folder.

    Only the folder is resolved here. The Paseo workspace of that folder is obtained from the
    runtime when the saved launch call runs; master/task placement is separate from this cwd.
    """

    if context.role not in LEAF_ROLES:
        return workspace_folder(config.workspace_root)
    assert context.task is not None and context.sprint is not None
    _refuse_open_intent_review(context.task)
    direct = _direct_leaf_grant(config, context.task)
    if direct:
        return _resolve_direct_workspace(config, context)
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
    messages_warning = link_leaf_workspace_messages(
        group, context.task.path.parent, context.task.ref.key
    )
    workspace["messagesLink"] = (group / MESSAGES_LINK).as_posix()
    if messages_warning is not None:
        workspace["messagesLinkWarning"] = messages_warning
    reference_result = prepare_leaf_workspace_reference(
        config.coordination_root, group, context.task.path
    )
    workspace["referenceRoot"] = str(
        status.get("referenceRoot") or reference_result["referenceRoot"]
    )
    reference_warning = status.get("referenceWarning") or reference_result.get("warning")
    if isinstance(reference_warning, str) and reference_warning:
        workspace["referenceWarning"] = reference_warning
    task_reports = context.task.path.parent / "notes" / "reports"
    task_reports.mkdir(parents=True, exist_ok=True)
    report_access = group / "role-reports"
    report_access.mkdir(exist_ok=True)
    if report_access.is_symlink():
        raise ValueError(
            "The new role-reports folder must be a real directory inside its working folder."
        )
    workspace.update(
        enclosurePreparation=str(status["enclosurePreparation"]),
        contractPath=contract_path.as_posix(),
        codeRoot=code.as_posix(),
        memoryRoot=memory.as_posix(),
        taskReportRoot=task_reports.resolve().as_posix(),
        taskReportAccessRoot=report_access.as_posix(),
        outsideScratchRoot=_ensure_leaf_root(contract_path, group),
    )
    _move_found_enclosure_to_in_progress(context, contract_path, status)
    return workspace


def _outside_scratch_field(contract: WorktreeContract) -> dict[str, str]:
    """``workspace.outsideScratchRoot``, beside ``workspace.scratchRoot`` (MIK-R207 rule 3).

    A direct leaf shares the root of the series enclosure it works under; a leaf's own
    enclosure records its own. The field is always present so that the role and operation
    texts may name it unconditionally; an enclosure from before this change records no root and
    the field is empty, which no text reads as a place to write.
    """

    record = outside_scratch.recorded_root(contract)
    if record is None:
        return {"outsideScratchRoot": ""}
    outside_scratch.ensure_recorded_root(contract)
    return {"outsideScratchRoot": record.root.as_posix()}


def _ensure_leaf_root(contract_path: Path, group: Path) -> str:
    """The leaf enclosure's root, recreated under its recorded identity when it aged away.

    The manifest is read first and the contract only when it records a root, so an enclosure
    from before this change is answered without a second read of it. A recorded root that no
    longer exists is recreated here: the next use is what keeps the place a task writes in.
    """

    if not outside_scratch.recorded_root_in_group(group):
        return ""
    contract = load_contract(contract_path)
    outside_scratch.ensure_recorded_root(contract)
    record = outside_scratch.recorded_root(contract)
    return "" if record is None else record.root.as_posix()


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
    state = str(status.get("state") or status.get("status") or "")
    if state in (
        "terminal-cleanup-completed",
        "terminal-archive-ready",
    ):
        raise RolePreparationError(
            "reopen-required",
            str(status.get("summary") or "The leaf's enclosure generation is terminal.")
            + " A role cannot run in this terminal generation; preview and apply task_reopen before repeating this start.",
            f"task_reopen(contract_path={contract_path.as_posix()!r}, dry_run=True)",
        )
    # A reopened leaf sits in its own state: the reset contract is durable and the archived
    # generation it came from stays proven, but the worktrees the cleanup removed are still
    # gone. The start path is what recreates them (``cleanup: reopened`` starts fresh), so this
    # is the same prepared start a leaf with no enclosure takes, not a refusal.
    if (status.get("ok") is not True and not contract_path.exists()) or state == "reopened":
        planned_name = (
            f"{slugify(leaf.document.slug)[:40]}-"
            f"{hashlib.sha256(leaf.ref.key.encode('utf-8')).hexdigest()[:10]}"
        )
        created = _start_leaf_enclosure(
            config,
            TaskIdentity(
                repo_id=leaf.ref.repository,
                task_name=task_root.name,
                # A reopened leaf keeps the worktree name its contract already owns, so the
                # restart re-creates the same group and branches instead of renaming the leaf's
                # identity under a caller that only asked for a seat.
                worktree_name=(_recorded_worktree_name(contract_path) or planned_name)
                if state == "reopened"
                else planned_name,
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
    raw_group = status.get("worktree_group")
    if isinstance(raw_group, str) and raw_group and Path(raw_group).is_dir():
        ref_copy = prepare_leaf_workspace_reference(
            config.coordination_root, Path(raw_group).resolve(), leaf.path
        )
        status["referenceRoot"] = ref_copy["referenceRoot"]
        if isinstance(ref_copy.get("warning"), str) and ref_copy["warning"]:
            status["referenceWarning"] = ref_copy["warning"]
    return contract_path, status


def _recorded_worktree_name(contract_path: Path) -> str:
    """The worktree name this enclosure already owns, or ``""`` when it cannot be read."""

    try:
        return load_contract(contract_path).code_worktree.name
    except (ContractError, MemoryModeUnsupportedError, OSError, UnicodeError, ValueError):
        return ""


def _start_leaf_enclosure(config: McpRuntimeConfig, identity: TaskIdentity) -> dict[str, Any]:
    """Use the existing preparation child for both role-start entry points.

    Preparation runs only for this operation in the existing checked child.
    The child retains its root checks, writer admission and cut-off policy;
    the public worktree_start tool keeps its caller semantics.
    """
    return start_leaf_enclosure_in_child(config, identity)


def workspace_folder(path: Path) -> dict[str, str]:
    # Directory placement and native membership both use this resolved execution folder.
    root = path.resolve()
    root.mkdir(parents=True, exist_ok=True)
    return {"path": root.as_posix()}


def _require_directory_of(path: Path | None, label: str) -> Path:
    """The series checkout directory a direct role works in, else a refusal."""
    if path is None or not path.is_dir():
        raise ValueError(f"The selected AR enclosure {label} directory is unavailable.")
    return path.resolve()


def _require_directory(payload: dict[str, Any], key: str) -> Path:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"AR worktree status did not return {key}.")
    path = Path(value).resolve()
    if not path.is_dir():
        raise ValueError(f"The selected AR enclosure {key} directory is unavailable.")
    return path
