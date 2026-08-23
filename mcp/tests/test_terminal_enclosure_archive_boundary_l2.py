"""L2 fail-closed forcing before L5 owns terminal enclosure archival."""

from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest
from agents_remember.application.worktree_tools import (
    worktree_abandon_tool,
    worktree_cleanup_tool,
)
from agents_remember.kernel.primitives.runtime_config import load_config
from agents_remember.worktrees.modules import abandon as abandon_module
from agents_remember.worktrees.modules import cleanup as cleanup_module
from lifecycle_enclosure_test_support import byte_tree
from test_lifecycle_operations import _contract


@pytest.mark.parametrize("operation", ["cleanup", "abandon"])
@pytest.mark.parametrize("dry_run", [True, False])
def test_terminal_cleanup_and_abandon_refuse_before_any_destructive_seam(
    tmp_path: Path,
    operation: str,
    dry_run: bool,
) -> None:
    contract = _contract(tmp_path)
    config = load_config(tmp_path / "settings.json")
    before = byte_tree(tmp_path)
    forbidden = mock.Mock(side_effect=AssertionError("terminal mutation ran before archive proof"))

    if operation == "cleanup":
        call = lambda: worktree_cleanup_tool(  # noqa: E731
            config,
            contract_path=contract.contract_path.as_posix(),
            dry_run=dry_run,
        )
        patches = (
            mock.patch.object(cleanup_module, "terminal_preflight", forbidden),
            mock.patch.object(cleanup_module, "_cleanup_terminal_outputs", forbidden),
            mock.patch.object(cleanup_module, "_removed_directories", forbidden),
            mock.patch.object(cleanup_module, "write_contract", forbidden),
            mock.patch.object(cleanup_module, "worktree_services", forbidden),
        )
    else:
        call = lambda: worktree_abandon_tool(  # noqa: E731
            config,
            contract_path=contract.contract_path.as_posix(),
            dry_run=dry_run,
            force=True,
        )
        patches = (
            mock.patch.object(abandon_module, "terminal_preflight", forbidden),
            mock.patch.object(abandon_module, "_abandon_terminal_outputs", forbidden),
            mock.patch.object(abandon_module, "write_contract", forbidden),
            mock.patch.object(abandon_module, "worktree_services", forbidden),
        )

    with patches[0], patches[1], patches[2], patches[3]:
        if len(patches) == 5:
            with patches[4]:
                result = call()
        else:
            result = call()

    assert result["ok"] is False
    assert result["status"] == "terminal-archive-required"
    assert result["developerDecisionRequired"] is True
    assert result["nextAction"] == "developer-decision"
    assert byte_tree(tmp_path) == before
    forbidden.assert_not_called()
