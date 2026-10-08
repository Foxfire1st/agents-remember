"""Leaf archive ownership, durable recovery and admitted pre-removal ordering."""

from __future__ import annotations

import asyncio
import contextlib
import json
import threading
import uuid
from dataclasses import replace
from functools import partial
from types import SimpleNamespace
from typing import cast
from unittest.mock import Mock, patch

import pytest
from agents_remember.cli import paseo_launch, role_launch_receipts
from agents_remember.cli import role_launch_archive as archive
from agents_remember.cli import role_launch_archive_recovery as recovery
from agents_remember.cli.paseo_bridge import DAEMON_UNREACHABLE, PaseoBridgeFailure
from agents_remember.cli.role_launch_liveness import _reconcile_prior_execution
from agents_remember.cli.role_launch_receipts import EXECUTIONS_DIRECTORY, RECEIPT_SCHEMA
from agents_remember.kernel.file_lock import lock_held
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.role_launcher import RoleDispatchRequest
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.serving import _app_lifespan as lifespan
from agents_remember.serving._app_common import _ServingRuntime
from agents_remember.tasks import read_task_doc
from agents_remember.worktrees.modules import cleanup, terminal_abandon, terminal_cleanup
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.finalize import FinalizeArgs, finalize_result
from agents_remember.worktrees.modules.models import WorktreeCommandResult
from agents_remember.worktrees.services import bind_worktree_services, worktree_services
from fastapi import HTTPException
from test_lifecycle_finalize import _FinalizeFixtures, _payload

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

    def _assert_terminal_launch_interleaving(self, phase, created_first):
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
            thread.start()
            try:
                self.assertTrue(entered.wait(5))
                closing = archive.LeafAgentArchive(self.config).archive(self.contract)
                self.assertTrue(thread.is_alive())
                self.assertFalse(release.is_set(), "closing waited for the opaque creation call")
                before = json.loads(path.read_text())
                if created_first:
                    self.assertIn(body["agentId"], {row["agentId"] for row in closing["archived"]})
                else:
                    self.assertIn(body["agentId"], {row["agentId"] for row in closing["owed"]})
                    self.assertNotIn(body["agentId"], {row["agentId"] for row in closing["gone"]})
            finally:
                release.set()
                thread.join(5)
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

    def _paused_creation_host(self, state, _config, command, _payload, **_kwargs):
        self.assertFalse(lock_held(state.path), "host call held the receipt lock")
        state.calls.append(command)
        if command == "agent-archive":
            if state.live.get(state.body["agentId"]):
                state.live[state.body["agentId"]] = False
                return {"found": True, "archived": True}
            return {"found": False}
        if command == "agent-create" and state.created_first:
            state.live[state.body["agentId"]] = True
        if command == state.phase:
            state.entered.set()
            self.assertTrue(state.release.wait(5), "deterministic creation barrier timed out")
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
        foreign = [self.receipt("manager"), self.receipt(ref={**self.ref, "path": "another.json"})]
        foreign_bytes = {path: path.read_bytes() for path, _body in foreign}
        report = self.directory.parent / "worker.md"
        artifact = self.directory.parent / "worker.handover.txt"
        report.write_text("A surviving report.")
        artifact.write_text("A surviving assignment.")
        with patch.object(
            archive, "bridge_call", return_value={"found": True, "archived": True}
        ) as call:
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
        self.assertTrue(
            all(json.loads(path.read_text())["status"] == "running" for path, _body in records)
        )

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
            ),
        ):
            result = archive.LeafAgentArchive(self.config).archive(self.contract)
        self.assertTrue(result["owed"])
        self.assertTrue(result["leftAlone"])
        self.assertEqual(path.read_bytes(), original)
        with patch.object(archive, "bridge_call", return_value={"found": False}) as call:
            recovery.LeafArchiveRecovery(self.config).tick()
        self.assertEqual(call.call_args.args[2]["agentId"], body["agentId"])
        self.assertEqual(
            json.loads(path.read_text())["leafArchive"]["outcomes"][body["agentId"]]["state"],
            "gone",
        )

    def test_existing_observer_archives_terminal_debt_without_a_person_calling_recovery(self):
        path, body = self.receipt()

        async def scenario():
            observed = asyncio.Event()
            loop = asyncio.get_running_loop()

            def host(*args, **kwargs):
                loop.call_soon_threadsafe(observed.set)
                return {"found": True, "archived": True}

            runtime = cast(_ServingRuntime, SimpleNamespace(config=self.config))
            with (
                patch.object(lifespan, "_observe_terminal_catalog", return_value=None),
                patch.object(archive, "bridge_call", side_effect=host) as call,
            ):
                owner = asyncio.create_task(lifespan._terminal_observation_loop(runtime))
                try:
                    await asyncio.wait_for(observed.wait(), timeout=10)
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
                    return {"owed": [{"reason": "Host stopped."}]}

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
                self.assertTrue(_payload(result)["agentArchive"]["owed"])

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
