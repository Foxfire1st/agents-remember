from __future__ import annotations

import io
import json
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
from test_worktree_support import (
    closeout_args,
    dirty_open_external_contract_fixture,
    git,
    write_file_onboarding,
)


def _checkout_with_wrapper(root: Path) -> Path:
    wrapper = root / code_quality_gate.QUALITY_WRAPPER
    wrapper.parent.mkdir(parents=True, exist_ok=True)
    wrapper.write_text("# wrapper marker\n", encoding="utf-8")
    return root


class CodeQualityGateTests(unittest.TestCase):
    def test_preview_requires_strict_wrapper_for_any_repo_that_carries_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            worktree = _checkout_with_wrapper(Path(tmp))

            preview = code_quality_gate.code_quality_gate_preview(worktree, code_would_commit=True)

            self.assertTrue(preview["required"])
            self.assertEqual(preview["status"], code_quality_gate.GATE_ENFORCED)
            self.assertEqual(preview["command"], "python -m agents_remember.code_quality.check")
            self.assertIn("before the code commit", str(preview["reason"]))
            self.assertTrue(
                code_quality_gate.requires_strict_code_quality(worktree, code_would_commit=True)
            )

    def test_preview_reports_no_code_commit_when_nothing_would_commit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            worktree = _checkout_with_wrapper(Path(tmp))

            preview = code_quality_gate.code_quality_gate_preview(worktree, code_would_commit=False)

            self.assertFalse(preview["required"])
            self.assertEqual(preview["status"], code_quality_gate.GATE_NO_CODE_COMMIT)
            self.assertEqual(preview["command"], "")
            self.assertFalse(
                code_quality_gate.requires_strict_code_quality(worktree, code_would_commit=False)
            )

    def test_preview_reports_missing_wrapper_instead_of_skipping_silently(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            consuming_repo = Path(tmp)

            preview = code_quality_gate.code_quality_gate_preview(
                consuming_repo, code_would_commit=True
            )

            self.assertFalse(preview["required"])
            self.assertEqual(preview["status"], code_quality_gate.GATE_WRAPPER_UNAVAILABLE)
            self.assertIn(code_quality_gate.QUALITY_WRAPPER.as_posix(), str(preview["reason"]))
            self.assertIn("not quality-checked", str(preview["reason"]))
            self.assertFalse(
                code_quality_gate.requires_strict_code_quality(
                    consuming_repo, code_would_commit=True
                )
            )

    def test_gate_refuses_to_run_when_the_wrapper_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            worktree = Path(tmp)

            with self.assertRaisesRegex(RuntimeError, "project-owned wrapper is missing"):
                code_quality_gate.run_strict_code_quality_gate(worktree)

    def test_gate_runs_current_worktree_source_with_default_strict_wrapper(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            worktree = _checkout_with_wrapper(Path(tmp))
            calls: list[tuple[list[str], Path, dict[str, str]]] = []

            def runner(
                command: list[str], cwd: Path, env: Mapping[str, str]
            ) -> subprocess.CompletedProcess[str]:
                calls.append((command, cwd, dict(env)))
                return subprocess.CompletedProcess(command, 0, stdout="passed\n")

            with mock.patch.object(
                code_quality_gate, "quality_python", return_value=Path(sys.executable)
            ):
                result = code_quality_gate.run_strict_code_quality_gate(worktree, runner=runner)

            self.assertTrue(result["passed"])
            self.assertEqual(result["status"], code_quality_gate.GATE_ENFORCED)
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
            worktree = _checkout_with_wrapper(Path(tmp))

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

            with mock.patch.object(code_quality_gate, "_git_common_dir", return_value=common_dir):
                self.assertEqual(code_quality_gate.quality_python(worktree), shared_python)


class CloseoutCodeQualityGateTests(unittest.TestCase):
    def test_closeout_hands_the_gate_the_code_worktree_not_the_repository_name(self) -> None:
        """Both closeout entry points must pass the checkout, and nothing else catches it.

        The deciders take a checkout path. Handing them ``contract.repo_name`` -- the
        signature they had before the repository-name hard-code was removed -- makes
        ``quality_wrapper_path`` build a relative path off the process CWD, which is not a
        file, so ``requires_strict_code_quality`` returns ``False`` and the gate the product
        documents as mandatory silently never runs. ``contract`` is unannotated in
        ``closeout.py``, so Pyright type-checks that mistake in silence; every other test in
        this file patches ``requires_strict_code_quality`` out and cannot see the argument.
        """
        with tempfile.TemporaryDirectory() as tmp:
            contract = dirty_open_external_contract_fixture(Path(tmp))
            _checkout_with_wrapper(contract.code_worktree)
            assert contract.memory_worktree is not None
            write_file_onboarding(  # the planted wrapper is a changed source file too
                contract.memory_worktree / "onboarding",
                contract.repo_name,
                code_quality_gate.QUALITY_WRAPPER.as_posix(),
                contract.code_base_commit,
            )
            deciders: list[object] = []
            real_requires = code_quality_gate.requires_strict_code_quality

            def spy(target: Path, *, code_would_commit: bool) -> bool:
                deciders.append(target)
                return real_requires(target, code_would_commit=code_would_commit)

            # Preview path (closeout.py:282): reports the enforced state for a dirty
            # checkout that carries the wrapper, rather than "wrapper-unavailable".
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(
                    worktree_manager.command_closeout(closeout_args(contract, dry_run=True)),
                    0,
                )
            gate = json.loads(output.getvalue())["code_quality_gate"]
            self.assertEqual(gate["status"], code_quality_gate.GATE_ENFORCED)
            self.assertTrue(gate["required"])

            # Apply path (closeout.py:589-593): the real decider runs and fires the gate.
            with (
                mock.patch.object(closeout_module, "requires_strict_code_quality", side_effect=spy),
                mock.patch.object(
                    closeout_module,
                    "run_strict_code_quality_gate",
                    return_value={"required": True, "passed": True, "command": "x"},
                ) as gate_run,
                redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(worktree_manager.command_closeout(closeout_args(contract)), 0)

            self.assertEqual(deciders, [contract.code_worktree])
            gate_run.assert_called_once_with(contract.code_worktree)

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
                    side_effect=RuntimeError("strict code-quality gate failed before code commit"),
                ),
                self.assertRaisesRegex(
                    RuntimeError, "strict code-quality gate failed before code commit"
                ),
            ):
                worktree_manager.command_closeout(closeout_args(contract))

            self.assertEqual(git(contract.code_worktree, "rev-parse", "HEAD"), code_head)
            self.assertEqual(git(contract.memory_worktree, "rev-parse", "HEAD"), memory_head)
            self.assertEqual((contract.memory_worktree / "memory.md").read_bytes(), ledger_before)
            self.assertEqual(load_contract(contract.contract_path).closeout_status, "not-started")

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
                mock.patch.object(closeout_module, "commit_if_dirty", side_effect=record_commit),
                redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(worktree_manager.command_closeout(closeout_args(contract)), 0)

            self.assertEqual(events[:2], ["quality", "code-commit"])


if __name__ == "__main__":
    unittest.main()
