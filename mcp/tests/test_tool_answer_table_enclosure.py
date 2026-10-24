"""The enclosure and reader-change cases of the tool-answer log.

Split text-identical out of test_tool_answer_table.py under MIK-R196 rule 10."""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from agents_remember.application.tool_answer_log import (
    ENCLOSURE_LOGS_DIRNAME,
    TOOL_LOG_BASENAME_PREFIX,
    detail_dir_for,
    ensure_enclosure_logs_dir,
    log_file_for,
)
from agents_remember.worktrees.modules import cleanup as cleanup_module
from agents_remember.worktrees.modules.closeout import closeout_changed_paths
from agents_remember.worktrees.modules.git import worktree_dirty
from agents_remember.worktrees.worktree_contract import load_contract
from tool_entry_point_world import EntryPointWorld


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
            with mock.patch.object(cleanup_module, "worktree_services", return_value=services):
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
