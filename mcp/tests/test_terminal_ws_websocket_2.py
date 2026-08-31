from __future__ import annotations

from dataclasses import replace
from unittest.mock import patch

from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.serving.harness_control_runner import parse_runner_config
from fastapi.testclient import TestClient
from test_terminal_ws import TerminalWebSocketTests, _catalog_entry, _which
from test_worktree_support import git


class TerminalWebSocketTests2(TerminalWebSocketTests):
    LEAF_REF = TaskDocumentRef(repository="repo", path="master/leaf-1.json")
    LEAF_BODY = LEAF_REF.model_dump()

    def _move_super(self) -> None:
        repo = self.tmp / "fixture-repositories" / "repo"
        git(repo, "switch", "super")
        marker = repo / "super-moved.txt"
        marker.write_text("new super\n", encoding="utf-8")
        git(repo, "add", marker.name)
        git(repo, "commit", "-m", "move super")
        git(repo, "switch", "main")

    def test_post_open_409_when_source_lineage_is_stale(self) -> None:
        self._move_super()

        with patch("shutil.which", _which("claude")), TestClient(self.app) as client:
            response = client.post(
                "/api/terminal/stale",
                json={
                    "kind": "harness",
                    "harness": "claude",
                    "role": "worker",
                    "taskDocumentRef": self.LEAF_BODY,
                },
            )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["status"], "source-lineage-stale")
        self.assertEqual(response.json()["sourceLineage"]["state"], "blocked")
        self.assertEqual(self.host.ensured, [])

    def test_attach_task_409_when_source_lineage_is_stale(self) -> None:
        self.catalog.upsert(_catalog_entry("live", cwd=self.tmp))
        self._move_super()

        with TestClient(self.app) as client:
            response = client.post(
                "/api/terminal/live/attach-task",
                json={"taskDocumentRef": self.LEAF_BODY},
            )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["status"], "source-lineage-stale")
        self.assertEqual(response.json()["sourceLineage"]["state"], "blocked")
        self.assertIsNone(self.catalog.get("live").task_document_ref)  # type: ignore[union-attr]

    def test_post_open_409_when_seat_taken_by_other_running_session(self) -> None:
        with TestClient(self.app) as client:
            first = client.post(
                "/api/terminal/owner",
                json={"kind": "terminal", "taskDocumentRef": self.LEAF_BODY},
            )
            self.assertEqual(first.status_code, 200)
            # A second terminal claiming the same document+role seat is refused.
            second = client.post(
                "/api/terminal/intruder",
                json={"kind": "terminal", "taskDocumentRef": self.LEAF_BODY},
            )
        self.assertEqual(second.status_code, 409)
        body = second.json()
        self.assertEqual(body["status"], "seat-taken")
        self.assertEqual(body["taskDocumentRef"], self.LEAF_BODY)
        self.assertEqual(body["session"], "owner")
        self.assertIsNone(self.catalog.get("intruder"))  # no row created for the refused claim
        self.assertEqual([e["sid"] for e in self.host.ensured], ["owner"])

    def test_post_open_reclaims_own_task_seat_on_reopen(self) -> None:
        with TestClient(self.app) as client:
            client.post(
                "/api/terminal/term-1",
                json={"kind": "terminal", "taskDocumentRef": self.LEAF_BODY},
            )
            # Re-opening the same occupant on the same structural seat is idempotent.
            response = client.post(
                "/api/terminal/term-1",
                json={"kind": "terminal", "taskDocumentRef": self.LEAF_BODY},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["taskDocumentRef"], self.LEAF_BODY)

    def test_attach_task_binds_existing_session_when_free(self) -> None:
        self.catalog.upsert(_catalog_entry("live", cwd=self.tmp))
        with TestClient(self.app) as client:
            response = client.post(
                "/api/terminal/live/attach-task",
                json={"taskDocumentRef": self.LEAF_BODY},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["taskDocumentRef"], self.LEAF_BODY)
        entry = self.catalog.get("live")
        assert entry is not None
        self.assertEqual(entry.task_document_ref, self.LEAF_REF)

    def test_attach_task_rejects_unmatchable_ref_without_mutating(self) -> None:
        self.catalog.upsert(_catalog_entry("live", cwd=self.tmp))
        with TestClient(self.app) as client:
            response = client.post(
                "/api/terminal/live/attach-task",
                json={
                    "taskDocumentRef": {
                        "repository": "repo",
                        "path": "master/missing-leaf.json",
                    }
                },
            )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["status"], "task-binding-invalid")
        entry = self.catalog.get("live")
        assert entry is not None
        self.assertIsNone(entry.task_document_ref)

    def test_attach_task_409_when_taken_by_other_running_session(self) -> None:
        self.catalog.upsert(_catalog_entry("owner", cwd=self.tmp, tmux_name="ar-owner"))
        self.catalog.upsert(
            replace(
                _catalog_entry("seeker", cwd=self.tmp, tmux_name="ar-seeker"),
                task_document_ref=None,
            )
        )
        self.host.probe_names.update({"ar-owner", "ar-seeker"})
        with TestClient(self.app) as client:
            client.post(
                "/api/terminal/owner/attach-task",
                json={"taskDocumentRef": self.LEAF_BODY},
            )
            response = client.post(
                "/api/terminal/seeker/attach-task",
                json={"taskDocumentRef": self.LEAF_BODY},
            )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["status"], "seat-taken")
        seeker = self.catalog.get("seeker")
        assert seeker is not None
        self.assertIsNone(seeker.task_document_ref)

    def test_attach_task_404_for_unknown_session(self) -> None:
        with TestClient(self.app) as client:
            response = client.post(
                "/api/terminal/ghost/attach-task",
                json={"taskDocumentRef": self.LEAF_BODY},
            )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["status"], "unknown-session")

    def test_attach_task_404_for_terminated_session(self) -> None:
        self.catalog.upsert(_catalog_entry("dead", cwd=self.tmp, status="terminated"))
        with TestClient(self.app) as client:
            response = client.post(
                "/api/terminal/dead/attach-task", json={"taskDocumentRef": self.LEAF_BODY}
            )
        self.assertEqual(response.status_code, 404)

    def test_attach_task_404_for_landed_session(self) -> None:
        self.catalog.upsert(_catalog_entry("landed", cwd=self.tmp, status="landed"))
        with TestClient(self.app) as client:
            response = client.post(
                "/api/terminal/landed/attach-task", json={"taskDocumentRef": self.LEAF_BODY}
            )
        self.assertEqual(response.status_code, 404)

    def test_terminal_shares_a_document_with_chat_but_same_role_conflicts(self) -> None:
        # Uniqueness is per (task document, role): terminal and chat may coexist.
        with patch("shutil.which", _which("claude")), TestClient(self.app) as client:
            chat = client.post(
                "/api/terminal/chat-1",
                json={
                    "kind": "harness",
                    "harness": "claude",
                    "taskDocumentRef": self.LEAF_BODY,
                },
            )
            self.assertEqual(chat.status_code, 200)
            term = client.post(
                "/api/terminal/term-1",
                json={"kind": "terminal", "taskDocumentRef": self.LEAF_BODY},
            )
            self.assertEqual(term.status_code, 200)
            self.assertEqual(term.json()["taskDocumentRef"], self.LEAF_BODY)
            # A second chat is refused -- the chat slot is taken by chat-1.
            second_chat = client.post(
                "/api/terminal/chat-2",
                json={
                    "kind": "harness",
                    "harness": "claude",
                    "taskDocumentRef": self.LEAF_BODY,
                },
            )
            self.assertEqual(second_chat.status_code, 409)
            self.assertEqual(second_chat.json()["session"], "chat-1")
            # A second terminal is refused too -- the terminal slot is taken by term-1.
            second_term = client.post(
                "/api/terminal/term-2",
                json={"kind": "terminal", "taskDocumentRef": self.LEAF_BODY},
            )
            self.assertEqual(second_term.status_code, 409)
            self.assertEqual(second_term.json()["session"], "term-1")

    def test_attach_task_terminal_does_not_conflict_with_existing_chat(self) -> None:
        self.catalog.upsert(
            replace(
                _catalog_entry("term", cwd=self.tmp, tmux_name="ar-term"),
                task_document_ref=None,
            )
        )
        self.host.probe_names.add("ar-term")
        with patch("shutil.which", _which("claude")), TestClient(self.app) as client:
            client.post(
                "/api/terminal/chat-1",
                json={
                    "kind": "harness",
                    "harness": "claude",
                    "taskDocumentRef": self.LEAF_BODY,
                },
            )
            response = client.post(
                "/api/terminal/term/attach-task",
                json={"taskDocumentRef": self.LEAF_BODY},
            )
        self.assertEqual(response.status_code, 200)
        entry = self.catalog.get("term")
        assert entry is not None
        self.assertEqual(entry.task_document_ref, self.LEAF_REF)

    def test_get_harnesses_lists_supported_set_with_detection(self) -> None:
        with patch("shutil.which", _which("claude")), TestClient(self.app) as client:
            response = client.get("/api/harnesses")
        self.assertEqual(response.status_code, 200)
        harnesses = response.json()["harnesses"]
        self.assertEqual([h["id"] for h in harnesses], ["claude", "codex", "pi"])
        self.assertEqual(
            {h["id"]: h["detected"] for h in harnesses},
            {"claude": True, "codex": False, "pi": False},
        )
        self.assertTrue(all("control" not in harness for harness in harnesses))

    def test_post_open_harness_spawns_registry_argv_at_workspace_root(self) -> None:
        with patch("shutil.which", _which("claude")), TestClient(self.app) as client:
            response = client.post(
                "/api/terminal/h-1",
                json={"kind": "harness", "harness": "claude", "label": "Claude Code 1"},
            )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual((body["kind"], body["harness"]), ("harness", "claude"))
        self.assertEqual(body["label"], "Claude Code 1")
        self.assertEqual(len(self.host.ensured), 1)
        ensured = self.host.ensured[0]
        command = ensured["command"]
        assert isinstance(command, list)
        self.assertEqual(command[1:3], ["-m", "agents_remember.serving.harness_control_runner"])
        runner = parse_runner_config(command[3])
        self.assertEqual(runner.argv, ("claude",))  # server-resolved argv, never wire-supplied
        self.assertEqual(runner.harness_id, "claude")
        self.assertEqual(runner.identity.ar_session_id, "h-1")
        self.assertEqual(ensured["cwd"], self.tmp)  # workspace_root
        self.assertTrue(ensured["suspend_unsafe"])  # a bare-pane harness gets the Ctrl-Z strip
        entry = self.catalog.get("h-1")
        assert entry is not None
        self.assertEqual(entry.kind, "harness")
        self.assertEqual(entry.harness, "claude")

    def test_post_open_harness_carries_complete_model_effort_pair_once(self) -> None:
        with patch("shutil.which", _which("claude")), TestClient(self.app) as client:
            response = client.post(
                "/api/terminal/h-selected",
                json={
                    "kind": "harness",
                    "harness": "claude",
                    "model": "claude-opus-4-8",
                    "effort": "high",
                },
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["resolvedModel"], "claude-opus-4-8")
        self.assertEqual(response.json()["resolvedEffort"], "high")
        self.assertEqual(len(self.host.ensured), 1)
        command = self.host.ensured[0]["command"]
        assert isinstance(command, list)
        runner = parse_runner_config(command[3])
        selection = runner.resolved_launch
        assert selection is not None
        self.assertEqual(selection.model_key, "claude-opus-4-8")
        self.assertEqual(selection.effort, "high")

    def test_post_open_reopen_preserves_live_truth_conflicts_then_replaces_dead(self) -> None:
        first_body = {
            "kind": "harness",
            "harness": "claude",
            "model": "model-a",
            "effort": "high",
        }
        changed_body = {**first_body, "model": "model-b", "effort": "max"}
        with patch("shutil.which", _which("claude")), TestClient(self.app) as client:
            first = client.post("/api/terminal/reopen-selected", json=first_body)
            same = client.post("/api/terminal/reopen-selected", json=first_body)
            conflict = client.post("/api/terminal/reopen-selected", json=changed_body)

            first_entry = self.catalog.get("reopen-selected")
            assert first_entry is not None and first_entry.control_endpoint is not None
            first_endpoint = str(first_entry.control_endpoint)
            self.host.probe_names.discard(first_entry.tmux_name)
            replacement = client.post("/api/terminal/reopen-selected", json=changed_body)

        self.assertEqual((first.status_code, same.status_code), (200, 200))
        self.assertEqual(len(self.host.ensured), 2)
        self.assertEqual(same.json()["resolvedModel"], "model-a")
        self.assertEqual(same.json()["controlEndpoint"], first_endpoint)
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.json()["status"], "launch-selection-conflict")
        self.assertEqual(conflict.json()["resolvedModel"], "model-a")
        self.assertEqual(conflict.json()["resolvedEffort"], "high")
        self.assertEqual(conflict.json()["controlEndpoint"], first_endpoint)
        self.assertEqual(replacement.status_code, 200)
        self.assertEqual(replacement.json()["resolvedModel"], "model-b")
        self.assertEqual(replacement.json()["resolvedEffort"], "max")
        self.assertNotEqual(replacement.json()["controlEndpoint"], first_endpoint)
        second_command = self.host.ensured[1]["command"]
        assert isinstance(second_command, list)
        second_selection = parse_runner_config(second_command[3]).resolved_launch
        assert second_selection is not None
        self.assertEqual((second_selection.model_key, second_selection.effort), ("model-b", "max"))

    def test_post_open_rejects_partial_or_non_harness_selection_before_spawn(self) -> None:
        with patch("shutil.which", _which("claude")), TestClient(self.app) as client:
            partial = client.post(
                "/api/terminal/h-partial",
                json={"kind": "harness", "harness": "claude", "model": "opus"},
            )
            terminal = client.post(
                "/api/terminal/plain-selected",
                json={"kind": "terminal", "model": "opus", "effort": "high"},
            )
            non_native = client.post(
                "/api/terminal/custom-selected",
                json={
                    "kind": "harness",
                    "harness": "gemini",
                    "model": "pro",
                    "effort": "high",
                },
            )
        self.assertEqual(
            (partial.status_code, terminal.status_code, non_native.status_code),
            (400, 400, 400),
        )
        self.assertEqual(partial.json()["status"], "launch-selection-invalid")
        self.assertEqual(self.host.ensured, [])

    def test_post_open_harness_rejects_uninstalled(self) -> None:
        with patch("shutil.which", _which()), TestClient(self.app) as client:
            response = client.post(
                "/api/terminal/h-2", json={"kind": "harness", "harness": "claude"}
            )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.host.ensured, [])

    def test_post_open_harness_rejects_unknown_id(self) -> None:
        with patch("shutil.which", _which("gemini")), TestClient(self.app) as client:
            response = client.post(
                "/api/terminal/h-3", json={"kind": "harness", "harness": "gemini"}
            )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.host.ensured, [])
