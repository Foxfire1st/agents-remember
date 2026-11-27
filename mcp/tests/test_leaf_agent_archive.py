"""Leaf archive ownership, durable recovery and admitted pre-removal ordering."""

from __future__ import annotations

import asyncio
import contextlib
import json
import threading
import uuid
from dataclasses import replace
from functools import partial
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import Mock, patch

import integration_branch_authority_test_support as authority_fixtures
import pytest
from agents_remember.application import worktree_tools
from agents_remember.cli import paseo_launch, role_launch_receipts
from agents_remember.cli import role_launch_archive as archive
from agents_remember.cli import role_launch_archive_recovery as recovery
from agents_remember.cli.paseo_bridge import DAEMON_UNREACHABLE, PaseoBridgeFailure
from agents_remember.cli.paseo_status import AgentReading
from agents_remember.cli.role_launch_liveness import (
    _reconcile_prior_execution,
    apply_execution_reading,
)
from agents_remember.cli.role_launch_receipts import RECEIPT_SCHEMA
from agents_remember.controlplane.held_store_discovery import EXECUTIONS_DIRECTORY
from agents_remember.controlplane.held_store_layout import HeldMessage, hold_message, store_path_for
from agents_remember.kernel.file_lock import lock_held
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.role_launcher import RoleDispatchRequest
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.serving import _app_lifespan as lifespan
from agents_remember.serving._app_common import _ServingRuntime
from agents_remember.serving.background_accounting import BackgroundLedger
from agents_remember.serving.stream_shutdown import StreamShutdown
from agents_remember.tasks import read_task_doc
from agents_remember.tasks.leaf_doc import resolve_terminal_leaf_doc
from agents_remember.worktrees.modules import cleanup, terminal_abandon, terminal_cleanup
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.finalize import FinalizeArgs, finalize_result
from agents_remember.worktrees.modules.models import WorktreeCommandResult
from agents_remember.worktrees.modules.terminal_group import prepare_terminal_group
from agents_remember.worktrees.services import bind_worktree_services, worktree_services
from agents_remember.worktrees.worktree_contract import write_contract
from agents_remember_test_support.testing.waits import HANG_GUARD_SECONDS
from fastapi import HTTPException
from test_lifecycle_finalize import _FinalizeFixtures, _payload
from test_terminal_blocker_reasons import _landed_leaf, _landed_leaf_config, _public_config

pytestmark = pytest.mark.integration


class LeafAgentArchiveTests(_FinalizeFixtures):
    def setUp(self) -> None:
        super().setUp()
        self.contract = self._contract()
        self.leaf, _master = self._docs(self.contract)
        self.config = McpRuntimeConfig(
            config_path=self.tmp / "settings.json",
            coordination_root=self.contract.coordination_root,
            workspace_root=self.tmp / "projects",
            transcript_root=self.tmp / "logs",
        )
        self.ref = {
            "repository": self.contract.repo_name,
            "path": self.leaf.relative_to(
                self.config.coordination_root / "tasks" / self.contract.repo_name
            ).as_posix(),
        }
        self.directory = self.contract.task_root / "notes" / "reports" / EXECUTIONS_DIRECTORY

    def receipt(self, role="worker", *, history=False, ref=None):
        agent_id, request_id = str(uuid.uuid4()), str(uuid.uuid4())
        path = self.directory / ("history" if history else "") / f"{request_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        body = {
            "schema": RECEIPT_SCHEMA,
            "requestId": request_id,
            "role": role,
            "agentId": agent_id,
            "status": "running",
            "selection": {"role": role, "taskDocumentRef": self.ref if ref is None else ref},
            "workspace": {"contractPath": str(self.contract.contract_path)},
        }
        path.write_text(json.dumps(body))
        return path, body

    def prepared_receipt(self, role="worker", *, ref=None):
        path, body = self.receipt(role, ref=ref)
        body.update(
            status="starting",
            requestDigest="prepared-request",
            replayRequest={
                "workspace": {"cwd": str(self.tmp)},
                "agent": {"agentId": body["agentId"]},
            },
        )
        path.write_text(json.dumps(body))
        return path, body

    def test_terminal_mark_refuses_prepared_start_and_settles_returned_creation(self):
        for phase, created_first in [
            ("workspace-open", False),
            ("agent-create", False),
            ("agent-create", True),
        ]:
            with self.subTest(phase=phase, created_first=created_first):
                self._assert_terminal_launch_interleaving(phase, created_first)

    def test_enumerated_receipt_preserves_debt_when_native_create_enters(self):
        self.contract.worktree_group.mkdir(parents=True)
        bind_worktree_services(
            replace(worktree_services(), leaf_agent_archive=archive.LeafAgentArchive(self.config))
        )
        self._assert_terminal_launch_interleaving("agent-create", False, archive_reads_first=True)
        result = prepare_terminal_group(self.contract, dry_run=True)
        self.assertFalse(result.blockers, result)
        self.assertTrue(result.directories["worktree_group"]["would_remove"])

    def _allow_prepared_start(self):
        self.contract = replace(self.contract, cleanup="pending")
        write_contract(self.contract.contract_path, self.contract)

    def _assert_terminal_launch_interleaving(
        self, phase, created_first, *, archive_reads_first=False
    ):
        self._allow_prepared_start()
        path, body = self.prepared_receipt()
        entered, release = threading.Event(), threading.Event()
        live, failures, answers, calls = {}, [], [], []

        def launch():
            try:
                answers.append(
                    role_launch_receipts._execute_prepared_launch(self.config, path, body)
                )
            except BaseException as error:
                failures.append(error)

        state = SimpleNamespace(
            phase=phase,
            created_first=created_first,
            path=path,
            body=body,
            live=live,
            calls=calls,
            entered=entered,
            release=release,
        )
        host = partial(self._paused_creation_host, state)
        with (
            patch.object(paseo_launch, "bridge_call", side_effect=host),
            patch.object(archive, "bridge_call", side_effect=host),
        ):
            thread = threading.Thread(target=launch)
            try:
                if archive_reads_first:
                    closing = self._start_during_archive_census(thread, state)
                else:
                    thread.start()
                    self.assertTrue(entered.wait(HANG_GUARD_SECONDS))
                    closing = archive.LeafAgentArchive(self.config).archive(self.contract)
                self.assertTrue(thread.is_alive())
                self.assertFalse(release.is_set(), "closing waited for the opaque creation call")
                before = json.loads(path.read_text())
                if phase == "workspace-open":
                    self.assertIn(
                        body["agentId"],
                        {row["agentId"] for row in cast(list[dict[str, object]], closing["gone"])},
                    )
                    self.assertNotIn(
                        body["agentId"],
                        {row["agentId"] for row in cast(list[dict[str, object]], closing["owed"])},
                    )
                    self.assertFalse(before["leafArchive"]["creationPending"])
                elif created_first:
                    self.assertIn(
                        body["agentId"],
                        {
                            row["agentId"]
                            for row in cast(list[dict[str, object]], closing["archived"])
                        },
                    )
                else:
                    self.assertIn(
                        body["agentId"],
                        {row["agentId"] for row in cast(list[dict[str, object]], closing["owed"])},
                    )
                    self.assertNotIn(
                        body["agentId"],
                        {row["agentId"] for row in cast(list[dict[str, object]], closing["gone"])},
                    )
            finally:
                release.set()
                if thread.ident is not None:
                    thread.join(HANG_GUARD_SECONDS)
            self._assert_completed_launch(state, before, thread, failures, answers)

    def _assert_completed_launch(self, state, before, thread, failures, answers):
        path, body, live, calls = state.path, state.body, state.live, state.calls
        phase, created_first = state.phase, state.created_first
        self.assertFalse(thread.is_alive())
        self.assertFalse(failures)
        self.assertEqual(answers[0].status_code, 409)
        self.assertEqual(json.loads(bytes(answers[0].body))["status"], "rejected")
        self.assertFalse(live.get(body["agentId"], False))
        after = json.loads(path.read_text())
        self.assertEqual(after["status"], "rejected")
        self.assertTrue(after["leafArchive"]["closing"])
        self.assertTrue(after["leafArchive"]["startRefused"])
        self.assertEqual(after["requestId"], body["requestId"])
        self.assertEqual(
            after["leafArchive"]["outcomes"][body["agentId"]]["state"],
            "gone" if phase == "workspace-open" else "archived",
        )
        self.assertEqual(calls.count("agent-create"), 0 if phase == "workspace-open" else 1)
        if phase == "agent-create":
            self.assertEqual(after["execution"]["agentId"], body["agentId"])
            self.assertTrue(after["hostAgentExists"])
        if created_first:
            self.assertEqual(
                after["leafArchive"]["outcomes"][body["agentId"]],
                before["leafArchive"]["outcomes"][body["agentId"]],
            )

    def _start_during_archive_census(self, thread, state):
        prepare = archive.ArchiveAttempt.prepare

        def start_after_snapshot(attempt, path, snapshot):
            if thread.ident is None:
                self.assertEqual(path, state.path)
                self.assertNotIn("leafCreateEntered", snapshot)
                thread.start()
                self.assertTrue(state.entered.wait(HANG_GUARD_SECONDS))
                self.assertTrue(json.loads(path.read_text())["leafCreateEntered"])
            return prepare(attempt, path, snapshot)

        with patch.object(archive.ArchiveAttempt, "prepare", start_after_snapshot):
            result = prepare_terminal_group(self.contract, dry_run=False)
        agent_id = state.body["agentId"]
        self.assertTrue(result.blockers, "entered native create lost its archive debt")
        self.assertTrue(self.contract.worktree_group.exists())
        self.assertIn(agent_id, {row["agent"] for row in result.blockers})
        self.assertTrue(json.loads(state.path.read_text())["leafArchive"]["creationPending"])
        preview = prepare_terminal_group(self.contract, dry_run=True)
        self.assertIn(agent_id, {row["agent"] for row in preview.blockers})
        return result.agent_archive

    def _paused_creation_host(self, state, _config, command, _payload, **_kwargs):
        self.assertFalse(lock_held(state.path), "host call held the receipt lock")
        state.calls.append(command)
        if command == "agent-archive":
            if state.live.get(state.body["agentId"]):
                state.live[state.body["agentId"]] = False
                return {"found": True, "archived": True}
            return {"found": False}
        if command == "agent-state":
            return {"agent": None}
        if command == "agent-create" and state.created_first:
            state.live[state.body["agentId"]] = True
        if command == state.phase:
            state.entered.set()
            self.assertTrue(
                state.release.wait(HANG_GUARD_SECONDS), "deterministic creation barrier timed out"
            )
        if command == "workspace-open":
            return {"workspace": {"id": "mock-workspace", "directory": str(self.tmp)}}
        if command == "agent-create":
            if not state.created_first:
                state.live[state.body["agentId"]] = True
            return {
                "serverId": "mock-host",
                "agent": {
                    "id": state.body["agentId"],
                    "provider": "codex",
                    "workspaceId": "mock-workspace",
                },
            }
        raise AssertionError(command)

    def test_marked_same_request_replay_refuses_without_creation_and_keeps_debt(self):
        path, body = self.prepared_receipt()
        body.update(status="unknown", leafCreateEntered=True)
        path.write_text(json.dumps(body))
        with patch.object(archive, "bridge_call", return_value={"found": False}):
            closing = archive.LeafAgentArchive(self.config).archive(self.contract)
        self.assertTrue(closing["owed"])
        request = RoleDispatchRequest(
            role="worker",
            requestId=body["requestId"],
            taskDocumentRef=TaskDocumentRef(
                repository=self.ref["repository"], path=self.ref["path"]
            ),
        )
        with (
            patch.object(paseo_launch, "bridge_call") as create,
            patch.object(archive, "bridge_call", return_value={"found": False}),
        ):
            answer = _reconcile_prior_execution(self.config, path, request, "prepared-request")
        create.assert_not_called()
        assert answer is not None
        self.assertEqual(answer.status_code, 409)
        self.assertTrue(json.loads(bytes(answer.body))["startRefused"])
        saved = json.loads(path.read_text())
        self.assertEqual(saved["leafArchive"]["outcomes"][body["agentId"]]["state"], "owed")
        self.assertEqual(saved["pendingArchiveAgentId"], body["agentId"])
        self.assertIn("replayRequest", saved)
        with (
            self.assertRaises(HTTPException),
            patch.object(paseo_launch, "bridge_call") as create,
        ):
            _reconcile_prior_execution(
                self.config,
                path,
                request.model_copy(update={"request_id": uuid.uuid4()}),
                "new-request",
            )
        create.assert_not_called()
        self.assertEqual(
            path.read_bytes(), json.dumps(saved, ensure_ascii=False, indent=2).encode() + b"\n"
        )
        with patch.object(
            archive, "bridge_call", return_value={"found": True, "archived": True}
        ) as settle:
            recovery.LeafArchiveRecovery(self.config).tick()
        self.assertEqual(settle.call_args.args[2]["agentId"], body["agentId"])
        settled = json.loads(path.read_text())["leafArchive"]
        with (
            patch.object(paseo_launch, "bridge_call") as create,
            patch.object(archive, "bridge_call") as settle,
        ):
            answer = _reconcile_prior_execution(self.config, path, request, "prepared-request")
        create.assert_not_called()
        settle.assert_not_called()
        assert answer is not None
        self.assertEqual(answer.status_code, 409)
        self.assertEqual(
            json.loads(path.read_text())["leafArchive"]["outcomes"], settled["outcomes"]
        )

    def test_terminal_mark_covers_other_prepared_roles_before_host_calls(self):
        self.prepared_receipt()
        reviewer_path, reviewer = self.prepared_receipt("reviewer")
        answers = []

        def host(_config, _command, _payload, **_kwargs):
            if not answers:
                # A different prepared role continues while the first archive RPC is in flight.
                answers.append(None)
                answers[0] = role_launch_receipts._execute_prepared_launch(
                    self.config, reviewer_path, reviewer
                )
            return {"found": False}

        with (
            patch.object(paseo_launch, "bridge_call") as create,
            patch.object(archive, "bridge_call", side_effect=host),
        ):
            archive.LeafAgentArchive(self.config).archive(self.contract)
        create.assert_not_called()
        self.assertEqual(answers[0].status_code, 409)
        self.assertEqual(json.loads(reviewer_path.read_text())["status"], "rejected")

    def test_stale_publication_keeps_archive_and_only_exact_leaf_mark_refuses(self):
        self._allow_prepared_start()
        path, body = self.prepared_receipt()
        with patch.object(archive, "bridge_call", return_value={"found": False}):
            archive.LeafAgentArchive(self.config).archive(self.contract)
        marked = json.loads(path.read_text())
        stale = {**body, "status": "running"}
        role_launch_receipts._write_receipt(path, stale)
        self.assertEqual(json.loads(path.read_text())["leafArchive"], marked["leafArchive"])
        for role, ref in [("manager", self.ref), ("worker", {**self.ref, "path": "other.json"})]:
            with self.subTest(role=role, ref=ref):
                foreign, saved = self.prepared_receipt(role, ref=ref)
                saved["leafArchive"] = marked["leafArchive"]
                foreign.write_text(json.dumps(saved))
                with patch.object(
                    role_launch_receipts,
                    "run_launch_call",
                    return_value=paseo_launch.LaunchOutcome(
                        kind="created",
                        execution={"kind": "paseo-agent", "agentId": saved["agentId"]},
                        applied={},
                    ),
                ):
                    answer = role_launch_receipts._execute_prepared_launch(
                        self.config, foreign, saved
                    )
                self.assertEqual(answer.status_code, 200)
                self.assertEqual(json.loads(bytes(answer.body))["status"], "running")

    def test_current_history_retry_and_other_selections_preserve_every_artifact(self):
        records = [self.receipt(), self.receipt("reviewer"), self.receipt("curator", history=True)]
        by_agent = {body["agentId"]: path for path, body in records}
        foreign = [self.receipt("manager"), self.receipt(ref={**self.ref, "path": "another.json"})]
        foreign_bytes = {path: path.read_bytes() for path, _body in foreign}
        report = self.directory.parent / "worker.md"
        artifact = self.directory.parent / "worker.handover.txt"
        report.write_text("A surviving report.")
        artifact.write_text("A surviving assignment.")

        def archive_then_refresh(_config, _command, payload, **_options):
            path = by_agent[payload["agentId"]]
            before = json.loads(path.read_text())
            apply_execution_reading(path, before, AgentReading(archived=True))
            self.assertNotIn("retiredBy", json.loads(path.read_text()))
            return {"found": True, "archived": True}

        with patch.object(archive, "bridge_call", side_effect=archive_then_refresh) as call:
            result = archive.LeafAgentArchive(self.config).archive(self.contract)
            self.assertEqual(
                {row["agentId"] for row in result["archived"]},
                {body["agentId"] for _path, body in records},
            )
            result = archive.LeafAgentArchive(self.config).archive(self.contract)
            self.assertEqual(len(result["alreadyArchived"]), 3)
            self.assertEqual(call.call_count, 3)
        self.assertEqual(foreign_bytes, {path: path.read_bytes() for path, _body in foreign})
        self.assertEqual(report.read_text(), "A surviving report.")
        self.assertEqual(artifact.read_text(), "A surviving assignment.")
        for path, _body in records:
            with self.subTest(path=path):
                prior = json.loads(path.read_text())
                self.assertEqual(prior["status"], "stopped")
                self.assertEqual(prior["retiredBy"]["caller"], "LeafAgentArchive")
                self.assertEqual(prior["retiredBy"]["reason"], "task finished")

    def test_archive_preserves_the_successor_lifecycle_receipt(self):
        contract = replace(self.contract, lifecycle_id="01ARZ3NDEKTSV4RRFFQ69G5FAV")
        path, receipt = self.receipt()
        receipt["workspace"]["lifecycleId"] = "01ARZ3NDEKTSV4RRFFQ69G5FAW"
        path.write_text(json.dumps(receipt))
        expected = path.read_bytes()
        with patch.object(
            archive, "bridge_call", return_value={"found": True, "archived": True}
        ) as host:
            report = archive.LeafAgentArchive(self.config).archive(contract)
        self.assertEqual(path.read_bytes(), expected, "archive rewrote a successor receipt")
        host.assert_not_called()
        self.assertFalse(report["archived"])

    def test_predecessor_debt_survives_host_failure_and_automatic_recovery_advances_it(self):
        path, body = self.receipt()
        predecessor = str(uuid.uuid4())
        body["pendingArchiveAgentId"] = predecessor
        path.write_text(json.dumps(body))
        with patch.object(
            archive,
            "bridge_call",
            side_effect=PaseoBridgeFailure(DAEMON_UNREACHABLE, "Host stopped."),
        ):
            result = archive.LeafAgentArchive(self.config).archive(self.contract)
        self.assertEqual({row["agentId"] for row in result["owed"]}, {predecessor, body["agentId"]})
        self.assertEqual(json.loads(path.read_text())["pendingArchiveAgentId"], predecessor)
        with patch.object(
            archive, "bridge_call", return_value={"found": True, "archived": True}
        ) as call:
            recovery.LeafArchiveRecovery(self.config).tick()
        self.assertEqual(
            [row.args[2]["agentId"] for row in call.call_args_list], [predecessor, body["agentId"]]
        )
        settled = json.loads(path.read_text())
        self.assertNotIn("pendingArchiveAgentId", settled)
        self.assertEqual(len(settled["leafArchive"]["outcomes"]), 2)
        settled_bytes = path.read_bytes()
        with patch.object(archive, "bridge_call") as call:
            repeated = archive.LeafAgentArchive(self.config).archive(self.contract)
        call.assert_not_called()
        self.assertEqual(path.read_bytes(), settled_bytes)
        self.assertEqual(
            {row["agentId"] for row in repeated["alreadyArchived"]},
            {predecessor, body["agentId"]},
        )

    def test_debt_write_failure_recovers_from_terminal_truth_after_restart(self):
        path, body = self.receipt()
        original = path.read_bytes()
        with (
            patch.object(archive, "_write_receipt", side_effect=PermissionError("Read only.")),
            patch.object(
                archive,
                "bridge_call",
                side_effect=PaseoBridgeFailure(DAEMON_UNREACHABLE, "Host stopped."),
            ) as host,
        ):
            result = archive.LeafAgentArchive(self.config).archive(self.contract)
        host.assert_not_called()
        self.assertFalse(result["owed"])
        self.assertTrue(
            any(
                row["recordPath"] == str(path) and "Read only." in row["reason"]
                for row in result["leftAlone"]
            ),
            result,
        )
        self.assertEqual(path.read_bytes(), original)
        with patch.object(archive, "bridge_call", return_value={"found": False}) as call:
            recovery.LeafArchiveRecovery(self.config).tick()
        self.assertEqual(call.call_args.args[2]["agentId"], body["agentId"])
        self.assertEqual(
            json.loads(path.read_text())["leafArchive"]["outcomes"][body["agentId"]]["state"],
            "gone",
        )

    def test_the_dashboard_s_own_loop_archives_terminal_debt_without_a_person_calling_recovery(
        self,
    ):
        """The recovery ticks in a loop of its own: nothing else of the serving app is started."""
        path, body = self.receipt()

        async def scenario():
            observed = asyncio.Event()
            loop = asyncio.get_running_loop()

            def host(*args, **kwargs):
                loop.call_soon_threadsafe(observed.set)
                return {"found": True, "archived": True}

            runtime = cast(
                _ServingRuntime,
                SimpleNamespace(
                    launcher_catalog_prepare=None,
                    master_preparation=None,
                    config=self.config,
                    background_ledger=BackgroundLedger(),
                ),
            )
            with patch.object(archive, "bridge_call", side_effect=host) as call:
                owner = asyncio.create_task(lifespan._leaf_archive_recovery_loop(runtime))
                try:
                    await asyncio.wait_for(observed.wait(), timeout=HANG_GUARD_SECONDS)
                finally:
                    owner.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await owner
                self.assertEqual(call.call_args.args[2]["agentId"], body["agentId"])

        asyncio.run(scenario())
        settled = json.loads(path.read_text())
        self.assertEqual(settled["leafArchive"]["outcomes"][body["agentId"]]["state"], "archived")
        self.assertNotIn("pendingArchiveAgentId", settled)

    def test_empty_unreadable_and_conflicting_records_are_named_without_host_calls(self):
        service = archive.LeafAgentArchive(self.config)
        self.assertIn("No role agents", service.archive(self.contract)["summary"])
        bad = self.directory / "broken.json"
        bad.parent.mkdir(parents=True, exist_ok=True)
        bad.write_text("broken")
        path, body = self.receipt()
        body["arMcpContext"] = {
            "taskContext": {"task_document_ref": {**self.ref, "path": "another.json"}}
        }
        path.write_text(json.dumps(body))
        before = path.read_bytes()
        with patch.object(archive, "bridge_call") as call:
            result = service.archive(self.contract)
        call.assert_not_called()
        self.assertEqual({row["recordPath"] for row in result["leftAlone"]}, {str(bad), str(path)})
        self.assertEqual(path.read_bytes(), before)

    def test_host_budget_and_receipt_settlement_stay_bounded_at_two_scales(self):
        for size in (8, 32):
            with self.subTest(size=size):
                records = [self.receipt() for _ in range(size)]
                clock = [0.0]

                def slow_host(*args, clock=clock, **kwargs):
                    clock[0] += 2.0
                    return {"found": True, "archived": True}

                attempt = archive.ArchiveAttempt(self.config, self.contract, self.ref, 8.0)
                with (
                    patch.object(
                        archive.time, "monotonic", side_effect=lambda clock=clock: clock[0]
                    ),
                    patch.object(archive, "bridge_call", side_effect=slow_host) as call,
                ):
                    for path, body in records:
                        attempt.record(path, body)
                self.assertEqual(call.call_count, 4)
                self.assertEqual(len(attempt.report["owed"]), size - 4)
                self.assertTrue(
                    all(
                        len(json.loads(path.read_text())["leafArchive"]["outcomes"]) <= 2
                        for path, _body in records
                    )
                )

    def test_automatic_scan_is_bounded_and_does_not_starve_the_tail(self):
        for size in (128, 256):
            with self.subTest(size=size):
                observer = recovery.LeafArchiveRecovery(self.config)
                observer._cursor = (self.tmp / f"{index}.json" for index in range(size))
                with patch.object(observer, "_record") as record:
                    observer.tick()
                    self.assertEqual(record.call_count, recovery.SCAN_ENTRIES_PER_PASS)
                    observer._next_at = 0
                    observer.tick()
                    self.assertEqual(record.call_count, size)

    def test_finalize_preview_and_rejected_admission_archive_nobody(self):
        port = Mock()
        bind_worktree_services(replace(worktree_services(), leaf_agent_archive=port))
        finalize_result(FinalizeArgs(contract_path=self.contract.contract_path, dry_run=True))
        self._set_leaf_steps(self.leaf, [{"id": "S1", "title": "Unfinished", "status": "pending"}])
        result = finalize_result(FinalizeArgs(contract_path=self.contract.contract_path))
        self.assertEqual(result.payload["state"], "task-steps-blocked")
        port.archive.assert_not_called()

    def test_repeated_finalize_reports_archive_outcomes_without_changing_task_again(self):
        self.receipt()
        bind_worktree_services(
            replace(worktree_services(), leaf_agent_archive=archive.LeafAgentArchive(self.config))
        )
        with patch.object(
            archive, "bridge_call", return_value={"found": True, "archived": True}
        ) as call:
            first = _payload(
                finalize_result(FinalizeArgs(contract_path=self.contract.contract_path))
            )
            before = self.leaf.read_bytes()
            second = _payload(
                finalize_result(FinalizeArgs(contract_path=self.contract.contract_path))
            )
        self.assertEqual(first["state"], "finalized")
        self.assertEqual(len(first["agentArchive"]["archived"]), 1)
        self.assertEqual(len(second["agentArchive"]["alreadyArchived"]), 1)
        self.assertEqual(self.leaf.read_bytes(), before)
        self.assertEqual(read_task_doc(self.leaf).status, "Completed")
        self.assertEqual(call.call_count, 1)

    def test_admitted_cleanup_and_abandon_archive_before_destructive_outputs(self):
        for module, function, owner, outputs, publication in [
            (
                terminal_cleanup,
                terminal_cleanup._cleanup_with_guard,
                cleanup,
                "_cleanup_terminal_outputs",
                "_cleanup_outputs_result",
            ),
            (
                terminal_abandon,
                terminal_abandon._abandon_with_guard,
                terminal_abandon.abandon,
                "_abandon_terminal_outputs",
                "_abandon_outputs_result",
            ),
        ]:
            with self.subTest(module=module.__name__):
                order = []

                def archived(contract, order=order):
                    order.append("archive")
                    return {"archived": [{"agentId": "archived-agent"}]}

                port = SimpleNamespace(archive=archived)
                bind_worktree_services(replace(worktree_services(), leaf_agent_archive=port))
                terminal = SimpleNamespace(archived_contract=self.contract)
                success = WorktreeCommandResult(0, {"state": "closed"})
                with (
                    patch.object(module, "terminal_archive_required_result", return_value=success),
                    patch.object(
                        module, "terminal_contract_authority_if_present", return_value=terminal
                    ),
                    patch.object(
                        owner,
                        outputs,
                        side_effect=lambda *args, order=order, **kwargs: order.append("remove"),
                    ),
                    patch.object(owner, publication, return_value=success),
                ):
                    result = function(
                        WorktreeArgs(contract_path=self.contract.contract_path, approved=True),
                        self.contract,
                        Mock(),
                        Mock(),
                    )
                self.assertEqual(order, ["archive", "remove"])
                self.assertEqual(result.returncode, 0)
                self.assertTrue(_payload(result)["agentArchive"]["archived"])

    def _native_terminal_receipt(self, *, landed: bool):
        fixture_root = self.tmp / "native-terminal"
        fixture_root.mkdir()
        native_fixture = authority_fixtures._fixture

        def fixture_with_lifecycle(root: Path, *, publish_locations: bool, selected_profile: bool):
            assert not publish_locations
            fixture = native_fixture(
                root, publish_locations=publish_locations, selected_profile=selected_profile
            )
            fixture.leaf_contract = replace(
                fixture.leaf_contract, lifecycle_id="01ARZ3NDEKTSV4RRFFQ69G5FAV"
            )
            return fixture

        # The locator and the citation-cache guard must observe the same leaf generation.
        with patch.object(authority_fixtures, "_fixture", side_effect=fixture_with_lifecycle):
            contract = _landed_leaf(fixture_root)
        config = (
            _landed_leaf_config(fixture_root, contract)
            if landed
            else _public_config(fixture_root, contract)
        )
        leaf = resolve_terminal_leaf_doc(contract.task_root, contract.leaf_id)
        assert leaf is not None
        path = contract.task_root / "notes" / "reports" / EXECUTIONS_DIRECTORY / "worker.json"
        path.parent.mkdir(parents=True)
        agent_id = str(uuid.uuid4())
        ref = {
            "repository": contract.repo_name,
            "path": leaf[0]
            .relative_to(contract.coordination_root / "tasks" / contract.repo_name)
            .as_posix(),
        }
        path.write_text(
            json.dumps(
                {
                    "schema": RECEIPT_SCHEMA,
                    "requestId": str(uuid.uuid4()),
                    "role": "worker",
                    "agentId": agent_id,
                    "status": "running",
                    "selection": {"role": "worker", "taskDocumentRef": ref},
                    "workspace": {
                        "contractPath": str(contract.contract_path),
                        "lifecycleId": contract.lifecycle_id,
                        "path": str(contract.worktree_group),
                    },
                }
            )
        )
        bind_worktree_services(
            replace(worktree_services(), leaf_agent_archive=archive.LeafAgentArchive(config))
        )
        return contract, config, path, agent_id

    def _assert_terminal_waiting_message(self, *, force: bool) -> None:
        contract, config, receipt, agent_id = self._native_terminal_receipt(landed=not force)
        text = "A still waiting message with exact text.\nPreserve the second line."
        hold_message(
            contract.worktree_group,
            agent_id,
            HeldMessage(
                sender_line="From manager",
                sender_agent_id=str(uuid.uuid4()),
                text=text,
                reason="test",
            ),
        )
        with patch.object(archive, "bridge_call", return_value={"found": True, "archived": True}):
            if force:
                result = worktree_tools.worktree_abandon_tool(
                    config, contract_path=contract.contract_path.as_posix(), force=True
                )
            else:
                result = worktree_tools.worktree_cleanup_tool(
                    config, contract_path=contract.contract_path.as_posix()
                )
        self.assertTrue(result["ok"], result)
        self.assertFalse(contract.worktree_group.exists())
        closed = json.loads(receipt.read_text())
        waiting = closed["heldStoreClosures"]["0"]["waitingMessages"]
        self.assertEqual([row["text"] for row in waiting], [text])

    def test_cleanup_records_held_message_before_group_removal(self) -> None:
        self._assert_terminal_waiting_message(force=False)

    def test_forced_abandon_records_held_message_before_group_removal(self) -> None:
        self._assert_terminal_waiting_message(force=True)

    def test_cleanup_blocks_on_owed_in_group_seat(self) -> None:
        contract, config, receipt, agent_id = self._native_terminal_receipt(landed=True)
        with patch.object(
            archive,
            "bridge_call",
            side_effect=PaseoBridgeFailure(DAEMON_UNREACHABLE, "Host stopped."),
        ):
            result = worktree_tools.worktree_cleanup_tool(
                config, contract_path=contract.contract_path.as_posix()
            )
        self.assertFalse(result["ok"], result)
        self.assertIn("blockers", result, result)
        self.assertTrue(any(row.get("agent") == agent_id for row in result["blockers"]), result)
        self.assertTrue(contract.code_worktree.exists())
        self.assertTrue(json.loads(receipt.read_text())["leafArchive"]["closing"])

    def test_cleanup_preview_reports_persisted_archive_debt_without_writing(self) -> None:
        contract, config, receipt, agent_id = self._native_terminal_receipt(landed=True)
        run = partial(
            worktree_tools.worktree_cleanup_tool, config, contract_path=str(contract.contract_path)
        )
        store = store_path_for(contract.worktree_group, agent_id)
        hold_message(
            contract.worktree_group,
            agent_id,
            HeldMessage(
                sender_line="From manager",
                sender_agent_id=str(uuid.uuid4()),
                text="Preserve this waiting message.",
                reason="test",
            ),
        )
        self.assertTrue(store.is_file())
        with patch.object(
            archive,
            "bridge_call",
            side_effect=PaseoBridgeFailure(DAEMON_UNREACHABLE, "Host stopped."),
        ) as host:
            applied = run()
            self.assertIn(agent_id, {row.get("agent") for row in applied["blockers"]})
            self.assertFalse(store.exists())
            closed_bytes = receipt.read_bytes()
            host.reset_mock()
            preview = run(dry_run=True)
            self.assertEqual(
                {row.get("agent") for row in preview.get("blockers", [])},
                {agent_id},
                "preview omitted the persisted archive debt",
            )
            self.assertFalse(preview["ok"], preview)
            self.assertEqual([call.args[1] for call in host.call_args_list], ["agent-state"])
            self.assertEqual(receipt.read_bytes(), closed_bytes)
            self.assertFalse(store.exists())
        self.assertTrue(contract.code_worktree.exists())

        def archived(_config, command, _payload, **_kwargs):
            if command == "agent-state":
                return {"agent": {"id": agent_id, "status": "idle", "archivedAt": "now"}}
            self.assertEqual(command, "agent-archive")
            return {"found": True, "archived": True, "alreadyArchived": True}

        with patch.object(archive, "bridge_call", side_effect=archived) as host:
            preview = run(dry_run=True)
            self.assertTrue(preview["ok"], preview)
            self.assertEqual([call.args[1] for call in host.call_args_list], ["agent-state"])
            self.assertEqual(receipt.read_bytes(), closed_bytes)
            self.assertFalse(store.exists())
            applied = run()
        self.assertTrue(applied["ok"], applied)
        self.assertFalse(contract.worktree_group.exists())
        self.assertEqual(
            json.loads(receipt.read_text())["leafArchive"]["outcomes"][agent_id]["state"],
            "archived",
        )

    def test_rejected_terminal_archive_never_calls_the_host_or_removes_worktrees(self):
        port = Mock()
        bind_worktree_services(replace(worktree_services(), leaf_agent_archive=port))
        refusal = WorktreeCommandResult(2, {"state": "refused"})
        with (
            patch.object(
                terminal_cleanup, "terminal_archive_required_result", return_value=refusal
            ),
            patch.object(cleanup, "_cleanup_terminal_outputs") as remove,
        ):
            result = terminal_cleanup._cleanup_with_guard(
                WorktreeArgs(contract_path=self.contract.contract_path),
                self.contract,
                Mock(),
                Mock(),
            )
        self.assertEqual(result.returncode, 2)
        remove.assert_not_called()
        port.archive.assert_not_called()


def test_the_recovery_loop_is_a_background_task_of_the_serving_lifespan_and_keeps_its_interval(
    tmp_path,
):
    """The loop is started by the lifespan itself and asks once a second, as it did before."""

    assert recovery.RECOVERY_TICK_SECONDS == 1.0
    started: list[str] = []

    async def record(name: str, *_arguments: object) -> None:
        started.append(name)
        await asyncio.Event().wait()

    async def scenario() -> None:
        runtime = cast(
            _ServingRuntime,
            SimpleNamespace(
                launcher_catalog_prepare=None,
                stream_shutdown=StreamShutdown(),
                master_preparation=None,
                config=McpRuntimeConfig(
                    config_path=tmp_path / "settings.json",
                    coordination_root=tmp_path,
                    workspace_root=tmp_path / "projects",
                    transcript_root=tmp_path / "logs",
                ),
                background_ledger=BackgroundLedger(),
                observer_root=None,
                clock=lambda: None,
                review_trees_shutdown=None,
                held_reset_delivery=None,
                # The lifespan reads the explainer's ports to decide whether the moved
                # watcher starts (``_app_common.py``: ``explainer_application = None``).
                explainer_application=None,
                projector=SimpleNamespace(
                    close_readiness=lambda: None, prime=_settled, run=partial(record, "projection")
                ),
            ),
        )
        with (
            patch.object(lifespan, "compact_workspace_river"),
            patch.object(lifespan, "start_heap_tracing", return_value=False),
            patch.object(lifespan, "malloc_trim_enabled", return_value=False),
            patch.object(lifespan, "_held_delivery_loop", partial(record, "held-delivery")),
            patch.object(lifespan, "_warmup_watch_loop", partial(record, "warmup-watch")),
            patch.object(
                lifespan, "_workspace_river_compaction_loop", partial(record, "river-compaction")
            ),
            patch.object(
                lifespan, "_leaf_archive_recovery_loop", partial(record, "archive-recovery")
            ),
        ):
            async with lifespan._serving_lifespan(runtime)(cast(Any, None)):
                await asyncio.sleep(0)

    asyncio.run(scenario())
    assert sorted(started) == [
        "archive-recovery",
        "held-delivery",
        "projection",
        "river-compaction",
        "warmup-watch",
    ]


async def _settled() -> None:
    return None
