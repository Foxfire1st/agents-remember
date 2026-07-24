from __future__ import annotations

import io
import os
import subprocess
import sys
import tempfile
import unittest
from collections.abc import Mapping
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.worktrees import git_worktree_manager as worktree_manager
from agents_remember.worktrees.modules import closeout as closeout_module
from agents_remember.worktrees.modules import code_quality_gate
from agents_remember.worktrees.modules.git import commit_if_dirty
from agents_remember.worktrees.worktree_contract import load_contract
from test_worktree_support import closeout_args, dirty_open_external_contract_fixture, git


class CodeQualityGateTests(unittest.TestCase):
    def test_preview_requires_strict_wrapper_before_agents_remember_code_commit(self) -> None:
        preview = code_quality_gate.code_quality_gate_preview(
            "agents-remember", code_would_commit=True
        )

        self.assertTrue(preview["required"])
        self.assertEqual(
            preview["command"], "python -m agents_remember.code_quality.check"
        )
        self.assertIn("before the code commit", str(preview["reason"]))
        self.assertFalse(
            code_quality_gate.code_quality_gate_preview(
                "agents-remember", code_would_commit=False
            )["required"]
        )

    def test_gate_runs_current_worktree_source_with_default_strict_wrapper(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            worktree = Path(tmp)
            wrapper = worktree / code_quality_gate.QUALITY_WRAPPER
            wrapper.parent.mkdir(parents=True)
            wrapper.write_text("# wrapper marker\n", encoding="utf-8")
            calls: list[tuple[list[str], Path, dict[str, str]]] = []

            def runner(
                command: list[str], cwd: Path, env: Mapping[str, str]
            ) -> subprocess.CompletedProcess[str]:
                calls.append((command, cwd, dict(env)))
                return subprocess.CompletedProcess(command, 0, stdout="passed\n")

            with mock.patch.object(
                code_quality_gate, "quality_python", return_value=Path(sys.executable)
            ):
                result = code_quality_gate.run_strict_code_quality_gate(
                    worktree, runner=runner
                )

            self.assertTrue(result["passed"])
            command, cwd, env = calls[0]
            self.assertEqual(
                command,
                [sys.executable, "-m", "agents_remember.code_quality.check"],
            )
            self.assertEqual(cwd, worktree)
            self.assertEqual(
                env["PYTHONPATH"].split(os.pathsep)[0],
                (worktree / "mcp" / "src").as_posix(),
            )

    def test_gate_failure_includes_bounded_wrapper_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            worktree = Path(tmp)
            wrapper = worktree / code_quality_gate.QUALITY_WRAPPER
            wrapper.parent.mkdir(parents=True)
            wrapper.write_text("# wrapper marker\n", encoding="utf-8")

            def runner(
                command: list[str], cwd: Path, env: Mapping[str, str]
            ) -> subprocess.CompletedProcess[str]:
                del cwd, env
                return subprocess.CompletedProcess(
                    command, 1, stdout="\n".join(f"line-{index}" for index in range(50))
                )

            with (
                mock.patch.object(
                    code_quality_gate, "quality_python", return_value=Path(sys.executable)
                ),
                self.assertRaisesRegex(
                    RuntimeError, "strict code-quality gate failed before code commit"
                ) as caught,
            ):
                code_quality_gate.run_strict_code_quality_gate(worktree, runner=runner)

            self.assertNotIn("line-0", str(caught.exception))
            self.assertIn("line-49", str(caught.exception))

    def test_quality_python_prefers_worktree_virtualenv(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            worktree = Path(tmp)
            local_python = worktree / ".venv" / "bin" / "python"
            local_python.parent.mkdir(parents=True)
            local_python.write_text("", encoding="utf-8")

            self.assertEqual(code_quality_gate.quality_python(worktree), local_python)

    def test_quality_python_uses_shared_clone_virtualenv_for_linked_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            worktree = root / "worktree"
            worktree.mkdir()
            common_dir = root / "primary" / ".git"
            shared_python = root / "primary" / ".venv" / "bin" / "python"
            shared_python.parent.mkdir(parents=True)
            shared_python.write_text("", encoding="utf-8")

            with mock.patch.object(
                code_quality_gate, "_git_common_dir", return_value=common_dir
            ):
                self.assertEqual(
                    code_quality_gate.quality_python(worktree), shared_python
                )


class CloseoutCodeQualityGateTests(unittest.TestCase):
    def test_gate_failure_precedes_all_closeout_commits(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            contract = dirty_open_external_contract_fixture(Path(tmp))
            assert contract.memory_worktree is not None
            code_head = git(contract.code_worktree, "rev-parse", "HEAD")
            memory_head = git(contract.memory_worktree, "rev-parse", "HEAD")
            ledger_before = (contract.memory_worktree / "memory.md").read_bytes()

            with (
                mock.patch.object(
                    closeout_module, "requires_strict_code_quality", return_value=True
                ),
                mock.patch.object(
                    closeout_module,
                    "run_strict_code_quality_gate",
                    side_effect=RuntimeError(
                        "strict code-quality gate failed before code commit"
                    ),
                ),
                self.assertRaisesRegex(
                    RuntimeError, "strict code-quality gate failed before code commit"
                ),
            ):
                worktree_manager.command_closeout(closeout_args(contract))

            self.assertEqual(git(contract.code_worktree, "rev-parse", "HEAD"), code_head)
            self.assertEqual(git(contract.memory_worktree, "rev-parse", "HEAD"), memory_head)
            self.assertEqual(
                (contract.memory_worktree / "memory.md").read_bytes(), ledger_before
            )
            self.assertEqual(
                load_contract(contract.contract_path).closeout_status, "not-started"
            )

    def test_success_runs_quality_before_code_commit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            contract = dirty_open_external_contract_fixture(Path(tmp))
            events: list[str] = []

            def run_gate(_worktree: Path) -> dict[str, object]:
                events.append("quality")
                return {
                    "required": True,
                    "passed": True,
                    "command": "python -m agents_remember.code_quality.check",
                }

            def record_commit(repo: Path, message: str) -> str:
                if repo == contract.code_worktree:
                    events.append("code-commit")
                return commit_if_dirty(repo, message)

            with (
                mock.patch.object(
                    closeout_module, "requires_strict_code_quality", return_value=True
                ),
                mock.patch.object(
                    closeout_module,
                    "run_strict_code_quality_gate",
                    side_effect=run_gate,
                ),
                mock.patch.object(
                    closeout_module, "commit_if_dirty", side_effect=record_commit
                ),
                redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(
                    worktree_manager.command_closeout(closeout_args(contract)), 0
                )

            self.assertEqual(events[:2], ["quality", "code-commit"])


if __name__ == "__main__":
    unittest.main()
