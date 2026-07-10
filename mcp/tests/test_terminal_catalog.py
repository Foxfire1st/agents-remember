"""Tests for the dashboard terminal-session catalog."""

from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
from dataclasses import replace
from datetime import datetime
from pathlib import Path

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.serving.terminal_catalog import (
    TerminalCatalog,
    TerminalCatalogEntry,
    TerminalSessionKind,
    terminal_catalog_path,
)


def _entry(
    session_id: str,
    *,
    created_at: str = "2026-06-26T00:00:00Z",
    leaf_key: str | None = None,
    kind: TerminalSessionKind = "terminal",
) -> TerminalCatalogEntry:
    return TerminalCatalogEntry(
        id=session_id,
        label=f"Terminal {session_id}",
        kind=kind,
        harness="claude" if kind == "harness" else None,
        lifecycle_id=None,
        cwd=Path("/workspace"),
        tmux_name=f"ar-{session_id}",
        command=("bash",),
        created_at=created_at,
        last_attached_at=created_at,
        status="running",
        leaf_key=leaf_key,
    )


class TerminalCatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._dir.name)
        self.catalog = TerminalCatalog(self.tmp / "terminal-sessions.json")

    def tearDown(self) -> None:
        self._dir.cleanup()

    def test_terminal_catalog_path_is_under_dashboard_logs(self) -> None:
        self.assertEqual(
            terminal_catalog_path(self.tmp),
            self.tmp / "logs" / "dashboard" / "terminal-sessions.json",
        )

    def test_upsert_persists_json_rows_sorted_by_creation(self) -> None:
        self.catalog.upsert(_entry("b", created_at="2026-06-26T00:00:02Z"))
        self.catalog.upsert(_entry("a", created_at="2026-06-26T00:00:01Z"))

        self.assertEqual([entry.id for entry in self.catalog.list()], ["a", "b"])
        raw = json.loads(self.catalog.path.read_text(encoding="utf-8"))
        self.assertEqual(raw["schema"], "ar-dashboard-terminal-sessions/v1")
        self.assertEqual([row["id"] for row in raw["sessions"]], ["a", "b"])

    def test_status_transitions_keep_exited_visible_and_filter_terminated(self) -> None:
        self.catalog.upsert(_entry("a"))
        self.catalog.upsert(_entry("b"))

        self.catalog.mark_exited("a")
        self.catalog.mark_terminated("b", "2026-06-26T00:01:00Z")

        self.assertEqual([entry.id for entry in self.catalog.list()], ["a"])
        self.assertEqual([entry.id for entry in self.catalog.list(include_terminated=True)], ["a", "b"])
        exited = self.catalog.get("a")
        terminated = self.catalog.get("b")
        assert exited is not None
        assert terminated is not None
        self.assertEqual(exited.status, "exited")
        self.assertEqual(terminated.terminated_at, "2026-06-26T00:01:00Z")

    def test_mark_attached_restores_running_status(self) -> None:
        self.catalog.upsert(_entry("a"))
        self.catalog.mark_exited("a")

        updated = self.catalog.mark_attached("a", "2026-06-26T00:02:00Z")

        assert updated is not None
        self.assertEqual(updated.status, "running")
        self.assertEqual(updated.last_attached_at, "2026-06-26T00:02:00Z")

    def test_mark_exited_does_not_downgrade_terminated_session(self) -> None:
        self.catalog.upsert(_entry("a"))
        self.catalog.mark_terminated("a", "2026-06-26T00:03:00Z")

        updated = self.catalog.mark_exited("a")

        assert updated is not None
        self.assertEqual(updated.status, "terminated")
        self.assertEqual(updated.terminated_at, "2026-06-26T00:03:00Z")
        self.assertEqual(self.catalog.list(), [])

    def test_mark_landed_keeps_row_visible_and_non_active(self) -> None:
        leaf = "repo/master/leaf-1"
        self.catalog.upsert(_entry("a", leaf_key=leaf, kind="harness"))

        updated = self.catalog.mark_landed(
            "a",
            at="2026-07-09T00:00:00+00:00",
            reason="leaf integrated",
            edge="leaf-integration",
        )

        assert updated is not None
        self.assertEqual(updated.status, "landed")
        self.assertEqual(updated.landed_reason, "leaf integrated")
        self.assertEqual([entry.id for entry in self.catalog.list()], ["a"])
        self.assertIsNone(self.catalog.active_for_leaf(leaf))

    def test_landed_state_round_trips_and_is_not_reanimated(self) -> None:
        self.catalog.upsert(_entry("a"))
        self.catalog.mark_landed(
            "a",
            at="2026-07-09T00:00:00+00:00",
            reason="done",
            edge="leaf-integration",
        )

        raw = json.loads(self.catalog.path.read_text(encoding="utf-8"))
        row = raw["sessions"][0]
        self.assertEqual(row["status"], "landed")
        self.assertEqual(row["landedAt"], "2026-07-09T00:00:00+00:00")
        self.assertEqual(row["landedReason"], "done")
        self.assertEqual(row["landedEdge"], "leaf-integration")

        attached = self.catalog.mark_attached("a", "2026-07-09T00:01:00+00:00")
        assert attached is not None
        self.assertEqual(attached.status, "landed")
        liveness = self.catalog.record_liveness_probe(
            "a", alive=True, checked_at=datetime.fromisoformat("2026-07-09T00:02:00+00:00")
        )
        assert liveness is not None
        self.assertEqual(liveness.status, "landed")
        exited = self.catalog.mark_exited("a")
        assert exited is not None
        self.assertEqual(exited.status, "landed")

    def test_leaf_key_round_trips_through_json(self) -> None:
        leaf = "agents-remember/260628_operations-integration/260628-L5"
        self.catalog.upsert(_entry("a", leaf_key=leaf))

        raw = json.loads(self.catalog.path.read_text(encoding="utf-8"))
        self.assertEqual(raw["sessions"][0]["leafKey"], leaf)

        entry = self.catalog.get("a")
        assert entry is not None
        self.assertEqual(entry.leaf_key, leaf)

    def test_to_json_omits_leaf_key_when_unset(self) -> None:
        self.catalog.upsert(_entry("a"))
        self.assertNotIn("leafKey", self.catalog.get("a").to_json())  # type: ignore[union-attr]

    def test_spawn_role_round_trips_and_is_omitted_when_unset(self) -> None:
        # L14: the AR_SPAWN_ROLE recorded at spawn is a durable column (the Chats command-tree
        # grouping key) — written only when set, so legacy/hand-opened rows read back as None.
        self.catalog.upsert(replace(_entry("a"), spawn_role="manager"))
        self.catalog.upsert(_entry("b"))

        raw = json.loads(self.catalog.path.read_text(encoding="utf-8"))
        by_id = {row["id"]: row for row in raw["sessions"]}
        self.assertEqual(by_id["a"]["spawnRole"], "manager")
        self.assertNotIn("spawnRole", by_id["b"])

        entry = self.catalog.get("a")
        assert entry is not None
        self.assertEqual(entry.spawn_role, "manager")
        unset = self.catalog.get("b")
        assert unset is not None
        self.assertIsNone(unset.spawn_role)

    def test_dispatch_binding_fields_round_trip(self) -> None:
        leaf = "repo/master/leaf-1"
        self.catalog.upsert(
            replace(
                _entry("a", kind="harness"),
                replacement_for_leaf=leaf,
                session_log_entry_id="brief-1",
                session_log_path=Path("/tmp/session.jsonl"),
            )
        )

        entry = self.catalog.get("a")
        assert entry is not None
        self.assertEqual(entry.replacement_for_leaf, leaf)
        self.assertEqual(entry.session_log_entry_id, "brief-1")
        self.assertEqual(entry.session_log_path, Path("/tmp/session.jsonl"))
        self.assertEqual(entry.to_json()["replacementForLeaf"], leaf)
        self.assertEqual(entry.to_json()["sessionLogEntryId"], "brief-1")

    def test_bind_session_log_preserves_newer_liveness_state(self) -> None:
        self.catalog.upsert(_entry("a", kind="harness"))
        stale_snapshot = self.catalog.get("a")
        assert stale_snapshot is not None
        exited = self.catalog.record_liveness_probe(
            "a",
            alive=False,
            evidence="pane-gone",
            checked_at=datetime.fromisoformat("2026-07-10T10:00:00+00:00"),
        )
        assert exited is not None
        self.assertEqual(exited.status, "exited")

        bound = self.catalog.bind_session_log(
            stale_snapshot.id,
            entry_id="brief-1",
            path=Path("/tmp/session.jsonl"),
        )

        assert bound is not None
        self.assertEqual(bound.status, "exited")
        self.assertEqual(bound.liveness_failures, 1)
        self.assertEqual(bound.exit_evidence, "pane-gone")
        self.assertEqual(bound.session_log_entry_id, "brief-1")
        self.assertEqual(bound.session_log_path, Path("/tmp/session.jsonl"))

    def test_legacy_row_without_leaf_key_reads_as_none(self) -> None:
        # A v1 row written before L5 has no leafKey; it must read back as None (migration-safe).
        self.catalog.path.parent.mkdir(parents=True, exist_ok=True)
        self.catalog.path.write_text(
            json.dumps(
                {
                    "schema": "ar-dashboard-terminal-sessions/v1",
                    "sessions": [
                        {
                            "id": "legacy",
                            "label": "Terminal legacy",
                            "kind": "terminal",
                            "cwd": "/workspace",
                            "tmuxName": "ar-legacy",
                            "command": ["bash"],
                            "createdAt": "2026-06-26T00:00:00Z",
                            "lastAttachedAt": "2026-06-26T00:00:00Z",
                            "status": "running",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        entry = self.catalog.get("legacy")
        assert entry is not None
        self.assertIsNone(entry.leaf_key)

    def test_active_for_leaf_returns_running_owner(self) -> None:
        leaf = "repo/master/leaf-1"
        self.catalog.upsert(_entry("a", leaf_key=leaf, kind="harness"))
        owner = self.catalog.active_for_leaf(leaf)  # defaults to the chat role
        assert owner is not None
        self.assertEqual(owner.id, "a")
        self.assertIsNone(self.catalog.active_for_leaf("repo/master/other"))

    def test_active_for_leaf_is_scoped_by_role(self) -> None:
        # A chat and a terminal can both own the same leaf; the probe resolves each independently.
        leaf = "repo/master/leaf-1"
        self.catalog.upsert(_entry("chat", leaf_key=leaf, kind="harness"))
        self.catalog.upsert(_entry("term", leaf_key=leaf, kind="terminal"))
        chat = self.catalog.active_for_leaf(leaf, role="chat")
        term = self.catalog.active_for_leaf(leaf, role="terminal")
        assert chat is not None
        assert term is not None
        self.assertEqual(chat.id, "chat")
        self.assertEqual(term.id, "term")

    def test_active_for_leaf_ignores_exited_and_terminated(self) -> None:
        leaf = "repo/master/leaf-1"
        self.catalog.upsert(_entry("exited", leaf_key=leaf, kind="harness"))
        self.catalog.mark_exited("exited")
        # An exited chat frees its leaf.
        self.assertIsNone(self.catalog.active_for_leaf(leaf))

        self.catalog.upsert(_entry("terminated", leaf_key=leaf, kind="harness"))
        self.catalog.mark_terminated("terminated", "2026-06-26T00:05:00Z")
        # A terminated chat frees its leaf too.
        self.assertIsNone(self.catalog.active_for_leaf(leaf))

    def test_with_leaf_key_copies_binding(self) -> None:
        entry = _entry("a")
        bound = entry.with_leaf_key("repo/master/leaf-1")
        self.assertEqual(bound.leaf_key, "repo/master/leaf-1")
        self.assertIsNone(bound.with_leaf_key(None).leaf_key)
        self.assertIsNone(entry.leaf_key)  # the original is untouched (frozen copy)

    def test_read_recovers_from_torn_extra_data_write(self) -> None:
        # The exact corruption two writers (or one process's two request threads) produced on the old
        # fixed-temp write: a valid object followed by a partial duplicate ("Extra data"). The reader must
        # recover the first complete object instead of raising and 500-ing every catalog request.
        self.catalog.upsert(_entry("a"))
        good = self.catalog.path.read_text(encoding="utf-8")
        self.catalog.path.write_text(good + '\nxName": "ar-torn"\n}\n', encoding="utf-8")

        self.assertEqual([entry.id for entry in self.catalog.list()], ["a"])

    def test_read_degrades_to_empty_on_unparseable_file(self) -> None:
        # Total garbage is treated as "no sessions" (the next write overwrites it clean) — never a 500.
        self.catalog.path.parent.mkdir(parents=True, exist_ok=True)
        self.catalog.path.write_text("}{ not json at all", encoding="utf-8")
        self.assertEqual(self.catalog.list(), [])

    def test_write_leaves_no_fixed_name_temp_sibling(self) -> None:
        # The old shared `.terminal-sessions.json.tmp` is what concurrent writers tore; a write must not
        # create that fixed sibling (the per-write temp is unique and removed by os.replace).
        self.catalog.upsert(_entry("a"))
        self.assertFalse((self.catalog.path.with_name(f".{self.catalog.path.name}.tmp")).exists())
        leftover = list(self.catalog.path.parent.glob(f".{self.catalog.path.name}.*.tmp"))
        self.assertEqual(leftover, [])

    def test_concurrent_upserts_do_not_lose_or_corrupt_rows(self) -> None:
        # Reproduces the bug class directly: many threads upserting distinct rows at once. With the
        # read-modify-write serialized under the lock + a unique temp per write, every row survives and the
        # file stays valid JSON. (Pre-fix: lost updates and a torn, unreadable file.)
        ids = [f"s{i:03d}" for i in range(40)]
        barrier = threading.Barrier(len(ids))

        def _add(session_id: str) -> None:
            barrier.wait()  # maximize overlap on the read-modify-write
            self.catalog.upsert(_entry(session_id, created_at=f"2026-06-26T00:00:{session_id[-2:]}Z"))

        threads = [threading.Thread(target=_add, args=(session_id,)) for session_id in ids]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        stored = {entry.id for entry in self.catalog.list()}
        self.assertEqual(stored, set(ids))  # no lost updates
        json.loads(self.catalog.path.read_text(encoding="utf-8"))  # still valid JSON (no torn write)


if __name__ == "__main__":
    unittest.main()
