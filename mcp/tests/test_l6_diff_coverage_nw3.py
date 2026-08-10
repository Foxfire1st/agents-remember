"""L6 closeout diff-coverage tests for worker batch NW3.

Each test targets one line or branch edge listed in ``/tmp/l6-cov-NW3.json``
for gate-tool helpers, claim-reopen evaluation, citation grammars, worktree
cleanup, single-owner task-writer bindings, dispatch expectations, and
citation tree resolution.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest import mock

import pytest

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.application import gate_tools
from agents_remember.application.gate_tools import (
    _resolve_deciding_actor,
    gate_create_tool,
    raise_lifecycle_gate,
    record_gate_decision,
    record_lifecycle_gate_decision,
)
from agents_remember.code_quality import single_owner
from agents_remember.controlplane.operator_inbox_records import OperatorInboxEntry
from agents_remember.controlplane.records import GateAnchor
from agents_remember.kernel.primitives.runtime_config import (
    McpRuntimeConfig,
)
from agents_remember.memory_quality.style.citations import (
    claim_change_router,
    claim_reopen,
    grammars,
    model,
    provenance,
    resolution,
)
from agents_remember.memory_quality.style.citations.claim_change_router import (
    ClaimRoute,
    LocalCitation,
)
from agents_remember.memory_quality.style.citations.claim_reopen import Evaluation
from agents_remember.models.application_requests import (
    GateDecisionRequest,
    LifecycleGateRequest,
)
from agents_remember.models.terminal_catalog import (
    TerminalCatalogEntry,
)
from agents_remember.observer.lifecycle_state import LifecycleError
from agents_remember.serving import dispatch_brief
from agents_remember.worktrees.modules import cleanup
from agents_remember.worktrees.modules.terminal_validation import TerminalPreflight
from agents_remember.worktrees.worktree_contract import WorktreeContract


def _citation(path: str = "a.py") -> model.Citation:
    return model.Citation(text=f"{path}:1-1", path=path, start=1, end=1)


def _claim(citation: model.Citation) -> model.Claim:
    return model.Claim(
        line=1,
        anchors=(model.Anchor(kind=model.SYMBOL, text="f"),),
        citations=(citation,),
        malformed=(),
        unchecked_spans=0,
    )


def _evaluation(router: Any, trees: claim_reopen.Trees) -> Evaluation:
    return Evaluation(
        code_commit="abc",
        trees=trees,
        histories=cast(provenance.Histories, SimpleNamespace()),
        current_files=cast(claim_reopen.CurrentFiles, SimpleNamespace()),
        source_views=cast(claim_reopen.SourceViews, SimpleNamespace()),
        router=cast(claim_change_router.ClaimChangeRouter, router),
    )


def _ambiguous_trees(root: Path) -> claim_reopen.Trees:
    """Both roots own the cited top-level path, so classification is ambiguous."""
    code = root / "code"
    memory = root / "memory"
    code.mkdir()
    memory.mkdir()
    (code / "a.py").mkdir()
    (memory / "a.py").mkdir()
    return claim_reopen.Trees(code_root=code, memory_root=memory)


def _memory_trees(root: Path) -> claim_reopen.Trees:
    """Only the memory root owns the cited path."""
    code = root / "code"
    memory = root / "memory"
    code.mkdir()
    memory.mkdir()
    (memory / "a.py").write_text("def f():\n    pass\n", encoding="utf-8")
    return claim_reopen.Trees(code_root=code, memory_root=memory)


def _absent_trees(root: Path) -> claim_reopen.Trees:
    """Neither root owns the cited path, so classification is silently absent."""
    code = root / "code"
    memory = root / "memory"
    code.mkdir()
    memory.mkdir()
    return claim_reopen.Trees(code_root=code, memory_root=memory)


class TestGateToolBranches:
    def test_resolve_deciding_actor_missing_actor(self) -> None:
        with pytest.raises(ValueError, match="gate decision actor must be non-empty"):
            _resolve_deciding_actor(None, "cli")

    def test_resolve_deciding_actor_orchestration_without_ambient(self) -> None:
        with (
            mock.patch.object(gate_tools, "ambient", return_value=None),
            pytest.raises(LifecycleError, match="active deciding lifecycle"),
        ):
            _resolve_deciding_actor(None, "orchestration")

    def test_gate_create_skips_non_open_current(self) -> None:
        store = SimpleNamespace(
            current=lambda lifecycle_id: {"old": SimpleNamespace(state="expired", id="old")},
            append=lambda gate: None,
        )
        with mock.patch.object(gate_tools, "_store", return_value=store):
            result = gate_create_tool(
                cast(McpRuntimeConfig, SimpleNamespace()),
                kind="plan-approval",
                anchor=GateAnchor(lifecycle_id="L"),
            )
        assert result["ok"] is True and result["state"] == "open"

    def test_raise_lifecycle_gate_blocking_without_ask(self) -> None:
        current = SimpleNamespace(id="L", state="running", phase="build")
        fake_ambient = SimpleNamespace(
            current=current,
            block=lambda **kwargs: SimpleNamespace(id="BLOCKED", state="blocked", phase="ask"),
        )
        store = SimpleNamespace(
            current=lambda lifecycle_id: {"old": SimpleNamespace(state="approved", id="old")},
            append=lambda gate: None,
        )
        wait_result = {
            "state": "approved",
            "gateId": "g",
            "timedOut": False,
            "entryCount": 0,
            "entries": [],
            "decidedBy": "developer",
            "decidedVia": "dashboard",
            "decisionNote": "ok",
        }
        request = SimpleNamespace(
            kind="plan-approval",
            ask=None,
            lifecycle_id="L",
            enclosure=None,
            repo_id=None,
            packet=None,
            required_decision=None,
            evidence_refs=None,
            wait=True,
        )
        with (
            mock.patch.object(gate_tools, "require_ambient", return_value=fake_ambient),
            mock.patch.object(gate_tools, "_store", return_value=store),
            mock.patch.object(gate_tools, "gate_response_wait_tool", return_value=wait_result),
        ):
            result = raise_lifecycle_gate(
                cast(McpRuntimeConfig, SimpleNamespace()),
                cast(LifecycleGateRequest, request),
            )
        assert result["ok"] is True
        assert "ask" not in result

    def test_record_gate_decision_success(self) -> None:
        request = SimpleNamespace(
            gate_id="g",
            lifecycle_id="L",
            decision="approved",
            decided_by="developer",
            decided_via="cli",
            note=None,
            deciding_role=None,
            evidence_refs=None,
        )
        with (
            mock.patch.object(gate_tools, "_store", return_value=SimpleNamespace()),
            mock.patch.object(gate_tools, "_inbox_store", return_value=SimpleNamespace()),
            mock.patch.object(gate_tools, "_gate_policy", return_value=SimpleNamespace()),
            mock.patch.object(
                gate_tools,
                "persist_gate_decision",
                return_value={"ok": True, "state": "approved"},
            ),
        ):
            result = record_gate_decision(
                cast(McpRuntimeConfig, SimpleNamespace()),
                cast(GateDecisionRequest, request),
            )
        assert result["ok"] is True and result["state"] == "approved"

    def test_record_lifecycle_gate_decision_success(self) -> None:
        request = SimpleNamespace(
            gate_id=None,
            lifecycle_id="L",
            decision="approved",
            decided_by="developer",
            decided_via="cli",
            note=None,
            deciding_role=None,
            evidence_refs=None,
        )
        with (
            mock.patch.object(gate_tools, "_store", return_value=SimpleNamespace()),
            mock.patch.object(gate_tools, "_inbox_store", return_value=SimpleNamespace()),
            mock.patch.object(gate_tools, "_gate_policy", return_value=SimpleNamespace()),
            mock.patch.object(
                gate_tools,
                "persist_lifecycle_gate_decision",
                return_value={"ok": True, "state": "approved"},
            ),
        ):
            result = record_lifecycle_gate_decision(
                cast(McpRuntimeConfig, SimpleNamespace()),
                cast(GateDecisionRequest, request),
            )
        assert result["ok"] is True and result["state"] == "approved"


class TestClaimReopenEvaluationBranches:
    def test_memory_commit_uses_cache(self, tmp_path: Path) -> None:
        calls: list[str] = []
        router = SimpleNamespace(
            memory_commit=lambda commit: calls.append(commit) or provenance.Read(text="abc")
        )
        evaluation = _evaluation(router, _absent_trees(tmp_path))
        assert evaluation.memory_commit() is evaluation.memory_commit()
        assert calls == ["abc"]

    def test_source_classify_error(self, tmp_path: Path) -> None:
        router = SimpleNamespace()
        source, error = _evaluation(router, _ambiguous_trees(tmp_path)).source(_citation())
        assert source is None and error is not None and "ambiguous" in error

    def test_memory_source_unmapped(self, tmp_path: Path) -> None:
        router = SimpleNamespace(
            memory_commit=lambda commit: provenance.Read(text=None, error="memory mapping missing"),
        )
        source, error = _evaluation(router, _memory_trees(tmp_path)).source(_citation())
        assert source is None and error == "memory mapping missing"

    def test_evaluate_claim_local_source_error(self, tmp_path: Path) -> None:
        citation = _citation()
        route = ClaimRoute(
            local=(cast(LocalCitation, SimpleNamespace(citation=citation)),),
            dependencies=(),
            status="semantic-required",
        )
        router = SimpleNamespace(
            route_claim=lambda citations, commit: route,
        )
        finding = claim_reopen.evaluate_claim(
            "doc.md", _claim(citation), _evaluation(router, _ambiguous_trees(tmp_path))
        )
        assert finding is not None and "ambiguous" in finding.message

    def test_evaluate_claim_local_source_none_without_error(self, tmp_path: Path) -> None:
        citation = _citation()
        route = ClaimRoute(
            local=(cast(LocalCitation, SimpleNamespace(citation=citation)),),
            dependencies=(),
            status="semantic-required",
        )
        router = SimpleNamespace(
            route_claim=lambda citations, commit: route,
        )
        assert (
            claim_reopen.evaluate_claim(
                "doc.md", _claim(citation), _evaluation(router, _absent_trees(tmp_path))
            )
            is None
        )


class _FakeNode:
    def __init__(self, type: str, **kwargs: Any) -> None:
        self.type = type
        self.start_byte = kwargs.get("start_byte", 0)
        self.end_byte = kwargs.get("end_byte", 0)
        self.has_error = kwargs.get("has_error", False)
        self.named_children = kwargs.get("named_children", ())
        children = kwargs.get("children")
        self.children = self.named_children if children is None else children
        self.text = kwargs.get("text")
        self._fields = kwargs.get("fields") or {}

    def child_by_field_name(self, field: str) -> _FakeNode | None:
        return self._fields.get(field)


class _FakeTree:
    def __init__(self, root_node: _FakeNode) -> None:
        self.root_node = root_node


class _FakeParser:
    def __init__(self, trees: list[_FakeTree]) -> None:
        self._trees = iter(trees)

    def parse(self, _source: bytes) -> _FakeTree:
        return next(self._trees)


class TestCitationGrammarBranches:
    @staticmethod
    def _wrapper_call_node(text: str, name: bytes) -> _FakeNode:
        prefix = "const __citation_anchor = "
        start = len(prefix.encode("utf-8"))
        end = start + len(text.encode("utf-8"))
        return _FakeNode(
            "call_expression",
            start_byte=start,
            end_byte=end,
            has_error=False,
            named_children=(_FakeNode("identifier", text=name),),
        )

    def test_anchor_identifier_signature_without_declaration(self) -> None:
        text = "alpha()"
        signature_root = _FakeNode("program", has_error=False, named_children=())
        wrapper_root = _FakeNode(
            "program",
            has_error=False,
            named_children=(self._wrapper_call_node(text, b"alpha"),),
        )
        parser = _FakeParser([_FakeTree(signature_root), _FakeTree(wrapper_root)])
        with (
            mock.patch.object(grammars, "Parser", return_value=parser),
            mock.patch.object(grammars, "language", return_value="typescript"),
        ):
            assert grammars.typescript_anchor_identifier(text) == "alpha"

    def test_anchor_identifier_declaration_without_identifier_name(self) -> None:
        text = "beta()"
        declaration = _FakeNode("function_declaration", named_children=(), fields={"name": None})
        signature_root = _FakeNode(
            "program",
            has_error=False,
            named_children=(declaration,),
        )
        wrapper_root = _FakeNode(
            "program",
            has_error=False,
            named_children=(self._wrapper_call_node(text, b"beta"),),
        )
        parser = _FakeParser([_FakeTree(signature_root), _FakeTree(wrapper_root)])
        with (
            mock.patch.object(grammars, "Parser", return_value=parser),
            mock.patch.object(grammars, "language", return_value="typescript"),
        ):
            assert grammars.typescript_anchor_identifier(text) == "beta"


class TestCleanupBranches:
    def test_origin_refusal_remote_query_failure(self) -> None:
        failed = SimpleNamespace(returncode=128, stdout="", stderr="remote error")
        with mock.patch.object(cleanup, "run_git", return_value=failed):
            result = cleanup._origin_refusal(Path("/repo"))
        assert result == {"remote_deleted": False, "reason": "remote error"}

    def test_cleanup_terminal_outputs_drift_blocker(self, tmp_path: Path) -> None:
        args = SimpleNamespace(dry_run=False, teardown_providers=False)
        contract = SimpleNamespace(
            coordination_root=tmp_path,
            code_worktree=SimpleNamespace(name="code-wt"),
            code_work_branch="ar/x",
        )
        with (
            mock.patch.object(
                cleanup,
                "terminal_result_blockers",
                side_effect=[False, False, False, True],
            ),
            mock.patch.object(cleanup, "_removed_worktrees", return_value={}),
            mock.patch.object(cleanup, "_deleted_branches", return_value={}),
            mock.patch.object(cleanup, "remove_drift_snapshot", return_value={"removed": False}),
        ):
            providers, worktrees, branches, drift_snapshots, directories = (
                cleanup._cleanup_terminal_outputs(
                    cast(Any, args),
                    cast(WorktreeContract, contract),
                    cast(TerminalPreflight, SimpleNamespace()),
                )
            )
        assert providers["state"] == "skipped"
        assert worktrees == {} and branches == {} and directories == {}
        assert drift_snapshots["code"] == {"removed": False}


class TestSingleOwnerBranches:
    def test_task_writer_bindings_module_alias_from_import(self) -> None:
        tree = ast.parse("from agents_remember.tasks import store\n")
        _writers, modules = single_owner._task_writer_bindings(
            tree, "agents_remember.tasks.leaf_doc"
        )
        assert modules["store"] == "agents_remember.tasks.store"


class TestDispatchBriefBranches:
    def test_start_dispatch_expectations_skips_existing_rows(self) -> None:
        store = SimpleNamespace(find_by_source=lambda entry_id, kind: SimpleNamespace(id="row"))
        entry = SimpleNamespace(id="e", createdAt="2026-08-05T00:00:00+00:00")
        target = SimpleNamespace(
            binding_leaf_key="repo/master/leaf-1",
            label="curator",
            spawn_role="curator",
            kind="harness",
            id="t",
            lifecycle_id="L",
            binding_role="curator",
        )
        with (
            mock.patch.object(dispatch_brief, "expectation_store", return_value=store),
            mock.patch.object(dispatch_brief, "write_expectation_row") as write,
        ):
            dispatch_brief.start_dispatch_expectations(
                cast(McpRuntimeConfig, SimpleNamespace()),
                cast(OperatorInboxEntry, entry),
                cast(TerminalCatalogEntry, target),
            )
        assert store.find_by_source("e", "briefed-by") is not None
        write.assert_not_called()


class TestResolutionBranches:
    def test_operation_trees_without_cache_authority(self, tmp_path: Path) -> None:
        onboarding = tmp_path / "onboarding"
        onboarding.mkdir()
        trees = resolution.Trees(code_root=tmp_path / "code", memory_root=tmp_path)
        assert resolution.operation_trees(onboarding, trees) is trees
