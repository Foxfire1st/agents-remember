"""L6 closeout diff-coverage tests for batch NW4.

Each test exercises one changed line or untaken branch edge from
``/tmp/l6-cov-NW4.json`` without modifying production code:

* claim_change_router: working-status parse success/invalid, HEAD tree failure,
  historical diff parse success (tabbed name-status) and invalid parse.
* unclaimed_entities: ``priority`` authority/schema returns and the
  non-Name/non-Attribute call-function fallback.
* application_boundary: empty relative-import base and unknown package rank.
* abandon: terminal outputs stopping on provider or worktree blockers.
* orchestration_tools: the rate-limited nudge result that skips the inbox post.
* mcp/tools/gates: registered lifecycle-gate and lifecycle gate-decide adapters.
* leaf_doc: asserted terminal document read failure.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest import mock

import pytest

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.application import orchestration_tools, provider_runtime
from agents_remember.code_quality import application_boundary
from agents_remember.controlplane.orchestration_nudges import OrchestrationNudgeRecord
from agents_remember.kernel.primitives.runtime_config import (
    McpRuntimeConfig,
)
from agents_remember.mcp.tools import gates as gate_tools
from agents_remember.memory_quality.integrity.onboarding_drift_check import unclaimed_entities
from agents_remember.memory_quality.integrity.onboarding_drift_check.unclaimed_entities import (
    UnclaimedEntitySource,
)
from agents_remember.memory_quality.style.citations import claim_change_router
from agents_remember.memory_quality.style.citations.claim_change_router import RepositoryChanges
from agents_remember.tasks import TaskDocument
from agents_remember.tasks import leaf_doc as leaf_doc_module
from agents_remember.tasks.leaf_doc import TerminalLeafResolutionError, resolve_terminal_leaf_doc
from agents_remember.tasks.store import write_task_docs
from agents_remember.worktrees.modules import abandon
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.terminal_validation import TerminalPreflight
from agents_remember.worktrees.worktree_contract import WorktreeContract


def _completed(
    returncode: int, stdout: str = "", stderr: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr=stderr)


def _fake_run_git(
    *,
    status: subprocess.CompletedProcess[str] | None = None,
    ls_tree: subprocess.CompletedProcess[str] | None = None,
    diff_tree: subprocess.CompletedProcess[str] | None = None,
):
    def fake(root: Path, args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if args and args[0] == "status":
            return status if status is not None else _completed(0)
        if args and args[0] == "ls-tree":
            return ls_tree if ls_tree is not None else _completed(0)
        if args and args[0] == "diff-tree":
            return diff_tree if diff_tree is not None else _completed(0)
        raise AssertionError(f"unexpected git arguments: {args}")

    return fake


def _args(**over: object) -> WorktreeArgs:
    base = {"contract_path": Path("/c/series-contract.md"), "dry_run": False, "force": False}
    base.update(over)
    return WorktreeArgs(**base)


def _contract(**over: object) -> WorktreeContract:
    base = {
        "code_repo_path": Path("/repo"),
        "code_work_branch": "ar/leaf",
        "code_source_branch": "ar/base",
        "memory_mode": "external",
        "memory_repo_path": Path("/mem"),
        "memory_work_branch": "ar/leaf-mem",
        "memory_source_branch": "ar/base-mem",
    }
    base.update(over)
    return cast(WorktreeContract, SimpleNamespace(**base))


def _doc(**over: Any) -> TaskDocument:
    base: dict[str, Any] = {
        "id": "L1",
        "slug": "a",
        "title": "Leaf",
        "kind": "subTask",
        "repo": "r",
        "type": "Docs",
        "createdAt": "2026-01-01T00:00",
    }
    base.update(over)
    return TaskDocument.model_validate(base)


class TestClaimChangeRouterNw4:
    """claim_change_router.py entries 89, 117-118, 133, 162-163, 302-303."""

    def test_working_status_success_parse(self) -> None:
        changes = RepositoryChanges(Path("/repo"), "code")
        fake = _fake_run_git(status=_completed(0, stdout=" M pkg/a.py\0"))
        with mock.patch.object(claim_change_router, "run_git", side_effect=fake):
            assert changes.route("c0ffee", "pkg/a.py") == (False, None)
        assert changes.metrics.working_delta_paths == 1

    def test_working_status_invalid_parse(self) -> None:
        changes = RepositoryChanges(Path("/repo"), "code")
        fake = _fake_run_git(status=_completed(0, stdout="M\0"))
        with mock.patch.object(claim_change_router, "run_git", side_effect=fake):
            ok, error = changes.route("c0ffee", "pkg/a.py")
        assert ok is False
        assert error is not None and "Git status is invalid" in error

    def test_head_tree_failure(self) -> None:
        changes = RepositoryChanges(Path("/repo"), "code")
        fake = _fake_run_git(ls_tree=_completed(128, stderr="ls boom"))
        with mock.patch.object(claim_change_router, "run_git", side_effect=fake):
            ok, error = changes.route("c0ffee", "pkg/a.py")
        assert ok is False and error == "ls boom"

    def test_historical_diff_success_tabbed(self) -> None:
        changes = RepositoryChanges(Path("/repo"), "code")
        fake = _fake_run_git(
            ls_tree=_completed(0, stdout="pkg/a.py\0"),
            diff_tree=_completed(0, stdout="M\tpkg/a.py\0"),
        )
        with mock.patch.object(claim_change_router, "run_git", side_effect=fake):
            assert changes.route("c0ffee", "pkg/a.py") == (False, None)
        assert changes.metrics.historical_delta_paths == 1

    def test_historical_diff_invalid_parse(self) -> None:
        changes = RepositoryChanges(Path("/repo"), "code")
        fake = _fake_run_git(
            ls_tree=_completed(0, stdout="pkg/a.py\0"),
            diff_tree=_completed(0, stdout="M\0"),
        )
        with mock.patch.object(claim_change_router, "run_git", side_effect=fake):
            ok, error = changes.route("c0ffee", "pkg/a.py")
        assert ok is False
        assert error is not None and "historical Git delta is invalid" in error


class TestUnclaimedEntitiesNw4:
    """unclaimed_entities.py entries 63-65 and 94."""

    def test_priority_authority_and_schema(self) -> None:
        authority = UnclaimedEntitySource("a.py", (), ("owner",), ())
        assert authority.priority == "authority"
        schema = UnclaimedEntitySource("b.py", (), (), ("SCHEMA_VERSION=1",))
        assert schema.priority == "schema"

    def test_call_name_unresolved_call_function(self, tmp_path: Path) -> None:
        py = tmp_path / "x.py"
        py.write_text("x = fn()['k']()\n", encoding="utf-8")
        assert unclaimed_entities.declaration_signals(py, "x.py") is None
        statement = ast.parse("x = fn()['k']()").body[0]
        assert isinstance(statement, ast.Assign)
        value = statement.value
        assert unclaimed_entities._call_name(value) is None


class TestApplicationBoundaryNw4:
    """application_boundary.py entries 99 and 113."""

    def test_resolved_imports_empty_base(self) -> None:
        node = ast.ImportFrom(module=None, names=[], level=0)
        assert application_boundary._resolved_imports(node, ["pkg"]) == [""]

    def test_permitted_unknown_package(self) -> None:
        contract = SimpleNamespace(ranks={"models": 0, "application": 1, "mcp": 2}, models_rank=0)
        assert (
            application_boundary._permitted(
                "unknown", cast(application_boundary._LayerContract, contract)
            )
            is False
        )


class TestAbandonTerminalOutputsNw4:
    """abandon.py entries 252 and 264."""

    def test_provider_blockers_stop_before_worktrees(self) -> None:
        with (
            mock.patch.object(
                provider_runtime, "teardown_worktree_providers", return_value={"p": {}}
            ),
            mock.patch.object(
                abandon, "terminal_result_blockers", return_value=[{"reason": "provider"}]
            ),
            mock.patch.object(abandon, "_abandon_worktrees") as remove_worktrees,
        ):
            result = abandon._abandon_terminal_outputs(
                _args(), _contract(), TerminalPreflight({}, {}, ())
            )
        assert result == ({"p": {}}, {}, {}, {})
        remove_worktrees.assert_not_called()

    def test_worktree_blockers_stop_after_removal(self) -> None:
        with (
            mock.patch.object(
                provider_runtime, "teardown_worktree_providers", return_value={"p": {}}
            ),
            mock.patch.object(
                abandon,
                "terminal_result_blockers",
                side_effect=[[], [{"reason": "worktree"}]],
            ),
            mock.patch.object(
                abandon, "_abandon_worktrees", return_value={"w": {"removed": True}}
            ) as remove_worktrees,
        ):
            result = abandon._abandon_terminal_outputs(
                _args(), _contract(), TerminalPreflight({}, {}, ())
            )
        assert result == ({"p": {}}, {"w": {"removed": True}}, {}, {})
        remove_worktrees.assert_called_once_with(_contract(), dry_run=False, force=False)


class TestOrchestrationNudgeRateLimitedNw4:
    """orchestration_tools.py entry 87 (rate-limited branch)."""

    def test_rate_limited_result_skips_inbox_post(self) -> None:
        store = mock.Mock()
        store.record.return_value = OrchestrationNudgeRecord(
            id="N1",
            ts="2026-08-05T10:00:00+00:00",
            state="rate-limited",
            reason="inactive",
            targetAgentId="manager-a",
            subjectAgentId="worker-a",
            message="nudge",
        )
        config = cast(McpRuntimeConfig, SimpleNamespace(coordination_root=Path("/tmp/nw4-coord")))
        with (
            mock.patch.object(orchestration_tools, "OrchestrationNudgeStore", return_value=store),
            mock.patch.object(orchestration_tools, "EventStore") as event_store_cls,
        ):
            result = orchestration_tools.orchestration_nudge_manager_tool(
                config,
                reason="inactive",
                target=orchestration_tools.NudgeTarget(agent_id="manager-a"),
                subject=orchestration_tools.NudgeSubject(subject="worker stalled"),
            )
        assert result["status"] == "rate-limited"
        assert result["ok"] is True
        assert "entryId" not in result
        event_store_cls.assert_called_once()


class TestGatePayloadAdaptersNw4:
    """mcp/tools/gates.py entries 61 and 106."""

    def test_registered_lifecycle_gate_payload(self) -> None:
        with (
            mock.patch.object(
                gate_tools, "raise_lifecycle_gate", return_value={"ok": True}
            ) as raised,
            mock.patch.object(
                gate_tools,
                "_tool_payload",
                side_effect=lambda name, payload: {"tool": name, "payload": payload},
            ),
        ):
            result = gate_tools.registered_lifecycle_gate_payload(
                cast(McpRuntimeConfig, SimpleNamespace()),
                cast(Any, SimpleNamespace()),
            )
        assert result["tool"] == "lifecycle_gate"
        assert result["payload"] == {"ok": True}
        raised.assert_called_once()

    def test_gate_decide_for_lifecycle_payload(self) -> None:
        with (
            mock.patch.object(
                gate_tools, "gate_decide_for_lifecycle_tool", return_value={"ok": True}
            ) as decided,
            mock.patch.object(
                gate_tools,
                "_tool_payload",
                side_effect=lambda name, payload: {"tool": name, "payload": payload},
            ),
        ):
            result = gate_tools.gate_decide_for_lifecycle(
                cast(McpRuntimeConfig, SimpleNamespace()),
                lifecycle_id="L1",
                verdict="approve",
                expected_gate_id="G1",
                evidence_refs=[{"ref": "x"}],
            )
        assert result["tool"] == "gate_decide"
        assert result["payload"] == {"ok": True}
        assert decided.call_args.kwargs["lifecycle_id"] == "L1"
        assert decided.call_args.kwargs["verdict"] == "approve"
        assert decided.call_args.kwargs["expected_gate_id"] == "G1"
        assert decided.call_args.kwargs["evidence_refs"] == [{"ref": "x"}]


class TestLeafDocAssertedReadFailureNw4:
    """leaf_doc.py entries 146-147."""

    def test_asserted_doc_read_failure_raises(self, tmp_path: Path) -> None:
        write_task_docs(tmp_path, [_doc(id="L1", slug="a", kind="subTask")])
        asserted = tmp_path / "a.json"
        real_read = leaf_doc_module.read_task_doc
        calls = {"n": 0}

        def flaky(path: Path):
            if path == asserted:
                calls["n"] += 1
                if calls["n"] > 1:
                    raise ValueError("boom")
            return real_read(path)

        with (
            mock.patch.object(leaf_doc_module, "read_task_doc", side_effect=flaky),
            pytest.raises(TerminalLeafResolutionError, match="cannot read asserted task document"),
        ):
            resolve_terminal_leaf_doc(tmp_path, "L1", asserted_path=asserted)
        assert calls["n"] == 2
