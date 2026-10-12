"""MIK-R117: a message between agents wakes the recipient; the full text is a file."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import patch

from agents_remember.cli import paseo_role_tools, role_message_delivery
from agents_remember.cli.role_launch_workspace import link_leaf_workspace_messages
from agents_remember.cli.role_message_files import (
    MessageFileError,
    store_message_file,
)
from agents_remember.kernel.primitives.user_settings_location import user_settings_path
from agents_remember.mcp.registration.role_agents import register_role_agent_tools
from agents_remember.worktrees import role_reports
from mcp.server.fastmcp import FastMCP
from test_paseo_role_tools import (
    ARCHIVED_AT,
    RoleToolsTestCase,
    binding,
    selection_of,
)
from test_paseo_status import NO_ROLLOUT


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class MessageLimitTests(RoleToolsTestCase):
    def test_a_text_at_the_limit_is_delivered_one_character_more_is_refused(self) -> None:
        _request, worker = self.started(status="idle")
        with patch.object(paseo_role_tools, "role_message_limit", return_value=12):
            accepted = self.message(binding("worker"), "123456789012", agent_id=worker)
            self.assertEqual(accepted["status"], "accepted")
            refused = self.refusal(
                self.message(binding("worker"), "1234567890123", agent_id=worker),
                "message-too-long",
            )
        # The refusal names the count, the limit, and the two inputs of rule 3.
        self.assertIn("13 characters", refused["detail"])
        self.assertIn("limit of 12", refused["detail"])
        self.assertIn("body", refused["detail"])
        self.assertIn("attach", refused["detail"])
        self.assertEqual((refused["messageLength"], refused["messageLimit"]), (13, 12))
        self.assertEqual(self.received(worker), [accepted["senderLine"] + "\n123456789012"])

    def test_where_the_settings_name_no_number_the_limit_is_1000(self) -> None:
        with patch.dict(os.environ, {"XDG_CONFIG_HOME": str(self.root / "empty-config")}):
            self.assertEqual(
                paseo_role_tools.role_message_limit(self.config, binding("worker")), 1000
            )

    def write_user_settings(self, home: Path, orchestration: dict[str, Any]) -> Path:
        """One XDG settings file with the fixture's host block and one orchestration."""
        real = user_settings_path()
        data = json.loads(real.read_text()) if real.is_file() else {}
        data["orchestration"] = orchestration
        target = home / "agents-remember" / "settings.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(data))
        return target

    def test_a_changed_number_holds_for_the_next_call_without_a_restart(self) -> None:
        home = self.root / "user-config"
        _request, worker = self.started(status="idle")
        settings = self.write_user_settings(home, {"roleMessageLimit": 30})
        with patch.dict(os.environ, {"XDG_CONFIG_HOME": str(home)}):
            self.refusal(
                self.message(binding("worker"), "x" * 31, agent_id=worker),
                "message-too-long",
            )
            data = json.loads(settings.read_text())
            data["orchestration"] = {"roleMessageLimit": 40}
            settings.write_text(json.dumps(data))
            self.assertEqual(
                self.message(binding("worker"), "x" * 31, agent_id=worker)["status"],
                "accepted",
            )

    def test_a_settings_number_that_is_no_positive_whole_number_is_refused_at_load(
        self,
    ) -> None:
        home = self.root / "user-config"
        _request, worker = self.started(status="idle")
        self.write_user_settings(home, {"roleMessageLimit": 30})
        with patch.dict(os.environ, {"XDG_CONFIG_HOME": str(home)}):
            settings = home / "agents-remember" / "settings.json"
            for bad in (0, -3, 1.5, "many", True):
                with self.subTest(bad=bad):
                    data = json.loads(settings.read_text())
                    data["orchestration"] = {"roleMessageLimit": bad}
                    settings.write_text(json.dumps(data))
                    refused = self.refusal(
                        self.message(binding("worker"), "hello", agent_id=worker),
                        "settings-invalid",
                    )
                    self.assertIn("orchestration.roleMessageLimit", refused["detail"])


class MessageFileTests(RoleToolsTestCase):
    def folders(self) -> dict[str, Path]:
        tasks = self.config.coordination_root / "tasks" / "agents-remember"
        return {
            "leaf": tasks / "master" / "notes" / "messages" / "01_leaf",
            "master": tasks / "master" / "notes" / "messages",
            "sprint": tasks / "sprint" / "notes" / "messages",
            "taskless": (
                self.config.coordination_root
                / "notes"
                / "reports"
                / "paseo-native-executions"
                / "architect"
                / "messages"
            ),
        }

    def test_the_file_holds_head_message_and_body_and_the_text_names_it(self) -> None:
        _request, worker = self.started(status="idle")
        reviewer = binding("reviewer")
        body = "the whole verdict text"
        result = self.message(reviewer, "verdict attached", agent_id=worker, body=body)
        self.assertEqual(result["status"], "accepted")
        path = Path(result["messageFile"])
        self.assertEqual(_sha(path), result["messageFileSha256"])
        self.assertRegex(
            path.name,
            r"^\d{8}T\d{6}Z_reviewer-[0-9a-f]{8}_to_worker-[0-9a-f]{8}\.md$",
        )
        content = path.read_text()
        for line in (
            "time:",
            f"sender: From reviewer · 01_LEAF · agent {reviewer.agent_id}",
            "recipientRole: worker",
            f"recipientAgentId: {worker}",
            "messageId:",
            "## message\nverdict attached",
            f"## body\n{body}",
        ):
            self.assertIn(line, content)
        delivered = self.received(worker)[0]
        self.assertTrue(delivered.startswith(f"{result['senderLine']}\nverdict attached\n"))
        self.assertEqual(
            delivered.splitlines()[-1],
            f"Message file: {path.as_posix()} (SHA-256 {result['messageFileSha256']})",
        )
        self.assertNotIn(body, delivered)

    def test_a_body_of_200000_characters_is_written_whole(self) -> None:
        _request, worker = self.started(status="idle")
        body = "B" * 200_000
        result = self.message(binding("reviewer"), "verdict attached", agent_id=worker, body=body)
        path = Path(result["messageFile"])
        self.assertIn(f"## body\n{body}\n", path.read_text())
        self.assertNotIn("B" * 100, self.received(worker)[0].split("verdict attached")[0])

    def test_a_message_with_neither_gets_no_added_line_but_keeps_its_file(self) -> None:
        _request, worker = self.started(status="idle")
        result = self.message(binding("reviewer"), "ping", agent_id=worker)
        self.assertEqual(self.received(worker), [f"{result['senderLine']}\nping"])
        self.assertTrue(Path(result["messageFile"]).is_file())

    def test_two_messages_of_one_second_get_a_counter(self) -> None:
        _request, worker = self.started(status="idle")
        stem = "20261011T000000Z_reviewer-aaaaaaaa_to_worker-bbbbbbbb"
        with patch.object(paseo_role_tools, "utc_stem", return_value=stem):
            first = self.message(binding("reviewer"), "one", agent_id=worker)
            second = self.message(binding("reviewer"), "two", agent_id=worker)
        self.assertTrue(Path(first["messageFile"]).name == f"{stem}.md")
        self.assertTrue(Path(second["messageFile"]).name == f"{stem}-2.md")

    def test_attach_in_the_record_is_named_and_not_copied(self) -> None:
        _request, worker = self.started(status="idle")
        folders = self.folders()
        ruling = folders["master"].parent / "reports" / "ruling.md"
        ruling.parent.mkdir(parents=True, exist_ok=True)
        ruling.write_text("the ruling")
        before = sorted(p.as_posix() for p in self.root.rglob("*") if p.is_file())
        result = self.message(
            binding("manager"), "ruling attached", agent_id=worker, attach=[ruling.as_posix()]
        )
        self.assertEqual(result["status"], "accepted")
        content = Path(result["messageFile"]).read_text()
        self.assertIn(f"- {ruling.as_posix()} sha256:{_sha(ruling)}", content)
        self.assertNotIn("copied as", content)
        after = sorted(p.as_posix() for p in self.root.rglob("*") if p.is_file())
        added = [p for p in after if p not in before]
        self.assertEqual(len(added), 1)
        self.assertTrue(added[0].endswith(".md"))

    def test_attach_outside_is_copied_and_the_copy_is_named(self) -> None:
        _request, worker = self.started(status="idle")
        outside = self.root / "outside" / "note.md"
        outside.parent.mkdir(parents=True)
        outside.write_text("a note from elsewhere")
        result = self.message(
            binding("reviewer"), "note attached", agent_id=worker, attach=[outside.as_posix()]
        )
        content = Path(result["messageFile"]).read_text()
        copies = sorted(Path(result["messageFile"]).parent.glob("*.attach-1-note.md"))
        self.assertEqual(len(copies), 1)
        self.assertEqual(copies[0].read_text(), "a note from elsewhere")
        self.assertIn(
            f"- {outside.as_posix()} sha256:{_sha(outside)} (copied as {copies[0].as_posix()})",
            content,
        )

    def test_a_missing_attach_is_refused_and_nothing_is_written(self) -> None:
        _request, worker = self.started(status="idle")
        before = sorted(p.as_posix() for p in self.root.rglob("*") if p.is_file())
        refused = self.refusal(
            self.message(
                binding("reviewer"),
                "note attached",
                agent_id=worker,
                attach=[(self.root / "no-such.md").as_posix()],
            ),
            "message-file-failed",
        )
        self.assertIn("no-such.md", refused["detail"])
        self.assertEqual(sorted(p.as_posix() for p in self.root.rglob("*") if p.is_file()), before)
        self.assertEqual(self.received(worker), [])

    def test_a_refused_delivery_keeps_its_first_file_when_the_sender_sends_again(
        self,
    ) -> None:
        # A busy recipient is held, not refused (MIK-R100 rule 4b), so the kept
        # file is pinned on a refusal the hold does not take: unresumable.
        _request, worker = self.started(status="idle")
        self.runtime.agents[worker].update(status="closed", received=[])
        self.runtime.agents[worker]["resumeRefusal"] = NO_ROLLOUT
        refused = self.refusal(
            self.message(binding("reviewer"), "are you there", agent_id=worker),
            "recipient-cannot-be-resumed",
        )
        kept = Path(refused["messageFile"])
        self.assertTrue(kept.name.endswith(".undelivered.md"))
        del self.runtime.agents[worker]["resumeRefusal"]
        self.runtime.agents[worker].update(status="idle", received=[])
        accepted = self.message(binding("reviewer"), "are you there", agent_id=worker)
        self.assertEqual(accepted["status"], "accepted")
        self.assertTrue(kept.is_file())
        self.assertNotEqual(kept, Path(accepted["messageFile"]))

    def test_a_wait_reply_longer_than_the_limit_comes_back_cut_and_names_its_file(
        self,
    ) -> None:
        _request, worker = self.started(status="idle")
        reply = "R" * 200
        self.waits = [
            {"state": "running", "turnId": "turn-7"},
            {"state": "ended", "outcome": "finished", "text": reply, "textTruncated": False},
        ]
        self.clock = 0.0
        with patch.object(paseo_role_tools, "role_message_limit", return_value=50):
            result = self.message(self.architect, agent_id=worker, wait=True)
        self.assertEqual(result["status"], "turn-finished")
        self.assertEqual(result["text"], "R" * 50)
        self.assertTrue(result["textTruncated"])
        reply_file = Path(result["messageFile"])
        self.assertRegex(
            reply_file.name,
            r"^\d{8}T\d{6}Z_worker-[0-9a-f]{8}_to_architect-[0-9a-f]{8}\.md$",
        )
        content = reply_file.read_text()
        self.assertIn(f"## body\n{reply}\n", content)
        self.assertIn("The recipient's turn finished", result["detail"])
        self.assertIn(reply_file.as_posix(), result["detail"])

    def test_the_folder_is_the_narrowest_task_both_agents_share(self) -> None:
        folders = self.folders()
        _worker_request, worker = self.started("worker", status="idle")
        _reviewer_request, reviewer = self.started("reviewer", status="idle")
        _manager_request, manager = self.started("manager", status="idle")
        _orchestrator_request, orchestrator = self.started("orchestrator", status="idle")

        leaf = self.message(binding("reviewer", reviewer), "same leaf", agent_id=worker)
        self.assertEqual(Path(leaf["messageFile"]).parent, folders["leaf"])
        master = self.message(binding("manager", manager), "same master", agent_id=reviewer)
        self.assertEqual(Path(master["messageFile"]).parent, folders["master"])
        sprint = self.message(
            binding("orchestrator", orchestrator), "same sprint", agent_id=manager
        )
        self.assertEqual(Path(sprint["messageFile"]).parent, folders["sprint"])
        taskless = self.message(self.architect, "no shared task", agent_id=worker)
        self.assertEqual(Path(taskless["messageFile"]).parent, folders["taskless"])

    def test_a_leaf_workspace_gets_its_messages_link_at_creation(self) -> None:
        _worker_request, worker = self.started("worker", status="idle")
        _reviewer_request, reviewer = self.started("reviewer", status="idle")
        group = self.root / "enclosure-group"
        link = group / "messages"
        # New workspaces carry the link from creation (F02), before any message.
        self.assertTrue(link.is_symlink())
        self.assertEqual(link.resolve(), self.folders()["leaf"].resolve())
        result = self.message(binding("worker", worker), "hello", agent_id=reviewer)
        self.assertEqual(result["status"], "accepted")
        self.assertTrue(link.is_symlink())
        self.assertEqual(link.resolve(), self.folders()["leaf"].resolve())

    def test_cleanup_removes_the_link_and_leaves_every_file(self) -> None:
        group = self.root / "group"
        record = self.root / "task" / "notes" / "messages" / "01_leaf"
        record.mkdir(parents=True)
        kept = record / "20261011T000000Z_worker-aaaaaaaa_to_reviewer-bbbbbbbb.md"
        kept.write_text("a message")
        (group / "messages").parent.mkdir(parents=True, exist_ok=True)
        (group / "messages").symlink_to(record, target_is_directory=True)
        contract = SimpleNamespace(worktree_group=group, task_root=self.root / "task")
        outcome = role_reports.remove_group_role_folders(
            contract,  # type: ignore[arg-type]
            dry_run=False,
        )
        self.assertFalse((group / "messages").is_symlink())
        self.assertEqual(kept.read_text(), "a message")
        self.assertTrue(outcome["messages"]["removed"])
        # A real directory at the link's name is never deleted.
        occupied = self.root / "grouped"
        real = occupied / "messages"
        real.mkdir(parents=True)
        (real / "own.md").write_text("not a link")
        contract = SimpleNamespace(worktree_group=occupied, task_root=self.root / "task")
        outcome = role_reports.remove_group_role_folders(contract, dry_run=False)  # type: ignore[arg-type]
        self.assertTrue((real / "own.md").is_file())
        self.assertEqual(outcome["messages"]["reason"], "not-a-link-preserved")

    def test_a_first_inbound_message_links_the_recipient_workspace(self) -> None:
        _manager_request, manager = self.started("manager", status="idle")
        _reviewer_request, reviewer = self.started("reviewer", status="idle")
        group = self.root / "enclosure-group"
        link = group / "messages"
        # An old workspace predates links: drop the creation link first.
        link.unlink()
        self.assertFalse(link.exists() or link.is_symlink())
        # The sender is no leaf agent, so only the recipient side can link.
        result = self.message(binding("manager", manager), "read this", agent_id=reviewer)
        self.assertEqual(result["status"], "accepted")
        self.assertTrue(link.is_symlink())
        self.assertEqual(link.resolve(), self.folders()["leaf"].resolve())
        self.assertNotIn("warning", result)

    def test_an_occupied_recipient_path_is_preserved_with_a_warning(self) -> None:
        _manager_request, manager = self.started("manager", status="idle")
        _reviewer_request, reviewer = self.started("reviewer", status="idle")
        link = self.root / "enclosure-group" / "messages"
        link.unlink()
        occupied = self.root / "enclosure-group" / "messages"
        occupied.mkdir(parents=True)
        (occupied / "own.md").write_text("not a link")
        result = self.message(binding("manager", manager), "read this", agent_id=reviewer)
        self.assertEqual(result["status"], "accepted")
        self.assertFalse(occupied.is_symlink())
        self.assertEqual((occupied / "own.md").read_text(), "not a link")
        self.assertIn("warning", result)

    def test_sender_first_and_recipient_converge_on_the_leaf_folder(self) -> None:
        _worker_request, worker = self.started("worker", status="idle")
        _reviewer_request, reviewer = self.started("reviewer", status="idle")
        result = self.message(binding("worker", worker), "hello", agent_id=reviewer)
        self.assertEqual(result["status"], "accepted")
        link = self.root / "enclosure-group" / "messages"
        self.assertTrue(link.is_symlink())
        self.assertEqual(link.resolve(), self.folders()["leaf"].resolve())
        self.assertNotIn("warning", result)

    def test_workspace_creation_links_and_preserves(self) -> None:
        task_dir = self.root / "task"
        group = self.root / "group"
        self.assertIsNone(
            link_leaf_workspace_messages(group, task_dir, "agents-remember:leaf.json")
        )
        link = group / "messages"
        self.assertTrue(link.is_symlink())
        self.assertEqual(link.resolve(), (task_dir / "notes" / "messages" / "leaf").resolve())
        # An occupied non-link is preserved with a warning, never replaced.
        link.unlink()
        link.mkdir()
        (link / "own.md").write_text("mine")
        warning = link_leaf_workspace_messages(group, task_dir, "agents-remember:leaf.json")
        self.assertIn("occupied", str(warning))
        self.assertFalse(link.is_symlink())
        self.assertEqual((link / "own.md").read_text(), "mine")


class SuccessorAndReturnTests(RoleToolsTestCase):
    def test_a_message_to_an_archived_agent_names_its_live_successor(self) -> None:
        # The first execution is archived from its start, so the seat is free for
        # the successor (MIK-R100 rule 9: a completed holder would still hold it).
        _first_request, first = self.started("worker", status="closed", archivedAt=ARCHIVED_AT)
        _second_request, second = self.started("worker", status="idle")
        result = self.refusal(
            self.message(binding("manager"), agent_id=first), "recipient-archived"
        )
        self.assertEqual(result["recipientAgentId"], first)
        self.assertEqual(result["successorAgentId"], second)
        self.assertIn(second, result["detail"])
        self.assertIn(f"Send the message again to agent {second}.", result["nextAction"])
        self.assertNotIn("role_start", result["nextAction"])

    def test_no_refusal_tells_a_caller_to_start_a_role_it_may_not_start(self) -> None:
        _request, worker = self.started(status="idle")
        self.runtime.agents[worker].update(status="closed", archivedAt=ARCHIVED_AT, received=[])
        caller = binding("worker")
        result = self.refusal(self.message(caller, agent_id=worker), "recipient-archived")
        self.assertNotIn("role_start", result["nextAction"])
        self.assertIn("Ask the parent", result["nextAction"])
        starter = binding("manager")
        result = self.refusal(self.message(starter, agent_id=worker), "recipient-archived")
        self.assertIn("role_start", result["nextAction"])

    def test_a_return_goes_to_the_role_after_a_manager_is_replaced(self) -> None:
        first_request, first = self.started("manager", status="idle")
        self.close_execution(first_request, "completed")
        _second_request, second = self.started("manager", status="idle")
        self.runtime.agents[first].update(status="closed", archivedAt=ARCHIVED_AT, received=[])
        # The worker's return address names the Manager's role with its task
        # references: the live successor resolves through the receipts.
        _worker_request, worker = self.started("worker", status="idle")
        to_manager = {"role": "manager", **selection_of("manager")}
        result = self.message(binding("worker", worker), "returning", **to_manager)
        self.assertEqual((result["status"], result["recipientAgentId"]), ("accepted", second))


class _FailingWriter:
    """A write-mode binary stream that fails after a number of writes (F01)."""

    def __init__(self, stream: Any, allowed: int) -> None:
        self._stream = stream
        self._allowed = allowed

    def write(self, chunk: bytes) -> int:
        if self._allowed <= 0:
            raise OSError("no space left on device")
        self._allowed -= 1
        return self._stream.write(chunk)

    def __enter__(self) -> Any:
        return self

    def __exit__(self, *exc: Any) -> Any:
        return self._stream.__exit__(*exc)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._stream, name)


def _fail_writes_after(allowed: int) -> Any:
    """Patch open so write-mode binaries fail after some writes; reads pass."""
    real_open = open

    def flaky_open(path: Any, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
        stream = real_open(path, mode, *args, **kwargs)
        if "b" in mode and ("w" in mode or "x" in mode or "+" in mode):
            return _FailingWriter(stream, allowed)
        return stream

    return patch("builtins.open", flaky_open)


class TransactionTests(RoleToolsTestCase):
    """A failed preparation leaves no message-owned residue (L117-C1-F01)."""

    def folder_files(self, folder: Path) -> list[str]:
        if not folder.is_dir():
            return []
        return sorted(
            path.name
            for path in folder.iterdir()
            if path.is_file() and not path.name.startswith(".")
        )

    def leaf_folder(self) -> Path:
        tasks = self.config.coordination_root / "tasks" / "agents-remember"
        return tasks / "master" / "notes" / "messages" / "01_leaf"

    def test_a_failed_copy_removes_its_partial_target(self) -> None:
        _request, worker = self.started(status="idle")
        outside = self.root / "outside" / "ruling.txt"
        outside.parent.mkdir(parents=True)
        outside.write_bytes(b"r" * 100_000)
        with _fail_writes_after(1):
            result = self.refusal(
                self.message(
                    binding("worker"),
                    "ruling attached",
                    agent_id=worker,
                    attach=[outside.as_posix()],
                ),
                "message-file-failed",
            )
        self.assertIn("cannot be copied", result["detail"])
        self.assertEqual(self.folder_files(self.leaf_folder()), [])
        self.assertEqual(self.received(worker), [])

    def test_a_later_store_failure_removes_completed_copies(self) -> None:
        _request, worker = self.started(status="idle")
        outside = self.root / "outside" / "ruling.txt"
        outside.parent.mkdir(parents=True)
        outside.write_text("the ruling")
        with patch.object(
            role_message_delivery,
            "store_message_file",
            side_effect=MessageFileError("injected store failure"),
        ):
            result = self.refusal(
                self.message(
                    binding("worker"),
                    "ruling attached",
                    agent_id=worker,
                    attach=[outside.as_posix()],
                ),
                "message-file-failed",
            )
        self.assertIn("injected store failure", result["detail"])
        self.assertIn("Nothing was delivered", result["detail"])
        self.assertEqual(self.folder_files(self.leaf_folder()), [])
        self.assertEqual(self.received(worker), [])

    def test_a_folder_creation_failure_is_a_named_refusal(self) -> None:
        _request, worker = self.started(status="idle")
        with patch(
            "pathlib.Path.mkdir",
            side_effect=PermissionError("read-only record"),
        ):
            result = self.refusal(
                self.message(binding("worker"), "hello", agent_id=worker),
                "message-file-failed",
            )
        self.assertIn("cannot be created", result["detail"])
        self.assertIn("Nothing was delivered", result["detail"])
        self.assertEqual(self.received(worker), [])


class CollisionCostTests(RoleToolsTestCase):
    """One staged write per message across a same-second batch (L117-C1-F08)."""

    def test_a_same_second_batch_writes_once_per_message(self) -> None:
        for size in (10, 40):
            with self.subTest(size=size):
                stages, syncs, links, scans, paths = self._store_same_second_batch(
                    f"batch-{size}", size
                )
                self.assertEqual(stages, size)
                self.assertEqual(syncs, size)
                self.assertLessEqual(links, 2 * size)
                self.assertEqual(scans, 0)
                self.assertEqual(len({path.name for path in paths}), size)
                for path in paths:
                    self.assertEqual(path.read_text(), "the delivered text")

    def _store_same_second_batch(
        self, folder_name: str, size: int
    ) -> tuple[int, int, int, int, list[Path]]:
        """Store one batch under a pinned stem; count writes, syncs, links, scans."""
        folder = self.root / folder_name
        content = "the delivered text"
        stages = 0
        syncs = 0
        links = 0
        scans = 0
        real_mkstemp = tempfile.mkstemp
        real_fsync = os.fsync
        real_link = os.link
        real_scandir = os.scandir

        def counting_mkstemp(*args: Any, **kwargs: Any) -> Any:
            nonlocal stages
            stages += 1
            return real_mkstemp(*args, **kwargs)

        def counting_fsync(fd: int) -> None:
            nonlocal syncs
            syncs += 1
            real_fsync(fd)

        def counting_link(src: Any, dst: Any) -> None:
            nonlocal links
            links += 1
            real_link(src, dst)

        def counting_scandir(path: Any) -> Any:
            nonlocal scans
            scans += 1
            return real_scandir(path)

        with (
            patch.object(tempfile, "mkstemp", counting_mkstemp),
            patch("os.fsync", counting_fsync),
            patch("os.link", counting_link),
            patch("os.scandir", counting_scandir),
        ):
            paths = [
                store_message_file(folder, "20261011T000000Z_worker-aaa_to_worker-bbb", content)
                for _ in range(size)
            ]
        return stages, syncs, links, scans, paths

    def test_concurrent_same_stem_stores_keep_every_payload(self) -> None:
        folder = self.root / "batch-race"
        stem = "20261011T000000Z_worker-aaa_to_worker-bbb"
        contents = [f"payload {index}" for index in range(8)]
        with ThreadPoolExecutor(max_workers=8) as pool:
            paths = list(pool.map(lambda text: store_message_file(folder, stem, text), contents))
        self.assertEqual(len({path.name for path in paths}), 8)
        self.assertEqual({path.read_text() for path in paths}, set(contents))


class RegisteredRefusalTests(RoleToolsTestCase):
    """The new refusals through the registered tool, not only the function (F07)."""

    def registered(self, caller: Any, arguments: dict[str, Any]) -> dict[str, Any]:
        async def invoke() -> dict[str, Any]:
            server = FastMCP("role-message-registered")
            register_role_agent_tools(server, self.config)
            _content, structured = await server.call_tool("role_message", arguments)
            return cast(dict[str, Any], structured)

        with patch.dict(os.environ, caller.environment()):
            return asyncio.run(invoke())

    def test_exact_limit_accepted_and_over_limit_refused_with_count_and_limit(
        self,
    ) -> None:
        _request, worker = self.started(status="idle")
        caller = binding("worker")
        with patch.object(paseo_role_tools, "role_message_limit", return_value=12):
            accepted = self.registered(caller, {"text": "123456789012", "agent_id": worker})
            self.assertEqual(accepted["status"], "accepted")
            self.assertTrue(accepted["messageFile"])
            refused = self.registered(caller, {"text": "1234567890123", "agent_id": worker})
        self.assertEqual(
            (refused["ok"], refused["status"], refused["refusal"]),
            (False, "refused", "message-too-long"),
        )
        self.assertEqual((refused["messageLength"], refused["messageLimit"]), (13, 12))
        self.assertIn("13 characters", refused["detail"])

    def test_archived_successor_names_the_live_agent_through_registration(
        self,
    ) -> None:
        # The first execution is archived from its start, so the seat is free for
        # the successor (MIK-R100 rule 9: a completed holder would still hold it).
        _first_request, first = self.started("worker", status="closed", archivedAt=ARCHIVED_AT)
        _second_request, second = self.started("worker", status="idle")
        refused = self.registered(binding("manager"), {"text": "again", "agent_id": first})
        self.assertEqual(
            (refused["ok"], refused["status"], refused["refusal"]),
            (False, "refused", "recipient-archived"),
        )
        self.assertEqual(
            (refused["recipientAgentId"], refused["successorAgentId"]), (first, second)
        )


if __name__ == "__main__":
    unittest.main()
