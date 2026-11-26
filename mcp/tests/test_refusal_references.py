"""Unit and CI proofs for Poka-Yoke MCP refusal guidance pointers."""

from __future__ import annotations

import ast
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from agents_remember.application import worktree_tools
from agents_remember.application.knowledge_write_answer import write_answer
from agents_remember.application.memory_quality.answer import quality_result_answer
from agents_remember.application.role_capsules.refusal_references import (
    REFUSAL_REFERENCES,
    SKILL_URI_PREFIX,
    refusal_reference,
    with_refusal_reference,
)
from agents_remember.application.worktree_tool_requests import OperationControlRequest
from agents_remember.cli import paseo_role_tools
from agents_remember.cli.role_held_messages import HeldStoreFull
from agents_remember.controlplane.enforcement import CloseoutGuard
from agents_remember.errors import CuratorCoherenceError
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.mcp.tools.knowledge import WriteToolRequest, _write_answer
from agents_remember.models.closeout.input import (
    CloseoutCorrectedCall,
    CloseoutInvalidField,
    CloseoutLegPlan,
    ResolvedCloseoutPlan,
)
from agents_remember.tasks import TaskDocument, write_task_doc
from agents_remember.tasks.document import SubTaskRef
from agents_remember.worktrees import review_history, route_review
from agents_remember.worktrees.closeout_input import CloseoutInputError
from agents_remember.worktrees.integration.closeout import curator_coherence, door_evidence
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_store import (
    LifecycleOperationReadError,
)
from agents_remember.worktrees.modules import closeout as closeout_module
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.queue.closeout_queue_errors import CloseoutQueueError
from agents_remember.worktrees.sync_transaction_results import (
    active_preview,
    memory_choice_required,
    resolution_required,
)
from agents_remember.worktrees.sync_transaction_state import SyncOperationRecord, SyncSideRecord
from agents_remember_test_support.testing.curator_contracts import gfm_anchor
from agents_remember_test_support.testing.manifest_step_derivation import unfenced_lines
from paseo_role_tools_test_support import ARCHIVED_AT
from test_native_retire import retire
from test_paseo_role_tools import RoleToolsTestCase, binding

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPOSITORY_ROOT / "mcp/src/agents_remember"
SKILLS_ROOT = REPOSITORY_ROOT / "skills/l-01-agent-lifecycles"
HEADING_RE = re.compile(r"^ {0,3}#{1,6}[ \t]+(.*?)(?:[ \t]+#+)?[ \t]*$")
REFUSAL_CALLS = frozenset({"with_refusal_reference", "refusal_reference"})


def _markdown_anchors(path: Path) -> set[str]:
    """Return GFM heading slugs for one Markdown file, ignoring fenced code blocks."""
    anchors: set[str] = set()
    seen: dict[str, int] = {}
    for _, line in unfenced_lines(path.read_text(encoding="utf-8")):
        match = HEADING_RE.match(line)
        if match:
            slug = gfm_anchor(match.group(1))
            count = seen.get(slug, 0)
            seen[slug] = count + 1
            anchors.add(slug if count == 0 else f"{slug}-{count}")
    return anchors


def _call_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def _key_argument(node: ast.Call, name: str) -> ast.expr | None:
    position = 1 if name == "with_refusal_reference" else 0
    if len(node.args) > position:
        return node.args[position]
    for keyword in node.keywords:
        if keyword.arg == "key":
            return keyword.value
    return None


def test_production_lookup_is_fail_safe_and_idempotent() -> None:
    assert refusal_reference("unregistered-refusal-key") is None
    assert with_refusal_reference("base text", "unregistered-refusal-key") == "base text"

    uri = REFUSAL_REFERENCES["role-message-too-long"]
    assert with_refusal_reference("Shorten text.", "role-message-too-long") == (
        f"Shorten text. See {uri}."
    )
    assert with_refusal_reference("Shorten text", "role-message-too-long") == (
        f"Shorten text. See {uri}."
    )
    assert with_refusal_reference("", "knowledge-write-refused") == (
        f"See {REFUSAL_REFERENCES['knowledge-write-refused']}."
    )
    once = with_refusal_reference("Shorten text.", "role-message-too-long")
    assert with_refusal_reference(once, "role-message-too-long") == once


def test_every_registered_refusal_pointer_resolves_to_file_and_anchor() -> None:
    problems: list[str] = []
    root = SKILLS_ROOT.resolve()
    prefix = f"{SKILL_URI_PREFIX}/"
    for key, uri in REFUSAL_REFERENCES.items():
        if not uri.startswith(prefix):
            problems.append(f"{key}: URI does not start with {prefix}: {uri}")
            continue
        relpath, _, anchor = uri[len(prefix) :].partition("#")
        target = (root / relpath).resolve()
        if not target.is_relative_to(root):
            problems.append(f"{key}: target leaves skill root: {relpath}")
        elif not target.is_file():
            problems.append(f"{key}: missing target file: {relpath}")
        elif anchor and anchor not in _markdown_anchors(target):
            problems.append(f"{key}: missing anchor #{anchor} in {relpath}")
    assert problems == []


def test_every_production_refusal_emitter_key_is_registered() -> None:
    registry_files = {
        (SRC_ROOT / "models/role_capsules/refusal_references.py").resolve(),
        (SRC_ROOT / "application/role_capsules/refusal_references.py").resolve(),
    }
    problems: list[str] = []
    used_keys: set[str] = set()
    for path in sorted(SRC_ROOT.rglob("*.py")):
        if path.resolve() in registry_files:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=path.as_posix())
        rel = path.relative_to(SRC_ROOT).as_posix()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = _call_name(node)
            if name not in REFUSAL_CALLS:
                continue
            arg = _key_argument(node, name)
            if not isinstance(arg, ast.Constant) or not isinstance(arg.value, str):
                problems.append(f"{rel}:{node.lineno}: {name} key must be a string literal")
            elif arg.value not in REFUSAL_REFERENCES:
                problems.append(f"{rel}:{node.lineno}: unregistered refusal key {arg.value!r}")
            else:
                used_keys.add(arg.value)
    assert problems == []
    assert set(REFUSAL_REFERENCES) == used_keys


class RoleMessageRefusalPointerTests(RoleToolsTestCase):
    """Family 1: role_message refusals emit their pointers; ordinary holds do not."""

    def test_held_store_full_emits_pointer_but_ordinary_hold_does_not(self) -> None:
        manager = binding("manager")
        worker = self.start(manager, "worker")["agentId"]
        self.runtime.agents[worker]["pendingPermissions"] = [{"name": "Bash"}]

        held = self.message(manager, "waiting work", agent_id=worker)
        self.assertEqual(held["status"], "held")
        full_uri = REFUSAL_REFERENCES["role-message-held-store-full"]
        self.assertNotIn(full_uri, str(held))

        with patch.object(
            paseo_role_tools, "hold_message", side_effect=HeldStoreFull("store full")
        ):
            refused = self.refusal(
                self.message(manager, "overflow work", agent_id=worker), "recipient-busy"
            )
        self.assertIn(full_uri, refused["nextAction"])

    def test_over_limit_and_file_failed_emit_message_handover_pointer(self) -> None:
        _req, worker = self.started(status="idle")
        with patch.object(paseo_role_tools, "role_message_limit", return_value=8):
            too_long = self.refusal(
                self.message(binding("worker"), "123456789", agent_id=worker),
                "message-too-long",
            )
        self.assertIn(REFUSAL_REFERENCES["role-message-too-long"], too_long["nextAction"])

        missing_file = self.refusal(
            self.message(
                binding("reviewer"),
                "attached",
                agent_id=worker,
                attach=[(self.root / "missing.md").as_posix()],
            ),
            "message-file-failed",
        )
        self.assertIn(REFUSAL_REFERENCES["role-message-file-failed"], missing_file["nextAction"])

    def test_recipient_archived_emits_pointer_with_and_without_successor(self) -> None:
        _first_req, first = self.started("worker", status="closed", archivedAt=ARCHIVED_AT)
        without_successor = self.refusal(
            self.message(binding("worker"), "ping", agent_id=first), "recipient-archived"
        )
        expected = REFUSAL_REFERENCES["role-message-recipient-archived"]
        self.assertIn(expected, without_successor["nextAction"])
        self.assertNotIn("role_start", without_successor["nextAction"])

        _second_req, _second = self.started("worker", status="idle")
        with_successor = self.refusal(
            self.message(binding("manager"), "ping", agent_id=first), "recipient-archived"
        )
        self.assertIn(expected, with_successor["nextAction"])
        self.assertNotIn("role_start", with_successor["nextAction"])


def _task_document() -> TaskDocument:
    return TaskDocument.model_validate(
        {
            "id": "L1",
            "slug": "leaf",
            "title": "Leaf",
            "kind": "subTask",
            "repo": "agents-remember",
            "createdAt": "2026-01-01T00:00",
        }
    )


def test_family_2_review_and_route_review_refusals_emit_pointers() -> None:
    doc = _task_document()
    with pytest.raises(review_history.ReviewHistoryError) as admission_exc:
        review_history.begin_task_review(
            doc, {"developerApproval": "approved", "additionalRounds": 1}, limit=3
        )
    assert admission_exc.value.status == "review-admission-invalid"
    assert REFUSAL_REFERENCES["review-admission-invalid"] in str(admission_exc.value)

    with pytest.raises(review_history.ReviewHistoryError) as candidate_exc:
        review_history.record_task_review(doc, {"verdict": "pass", "candidateTree": "not-a-sha"})
    assert candidate_exc.value.status == "review-candidate-invalid"
    assert REFUSAL_REFERENCES["review-candidate-invalid"] in str(candidate_exc.value)

    with pytest.raises(review_history.ReviewHistoryError) as kind_exc:
        review_history.record_task_review(doc, {"verdict": "pass", "reviewKind": "invalid"})
    assert kind_exc.value.status == "review-kind-invalid"
    assert REFUSAL_REFERENCES["review-kind-invalid"] in str(kind_exc.value)

    with pytest.raises(route_review.RouteReviewError) as route_exc:
        route_review.build_route_review(
            SimpleNamespace(kind="leaf"),  # type: ignore[arg-type]
            SimpleNamespace(),  # type: ignore[arg-type]
            {"verdict": "pass", "unexpected": True},
        )
    assert route_exc.value.status == "route-review-invalid"
    assert REFUSAL_REFERENCES["route-review-invalid"] in str(route_exc.value)


def _sync_side(side: str = "code") -> SyncSideRecord:
    return SyncSideRecord(
        side=side,  # type: ignore[arg-type]
        repository="/tmp/repo",
        worktree="/tmp/wt",
        workBranch="ar/work",
        sourceBranch="ar/source",
        sourceCommit="c" * 40,
        preSyncHead="b" * 40,
        backupRef=f"refs/agents-remember/sync/{side}/head",
        sourceBackupRef=f"refs/agents-remember/sync/{side}/source",
        baseBackupRef=f"refs/agents-remember/sync/{side}/base",
        plan="merge",
        conflictFiles=("src/app.py",),
    )


def test_family_3_worktree_sync_states_emit_pointers_and_keep_next_operation() -> None:
    contract = SimpleNamespace(contract_path=Path("/tmp/contract.json"))
    code_side = _sync_side("code")
    memory_side = _sync_side("memory")
    choice = memory_choice_required(contract, code_side, memory_side, {})  # type: ignore[arg-type]
    assert choice.payload["state"] == "memory-sync-choice-required"
    assert choice.payload["nextOperation"] == "choose_memory_sync_recovery"
    assert REFUSAL_REFERENCES["memory-sync-choice-required"] in str(choice.payload["summary"])

    record = SyncOperationRecord(
        generation=1,
        contractPath="/tmp/contract.json",
        taskId="T1",
        contractKind="leaf",
        codeBaseFrom="a" * 40,
        phase="code-resolution-required",
        code=code_side,
        memory=memory_side,
        createdAt="2026-01-01T00:00:00Z",
        updatedAt="2026-01-01T00:00:00Z",
    )
    conflict = resolution_required(record, {})
    assert conflict.payload["state"] == "sync-resolution-required"
    assert conflict.payload["nextOperation"] == "continue_sync_resolution"
    assert REFUSAL_REFERENCES["sync-resolution-required"] in str(conflict.payload["summary"])

    active = active_preview(record, {})
    assert active.payload["state"] == "sync-active"
    assert REFUSAL_REFERENCES["sync-active"] in str(active.payload["summary"])


def test_family_4_knowledge_write_refusals_emit_pointers(tmp_path: Path) -> None:
    config = McpRuntimeConfig(
        config_path=tmp_path / "mcp.json",
        coordination_root=tmp_path / "coord",
        workspace_root=tmp_path / "ws",
        transcript_root=tmp_path / "logs",
        repositories={},
    )
    invalid = _write_answer(WriteToolRequest(), config)
    assert invalid["state"] == "refused"
    assert invalid["refusalCode"] == "invalid-input"
    assert REFUSAL_REFERENCES["knowledge-write-invalid-input"] in invalid["refusalDetail"]

    base_report = {
        "owner": "leaf",
        "memoryRoot": "/tmp/mem",
        "codeTree": "a" * 40,
        "authorization": "auth",
        "records": [],
        "entries": [],
        "historyRows": [],
        "evidence": [],
        "carried": [],
        "requirementEndpoints": [],
        "problems": [{"where": "entries[1]", "message": "missing title"}],
        "violations": [],
        "notes": [],
        "provisionalIds": [],
        "plannedBeforeRefusal": False,
        "written": [],
        "removed": [],
    }
    refused_answer = write_answer({**base_report, "state": "refused"}, None, None)
    assert any(
        REFUSAL_REFERENCES["knowledge-write-refused"] in note for note in refused_answer["notes"]
    )
    planned_answer = write_answer({**base_report, "state": "planned", "problems": []}, None, None)
    assert planned_answer["notes"] == []


def test_family_4_memory_quality_worklist_refusal_emits_pointer_in_guidance() -> None:
    quality_answer = quality_result_answer(
        {
            "ok": False,
            "checklistStatus": "action-required",
            "curatorActionableCount": 2,
            "repairFindings": [],
            "missingOnboardingRows": [],
            "staleRouteIndexes": [],
        }
    )
    assert quality_answer["error"] == (
        "Memory quality found 2 curator-actionable findings; repair the "
        "blocking findings and rerun the check."
    )
    assert quality_answer["nextAction"] == "memory_quality_check"
    assert REFUSAL_REFERENCES["memory-quality-worklist"] in quality_answer["guidance"]


def test_family_5_closeout_gate_and_coherence_refusals_emit_pointers() -> None:
    expected = REFUSAL_REFERENCES["closeout-gate-blocked"]
    guard = CloseoutGuard(
        kind="closeout-approval",
        permitted=False,
        gate_id="gate-1",
        reason="pending developer decision",
    )
    contract = SimpleNamespace(
        lifecycle_id="lc-1",
        coordination_root=Path("/tmp/coord"),
        contract_path=Path("/tmp/contract.json"),
    )
    with patch.object(closeout_module, "status_payload", return_value={}):
        preview = closeout_module._closeout_gate_blocked_preview(contract, guard)
    assert preview["state"] == "closeout-gate-blocked"
    assert expected in str(preview["summary"])
    next_step = preview["nextStep"]
    assert isinstance(next_step, dict) and expected in str(next_step["summary"])

    with (
        patch.object(closeout_module, "_closeout_gate_guard", return_value=guard),
        pytest.raises(RuntimeError) as apply_exc,
    ):
        closeout_module._refuse_unsatisfied_closeout_gate(contract, WorktreeArgs())
    assert expected in str(apply_exc.value)

    coherence_contract = SimpleNamespace(
        memory_repo_path=Path("/tmp/mem"),
        memory_worktree=Path("/tmp/mem"),
        memory_source_branch="main",
    )
    observation = SimpleNamespace(code_candidate_tree="a" * 40, memory_candidate_tree="b" * 40)
    with (
        patch.object(curator_coherence, "converted_memory", return_value=True),
        patch.object(curator_coherence, "leaf_gate_refusal", return_value="open worklist items"),
        pytest.raises(CuratorCoherenceError) as coherence_exc,
    ):
        curator_coherence._require_knowledge_gate(
            coherence_contract,  # type: ignore[arg-type]
            observation,  # type: ignore[arg-type]
        )
    assert coherence_exc.value.status == "curator-coherence-knowledge-gate-refused"
    assert coherence_exc.value.next_action == "memory_quality_check"
    assert REFUSAL_REFERENCES["curator-coherence-knowledge-gate-refused"] in str(
        coherence_exc.value
    )


def _sample_closeout_input_error() -> CloseoutInputError:
    return CloseoutInputError(
        invalid_fields=[
            CloseoutInvalidField(
                field="code_commit_message",
                leg="code",
                observation="empty",
                code="closeout-commit-message-empty",
            )
        ],
        resolved_plan=ResolvedCloseoutPlan(
            route="worktree",
            contractKind="leaf",
            memoryMode="external",
            code=CloseoutLegPlan(state="enabled", reason="dirty"),
            memory=CloseoutLegPlan(state="enabled", reason="dirty"),
        ),
        corrected_call=CloseoutCorrectedCall(
            tool="worktree_closeout_apply",
            arguments={"contract_path": "/tmp/contract.json"},
        ),
    )


def test_family_5_door_and_operation_control_refusals_emit_pointers() -> None:
    contract = SimpleNamespace(contract_path=Path("/tmp/contract.json"))
    blocked_candidate = SimpleNamespace(
        document=SimpleNamespace(routeReview=SimpleNamespace(verdict="block", verdictRef="v.md"))
    )
    with (
        patch.object(door_evidence, "code_change_present", return_value=True),
        patch.object(door_evidence, "require_current_route_review", return_value={"status": "ok"}),
        pytest.raises(CloseoutQueueError) as door_exc,
    ):
        door_evidence._review_provenance(
            contract,  # type: ignore[arg-type]
            blocked_candidate,  # type: ignore[arg-type]
            "a" * 40,
        )
    assert door_exc.value.status == "closeout-door-route-review-blocked"
    assert REFUSAL_REFERENCES["closeout-door-route-review-blocked"] in door_exc.value.detail

    control_refusal = worktree_tools._operation_control_request_refusal(
        OperationControlRequest(
            contract_path="/tmp/contract.json",
            operation_kind="closeout",
            action="retire",
            expected_generation=0,
            intent_note="",
        )
    )
    assert control_refusal is not None
    assert control_refusal["nextAction"] == "correct-request"
    assert REFUSAL_REFERENCES["worktree-operation-control-refused"] in control_refusal["detail"]

    journal_refusal = worktree_tools._journal_read_refusal(
        "worktree_operation_control",
        "closeout",
        LifecycleOperationReadError(
            Path("/tmp/journal.json"),
            side="current-record",
            error_type="json-invalid",
            observed={},
            expected={},
        ),
    )
    assert REFUSAL_REFERENCES["worktree-operation-control-refused"] in journal_refusal["detail"]

    input_refusal = worktree_tools._closeout_input_refusal(
        "worktree_closeout_apply", _sample_closeout_input_error()
    )
    assert REFUSAL_REFERENCES["worktree-operation-control-refused"] in input_refusal["detail"]


class RetireChildRefusalPointerTests(RoleToolsTestCase):
    """Family 6: retire_child refusals emit seat-recovery pointers and archive nothing."""

    def setUp(self) -> None:
        super().setUp()
        master = self.master.document.model_copy(
            update={"subTasks": [SubTaskRef(number="01_LEAF", name="Leaf", file="01_leaf.json")]}
        )
        write_task_doc(self.master.path.parent, master)
        sprint = self.sprint.document.model_copy(update={"orchestrates": [self.master.document.id]})
        write_task_doc(self.sprint.path.parent, sprint)

    def test_outside_scope_agent_in_turn_and_open_children_emit_pointers(self) -> None:
        _req, manager = self.started("manager", status="idle")
        parent = binding("manager", agent_id=manager)
        worker = self.start(parent, "worker")["agentId"]
        self.runtime.agents[worker].update(status="running")

        outside = retire(self, binding("investigator"), worker)
        self.assertEqual(outside["status"], "outside-scope")
        self.assertIn(REFUSAL_REFERENCES["retire-child-outside-scope"], outside["detail"])
        self.assertFalse(self.runtime.agents[worker]["archivedAt"])

        in_turn = retire(self, parent, worker)
        self.assertEqual(in_turn["status"], "agent-in-turn")
        self.assertIn(REFUSAL_REFERENCES["retire-child-agent-in-turn"], in_turn["detail"])
        self.assertFalse(self.runtime.agents[worker]["archivedAt"])

        open_children = retire(self, self.architect, manager, role="manager")
        self.assertEqual(open_children["status"], "open-children")
        self.assertIn(REFUSAL_REFERENCES["retire-child-open-children"], open_children["detail"])
        self.assertFalse(self.runtime.agents[manager]["archivedAt"])
