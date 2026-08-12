"""Strict quality-runner command, environment, cap, and report behavior."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from collections.abc import Mapping
from pathlib import Path
from unittest import mock

from agents_remember.kernel.git_command import GIT_REPOSITORY_SELECTOR_ENV
from agents_remember.worktrees.modules import code_quality_gate
from test_worktree_closeout_quality_gate import _checkout_with_wrapper, _quality_target


class CodeQualityGateTests(unittest.TestCase):
    def test_preview_requires_strict_wrapper_for_any_repo_that_carries_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            worktree = _checkout_with_wrapper(Path(tmp))

            preview = code_quality_gate.code_quality_gate_preview(worktree, code_would_commit=True)

            self.assertTrue(preview["required"])
            self.assertEqual(preview["status"], code_quality_gate.GATE_ENFORCED)
            self.assertEqual(
                preview["command"],
                "python -m agents_remember.code_quality.check --targeted",
            )
            self.assertEqual(preview["mode"], code_quality_gate.GATE_TARGETED)
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
                code_quality_gate.run_strict_code_quality_gate(_quality_target(worktree))

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
                result = code_quality_gate.run_strict_code_quality_gate(
                    _quality_target(worktree),
                    runner=runner,
                )

            self.assertTrue(result["passed"])
            self.assertEqual(result["status"], code_quality_gate.GATE_ENFORCED)
            command, cwd, env = calls[0]
            self.assertEqual(
                command,
                [
                    sys.executable,
                    "-m",
                    "agents_remember.code_quality.check",
                    "--targeted",
                ],
            )
            self.assertEqual(cwd, worktree)
            self.assertEqual(
                env["PYTHONPATH"].split(os.pathsep)[0],
                (worktree / "mcp" / "src").as_posix(),
            )
            report = worktree / "enclosure" / "reports" / "test-results.md"
            self.assertEqual(result["reportPath"], report.as_posix())
            report_text = report.read_text(encoding="utf-8")
            self.assertIn("# Strict Quality Test Results", report_text)
            self.assertIn("- Status: **passed**", report_text)
            self.assertIn("    passed", report_text)

    def test_gate_replaces_one_test_report_instead_of_accumulating_runs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            worktree = _checkout_with_wrapper(root / "code")
            worktree_group = root / "enclosure"
            report = worktree_group / "reports" / "test-results.md"
            report.parent.mkdir(parents=True)
            report.write_text("obsolete run\n", encoding="utf-8")

            outputs = iter(("first completed run\n", "second completed run\n"))

            def runner(
                command: list[str], cwd: Path, env: Mapping[str, str]
            ) -> subprocess.CompletedProcess[str]:
                del cwd, env
                return subprocess.CompletedProcess(command, 0, stdout=next(outputs))

            with mock.patch.object(
                code_quality_gate, "quality_python", return_value=Path(sys.executable)
            ):
                code_quality_gate.run_strict_code_quality_gate(
                    _quality_target(worktree, worktree_group), runner=runner
                )
                code_quality_gate.run_strict_code_quality_gate(
                    _quality_target(worktree, worktree_group), runner=runner
                )

            report_text = report.read_text(encoding="utf-8")
            self.assertNotIn("obsolete run", report_text)
            self.assertNotIn("first completed run", report_text)
            self.assertIn("second completed run", report_text)
            self.assertEqual([path.name for path in report.parent.iterdir()], [report.name])

    def test_interrupted_gate_keeps_the_previous_completed_test_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            worktree = _checkout_with_wrapper(root / "code")
            worktree_group = root / "enclosure"
            report = worktree_group / "reports" / "test-results.md"
            report.parent.mkdir(parents=True)
            report.write_text("previous completed run\n", encoding="utf-8")

            def interrupted_runner(
                command: list[str], cwd: Path, env: Mapping[str, str]
            ) -> subprocess.CompletedProcess[str]:
                del command, cwd, env
                raise KeyboardInterrupt

            with (
                mock.patch.object(
                    code_quality_gate, "quality_python", return_value=Path(sys.executable)
                ),
                self.assertRaises(KeyboardInterrupt),
            ):
                code_quality_gate.run_strict_code_quality_gate(
                    _quality_target(worktree, worktree_group),
                    runner=interrupted_runner,
                )

            self.assertEqual(report.read_text(encoding="utf-8"), "previous completed run\n")

    def test_gate_measures_the_leaf_diff_not_the_whole_branch(self) -> None:
        """The leaf's base commit reaches the wrapper as --diff-base.

        Without it the wrapper resolves its base to origin/HEAD or main, and the
        100% per-diff coverage floor then measures every change on the integration
        branch instead of this leaf's own diff. That is unpassable for any leaf, so
        the gate would block every closeout rather than enforce anything.
        """
        with tempfile.TemporaryDirectory() as tmp:
            worktree = _checkout_with_wrapper(Path(tmp))
            calls: list[list[str]] = []

            def runner(
                command: list[str], cwd: Path, env: Mapping[str, str]
            ) -> subprocess.CompletedProcess[str]:
                calls.append(command)
                return subprocess.CompletedProcess(command, 0, stdout="passed\n")

            with mock.patch.object(
                code_quality_gate, "quality_python", return_value=Path(sys.executable)
            ):
                result = code_quality_gate.run_strict_code_quality_gate(
                    _quality_target(worktree),
                    diff_base="c1dc5056",
                    runner=runner,
                )

            self.assertEqual(
                calls[0],
                [
                    sys.executable,
                    "-m",
                    "agents_remember.code_quality.check",
                    "--targeted",
                    "--diff-base",
                    "c1dc5056",
                ],
            )
            self.assertEqual(result["diffBase"], "c1dc5056")
            self.assertIn("--diff-base c1dc5056", str(result["command"]))

    def test_gate_preview_reports_the_diff_base_it_will_use(self) -> None:
        """The preview names the exact command, so a reader can rerun what will run."""
        with tempfile.TemporaryDirectory() as tmp:
            worktree = _checkout_with_wrapper(Path(tmp))
            preview = code_quality_gate.code_quality_gate_preview(
                worktree, code_would_commit=True, diff_base="c1dc5056"
            )
            self.assertEqual(preview["diffBase"], "c1dc5056")
            self.assertIn("--targeted --diff-base c1dc5056", str(preview["command"]))

    def test_gate_command_refuses_unknown_modes(self) -> None:
        with self.assertRaisesRegex(ValueError, "unknown quality gate mode"):
            code_quality_gate._gate_command("", mode="bogus")

    def test_gate_command_requires_a_cap_for_the_full_mode(self) -> None:
        with self.assertRaisesRegex(ValueError, "requires memory_cap_bytes"):
            code_quality_gate._gate_command("", mode=code_quality_gate.GATE_FULL)

    def test_full_gate_preview_names_the_memory_cap_and_policy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            worktree = _checkout_with_wrapper(Path(tmp))
            preview = code_quality_gate.code_quality_gate_preview(
                worktree,
                code_would_commit=True,
                diff_base="c1dc5056",
                plan=code_quality_gate.QualityGatePlan(
                    mode=code_quality_gate.GATE_FULL,
                    memory_cap_bytes=2147483648,
                    systemd_run_available=False,
                ),
            )
            self.assertEqual(preview["mode"], code_quality_gate.GATE_FULL)
            self.assertIn("--memory-cap-bytes 2147483648", str(preview["command"]))
            memory_cap = preview["memoryCap"]
            assert isinstance(memory_cap, dict)
            self.assertEqual(memory_cap["capBytes"], 2147483648)
            self.assertEqual(memory_cap["policy"], "orchestration.qualityGate.memoryCapBytes")

    def test_full_gate_preview_without_a_cap_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            worktree = _checkout_with_wrapper(Path(tmp))

            with self.assertRaisesRegex(ValueError, "requires memory_cap_bytes"):
                code_quality_gate.code_quality_gate_preview(
                    worktree,
                    code_would_commit=True,
                    plan=code_quality_gate.QualityGatePlan(mode=code_quality_gate.GATE_FULL),
                )

    def test_full_gate_without_a_cap_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            worktree = _checkout_with_wrapper(Path(tmp))

            with (
                mock.patch.object(
                    code_quality_gate,
                    "quality_python",
                    return_value=Path(sys.executable),
                ),
                self.assertRaisesRegex(RuntimeError, "settings-owned memory cap"),
            ):
                code_quality_gate.run_strict_code_quality_gate(
                    _quality_target(worktree),
                    plan=code_quality_gate.QualityGatePlan(mode=code_quality_gate.GATE_FULL),
                    runner=lambda command, cwd, env: subprocess.CompletedProcess(
                        command, 0, stdout=""
                    ),
                )

    def test_gate_run_refuses_unknown_modes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            worktree = _checkout_with_wrapper(Path(tmp))

            with (
                mock.patch.object(
                    code_quality_gate, "quality_python", return_value=Path(sys.executable)
                ),
                self.assertRaisesRegex(ValueError, "unknown quality gate mode"),
            ):
                code_quality_gate.run_strict_code_quality_gate(
                    _quality_target(worktree),
                    plan=code_quality_gate.QualityGatePlan(mode="bogus"),
                    runner=lambda command, cwd, env: subprocess.CompletedProcess(
                        command, 0, stdout=""
                    ),
                )

    def test_full_gate_run_uses_the_planned_cap_mechanism(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            worktree = _checkout_with_wrapper(Path(tmp))
            calls: list[list[str]] = []

            def runner(
                command: list[str], cwd: Path, env: Mapping[str, str]
            ) -> subprocess.CompletedProcess[str]:
                calls.append(command)
                return subprocess.CompletedProcess(command, 0, stdout="passed\n")

            with mock.patch.object(
                code_quality_gate, "quality_python", return_value=Path(sys.executable)
            ):
                result = code_quality_gate.run_strict_code_quality_gate(
                    _quality_target(worktree),
                    diff_base="c1dc5056",
                    plan=code_quality_gate.QualityGatePlan(
                        mode=code_quality_gate.GATE_FULL,
                        memory_cap_bytes=1024,
                        systemd_run_available=False,
                    ),
                    runner=runner,
                )

            self.assertTrue(result["passed"])
            self.assertIn("--memory-cap-bytes", calls[0])
            self.assertIn("1024", calls[0])
            self.assertEqual(result["mode"], code_quality_gate.GATE_FULL)
            memory_cap = result["memoryCap"]
            assert isinstance(memory_cap, dict)
            self.assertEqual(memory_cap["mechanism"], "rlimit-address-space")

    def test_full_gate_kill_names_the_policy_loudly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            worktree = _checkout_with_wrapper(Path(tmp))

            def runner(
                command: list[str], cwd: Path, env: Mapping[str, str]
            ) -> subprocess.CompletedProcess[str]:
                del cwd, env
                return subprocess.CompletedProcess(command, 137, stdout="")

            with (
                mock.patch.object(
                    code_quality_gate, "quality_python", return_value=Path(sys.executable)
                ),
                self.assertRaisesRegex(RuntimeError, "killed by the memory cap") as caught,
            ):
                code_quality_gate.run_strict_code_quality_gate(
                    _quality_target(worktree),
                    plan=code_quality_gate.QualityGatePlan(
                        mode=code_quality_gate.GATE_FULL,
                        memory_cap_bytes=1024,
                        systemd_run_available=False,
                    ),
                    runner=runner,
                )

            self.assertIn("orchestration.qualityGate.memoryCapBytes", str(caught.exception))

    def test_gate_failure_names_the_cap_when_the_scope_is_sigkilled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            worktree = _checkout_with_wrapper(Path(tmp))

            def runner(
                command: list[str], cwd: Path, env: Mapping[str, str]
            ) -> subprocess.CompletedProcess[str]:
                del cwd, env
                return subprocess.CompletedProcess(command, -9, stdout="")

            with (
                mock.patch.object(
                    code_quality_gate, "quality_python", return_value=Path(sys.executable)
                ),
                self.assertRaisesRegex(RuntimeError, "killed by the memory cap") as caught,
            ):
                code_quality_gate.run_strict_code_quality_gate(
                    _quality_target(worktree),
                    plan=code_quality_gate.QualityGatePlan(
                        mode=code_quality_gate.GATE_FULL,
                        memory_cap_bytes=1024,
                        systemd_run_available=False,
                    ),
                    runner=runner,
                )

            self.assertIn("orchestration.qualityGate.memoryCapBytes", str(caught.exception))

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
                code_quality_gate.run_strict_code_quality_gate(
                    _quality_target(worktree),
                    runner=runner,
                )

            self.assertNotIn("line-0", str(caught.exception))
            self.assertIn("line-49", str(caught.exception))
            report = worktree / "enclosure" / "reports" / "test-results.md"
            self.assertIn(report.as_posix(), str(caught.exception))
            report_text = report.read_text(encoding="utf-8")
            self.assertIn("- Status: **failed**", report_text)
            self.assertIn("    line-0", report_text)
            self.assertIn("    line-49", report_text)

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

    def test_the_gate_hands_the_wrapper_no_repository_selectors(self) -> None:
        # The gate spawns the wrapper, and the wrapper runs git: `git ls-files` for its scope
        # and `merge-base` for its diff base. Copying os.environ straight through made this
        # gate's correctness -- which repository gets certified before a code commit -- rest
        # on every git call inside a child process it cannot see behaving itself.
        with tempfile.TemporaryDirectory() as tmp:
            worktree = Path(tmp)
            selectors = {name: str(worktree / "decoy") for name in GIT_REPOSITORY_SELECTOR_ENV}

            with mock.patch.dict(os.environ, {**selectors, "PYTHONPATH": "/pre-existing"}):
                env = code_quality_gate.quality_environment(worktree)

            self.assertTrue(set(GIT_REPOSITORY_SELECTOR_ENV).isdisjoint(env))
            # and nothing else changes: this worktree's src still leads, the inherited
            # PYTHONPATH still follows, and PATH survives or the wrapper cannot start.
            self.assertEqual(
                env["PYTHONPATH"],
                os.pathsep.join([(worktree / "mcp" / "src").as_posix(), "/pre-existing"]),
            )
            self.assertIn("PATH", env)
            if os.name != "nt":
                self.assertEqual(
                    {name: env[name] for name in ("TMPDIR", "TMP", "TEMP")},
                    {"TMPDIR": "/tmp", "TMP": "/tmp", "TEMP": "/tmp"},
                )

    def test_gate_capture_replaces_non_utf8_output_before_report_or_transport(self) -> None:
        completed = subprocess.CompletedProcess(["quality"], 1, stdout="bad\ufffdoutput")
        with mock.patch.object(subprocess, "run", return_value=completed) as run:
            result = code_quality_gate.run_subprocess(["quality"], Path("/tmp"), {})

        self.assertIs(result, completed)
        self.assertEqual(run.call_args.kwargs["encoding"], "utf-8")
        self.assertEqual(run.call_args.kwargs["errors"], "replace")
