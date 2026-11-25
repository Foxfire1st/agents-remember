"""A prepared role launch and terminal cleanup arbitrate one enclosure generation."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path

import pytest
from agents_remember.application import worktree_tools
from agents_remember.cli import (
    paseo_catalog,
    paseo_launch,
    role_launch_preparation,
    role_launch_receipts,
    role_launch_routes,
)
from agents_remember.cli.role_launch_preparation import PreparedRoleHandover
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig, load_config
from agents_remember.models.role_launcher import RoleDispatchRequest
from agents_remember.worktrees.worktree_contract import WorktreeContract
from fastapi import HTTPException
from paseo_launch_test_support import prepare_host
from role_first_message_support import _CatalogRuntime
from test_terminal_blocker_reasons import (
    _landed_leaf,
    _landed_leaf_config,
    bound_worktree_services,  # noqa: F401 - shared pytest fixture
)

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("bound_worktree_services")]


@dataclass(frozen=True, slots=True)
class _RoleStartFixture:
    contract: WorktreeContract
    config: McpRuntimeConfig
    request: RoleDispatchRequest
    runtime: _CatalogRuntime
    receipt_path: Path

    def cleanup_before_receipt(self) -> None:
        assert not self.receipt_path.exists()
        cleaned = worktree_tools.worktree_cleanup_tool(
            self.config, contract_path=self.contract.contract_path.as_posix()
        )
        assert cleaned["ok"], cleaned
        assert not self.contract.worktree_group.exists()


@pytest.fixture
def role_start(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _RoleStartFixture:
    contract = _landed_leaf(tmp_path)
    config = _landed_leaf_config(tmp_path, contract)
    prepare_host(tmp_path / "paseo", config.config_path, configured=True)
    config = load_config(config.config_path)
    request = RoleDispatchRequest.model_validate(
        {
            "role": "worker",
            "requestId": uuid.uuid4(),
            "sprintDocumentRef": {"repository": contract.repo_name, "path": "sprint/task.json"},
            "masterDocumentRef": {"repository": contract.repo_name, "path": "master/task.json"},
            "taskDocumentRef": {"repository": contract.repo_name, "path": "master/leaf-1.json"},
            "agentOverride": {"agentId": "codex"},
        }
    )
    receipt_path = role_launch_receipts._receipt_path(config, request)
    runtime = _CatalogRuntime()
    monkeypatch.setenv("HOME", (tmp_path / "home").as_posix())
    monkeypatch.setattr(paseo_catalog, "bridge_call", runtime)
    monkeypatch.setattr(paseo_launch, "bridge_call", runtime)
    return _RoleStartFixture(contract, config, request, runtime, receipt_path)


def test_cleanup_cannot_be_undone_by_a_prepared_role_start(
    role_start: _RoleStartFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    contract = role_start.contract
    memory_repo = contract.memory_repo_path
    assert memory_repo is not None
    # Capsule compilation resolves the fixture's physical code repository basename.
    memory_alias = (
        contract.coordination_root / "memory-repos" / f"ar-{contract.code_repo_path.name}"
    )
    memory_alias.symlink_to(memory_repo, target_is_directory=True)
    prepare = role_launch_routes.prepare_role_handover
    cleanup_completed = False

    def cleanup_after_preparation(*args, **kwargs) -> PreparedRoleHandover:
        nonlocal cleanup_completed
        prepared = prepare(*args, **kwargs)
        report = Path(prepared.handover["taskReportPath"])
        assert report.is_relative_to(role_start.contract.worktree_group)
        role_start.cleanup_before_receipt()
        cleanup_completed = True
        return prepared

    monkeypatch.setattr(role_launch_routes, "prepare_role_handover", cleanup_after_preparation)
    with pytest.raises(HTTPException) as refused:
        role_launch_routes._role_launch_dispatch_endpoint(role_start.config, role_start.request)

    assert cleanup_completed, refused.value.detail
    assert refused.value.status_code == 409
    assert not role_start.contract.worktree_group.exists(), (
        "prepared role start recreated the retired group"
    )
    assert "enclosure" in str(refused.value.detail)
    assert not role_start.receipt_path.exists()
    assert not role_start.runtime.agents


def test_cleanup_cannot_be_undone_during_role_preparation(
    role_start: _RoleStartFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    resolve_workspace = role_launch_preparation._resolve_workspace

    def cleanup_after_workspace_resolution(*args, **kwargs) -> dict[str, str]:
        workspace = resolve_workspace(*args, **kwargs)
        assert Path(workspace["path"]) == role_start.contract.worktree_group
        role_start.cleanup_before_receipt()
        return workspace

    monkeypatch.setattr(
        role_launch_preparation, "_resolve_workspace", cleanup_after_workspace_resolution
    )
    with pytest.raises(HTTPException) as refused:
        role_launch_routes._role_launch_dispatch_endpoint(role_start.config, role_start.request)

    assert refused.value.status_code == 409
    assert not role_start.contract.worktree_group.exists(), (
        "role preparation recreated the retired group before writing a receipt"
    )
    assert not role_start.receipt_path.exists()
    assert not role_start.runtime.agents
