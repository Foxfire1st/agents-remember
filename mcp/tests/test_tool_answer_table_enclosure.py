"""The enclosure and reader-change cases of the tool-answer log.

Split text-identical out of test_tool_answer_table.py under MIK-R196 rule 10."""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest import mock

from agents_remember.application.tool_answer_log import (
    ENCLOSURE_LOGS_DIRNAME,
    TOOL_LOG_BASENAME_PREFIX,
    detail_dir_for,
    ensure_enclosure_logs_dir,
    log_file_for,
)
from agents_remember.worktrees import outside_scratch
from agents_remember.worktrees.modules import abandon as abandon_module
from agents_remember.worktrees.modules import cleanup as cleanup_module
from agents_remember.worktrees.modules import kept_tool_calls as kept_module
from agents_remember.worktrees.modules.closeout import closeout_changed_paths
from agents_remember.worktrees.modules.git import worktree_dirty
from agents_remember.worktrees.modules.kept_tool_calls import KEEP_FAILED_REASON, keep_tool_calls
from agents_remember.worktrees.modules.terminal_validation import (
    TerminalResult,
    terminal_result_blockers,
)
from agents_remember.worktrees.worktree_contract import WorktreeContract, load_contract
from tool_entry_point_world import LEAF_ID, MASTER, EntryPointWorld


class ToolAnswerEnclosureTests(unittest.TestCase):
    """The enclosure half of Expected Evidence 4, on a real enclosure.

    One hermetic EntryPointWorld (scratch coordination root, real code and
    memory repositories, one real leaf enclosure opened through the real
    worktree_start) proves: ``logs/`` exists after the enclosure is made,
    both worktrees show a clean status with files in it, the closeout
    changed-paths source lists no logs, and the real cleanup path removes
    ``logs/`` with the enclosure.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.world = EntryPointWorld()
        cls.world.build()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.world.close()

    def test_logs_made_with_the_enclosure(self) -> None:
        contract = load_contract(self.world.contract)
        logs = Path(contract.worktree_group) / ENCLOSURE_LOGS_DIRNAME
        self.assertTrue(logs.is_dir(), "no logs/ beside the new enclosure's worktrees")
        self.assertEqual(
            Path(contract.worktree_group).parent,
            self.world.coord / "worktrees" / "repo-a",
        )

    def test_both_worktrees_clean_with_files_in_logs(self) -> None:
        contract = load_contract(self.world.contract)
        logs = Path(contract.worktree_group) / ENCLOSURE_LOGS_DIRNAME
        logs.mkdir(parents=True, exist_ok=True)
        (logs / "ar-tools-2026-10-08.jsonl").write_text("{}\n", encoding="utf-8")
        self.assertIsNotNone(contract.code_worktree)
        self.assertIsNotNone(contract.memory_worktree)
        assert contract.code_worktree is not None
        assert contract.memory_worktree is not None
        self.assertFalse(
            worktree_dirty(Path(contract.code_worktree)),
            "enclosure logs/ dirties the code worktree",
        )
        self.assertFalse(
            worktree_dirty(Path(contract.memory_worktree)),
            "enclosure logs/ dirties the memory worktree",
        )
        self.assertFalse((Path(contract.code_worktree) / "logs").exists())
        self.assertFalse((Path(contract.memory_worktree) / "logs").exists())

    def test_closeout_changed_paths_list_no_logs(self) -> None:
        contract = load_contract(self.world.contract)
        logs = Path(contract.worktree_group) / ENCLOSURE_LOGS_DIRNAME
        logs.mkdir(parents=True, exist_ok=True)
        (logs / "ar-tools-2026-10-08.jsonl").write_text("{}\n", encoding="utf-8")
        worklist = closeout_changed_paths(contract)
        logged = [
            path
            for tier in ("all", "working", "committed")
            for path in worklist[tier]
            if "logs" in Path(path).parts
        ]
        self.assertEqual(logged, [])

    def test_cleanup_removes_logs_with_the_enclosure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            group = Path(tmp) / "group"
            logs = group / ENCLOSURE_LOGS_DIRNAME
            logs.mkdir(parents=True)
            (logs / "ar-tools-2026-10-08.jsonl").write_text("{}\n", encoding="utf-8")
            removed: list[tuple[str, bool]] = []

            class FakeLifecycle:
                def remove_tree(self, path: Path, *, dry_run: bool) -> dict[str, object]:
                    removed.append((path.as_posix(), dry_run))
                    if not dry_run and path.exists():
                        shutil.rmtree(path, ignore_errors=True)
                    return {"path": path.as_posix(), "removed": True}

            services = mock.Mock()
            services.provider_lifecycle = FakeLifecycle()
            # The fake contract names no leaf document; keeping its lines is
            # proven on a real enclosure in KeptToolCallsTests.
            with (
                mock.patch.object(cleanup_module, "worktree_services", return_value=services),
                mock.patch.object(kept_module, "kept_tool_calls_entry", return_value={}),
            ):
                directories = cleanup_module._removed_directories(
                    SimpleNamespace(worktree_group=group, kind="leaf", task_root=Path(tmp)),
                    dry_run=False,
                )
            self.assertIn(
                (logs.as_posix(), False),
                removed,
                "cleanup never offered the enclosure logs/ to removal",
            )
            self.assertIn("logs", directories)
            self.assertFalse(logs.exists(), "cleanup left the enclosure logs/ behind")


OTHER_TASK = f"{MASTER}/2_other.json"


def _line(task: str | None, time: str, **fields: Any) -> dict[str, Any]:
    return {
        "time": time,
        "tool": "knowledge_read",
        "operation": "knowledge_read",
        "caller": {"agentId": "a1", "role": "worker", "task": task},
        "ok": True,
        "code": None,
        "durationMs": 1.0,
        "answerTokens": 10,
        "resultBytes": 20,
        "resultDigest": "d" * 64,
        "guardCut": False,
        "detail": "191849951726-knowledge_read-3a2d37fd.json",
        "dropped": 0,
        "arguments": {},
        **fields,
    }


class _RecordingLifecycle:
    """Records each removal; deletes only the log folder, so the shared world stays."""

    def __init__(self, logs: Path, on_remove: Any = None) -> None:
        self.logs = logs
        self.on_remove = on_remove
        self.removed: list[Path] = []

    def remove_tree(self, path: Path, *, dry_run: bool) -> dict[str, object]:
        if self.on_remove is not None:
            self.on_remove(path)
        self.removed.append(path)
        if not dry_run and path == self.logs and path.exists():
            shutil.rmtree(path)
        return {"path": path.as_posix(), "removed": True}


class KeptToolCallsTests(unittest.TestCase):
    """A leaf's tool-call lines are kept in its master's notes.

    On a real master, leaf document and enclosure: cleanup keeps exactly the
    leaf's own lines without ``detail`` before it removes ``logs/``; a second
    keep adds no line twice; a forced abandon keeps the lines before it
    removes the whole enclosure; a kept line names the leaf id in place of a
    path inside the enclosure or its outside scratch.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.world = EntryPointWorld()
        cls.world.build()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.world.close()

    def setUp(self) -> None:
        self.contract: WorktreeContract = load_contract(self.world.contract)
        self.own_task = self.world.leaf_ref["path"]
        self.logs = Path(self.contract.worktree_group) / ENCLOSURE_LOGS_DIRNAME
        self.kept = (
            Path(self.contract.task_root) / "notes" / "reports" / f"{LEAF_ID}-tool-calls.jsonl"
        )
        shutil.rmtree(self.logs, ignore_errors=True)
        self.kept.unlink(missing_ok=True)
        self.logs.mkdir(parents=True)

    def _write_day(self, day: str, rows: list[dict[str, Any] | str]) -> None:
        text = "".join(f"{row if isinstance(row, str) else json.dumps(row)}\n" for row in rows)
        (self.logs / f"ar-tools-{day}.jsonl").write_text(text, encoding="utf-8")

    def _kept_lines(self) -> list[dict[str, Any]]:
        return [json.loads(raw) for raw in self.kept.read_text(encoding="utf-8").split("\n") if raw]

    def _services(self, lifecycle: _RecordingLifecycle) -> mock.Mock:
        services = mock.Mock()
        services.provider_lifecycle = lifecycle
        return services

    def test_cleanup_keeps_only_this_leafs_lines_before_removing_logs(self) -> None:
        refused = _line(self.own_task, "2026-11-19T10:00:00+00:00", ok=False, code="file-unknown")
        self._write_day("2026-11-19", [refused, _line(OTHER_TASK, "2026-11-19T10:01:00+00:00")])
        own = _line(self.own_task, "2026-11-18T09:00:00+00:00")
        self._write_day("2026-11-18", [own, _line(None, "2026-11-18T09:01:00+00:00"), "{not json"])
        lifecycle = _RecordingLifecycle(self.logs)
        with (
            mock.patch.object(
                cleanup_module, "worktree_services", return_value=self._services(lifecycle)
            ),
            mock.patch.object(outside_scratch, "removal_entry_for", return_value={}),
        ):
            directories = cleanup_module._removed_directories(self.contract, dry_run=False)
        self.assertFalse(self.logs.exists(), "cleanup left the enclosure logs/ behind")
        without_detail = [
            {key: value for key, value in row.items() if key != "detail"} for row in (own, refused)
        ]
        self.assertEqual(self._kept_lines(), without_detail)
        self.assertEqual(self._kept_lines()[1]["ok"], False)
        self.assertEqual(self._kept_lines()[1]["code"], "file-unknown")
        self.assertEqual(
            directories["logs"]["keptToolCalls"],
            {"path": self.kept.as_posix(), "kept": 2, "skipped": 3},
        )

    def test_second_keep_adds_no_line_twice(self) -> None:
        first = _line(self.own_task, "2026-11-19T10:00:00+00:00")
        self._write_day("2026-11-19", [first])
        keep_tool_calls(self.contract)
        later = _line(self.own_task, "2026-11-19T11:00:00+00:00", tool="task_doc")
        self._write_day("2026-11-19", [first, later])
        result = keep_tool_calls(self.contract)
        self.assertEqual(result.kept, 2)
        self.assertEqual(
            [row["time"] for row in self._kept_lines()], [first["time"], later["time"]]
        )

    def test_forced_abandon_keeps_lines_before_removing_the_enclosure(self) -> None:
        self._write_day("2026-11-19", [_line(self.own_task, "2026-11-19T10:00:00+00:00")])
        group = Path(self.contract.worktree_group)
        kept_at_group_removal: list[bool] = []

        def on_remove(path: Path) -> None:
            if path == group:
                kept_at_group_removal.append(self.kept.is_file())

        lifecycle = _RecordingLifecycle(self.logs, on_remove)
        with (
            mock.patch.object(
                abandon_module, "worktree_services", return_value=self._services(lifecycle)
            ),
            mock.patch.object(outside_scratch, "removal_entry_for", return_value={}),
        ):
            directories = abandon_module._abandon_directories(
                self.contract, dry_run=False, force=True
            )
        self.assertEqual(kept_at_group_removal, [True])
        self.assertEqual(
            directories["worktree_group"]["keptToolCalls"],
            {"path": self.kept.as_posix(), "kept": 1, "skipped": 0},
        )
        self.assertEqual(len(self._kept_lines()), 1)

    def _blockers(self, directories: dict[str, dict[str, object]]) -> list[dict[str, object]]:
        return terminal_result_blockers(
            TerminalResult(providers={}, worktrees={}, branches={}, directories=directories)
        )

    def test_a_failed_keep_leaves_the_log_and_blocks_the_cleanup_until_a_retry(self) -> None:
        own = _line(self.own_task, "2026-11-19T10:00:00+00:00")
        self._write_day("2026-11-19", [own])
        lifecycle = _RecordingLifecycle(self.logs)
        patches = (
            mock.patch.object(
                cleanup_module, "worktree_services", return_value=self._services(lifecycle)
            ),
            mock.patch.object(outside_scratch, "removal_entry_for", return_value={}),
        )
        with (
            patches[0],
            patches[1],
            mock.patch.object(
                kept_module, "atomic_write_text", side_effect=OSError(28, "No space left")
            ),
        ):
            directories = cleanup_module._removed_directories(self.contract, dry_run=False)
        self.assertNotIn(self.logs, lifecycle.removed, "the log was offered to removal")
        self.assertTrue((self.logs / "ar-tools-2026-11-19.jsonl").is_file())
        self.assertFalse(self.kept.exists())
        entry = directories["logs"]
        self.assertEqual((entry["removed"], entry["reason"]), (False, KEEP_FAILED_REASON))
        self.assertIn("No space left", str(cast(dict, entry["keptToolCalls"])["error"]))
        self.assertIn(
            {"directory": "logs", "reason": KEEP_FAILED_REASON},
            [
                {key: value for key, value in one.items() if key in {"directory", "reason"}}
                for one in self._blockers(directories)
            ],
        )

        with patches[0], patches[1]:
            retried = cleanup_module._removed_directories(self.contract, dry_run=False)
        self.assertFalse(self.logs.exists())
        self.assertEqual(len(self._kept_lines()), 1)
        self.assertEqual(retried["logs"]["removed"], True)
        self.assertNotIn("logs", [one.get("directory") for one in self._blockers(retried)])

    def test_a_failed_keep_leaves_a_forced_abandon_its_enclosure(self) -> None:
        self._write_day("2026-11-19", [_line(self.own_task, "2026-11-19T10:00:00+00:00")])
        group = Path(self.contract.worktree_group)
        lifecycle = _RecordingLifecycle(self.logs)
        with (
            mock.patch.object(
                abandon_module, "worktree_services", return_value=self._services(lifecycle)
            ),
            mock.patch.object(outside_scratch, "removal_entry_for", return_value={}),
            mock.patch.object(kept_module, "atomic_write_text", side_effect=OSError("disk")),
        ):
            directories = abandon_module._abandon_directories(
                self.contract, dry_run=False, force=True
            )
        self.assertNotIn(group, lifecycle.removed)
        self.assertTrue((self.logs / "ar-tools-2026-11-19.jsonl").is_file())
        entry = directories["worktree_group"]
        self.assertEqual((entry["removed"], entry["reason"]), (False, KEEP_FAILED_REASON))
        self.assertIn(
            "worktree_group", [one.get("directory") for one in self._blockers(directories)]
        )

    def test_a_line_whose_text_holds_a_unicode_line_separator_is_kept_whole(self) -> None:
        text = "first part\u2028second part\u2029third"
        own = _line(self.own_task, "2026-11-19T10:00:00+00:00", arguments={"text": text})
        day = self.logs / "ar-tools-2026-11-19.jsonl"
        day.write_text(json.dumps(own, ensure_ascii=False) + "\n", encoding="utf-8")
        self.assertIn("\u2028", day.read_text(encoding="utf-8"))
        result = keep_tool_calls(self.contract)
        self.assertEqual((result.kept, result.skipped), (1, 0))
        kept = [json.loads(raw) for raw in self.kept.read_text(encoding="utf-8").split("\n") if raw]
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["arguments"], {"text": text})
        self.assertEqual((kept[0]["ok"], kept[0]["resultDigest"]), (own["ok"], own["resultDigest"]))
        self.assertNotIn("detail", kept[0])

    def test_kept_lines_name_the_leaf_for_paths_cleanup_removes(self) -> None:
        group = Path(self.contract.worktree_group)
        scratch_root = group.parent / "outside-scratch-ar-0123456789ab"
        contract_path = Path(self.contract.contract_path).as_posix()
        arguments = {
            "memoryRoot": (group / "memory-leaf").as_posix(),
            "repositoryRoot": (group / "leaf").as_posix(),
            "handoffPath": (group / "scratch" / "curator" / "u1" / "m2.json").as_posix(),
            "contractPath": contract_path,
            "files": [(scratch_root / "probe.txt").as_posix(), "/elsewhere/kept.md"],
            "note": f"see {group.as_posix()}/scratch/a.md, then reply",
            "sibling": f"{group.as_posix()}-other/file.md",
        }
        self._write_day(
            "2026-11-19", [_line(self.own_task, "2026-11-19T10:00:00+00:00", arguments=arguments)]
        )
        recorded = SimpleNamespace(root=scratch_root)
        with mock.patch.object(outside_scratch, "recorded_root", return_value=recorded):
            keep_tool_calls(self.contract)
        self.assertEqual(
            self._kept_lines()[0]["arguments"],
            {
                "memoryRoot": LEAF_ID,
                "repositoryRoot": LEAF_ID,
                "handoffPath": LEAF_ID,
                "contractPath": contract_path,
                "files": [LEAF_ID, "/elsewhere/kept.md"],
                "note": f"see {LEAF_ID}, then reply",
                "sibling": f"{group.as_posix()}-other/file.md",
            },
        )
        self.assertFalse(
            Path(contract_path).is_relative_to(group), "the contract lies outside the enclosure"
        )


class ToolAnswerReaderChangeTests(unittest.TestCase):
    def test_log_file_and_detail_helpers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            log_file = log_file_for(root, "2026-10-08")
            self.assertEqual(log_file.name, f"{TOOL_LOG_BASENAME_PREFIX}2026-10-08.jsonl")
            self.assertEqual(
                detail_dir_for(root, "2026-10-08").name,
                f"{TOOL_LOG_BASENAME_PREFIX}2026-10-08",
            )

    def test_enclosure_logs_dir_made_with_the_enclosure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            group = Path(tmp) / "worktree-group"
            made = ensure_enclosure_logs_dir(group)
            self.assertIsNotNone(made)
            assert made is not None
            self.assertEqual(made.name, ENCLOSURE_LOGS_DIRNAME)
            code_tree = group / "code-worktree"
            code_tree.mkdir()
            self.assertFalse((code_tree / ENCLOSURE_LOGS_DIRNAME).exists())
