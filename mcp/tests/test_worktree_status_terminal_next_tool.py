"""The worktree surface's advertised next move must name a registered public tool.

``application/worktree_status._project_terminal_contract_status`` writes the next-move
triple (``nextAction`` / ``nextTool`` / ``nextArgs``) into the payload that is validated
once, against ``WorktreeStatusResponse``. Until those keys were declared on
``WorktreeCommandResponse`` the envelope was ``extra="allow"`` and declared none of them,
so the values crossed the wire verbatim and unchecked: ``worktree_abandon`` reached the
wire as a ``nextTool`` without ever having been a ``NextTool`` member, and
``model_validate({"ok": True, "nextTool": "not_a_tool"})`` succeeded. The *behaviour* was
already right -- the emitted args match the real tool signatures -- so this pins the
typing and the enforcement, not the projection.

THE SURVEY BEHIND THE INVARIANT. Every producer that writes a ``nextTool`` in ``mcp/src``
was traced, and the union of 20 values is: the ``NextTool`` and ``RecoveryTool`` literals
(``models/worktree.py``, ``worktrees/modules/guidance.py``); ``SourceLineageRecovery.tool``;
``TerminalCleanupOperation``; ``route_review.inspection_tool``; the narrowed ``nextTool`` on
``WorktreeSyncResponse`` and ``WorktreeOperationControlResponse``; the
``legal_operation_controls`` row ``tool`` values
(``lifecycle_operation_control_projection.py``); the ``terminal_enclosure_archive``
refusals; the ``task_unstarted_evidence`` ``RecoveryRoute`` tools; and the remaining direct
literals in ``worktrees/modules/{integrate,start}.py``, ``worktrees/task_leaf_binding.py``,
``application/worktree_tools.py``, ``application/next_step.py`` and ``models/base.py``.
Every one is in ``PUBLIC_TOOLS`` except ``session_retire``, which cannot reach the field
this file protects (see the boundary case at the end).

THE RULE IS PER SURFACE, DELIBERATELY. The worktree surface's rule is "a next move names a
registered *public* tool", because those values are advertised guidance an agent is meant to
act on and the roster is the already-enforced authority for what the agent can call. The
``task_doc`` surface may name a non-public tool, which is why the invariant lives on
``WorktreeCommandResponse`` and not on a shared envelope. That difference is intentional;
it is not an inconsistency to flatten.
"""

from __future__ import annotations

import inspect
import json
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from agents_remember.application.task_docs.task_ref import TaskRef
from agents_remember.kernel.primitives.runtime_config import load_config
from agents_remember.mcp.tools import PUBLIC_TOOLS
from agents_remember.mcp.tools.worktree import (
    worktree_abandon_payload,
    worktree_cleanup_payload,
    worktree_status_payload,
)
from agents_remember.models.lifecycles.enclosure import (
    TerminalCleanupOperation,
    TerminalWorktreeAbandonArguments,
    TerminalWorktreeCleanupArguments,
)
from agents_remember.models.task_doc import TaskDocResponse
from agents_remember.models.tools.tool_registry import TOOL_RESPONSE_MODELS
from agents_remember.models.worktree import WorktreeStatusResponse
from agents_remember.worktrees.integration.terminal_enclosure_archive import (
    terminal_archive_required_result,
)
from agents_remember.worktrees.worktree_contract import (
    ContractTask,
    LeafIdentity,
    RepoBranchPlan,
    WorktreeContract,
    default_contract,
    load_contract,
    write_contract,
)
from lifecycle_enclosure_test_support import publish_test_enclosure
from pydantic import ValidationError
from test_worktree_support import git, init_repo

pytestmark = pytest.mark.usefixtures("worktree_services")

TerminalArguments = TerminalWorktreeCleanupArguments | TerminalWorktreeAbandonArguments
ToolPayload = Callable[..., dict[str, Any]]
REPO_NAME = "repo-a"


def _draft_leaf_contract(root: Path) -> WorktreeContract:
    """An unstarted leaf over a real repository, so configured authority can bind it.

    ``ar-memory/`` makes the configured topology *internal*: ``default_memory_root`` never
    returns ``None`` for a configured repository, so a leaf in a configured repo cannot
    declare ``memory_mode="disabled"`` and still pass configured authority.
    """

    workspace = root / "workspace"
    code_repo = workspace / REPO_NAME
    base = init_repo(code_repo, "main")
    (code_repo / "ar-memory").mkdir()
    git(code_repo, "branch", "super", "main")
    git(code_repo, "branch", "ar/01-demo-leaf", "super")
    return default_contract(
        ContractTask(
            name="260698_demo-series",
            repo_name=REPO_NAME,
            coordination_root=workspace / "ar-coordination",
            workflow_kind="light-task",
            memory_mode="internal",
        ),
        leaf=LeafIdentity(worktree_name="01-demo-leaf", leaf_id="260698-l1"),
        code=RepoBranchPlan(
            repo_path=code_repo,
            source_branch="super",
            work_branch="ar/01-demo-leaf",
            base_commit=base,
        ),
    )


def _write_settings(root: Path, contract: WorktreeContract) -> Path:
    """A real MCP authority settings file; ``load_config`` refuses one inside coordination."""

    path = root / "mcp-settings.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "coordinationRoot": str(contract.coordination_root),
                "workspaceRoot": str(root / "workspace"),
                "repositories": {REPO_NAME: {}},
                "providers": {},
                "timeoutCaps": {"toolSeconds": 30, "providerSetupSeconds": 1800},
                "benchmarksEnabled": False,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return path


def _terminal_archive_ready_status(
    root: Path,
    *,
    operation: TerminalCleanupOperation,
    arguments: TerminalArguments,
) -> tuple[dict[str, Any], str]:
    """Reach the real archive-ready state: publish, archive, then delete the enclosure.

    Returns the ``worktree_status`` payload and the contract path it was addressed by.

    ``shutil.rmtree`` is the deletion step of the production cleanup, not a shortcut: it is
    what leaves the locator ``terminal-archived`` with the archive proof still durable,
    which is the state whose next move is the accepted cleanup operation itself.
    """

    contract = _draft_leaf_contract(root)
    write_contract(contract.contract_path, contract)
    location = publish_test_enclosure(
        contract,
        contract.contract_path.read_text(encoding="utf-8"),
    )
    accepted = load_contract(location.contract_path)
    archived = terminal_archive_required_result(
        accepted,
        operation=operation,
        arguments=arguments,
        dry_run=False,
    )
    assert archived.returncode == 0, archived.payload
    shutil.rmtree(location.worktree_group)
    contract_path = accepted.contract_path.as_posix()
    payload = worktree_status_payload(
        load_config(_write_settings(root, accepted)),
        TaskRef(repo_id=accepted.repo_name, contract_path=contract_path),
    )
    return payload, contract_path


@pytest.mark.parametrize(
    ("operation", "arguments", "payload_builder"),
    (
        pytest.param(
            "worktree_cleanup",
            TerminalWorktreeCleanupArguments(teardown_providers=True),
            worktree_cleanup_payload,
            id="worktree_cleanup",
        ),
        pytest.param(
            "worktree_abandon",
            TerminalWorktreeAbandonArguments(force=False),
            worktree_abandon_payload,
            id="worktree_abandon",
        ),
    ),
)
def test_archive_ready_status_names_the_accepted_cleanup_operation(
    tmp_path: Path,
    operation: TerminalCleanupOperation,
    arguments: TerminalArguments,
    payload_builder: ToolPayload,
) -> None:
    """The archive-ready branch names the accepted operation and the args that tool accepts."""

    payload, contract_path = _terminal_archive_ready_status(
        tmp_path, operation=operation, arguments=arguments
    )

    assert payload["state"] == "terminal-archive-ready"
    assert payload["status"] == "terminal-archive-ready"
    assert payload["nextTool"] == operation
    assert payload["nextAction"] == operation

    next_args = payload["nextArgs"]
    assert next_args["contract_path"] == contract_path
    assert next_args["dry_run"] is False
    assert next_args == {
        "contract_path": contract_path,
        "dry_run": False,
        **arguments.model_dump(mode="json"),
    }
    # Binding proves the emitted mapping is exactly what the named tool accepts: an extra
    # key or a missing required one raises TypeError.
    inspect.signature(payload_builder).bind(None, **next_args)


def test_worktree_status_declares_the_next_move_keys() -> None:
    """The wire contract declares the keys the projection writes; nothing rides as an extra."""

    assert TOOL_RESPONSE_MODELS["worktree_status"] is WorktreeStatusResponse
    declared = WorktreeStatusResponse.model_fields
    for key in ("nextAction", "nextTool", "nextArgs"):
        assert key in declared, f"{key} must be declared on WorktreeStatusResponse"


def test_next_tool_must_name_a_registered_public_tool() -> None:
    """An out-of-roster next move is refused; a roster member and ``None`` still pass."""

    for value in ("worktree_cleanup", "worktree_abandon", "worktree_status", "task_doc"):
        assert value in PUBLIC_TOOLS
        assert (
            WorktreeStatusResponse.model_validate({"ok": True, "nextTool": value}).nextTool == value
        )

    assert WorktreeStatusResponse.model_validate({"ok": True}).nextTool is None

    with pytest.raises(ValidationError) as refused:
        WorktreeStatusResponse.model_validate({"ok": True, "nextTool": "not_a_tool"})
    assert "not in PUBLIC_TOOLS" in str(refused.value)


def test_the_worktree_surface_refuses_a_registered_but_non_public_tool() -> None:
    """``session_retire`` is registered but deliberately non-public, and it is not this surface's.

    It is emitted as a ``nextTool`` only by ``task_unstarted_evidence._record_recovery_route``,
    and it reaches the wire only through the ``task_doc`` payload (``task_doc_discard``) and
    its nested ``discardEvidence``. ``TaskDocResponse`` is not a ``WorktreeCommandResponse``,
    so the worktree invariant never sees it. Do not widen ``PUBLIC_TOOLS`` to admit a
    non-public tool, and do not special-case it here.
    """

    assert "session_retire" in TOOL_RESPONSE_MODELS
    assert "session_retire" not in PUBLIC_TOOLS

    with pytest.raises(ValidationError) as refused:
        WorktreeStatusResponse.model_validate({"ok": True, "nextTool": "session_retire"})
    assert "not in PUBLIC_TOOLS" in str(refused.value)

    # The other surface genuinely may name it, which is what makes the rule per-surface.
    doc = TaskDocResponse.model_validate(
        {
            "ok": True,
            "operation": "remove_subtask",
            "taskId": "260698-L1",
            "slug": "01_demo-leaf",
            "kind": "subTask",
            "status": "inProgress",
            "docPath": "/tmp/01_demo-leaf.json",
            "renderedPath": "/tmp/01_demo-leaf.md",
            "nextTool": "session_retire",
        }
    )
    assert doc.nextTool == "session_retire"
