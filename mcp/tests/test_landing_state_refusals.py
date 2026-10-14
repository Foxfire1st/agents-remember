"""Task-state refusals answer instead of raising (MIK-R111 rules 1 and 7).

Each case drives the production MCP path (``EntryPointWorld.call``) and
asserts the delivered answer: ``ok: false`` with a ``code``, one sentence,
and the next step where exactly one exists -- and that nothing was written.
Every case fails without the change (the call raises and the envelope is
Every case fails without the change (the call raises and the envelope is
lost). The four sweep-pinned preconditions are covered here, plus the
dry-run/state agreement of rule 1 (evidence item 10).
"""

from __future__ import annotations

import ast as _ast
import inspect as _inspect
import json as _json
import os as _os
import re as _re
import tempfile as _tempfile
import unittest
from contextlib import contextmanager as _contextmanager
from dataclasses import replace as _replace
from pathlib import Path as _Path
from typing import Any, ClassVar, cast

import test_tool_answer_table as _rows_table
import tool_answer_l111_test_support as _l111
from agents_remember.application import worktree_tools as _worktree_tools
from agents_remember.application.knowledge_writer.memory_state import Owner
from agents_remember.application.knowledge_writer.writer import WriteRequest, write_knowledge
from agents_remember.application.tool_answer_log import tool_call_context
from agents_remember.application.tool_response import complete_tool_response
from agents_remember.controlplane.enforcement import GateGuard
from agents_remember.kernel.memory_attribution import render_memory_content_message
from agents_remember.kernel.primitives.gate_vocab import STORED_GATE_WAY_OUT
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.models.tokens import count_response_tokens as _count
from agents_remember.models.tools.tool_answer import FRAMEWORK_ANSWER_KEYS
from agents_remember.models.tools.tool_answer import bound_for as _bound_for
from agents_remember.tasks import read_task_doc
from agents_remember.tasks import write_task_doc as _write
from agents_remember.worktrees import task_document_copies as copies
from agents_remember.worktrees.integration.integration_branch_authority import (
    require_terminal_worktree as _require_terminal_worktree,
)
from agents_remember.worktrees.modules import closeout as closeout_module
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.closeout import REFUSED_CLOSEOUT_PREVIEW_STATES
from agents_remember.worktrees.modules.integrate import _checkpoint_dry_run_result, _dry_run_result
from agents_remember.worktrees.modules.integration_publication import IntegratePreview
from agents_remember.worktrees.modules.state_refusals import (
    closeout_queue_refusal,
    shaped_terminal_blockers,
    terminal_state_refusal,
)
from agents_remember.worktrees.modules.terminal_validation import (
    require_series_children_retired as _require_series_children_retired,
)
from agents_remember.worktrees.queue.closeout_queue_errors import CloseoutQueueError
from agents_remember.worktrees.worktree_contract import load_contract, write_contract
from agents_remember_test_support.testing.waits import wait_until
from test_closeout_queue import QueueFixture
from tool_entry_point_world import REPO, EntryPointWorld, _git


def _refusal(test: unittest.TestCase, kind: str, payload: dict[str, Any] | None) -> dict[str, Any]:
    """The delivered refusal, or a failure that names what arrived instead."""

    test.assertEqual(kind, "RETURNED", f"the call raised instead of answering: {payload}")
    assert payload is not None
    test.assertFalse(payload.get("ok"), f"a refusal reports ok:false: {payload}")
    test.assertNotIn("error", payload, "D279: an ordinary refusal is not a raised error")
    return payload


def _preseed_converted_memory(world: EntryPointWorld) -> EntryPointWorld:
    """Build converted memory in the fixture before task start (MIK-R46 rules 6+8).

    The cutover lock precedes ordinary memory operations, the invariant gate reads the
    pairing on the official line, and the source guard forbids moving branches after
    task start: the layout marker and the Code-Commit trailer ride the memory main
    checkout, so the scaffold derives an already-converted ar/m that the task records.
    Call between world construction and build(); assertions stay exactly as they are.
    """
    marker = world.memory / "knowledge" / "layout.json"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(
        '{\n  "conversion": "1",\n  "schema": "ar-memory-layout/v2"\n}\n',
        encoding="utf-8",
    )
    code_base = _git(world.code, "rev-parse", "HEAD").strip()
    _git(world.memory, "add", "knowledge/layout.json")
    _git(
        world.memory,
        "commit",
        "-m",
        render_memory_content_message("Build converted memory fixture", code_base),
    )
    return world


def _cover_fixture_code_changes(contract, *paths: str) -> None:
    """Author ``no_impact`` rows for the fixture's code files (MIK-R30).

    Same sanctioned shape as the closeout-queue fixture helper: a converted fixture is
    judged by the invariant gate, so the leaf covers its code change the way a real leaf
    does before the lifecycle under test judges the candidate.
    """
    if contract.memory_worktree is None:
        return
    report = write_knowledge(
        WriteRequest(
            memory_root=contract.memory_worktree,
            code_root=contract.code_worktree,
            owner=Owner(task=contract.task_id, kind="leaf", id=contract.leaf_id or "?"),
            handoff_path="handoff.json",
            document={
                "history": [
                    {
                        "subject": f"onboarding:{path}",
                        "disposition": "no_impact",
                        "reason": "The fixture leaf's code change carries no invariant.",
                    }
                    for path in paths
                ]
            },
            commit=True,
        )
    )
    assert report.state == "written", report.render()


class LandingStateRefusalTests(unittest.TestCase):
    def _world(self) -> EntryPointWorld:
        world = EntryPointWorld()
        self.addCleanup(world.close)
        world.build()
        return world

    def test_integrate_before_closeout_is_answered(self) -> None:
        world = self._world()
        kind, payload = world.call(
            "worktree_integrate",
            {"contract_path": world.contract.as_posix(), "dry_run": True},
        )
        answer = _refusal(self, kind, payload)
        self.assertEqual(answer.get("state"), "blocked")
        self.assertEqual(answer.get("code"), "closeout-incomplete")
        self.assertIn("integration requires closeout.status completed", answer.get("summary", ""))
        next_step = answer.get("next") or {}
        self.assertEqual(next_step.get("tool"), "worktree_closeout_preview")
        stored = load_contract(world.contract)
        self.assertEqual(stored.closeout_status, "not-started")
        self.assertEqual(stored.integration_status, "not-started")

    def test_checkpoint_on_a_leaf_contract_is_answered(self) -> None:
        world = self._world()
        kind, payload = world.call(
            "worktree_checkpoint_landing",
            {"contract_path": world.contract.as_posix(), "dry_run": True},
        )
        answer = _refusal(self, kind, payload)
        self.assertEqual(answer.get("state"), "refused")
        self.assertEqual(answer.get("code"), "contract-not-a-series")
        self.assertIn("requires an atomic series contract", answer.get("summary", ""))

    def test_record_commit_that_landed_nowhere_is_answered(self) -> None:
        world = self._world()
        _git(world.code, "switch", "-c", "side")
        (world.code / "unlanded.txt").write_text("x\n", encoding="utf-8")
        _git(world.code, "add", "-A")
        _git(world.code, "commit", "-m", "never merged")
        unlanded = _git(world.code, "rev-parse", "side")
        kind, payload = world.call(
            "worktree_record_landing",
            {
                "contract_path": world.contract.as_posix(),
                "landed_code_commit": unlanded,
                "dry_run": True,
            },
        )
        answer = _refusal(self, kind, payload)
        self.assertEqual(answer.get("state"), "refused")
        self.assertEqual(answer.get("code"), "commit-not-on-landing-target")
        self.assertIn("not reachable from any landing target", answer.get("summary", ""))
        stored = load_contract(world.contract)
        self.assertEqual(stored.integration_status, "not-started")

    def test_record_without_a_commit_is_answered(self) -> None:
        world = self._world()
        kind, payload = world.call(
            "worktree_record_landing",
            {"contract_path": world.contract.as_posix(), "landed_code_commit": "", "dry_run": True},
        )
        answer = _refusal(self, kind, payload)
        self.assertEqual(answer.get("state"), "refused")
        self.assertEqual(answer.get("code"), "commit-missing")

    def test_cleanup_before_integration_is_answered(self) -> None:
        world = self._world()
        kind, payload = world.call(
            "worktree_cleanup",
            {"contract_path": world.contract.as_posix(), "dry_run": True},
        )
        answer = _refusal(self, kind, payload)
        self.assertEqual(answer.get("state"), "blocked")
        self.assertEqual(answer.get("code"), "integration-incomplete")
        self.assertIn("cleanup requires integration.status completed", answer.get("summary", ""))
        next_step = answer.get("next") or {}
        self.assertEqual(next_step.get("tool"), "worktree_integrate")

    def test_integrate_with_a_dirty_worktree_is_answered(self) -> None:
        world = self._landed_world()
        contract = load_contract(world.contract)
        (contract.code_worktree / "dirty.txt").write_text("uncommitted\n", encoding="utf-8")
        kind, payload = world.call(
            "worktree_integrate",
            {"contract_path": world.contract.as_posix(), "dry_run": True},
        )
        answer = _refusal(self, kind, payload)
        self.assertEqual(answer.get("state"), "blocked")
        self.assertEqual(answer.get("code"), "integration-blocked")

    def test_cleanup_dry_run_with_blockers_agrees_with_the_real_call(self) -> None:
        """Rule 1, evidence item 10: a dry run with blockers answers blocked.

        After a real integration, one more commit on the work branch leaves
        unmerged work the preflight blocks on. The real call refuses; the dry
        run must promise nothing else.
        """

        world = self._landed_world()
        contract = load_contract(world.contract)
        _git(contract.code_worktree, "switch", contract.code_work_branch)
        (contract.code_worktree / "after-integration.txt").write_text(
            "unmerged\n", encoding="utf-8"
        )
        _git(contract.code_worktree, "add", "-A")
        _git(contract.code_worktree, "commit", "-m", "work after integration")

        dry_kind, dry = world.call(
            "worktree_cleanup",
            {"contract_path": world.contract.as_posix(), "dry_run": True},
        )
        dry_answer = _refusal(self, dry_kind, dry)
        real_kind, real = world.call(
            "worktree_cleanup",
            {"contract_path": world.contract.as_posix(), "dry_run": False},
        )
        real_answer = _refusal(self, real_kind, real)
        for answer in (dry_answer, real_answer):
            self.assertEqual(answer.get("state"), "blocked")
            self.assertEqual(answer.get("code"), "cleanup-blocked")
            blockers = answer.get("blockers")
            self.assertIsInstance(blockers, list)
            assert isinstance(blockers, list)
            self.assertTrue(blockers, "a blocked answer names its blockers")
        # The real refusal reclaims nothing: the unmerged work is still there.
        self.assertTrue((contract.code_worktree / "after-integration.txt").is_file())

    def _landed_world(self) -> EntryPointWorld:
        """One world driven through a real closeout and integration."""

        world = EntryPointWorld()
        self.addCleanup(world.close)
        world = _preseed_converted_memory(world)
        world.build()
        contract_path = world.contract.as_posix()
        contract = load_contract(world.contract)
        (contract.code_worktree / "landing-work.txt").write_text("work\n", encoding="utf-8")
        _cover_fixture_code_changes(contract, "landing-work.txt")
        kind, preview = world.call(
            "worktree_closeout_preview",
            {
                "contract_path": contract_path,
                "code_commit_message": "Fixture code",
                "memory_commit_message": "Fixture memory",
            },
        )
        self.assertEqual(kind, "RETURNED")
        assert preview is not None
        self.assertTrue(preview.get("ok"), preview.get("error"))
        kind, _applied = world.call(
            "worktree_closeout_apply",
            {
                "contract_path": contract_path,
                "intent_note": "landing state refusal fixture",
                "code_commit_message": "Fixture code",
                "memory_commit_message": "Fixture memory",
            },
        )
        self.assertEqual(kind, "RETURNED")

        def closed() -> bool:
            world.call("worktree_status", {"repo_id": REPO, "contract_path": contract_path})
            return load_contract(world.contract).closeout_status == "completed"

        wait_until(closed, "fixture closeout completes")
        self.assertEqual(load_contract(world.contract).closeout_status, "completed")
        kind, integrated = world.call("worktree_integrate", {"contract_path": contract_path})
        self.assertEqual(kind, "RETURNED")
        assert integrated is not None
        self.assertTrue(integrated.get("ok"), integrated.get("error"))
        self.assertEqual(load_contract(world.contract).integration_status, "completed")
        return world


if __name__ == "__main__":
    unittest.main()


class LandingAnswerSizeTests(unittest.TestCase):
    """Rule 1, evidence item 1 (ordinary half): separated answers meet targets.

    One real landing through the production MCP path. Each ordinary answer
    is measured three ways (revision 218d585d point 4): without its
    locations -- removed at every depth -- it is at or under its packet
    target; it carries at most two distinct locations; and the whole
    delivered answer stays under its unchanged bound. Every dropped
    pre-change field is still in the whole result the answer's ``log``
    names, compared field for field, itself under the unchanged R108 bound.
    """

    TARGETS: ClassVar = {
        "worktree_start": 300,
        "worktree_status": 300,
        "worktree_sync": 300,
        "worktree_closeout_preview": 600,
        "worktree_closeout_apply": 400,
        "worktree_integrate": 300,
        "worktree_cleanup": 200,
        "lifecycle_finalize_task": 300,
    }

    BANNED: ClassVar = frozenset(
        {
            "source_lineage",
            "freshness",
            "route_index_refresh",
            "onboarding_metadata_refresh",
            "entity_fingerprint_refresh",
            "route_overview_metadata_refresh",
            "refreshed_entities",
            "refreshed_onboarding",
            "refreshed_route_overviews",
            "agentArchive",
            "taskArchive",
            "terminalArchive",
            "branches",
            "directories",
            "removed_worktrees",
            "drift_snapshots",
            "kept_branches",
            "projectionEffects",
            "review_rebinding",
            "final_comparison_selection",
            "final_output_receipt",
            "landing",
            "external_git_movement",
            "memory_ledger_repair",
            "taskUpdates",
            "closeout_order",
            "integration_reopen",
        }
    )
    # ``closeout_gate`` is not banned: the preview evaluates the gate verdict
    # and the blocked preview must retain it (MIK-R111 rule 1).

    def test_ordinary_answers_meet_location_separated_targets_count_distinct_locations_and_hold_the_unchanged_whole_bound(
        self,
    ) -> None:
        world = EntryPointWorld()
        self.addCleanup(world.close)
        _preseed_converted_memory(world)
        with _l111.captured_validated_wholes() as cap:
            world.build()
        contract_path = world.contract.as_posix()
        _l111.assert_location_separated_target(
            self, "worktree_start", dict(world.started[1] or {}), self.TARGETS["worktree_start"]
        )
        _l111.assert_detail_holds_whole_result(
            self, "worktree_start", dict(world.started[1] or {}), cap["worktree_start"][-1]
        )

        def call(tool: str, args: dict[str, Any]) -> dict[str, Any]:
            with _l111.captured_validated_wholes() as one:
                kind, payload = world.call(tool, args)
            self.assertEqual(kind, "RETURNED", f"{tool} raised: {payload}")
            assert payload is not None
            captured[tool] = one[tool][-1]
            return payload

        captured: dict[str, dict[str, Any]] = {}

        status = call("worktree_status", {"repo_id": REPO, "contract_path": contract_path})
        sync = call(
            "worktree_sync", {"repo_id": REPO, "contract_path": contract_path, "dry_run": True}
        )
        contract = load_contract(world.contract)
        (contract.code_worktree / "landing-work.txt").write_text("work\n", encoding="utf-8")
        _cover_fixture_code_changes(contract, "landing-work.txt")
        preview = call(
            "worktree_closeout_preview",
            {
                "contract_path": contract_path,
                "code_commit_message": "Fixture code",
                "memory_commit_message": "Fixture memory",
            },
        )
        applied = call(
            "worktree_closeout_apply",
            {
                "contract_path": contract_path,
                "intent_note": "answer size fixture",
                "code_commit_message": "Fixture code",
                "memory_commit_message": "Fixture memory",
            },
        )
        _l111.assert_location_separated_target(
            self,
            "worktree_closeout_apply",
            dict(applied),
            self.TARGETS["worktree_closeout_apply"],
        )
        _l111.assert_detail_holds_whole_result(
            self, "worktree_closeout_apply", dict(applied), captured["worktree_closeout_apply"]
        )
        self.assertEqual(self.BANNED & set(applied), set(), "apply answer holds banned blocks")
        wait_until(
            lambda: load_contract(world.contract).closeout_status == "completed",
            "fixture closeout completes",
        )
        self.assertEqual(load_contract(world.contract).closeout_status, "completed")
        integrate = call("worktree_integrate", {"contract_path": contract_path})
        # A second landing so the cleanup below previews a real removal.
        cleanup_dry = call("worktree_cleanup", {"contract_path": contract_path, "dry_run": True})

        answers = {
            "worktree_status": status,
            "worktree_sync": sync,
            "worktree_closeout_preview": preview,
            "worktree_integrate": integrate,
            "worktree_cleanup": cleanup_dry,
        }
        for tool, answer in answers.items():
            _l111.assert_location_separated_target(self, tool, dict(answer), self.TARGETS[tool])
            banned = self.BANNED & set(answer)
            self.assertEqual(banned, set(), f"{tool} answer holds banned blocks: {banned}")
            whole_back = _l111.assert_detail_holds_whole_result(
                self, tool, dict(answer), captured[tool]
            )
            # The whole result keeps what the answer dropped.
            self.assertTrue(whole_back.get("ok"), f"{tool} whole result lost ok")
            self.assertTrue(
                self.BANNED & set(whole_back),
                f"{tool} detail file holds none of the dropped blocks",
            )
        # The apply and finalize answers are measured where they land: the
        # apply was accepted above; finalize runs after the real cleanup.
        # Both secondary sites take all three legs: the packet target counts
        # the answer without its locations at every depth.
        cleanup = call("worktree_cleanup", {"contract_path": contract_path})
        _l111.assert_location_separated_target(
            self, "worktree_cleanup", dict(cleanup), self.TARGETS["worktree_cleanup"]
        )
        _l111.assert_detail_holds_whole_result(
            self, "worktree_cleanup", dict(cleanup), captured["worktree_cleanup"]
        )
        finalize = call("lifecycle_finalize_task", {"contract_path": contract_path})
        _l111.assert_location_separated_target(
            self,
            "lifecycle_finalize_task",
            dict(finalize),
            self.TARGETS["lifecycle_finalize_task"],
        )
        _l111.assert_detail_holds_whole_result(
            self, "lifecycle_finalize_task", dict(finalize), captured["lifecycle_finalize_task"]
        )
        banned = self.BANNED & set(finalize)
        self.assertEqual(banned, set(), f"finalize answer holds banned blocks: {banned}")

    def test_largest_answers_stay_under_the_bound(self) -> None:
        """Rule 1, evidence item 1 (largest half): the biggest real answers fit the bound.

        Refusals with blockers, and a preview over many changed files, all stay
        within their genuine per-tool bound, carry the state, and keep the whole
        result in the file the answer's ``log`` names -- ok results and
        refusals alike, with the refusal's code retained whole.
        """
        world = EntryPointWorld()
        self.addCleanup(world.close)
        world.build()
        contract_path = world.contract.as_posix()

        def call(tool: str, args: dict[str, Any]) -> dict[str, Any]:
            with _l111.captured_validated_wholes() as one:
                kind, payload = world.call(tool, args)
            self.assertEqual(kind, "RETURNED", f"{tool} raised: {payload}")
            assert payload is not None
            captured[tool] = one[tool][-1]
            return payload

        captured: dict[str, dict[str, Any]] = {}
        integrate = call("worktree_integrate", {"contract_path": contract_path, "dry_run": True})
        self.assertFalse(integrate.get("ok"))
        self.assertEqual(integrate.get("code"), "closeout-incomplete")
        cleanup = call("worktree_cleanup", {"contract_path": contract_path, "dry_run": True})
        self.assertFalse(cleanup.get("ok"))
        contract = load_contract(world.contract)
        assert contract.code_worktree is not None
        for index in range(25):
            (contract.code_worktree / f"landing-file-{index}.txt").write_text(
                "work\n", encoding="utf-8"
            )
        preview = call(
            "worktree_closeout_preview",
            {
                "contract_path": contract_path,
                "code_commit_message": "Fixture code",
                "memory_commit_message": "Fixture memory",
            },
        )
        status = call("worktree_status", {"repo_id": REPO, "contract_path": contract_path})
        for tool, answer in (
            ("worktree_integrate", integrate),
            ("worktree_cleanup", cleanup),
            ("worktree_closeout_preview", preview),
            ("worktree_status", status),
        ):
            tokens = _count(dict(answer))
            self.assertLessEqual(
                tokens,
                _bound_for(tool),
                f"{tool} largest answer is {tokens} tokens over its genuine bound {_bound_for(tool)}: {sorted(answer)}",
            )
            self.assertTrue(
                answer.get("state") or answer.get("phase"),
                f"{tool} largest answer names no state: {sorted(answer)}",
            )
            # F01.17: the largest answers keep their complete validated whole too.
            _l111.assert_detail_holds_whole_result(self, tool, dict(answer), captured[tool])


class ToolDescriptionBoundsTests(unittest.TestCase):
    """MIK-R111 rule 6: packet descriptions say what they answer, within 600 characters.

    Each description names the state words its answers carry and points to the
    reference for what each operation takes.
    """

    FILES: ClassVar = {
        "tasks": ("task_doc", "closeout_queue", "task_reopen", "lifecycle_finalize_task"),
        "worktrees": (
            "worktree_start",
            "worktree_enclosure_adopt",
            "worktree_attach",
            "worktree_status",
            "worktree_sync",
            "worktree_pause",
        ),
        "closeout": (
            "direct_landing",
            "worktree_closeout_preview",
            "worktree_closeout_apply",
            "worktree_integrate",
            "worktree_checkpoint_landing",
            "worktree_record_landing",
            "worktree_operation_control",
            "worktree_cleanup",
            "worktree_abandon",
        ),
        "role_agents": ("role_start", "role_message"),
    }

    # One state word each description must list (MIK-R111 rule 1).
    STATES: ClassVar = {
        "closeout_queue": "valid-built",
        "task_reopen": "reopened",
        "lifecycle_finalize_task": "finalized",
        "worktree_start": "started",
        "worktree_attach": "attached",
        "worktree_status": "worktree-started",
        "worktree_sync": "synced",
        "worktree_pause": "paused",
        "direct_landing": "landed",
        "worktree_closeout_preview": "would-closeout",
        "worktree_closeout_apply": "closed",
        "worktree_integrate": "integrated",
        "worktree_checkpoint_landing": "checkpointed",
        "worktree_record_landing": "recorded",
        "worktree_operation_control": "refused",
        "worktree_cleanup": "cleanup-completed",
        "worktree_abandon": "abandoned",
        "role_start": "agent id",
    }

    # Blocked-state words each description must list beside the success
    # states: a caller reads what can block before it calls.
    BLOCKED_STATES: ClassVar = {
        "worktree_sync": "sync-resolution-required",
        "worktree_closeout_preview": "closeout-gate-blocked",
        "worktree_closeout_apply": "closeout-gate-blocked",
        "worktree_integrate": "handover-gate-blocked",
        "worktree_checkpoint_landing": "handover-gate-blocked",
        "worktree_cleanup": "provider-setup-running",
        "worktree_status": "carryover-pending",
    }

    def _descriptions(self) -> dict[str, str]:
        root = _Path(__file__).resolve().parent.parent / "src" / "agents_remember" / "mcp"
        found: dict[str, str] = {}
        for family, tools in self.FILES.items():
            tree = _ast.parse((root / "registration" / f"{family}.py").read_text())
            for node in _ast.walk(tree):
                if isinstance(node, _ast.Assign) and any(
                    getattr(t, "id", "") == "_TASK_DOC_TOOL_DESCRIPTION" for t in node.targets
                ):
                    found["task_doc"] = _ast.literal_eval(node.value)
                if isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef)) and (
                    node.name in tools
                    and isinstance(node.body[0], _ast.Expr)
                    and isinstance(node.body[0].value, _ast.Constant)
                    and isinstance(node.body[0].value.value, str)
                ):
                    found[node.name] = _inspect.cleandoc(node.body[0].value.value)
        return found

    def test_packet_descriptions_are_within_600_and_list_their_states(self) -> None:
        descriptions = self._descriptions()
        for tool, state in self.STATES.items():
            with self.subTest(tool=tool):
                self.assertIn(tool, descriptions, "description missing")
                # Served descriptions wrap lines, sometimes mid-word; rejoin wraps.
                text = " ".join(descriptions[tool].split())
                text = _re.sub(r"(\w)- (\w)", r"\1-\2", text)
                self.assertLessEqual(len(text), 600, f"{tool} description over 600")
                self.assertIn(
                    "docs/reference/mcp-tools.md", text, f"{tool} names no reference section"
                )
                self.assertIn(state, text, f"{tool} description lists no state")
        for tool, state in self.BLOCKED_STATES.items():
            with self.subTest(tool=tool, blocked=state):
                text = " ".join(descriptions[tool].split())
                text = _re.sub(r"(\w)- (\w)", r"\1-\2", text)
                self.assertIn(state, text, f"{tool} description lists no blocked state")

    def test_task_doc_description_names_its_operations(self) -> None:
        text = self._descriptions()["task_doc"]
        for operation in ("create", "set_step", "linkage_report", "retire_master", "set_field"):
            self.assertIn(operation, text, f"task_doc description names no {operation}")

    def test_role_message_description_is_within_600_with_host_request_and_cancel_warning(
        self,
    ) -> None:
        text = self._descriptions()["role_message"]
        text = " ".join(text.split())
        text = _re.sub(r"(\w)- (\w)", r"\1-\2", text)
        self.assertLessEqual(len(text), 600, "role_message description over 600")
        self.assertIn("docs/reference/mcp-tools.md", text)
        # The description promises only the host request ...
        self.assertIn("Only the host request is promised", text)
        # ... and retains the cancellation warning (delivery may cancel).
        self.assertIn("which the host may cancel", text)
        self.assertNotIn("never cancelled", text)


class TerminalPreconditionCodeTests(unittest.TestCase):
    """F01.5: terminal preconditions answer before writing; unknown raises.

    Converter mapping per code is proven live in SeriesTerminalLiveTests;
    this class keeps the full-stack refusal cases plus the fail-closed pin.
    """

    def _world(self) -> EntryPointWorld:
        world = EntryPointWorld()
        self.addCleanup(world.close)
        world.build()
        return world

    # The converter mapping for every terminal code is proven live in
    # SeriesTerminalLiveTests on caught producer exceptions (no authored
    # strings); the checkpoint and kept-line suites prove children and
    # carryover at tool level. This pin keeps only the fail-closed half:
    # an unrecognized check still raises instead of mapping.
    def test_unknown_check_still_raises(self) -> None:
        world = self._world()
        contract = load_contract(world.contract)
        self.assertIsNone(
            terminal_state_refusal(
                contract, RuntimeError("worktree_cleanup refused: a brand new check")
            )
        )

    def test_cleanup_dry_run_on_dirty_worktree_answers_shaped_blockers(self) -> None:
        world = self._world()
        contract = load_contract(world.contract)
        # Reach the terminal preflight past the integration gate.
        write_contract(world.contract, _replace(contract, integration_status="completed"))
        (contract.code_worktree / "dirty.txt").write_text("uncommitted\n", encoding="utf-8")
        code_before = _git(world.code, "rev-parse", "HEAD")
        kind, payload = world.call(
            "worktree_cleanup",
            {"contract_path": world.contract.as_posix(), "dry_run": True},
        )
        answer = _refusal(self, kind, payload)
        self.assertEqual(answer.get("state"), "blocked")
        self.assertEqual(answer.get("code"), "cleanup-blocked")
        blockers = answer.get("blockers") or []
        self.assertTrue(blockers, "dirty worktree names its blocker")
        for blocker in blockers:
            self.assertIn("code", blocker)
            self.assertIn("sentence", blocker)
        self.assertTrue(
            any(blocker.get("code") == "worktree-dirty" for blocker in blockers),
            f"dirty code worktree is coded: {blockers}",
        )
        self.assertEqual(_git(world.code, "rev-parse", "HEAD"), code_before)

    def test_abandon_dry_run_on_dirty_worktree_answers_shaped_blockers(self) -> None:
        world = self._world()
        contract = load_contract(world.contract)
        (contract.code_worktree / "dirty.txt").write_text("uncommitted\n", encoding="utf-8")
        kind, payload = world.call(
            "worktree_abandon",
            {"contract_path": world.contract.as_posix(), "dry_run": True},
        )
        answer = _refusal(self, kind, payload)
        self.assertEqual(answer.get("state"), "abandon-blocked")
        self.assertEqual(answer.get("code"), "abandon-blocked")
        blockers = answer.get("blockers") or []
        self.assertTrue(
            all("code" in blocker and "sentence" in blocker for blocker in blockers),
            f"every blocker coded: {blockers}",
        )

    def test_cleanup_on_misspelled_branch_answers_branch_code(self) -> None:
        world = self._world()
        contract = load_contract(world.contract)
        tampered = _replace(contract, code_work_branch="refs/heads/not-the-task-branch")
        write_contract(world.contract, tampered)
        kind, payload = world.call(
            "worktree_cleanup",
            {"contract_path": world.contract.as_posix(), "dry_run": True},
        )
        answer = _refusal(self, kind, payload)
        self.assertEqual(answer.get("code"), "worktree-checkout-mismatch")
        self.assertIn("checked out", answer.get("summary", ""))

    def test_attach_republish_with_unresolved_work_answers_terminal_blocked(self) -> None:
        """F01.5: the start/attach republish raise answers in the envelope.

        A leaf marked Completed with an open step, restamped under a
        lifecycle the enclosure does not carry, would republish terminal
        status with unresolved work units. The production attach path answers
        ``task-enclosure-binding-terminal-blocked`` with the repair sentence
        naming the open unit, and writes nothing -- the census's one failing
        start invocation never reaches this state, so this case is its proof.
        """
        world = self._world()
        leaf_path = world.coord / "tasks/repo-a/m/1_leaf.json"
        doc = _json.loads(leaf_path.read_text(encoding="utf-8"))
        open_steps = [step for step in doc.get("steps", []) if step.get("status") != "done"]
        self.assertTrue(open_steps, "the fixture leaf carries an open work unit")
        doc["status"] = "Completed"
        doc["lifecycleId"] = "01MISMATCHEDLIFECYCLE000000000"
        leaf_path.write_text(_json.dumps(doc), encoding="utf-8")
        contract_bytes = world.contract.read_bytes()
        kind, payload = world.call(
            "worktree_attach",
            {"repo_id": REPO, "contract_path": world.contract.as_posix()},
        )
        answer = _refusal(self, kind, payload)
        self.assertEqual(answer.get("state"), "task-enclosure-binding-terminal-blocked")
        self.assertIn("unresolved", str(answer.get("summary", "")))
        self.assertIn("S1", str(answer.get("summary", "")))
        self.assertEqual(world.contract.read_bytes(), contract_bytes)


class GateBlockedTests(unittest.TestCase):
    """F01.4: preview and real paths agree on gate-blocked states.

    Each pin drives the production builder with a real closed guard: before
    the repair the dry run promised what the real call would refuse.
    """

    def _world_contract(self) -> Any:
        world = EntryPointWorld()
        self.addCleanup(world.close)
        world.build()
        return load_contract(world.contract)

    def test_integrate_dry_run_with_closed_gate_answers_blocked(self) -> None:

        contract = self._world_contract()
        preview = IntegratePreview(
            guard=GateGuard(
                kind="master-handover-approval",
                permitted=False,
                reason="the addressed gate is undecided",
                gate_id="g1",
            ),
            handover_warning=None,
        )
        args = WorktreeArgs(contract_path=contract.contract_path, dry_run=True)
        # A closed gate answers before sources are touched.
        result = _dry_run_result(contract, args, cast(Any, None), preview=preview)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.payload["state"], "handover-gate-blocked")
        self.assertEqual(result.payload["code"], "handover-gate-blocked")
        self.assertEqual(result.payload["gateId"], "g1")
        self.assertIn("handover_gate", result.payload)

    def test_checkpoint_dry_run_with_closed_gate_answers_blocked(self) -> None:

        contract = self._world_contract()
        preview = IntegratePreview(
            guard=GateGuard(
                kind="master-handover-approval",
                permitted=False,
                reason="the addressed gate is undecided",
                gate_id="g1",
            ),
            handover_warning=None,
        )
        args = WorktreeArgs(contract_path=contract.contract_path, dry_run=True)
        # A closed gate answers before the landing is touched.
        result = _checkpoint_dry_run_result(contract, args, cast(Any, None), preview=preview)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.payload["state"], "handover-gate-blocked")
        self.assertEqual(result.payload["code"], "handover-gate-blocked")

    def test_closeout_preview_with_closed_gate_answers_blocked(self) -> None:

        contract = self._world_contract()
        guard = GateGuard(
            kind="closeout-approval",
            permitted=False,
            reason="the addressed gate is undecided",
            gate_id="g2",
        )
        preview: dict[str, Any] = closeout_module._closeout_gate_blocked_preview(contract, guard)
        self.assertEqual(preview["state"], "closeout-gate-blocked")
        self.assertEqual(preview["code"], "closeout-gate-blocked")
        self.assertFalse(preview["commit_approval_required"])
        self.assertEqual(preview["closeout_gate"]["gateId"], "g2")
        self.assertEqual(preview["blockers"][0]["code"], "closeout-gate-blocked")
        # No tool decides a stored gate (MIK-R113): no next tool, and the way out.
        self.assertNotIn("nextTool", preview["nextStep"])
        self.assertIn(STORED_GATE_WAY_OUT, preview["nextStep"]["summary"])

    def test_blocked_preview_states_fail_closed(self) -> None:

        for state in ("closeout-gate-blocked", "blocked"):
            self.assertIn(state, REFUSED_CLOSEOUT_PREVIEW_STATES)

    def test_shaped_blockers_name_code_and_sentence(self) -> None:

        shaped: list[dict[str, Any]] = shaped_terminal_blockers(
            [
                {"worktree": "code", "reason": "dirty"},
                {"branch": "code", "name": "ar/x", "reason": "gone", "unmergedCommits": 3},
                {"worktree": "memory", "reason": "locker jammed half-open"},
            ]
        )
        self.assertEqual(shaped[0]["code"], "worktree-dirty")
        self.assertIn("force=true", shaped[0]["sentence"])
        self.assertEqual(shaped[1]["code"], "branch-unmerged-commits")
        self.assertIn("ar/x", shaped[1]["sentence"])
        self.assertEqual(shaped[2]["code"], "worktree-unready")
        # The producer's own keys ride along.
        self.assertEqual(shaped[0]["worktree"], "code")

    def test_closeout_queue_refusal_maps_next_calls(self) -> None:

        contract = self._world_contract()
        integrate = closeout_queue_refusal(
            contract,
            CloseoutQueueError(
                status="atomic-series-checkpoint-master-complete", detail="done; land it"
            ),
            next_args={"contract_path": "c"},
        )
        self.assertEqual(integrate.payload["code"], "atomic-series-checkpoint-master-complete")
        self.assertEqual(
            cast("dict[str, Any]", integrate.payload["nextStep"])["nextTool"], "worktree_integrate"
        )
        abandon = closeout_queue_refusal(
            contract,
            CloseoutQueueError(status="atomic-series-closeout-master-abandoned", detail="gone"),
            next_args={"contract_path": "c"},
        )
        self.assertEqual(
            cast("dict[str, Any]", abandon.payload["nextStep"])["nextTool"], "worktree_abandon"
        )
        incomplete = closeout_queue_refusal(
            contract,
            CloseoutQueueError(
                status="atomic-series-closeout-master-incomplete", detail="finish rows"
            ),
            next_args={"contract_path": "c"},
        )
        self.assertNotIn("nextStep", incomplete.payload)


class SeriesTerminalLiveTests(unittest.TestCase):
    """F01.5: every terminal code's raise maps live; tools answer first-failing.

    The mapping pins above feed producer sentences to the converter; each
    case here pairs the producer's raise (the fail-before shape: the check
    raises) with the public tool call answering the code (nothing moves).
    The atomic master-a series lets each check fire at producer level; the
    live-children census precedes master status at tool level, so those cases
    assert the producer raise plus the converter mapping on the caught
    exception (no authored strings) and the tool answering the first-failing
    children code. Two codes ride existing live suites, cited where the pins
    name them: children-not-retired (checkpoint end to end) and
    carryover-required (kept-line carryover).
    """

    def _fixture(self) -> QueueFixture:
        temporary = _tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        # The atomic master-a series: atomic execution nature with no started
        # children, so each terminal check fires unmasked.
        return QueueFixture(_Path(temporary.name), atomic_a=True)

    def _series(self, fixture: QueueFixture):
        return load_contract(fixture.tasks / "master-a" / "series-contract.md")

    def _set_master_status(self, fixture: QueueFixture, status: str) -> None:
        doc_path = fixture.tasks / "master-a" / "task.json"
        doc = _json.loads(doc_path.read_text(encoding="utf-8"))
        doc["status"] = status
        doc_path.write_text(_json.dumps(doc), encoding="utf-8")

    def test_open_master_cleanup_answers_master_not_completed(self) -> None:
        fixture = self._fixture()
        series = self._series(fixture)
        with self.assertRaises(RuntimeError) as raised:
            _require_terminal_worktree(series, operation="worktree_cleanup")
        self.assertIn("must be Completed", str(raised.exception))
        mapped = terminal_state_refusal(series, raised.exception)
        assert mapped is not None
        self.assertEqual(mapped.payload["code"], "master-not-completed")
        # Census order: the live-children census precedes master status, so
        # the tool answers the first-failing check, not this one.
        cleaned = _worktree_tools.worktree_cleanup_tool(
            fixture.cfg, contract_path=series.contract_path.as_posix(), dry_run=True
        )
        self.assertFalse(cleaned["ok"])
        self.assertEqual(cleaned["code"], "children-not-retired")

    def test_open_master_abandon_answers_abandon_not_terminal(self) -> None:
        fixture = self._fixture()
        series = self._series(fixture)
        with self.assertRaises(RuntimeError) as raised:
            _require_terminal_worktree(series, operation="worktree_abandon")
        self.assertIn("must be declared abandoned", str(raised.exception))
        mapped = terminal_state_refusal(series, raised.exception)
        assert mapped is not None
        self.assertEqual(mapped.payload["code"], "abandon-master-not-terminal")
        abandoned = _worktree_tools.worktree_abandon_tool(
            fixture.cfg, contract_path=series.contract_path.as_posix(), dry_run=True
        )
        self.assertFalse(abandoned["ok"])
        self.assertEqual(abandoned["code"], "children-not-retired")

    def test_abandoned_landed_master_answers_landed(self) -> None:
        fixture = self._fixture()
        self._set_master_status(fixture, "abandoned")
        series = self._series(fixture)
        write_contract(
            series.contract_path,
            _replace(load_contract(series.contract_path), integration_status="completed"),
        )
        series = self._series(fixture)
        with self.assertRaises(RuntimeError) as raised:
            _require_terminal_worktree(series, operation="worktree_abandon")
        self.assertIn("already landed work", str(raised.exception))
        mapped = terminal_state_refusal(series, raised.exception)
        assert mapped is not None
        self.assertEqual(mapped.payload["code"], "abandon-landed-master")
        abandoned = _worktree_tools.worktree_abandon_tool(
            fixture.cfg, contract_path=series.contract_path.as_posix(), dry_run=True
        )
        self.assertFalse(abandoned["ok"])
        self.assertEqual(abandoned["code"], "children-not-retired")

    def test_misspelled_branch_answers_spelling_code(self) -> None:
        # A symbolic-ref alias resolves canonically (authority passes) but
        # keeps its alias spelling: the sentence's "aliases cannot be
        # retired" is this case, driven live, not an authored string.
        fixture = self._fixture()
        self._set_master_status(fixture, "Completed")
        series = self._series(fixture)
        expected = series.code_work_branch
        _git(series.code_repo_path, "symbolic-ref", "refs/heads/alias-x", f"refs/heads/{expected}")
        _git(series.code_worktree, "checkout", "alias-x")
        write_contract(
            series.contract_path,
            _replace(load_contract(series.contract_path), code_work_branch="alias-x"),
        )
        series = self._series(fixture)
        with self.assertRaises(RuntimeError) as raised:
            _require_terminal_worktree(series, operation="worktree_cleanup")
        self.assertIn("exact task-owned spelling", str(raised.exception))
        mapped = terminal_state_refusal(series, raised.exception)
        assert mapped is not None
        self.assertEqual(mapped.payload["code"], "branch-not-task-owned")
        cleaned = _worktree_tools.worktree_cleanup_tool(
            fixture.cfg, contract_path=series.contract_path.as_posix(), dry_run=True
        )
        self.assertFalse(cleaned["ok"])
        self.assertEqual(cleaned["code"], "children-not-retired")

    def test_tampered_group_answers_mismatch_code(self) -> None:
        fixture = self._fixture()
        series = self._series(fixture)
        write_contract(
            series.contract_path,
            _replace(
                load_contract(series.contract_path),
                worktree_group=series.worktree_group / "elsewhere",
            ),
        )
        series = self._series(fixture)
        with self.assertRaises(RuntimeError) as raised:
            _require_series_children_retired(series)
        self.assertIn("coordination.worktree_group cell of", str(raised.exception))
        mapped = terminal_state_refusal(series, raised.exception)
        assert mapped is not None
        self.assertEqual(mapped.payload["code"], "worktree-group-mismatch")
        # The tampered group breaks the entry-gate authority first: the tool
        # answers the developer-decision family, never a terminal code.
        cleaned = _worktree_tools.worktree_cleanup_tool(
            fixture.cfg, contract_path=series.contract_path.as_posix(), dry_run=True
        )
        self.assertFalse(cleaned["ok"])
        self.assertEqual(cleaned["state"], "refused")
        self.assertTrue(cleaned["developerDecisionRequired"])

    def test_protected_parent_ref_answers_protected_code(self) -> None:
        # The remote default is the task branch itself: retiring it would
        # retire the parent ref. Driven live through origin/HEAD, not authored.
        fixture = self._fixture()
        self._set_master_status(fixture, "Completed")
        series = self._series(fixture)
        expected = series.code_work_branch
        repo = series.code_repo_path
        if "origin" not in _git(repo, "remote").split():
            _git(repo, "remote", "add", "origin", repo.as_posix())
        _git(repo, "fetch", "origin", f"{expected}:refs/remotes/origin/{expected}")
        _git(repo, "symbolic-ref", "refs/remotes/origin/HEAD", f"refs/remotes/origin/{expected}")
        series = self._series(fixture)
        with self.assertRaises(RuntimeError) as raised:
            _require_terminal_worktree(series, operation="worktree_cleanup")
        self.assertIn("protected parent ref", str(raised.exception))
        mapped = terminal_state_refusal(series, raised.exception)
        assert mapped is not None
        self.assertEqual(mapped.payload["code"], "protected-ref")
        cleaned = _worktree_tools.worktree_cleanup_tool(
            fixture.cfg, contract_path=series.contract_path.as_posix(), dry_run=True
        )
        self.assertFalse(cleaned["ok"])
        self.assertEqual(cleaned["code"], "children-not-retired")

    def test_live_children_answer_children_code(self) -> None:
        # The atomic master-b series carries live children: producer and tool
        # agree on children-not-retired, and nothing moves.
        temporary = _tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        fixture = QueueFixture(_Path(temporary.name), atomic_b=True)
        series = load_contract(fixture.tasks / "master-b" / "series-contract.md")
        with self.assertRaises(RuntimeError) as raised:
            _require_series_children_retired(series)
        self.assertIn("every child leaf", str(raised.exception))
        mapped = terminal_state_refusal(series, raised.exception)
        assert mapped is not None
        self.assertEqual(mapped.payload["code"], "children-not-retired")
        cleaned = _worktree_tools.worktree_cleanup_tool(
            fixture.cfg, contract_path=series.contract_path.as_posix(), dry_run=True
        )
        self.assertFalse(cleaned["ok"])
        self.assertEqual(cleaned["code"], "children-not-retired")
        self.assertIn("every child leaf", cleaned["error"])


@_contextmanager
def _patched_tmpdir(path: _Path):
    """Point new temporary directories under ``path`` for one block.

    ``tempfile.tempdir`` is cached per process, so the environment variable
    alone would not move worlds built later in the same worker.
    """
    old_tmpdir, old_env = _tempfile.tempdir, _os.environ.get("TMPDIR")
    _tempfile.tempdir = str(path)
    _os.environ["TMPDIR"] = str(path)
    try:
        yield path
    finally:
        _tempfile.tempdir = old_tmpdir
        if old_env is None:
            _os.environ.pop("TMPDIR", None)
        else:
            _os.environ["TMPDIR"] = old_env


class RealLargestTests(unittest.TestCase):
    """F01.17: the eight real largest cases at their actual final bound.

    Maximized inputs through the production MCP path -- long paths, many
    changed files, diverged lines, real refusals with several blockers. Each
    delivered answer stays within its own genuine per-tool bound from
    ``bound_for`` (measured maxima plus the documented root-variance margin)
    and names its whole result, which keeps the dropped blocks.
    """

    def _world(self) -> EntryPointWorld:
        world = EntryPointWorld()
        self.addCleanup(world.close)
        world.build()
        return world

    def _called(
        self, world: EntryPointWorld, tool: str, args: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """One MCP call with its post-validation whole captured at the seam."""
        with _l111.captured_validated_wholes() as cap:
            kind, payload = world.call(tool, args)
        self.assertEqual(kind, "RETURNED", f"{tool} raised: {payload}")
        assert payload is not None
        return payload, cap[tool][-1]

    def _assert_bounded(
        self, tool: str, payload: dict[str, Any], expected_whole: dict[str, Any]
    ) -> dict[str, Any]:
        answer = payload
        self.assertLessEqual(
            _count(dict(answer)), _bound_for(tool), f"{tool} over its genuine bound"
        )
        # F01.17: the bound check compares the complete validated whole.
        _l111.assert_detail_holds_whole_result(self, tool, dict(answer), expected_whole)
        return answer

    def _deep_root(self, width: int) -> _Path:
        parent = _tempfile.TemporaryDirectory()
        self.addCleanup(parent.cleanup)
        base = _Path(parent.name)
        deep = base / ("q" * (width - len(str(base)) - 1))
        deep.mkdir()
        return deep

    def test_start_on_deepest_legal_root_is_bounded(self) -> None:
        # The genuine start maximum: path lengths are the only variable input,
        # so the largest success is a build under a ~200-character root (the
        # installation's own depth class), delivered through the MCP path.
        deep = self._deep_root(200)
        world = EntryPointWorld()
        self.addCleanup(world.close)
        with _l111.captured_validated_wholes() as cap, _patched_tmpdir(deep):
            world.build()
        answer = self._assert_bounded(
            "worktree_start", dict(world.started[1] or {}), cap["worktree_start"][-1]
        )
        self.assertTrue(answer.get("ok"))

    def test_apply_with_many_paths_is_bounded(self) -> None:
        world = self._world()
        contract = load_contract(world.contract)
        for index in range(30):
            (contract.code_worktree / f"apply-{index:02d}.txt").write_text(
                "work\n", encoding="utf-8"
            )
        payload, expected = self._called(
            world,
            "worktree_closeout_apply",
            {
                "contract_path": world.contract.as_posix(),
                "code_commit_message": "Fixture code",
                "memory_commit_message": "Fixture memory",
                "intent_note": "largest-case measurement",
                "dry_run": True,
            },
        )
        self._assert_bounded("worktree_closeout_apply", payload, expected)

    def test_finalize_is_bounded(self) -> None:
        # The genuine finalize maximum is a carried-out finalize on a landed
        # world with completed documents: updates, projection effects and the
        # cleanup payload all report, the fullest ok content.
        world = EntryPointWorld()
        self.addCleanup(world.close)
        world = _preseed_converted_memory(world)
        world.build()
        contract = load_contract(world.contract)
        (contract.code_worktree / "landing-work.txt").write_text("work\n", encoding="utf-8")
        _cover_fixture_code_changes(contract, "landing-work.txt")
        kind, payload = world.call(
            "worktree_closeout_preview",
            {
                "contract_path": world.contract.as_posix(),
                "code_commit_message": "Fixture code",
                "memory_commit_message": "Fixture memory",
            },
        )
        self.assertEqual(kind, "RETURNED")
        kind, payload = world.call(
            "worktree_closeout_apply",
            {
                "contract_path": world.contract.as_posix(),
                "intent_note": "largest finalize landing",
                "code_commit_message": "Fixture code",
                "memory_commit_message": "Fixture memory",
            },
        )
        self.assertEqual(kind, "RETURNED")
        wait_until(
            lambda: load_contract(world.contract).closeout_status == "completed",
            "fixture closeout completes",
        )
        self.assertEqual(load_contract(world.contract).closeout_status, "completed")
        kind, payload = world.call(
            "worktree_integrate", {"contract_path": world.contract.as_posix()}
        )
        self.assertEqual(kind, "RETURNED")
        assert payload is not None
        self.assertTrue(payload.get("ok"), payload.get("error"))
        leaf_path = world.coord / "tasks" / "repo-a" / "m" / "1_leaf.json"
        leaf_doc = _json.loads(leaf_path.read_text(encoding="utf-8"))
        for step in leaf_doc.get("steps", []):
            step["status"] = "done"
        leaf_path.write_text(_json.dumps(leaf_doc), encoding="utf-8")
        master_path = world.coord / "tasks" / "repo-a" / "m" / "task.json"
        master_doc = _json.loads(master_path.read_text(encoding="utf-8"))
        master_doc["status"] = "Completed"
        master_path.write_text(_json.dumps(master_doc), encoding="utf-8")
        payload, expected = self._called(
            world, "lifecycle_finalize_task", {"contract_path": world.contract.as_posix()}
        )
        answer = self._assert_bounded("lifecycle_finalize_task", payload, expected)
        self.assertTrue(answer.get("ok"), answer.get("error"))

    def test_integrate_refusal_is_bounded(self) -> None:
        world = self._world()
        payload, expected = self._called(
            world,
            "worktree_integrate",
            {"contract_path": world.contract.as_posix(), "dry_run": True},
        )
        answer = self._assert_bounded("worktree_integrate", payload, expected)
        self.assertEqual(answer.get("code"), "closeout-incomplete")

    def test_cleanup_refusal_is_bounded(self) -> None:
        world = self._world()
        payload, expected = self._called(
            world,
            "worktree_cleanup",
            {"contract_path": world.contract.as_posix(), "dry_run": True},
        )
        answer = self._assert_bounded("worktree_cleanup", payload, expected)
        self.assertEqual(answer.get("code"), "integration-incomplete")

    def test_preview_with_many_paths_is_bounded(self) -> None:
        world = self._world()
        contract = load_contract(world.contract)
        for index in range(30):
            (contract.code_worktree / f"work-{index:02d}.txt").write_text(
                "work\n", encoding="utf-8"
            )
        payload, expected = self._called(
            world,
            "worktree_closeout_preview",
            {
                "contract_path": world.contract.as_posix(),
                "code_commit_message": "Fixture code",
                "memory_commit_message": "Fixture memory",
            },
        )
        self._assert_bounded("worktree_closeout_preview", payload, expected)

    def test_diverged_sync_is_bounded(self) -> None:
        # The genuine sync maximum moves the RECORDED source, not main: both
        # sides of the recorded divergence report, the fullest dry content.
        world = self._world()
        contract = load_contract(world.contract)
        _git(world.code, "switch", contract.code_source_branch)
        (world.code / "source.txt").write_text("source\n", encoding="utf-8")
        _git(world.code, "add", "-A")
        _git(world.code, "commit", "-m", "recorded source moves")
        (contract.code_worktree / "local.txt").write_text("local\n", encoding="utf-8")
        _git(contract.code_worktree, "add", "-A")
        _git(contract.code_worktree, "commit", "-m", "worktree moves")
        payload, expected = self._called(
            world,
            "worktree_sync",
            {"repo_id": REPO, "contract_path": world.contract.as_posix(), "dry_run": True},
        )
        self._assert_bounded("worktree_sync", payload, expected)

    def test_closeout_queue_status_is_bounded(self) -> None:
        # The genuine queue maximum answers through the production shaping: a
        # real status over the atomic fixture, projected exactly as the
        # server projects it. The direct tool call stands in for dispatch;
        # the shaping, bound and log are production's.
        temporary = _tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        fixture = QueueFixture(_Path(temporary.name), atomic_b=True)
        whole = fixture.status()
        with (
            _l111.captured_validated_wholes() as cap,
            tool_call_context(
                "closeout_queue", {"request": {"action": "status"}}, config=fixture.cfg
            ),
        ):
            answer = complete_tool_response("closeout_queue", dict(whole), config=fixture.cfg)
        expected = cap["closeout_queue"][-1]
        self.assertLessEqual(
            _count(dict(answer)), _bound_for("closeout_queue"), "queue over its genuine bound"
        )
        # F01.17: the queue detail file holds the complete validated whole.
        _l111.assert_detail_holds_whole_result(self, "closeout_queue", dict(answer), expected)

    def test_status_with_drift_is_bounded(self) -> None:

        world = self._world()
        contract = load_contract(world.contract)
        leaf_path = world.coord / "tasks" / "repo-a" / "m" / "1_leaf.json"
        document = read_task_doc(leaf_path)
        spec = world.coord / "tasks" / "repo-a" / "m" / "requirements" / "spec.md"
        spec.parent.mkdir(parents=True, exist_ok=True)
        spec.write_text("spec\n", encoding="utf-8")
        document = document.model_copy(update={"requirements": ["[spec](requirements/spec.md)"]})
        _write(leaf_path.parent, document)
        ref = TaskDocumentRef(repository="repo-a", path="m/1_leaf.json")
        copies.prepare_task_document_copies(world.coord, contract.worktree_group, ref)
        spec.write_text("drifted\n", encoding="utf-8")
        payload, expected = self._called(
            world,
            "worktree_status",
            {"repo_id": REPO, "contract_path": world.contract.as_posix()},
        )
        answer = self._assert_bounded("worktree_status", payload, expected)
        self.assertIn("task_document_copies", answer)


class WholeIdentityDiscriminatorTests(unittest.TestCase):
    """F01.17: the whole-identity check fails when the file loses a field.

    A real status call keeps its complete validated whole. Deleting the
    projection-dropped ``source_lineage`` from the fixture detail file -- or
    changing a retained value -- must fail the comparison. Only pytest
    fixture output is tampered with; the original bytes come back in a
    finally block.
    """

    def _status_answer(self) -> tuple[dict[str, Any], dict[str, Any]]:
        world = EntryPointWorld()
        self.addCleanup(world.close)
        world.build()
        with _l111.captured_validated_wholes() as cap:
            kind, payload = world.call(
                "worktree_status",
                {"repo_id": REPO, "contract_path": world.contract.as_posix()},
            )
        self.assertEqual(kind, "RETURNED", f"worktree_status raised: {payload}")
        assert payload is not None
        return payload, cap["worktree_status"][-1]

    def _tampered_run(self, mutate: Any) -> None:
        answer, expected = self._status_answer()
        # source_lineage is legitimately dropped from the compact answer and
        # retained in the validated whole: exactly the loss class the old
        # answer-side oracle could not see.
        self.assertNotIn("source_lineage", answer)
        self.assertIn("source_lineage", expected)
        log = answer.get("log")
        self.assertIsInstance(log, str)
        assert isinstance(log, str)
        raw = _Path(log).read_bytes()
        tampered = mutate(_json.loads(raw.decode("utf-8")))
        _Path(log).write_text(_json.dumps(tampered), encoding="utf-8")
        try:
            with self.assertRaises(AssertionError):
                _l111.assert_detail_holds_whole_result(self, "worktree_status", answer, expected)
        finally:
            _Path(log).write_bytes(raw)

    def test_removed_dropped_field_fails_whole_identity(self) -> None:
        self._tampered_run(lambda whole: {k: v for k, v in whole.items() if k != "source_lineage"})

    def test_changed_retained_value_fails_whole_identity(self) -> None:
        def change(whole: dict[str, Any]) -> dict[str, Any]:
            tampered = dict(whole)
            tampered["summary"] = "a changed summary the whole never held"
            return tampered

        self._tampered_run(change)


class LiveRowOracleTests(unittest.TestCase):
    """F01.17: live answers keep the row contract, not just row-vs-row."""

    def test_live_answers_keep_their_row_fields(self) -> None:
        world = EntryPointWorld()
        self.addCleanup(world.close)
        world.build()
        contract_path = world.contract.as_posix()
        self.assertLessEqual(
            _count(dict(world.started[1] or {})),
            _rows_table.TOOL_ANSWER_TABLE[("worktree_start", None)].bound,
        )

        def call(tool: str, args: dict[str, Any]) -> dict[str, Any]:
            kind, payload = world.call(tool, args)
            self.assertEqual(kind, "RETURNED", f"{tool} raised: {payload}")
            assert payload is not None
            return payload

        checks = {
            "worktree_status": call(
                "worktree_status", {"repo_id": REPO, "contract_path": contract_path}
            ),
            "worktree_sync": call(
                "worktree_sync",
                {"repo_id": REPO, "contract_path": contract_path, "dry_run": True},
            ),
        }

        for tool, answer in checks.items():
            row = _rows_table.TOOL_ANSWER_TABLE[(tool, None)]
            self.assertIsNone(row.interim_leaf, f"{tool} row is interim")
            extra = (
                set(cast(frozenset[str], answer))
                - set(cast(frozenset[str], row.fields))
                - set(FRAMEWORK_ANSWER_KEYS)
            )
            self.assertEqual(extra, set(), f"{tool} live answer leaves its row: {extra}")
            self.assertLessEqual(_count(dict(answer)), row.bound)
