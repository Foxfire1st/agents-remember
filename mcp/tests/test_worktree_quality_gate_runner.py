"""Strict quality-runner command, environment, cap, and report behavior."""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agents_remember.models.test_evidence import _certifying_evidence_from_verified_dagger
from agents_remember.worktrees.modules import code_quality_gate
from agents_remember.worktrees.modules.clean_quality_executor import CleanQualityOutcome
from test_worktree_closeout_quality_gate import _checkout_with_wrapper, _quality_target


def _successful_quality_outcome(request, *, stdout: str = "passed\n") -> CleanQualityOutcome:
    candidate_tree = subprocess.run(
        ["git", "write-tree"],
        cwd=request.code_worktree,
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()
    evidence = _certifying_evidence_from_verified_dagger(
        candidate_tree=candidate_tree,
        result_sha256="0" * 64,
    )
    return CleanQualityOutcome(
        subprocess.CompletedProcess(["dagger"], 0, stdout=stdout),
        evidence,
    )


class CodeQualityGateTests(unittest.TestCase):
    def test_preview_requires_strict_wrapper_for_any_repo_that_carries_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            worktree = _checkout_with_wrapper(Path(tmp))

            preview = code_quality_gate.code_quality_gate_preview(worktree, code_would_commit=True)

            self.assertTrue(preview["required"])
            self.assertEqual(preview["status"], code_quality_gate.GATE_ENFORCED)
            self.assertEqual(
                preview["command"],
                "dagger call quality '--source=<exact-staged-candidate>' "
                "'--repository-bundle=<exact-git-ancestry-bundle>' --mode=targeted",
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

    def test_repository_policy_can_require_its_integrated_wrapper(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            candidate = Path(tmp)

            self.assertTrue(code_quality_gate.requires_integrated_acceptance("agents-remember"))
            self.assertFalse(code_quality_gate.requires_integrated_acceptance("consumer-repo"))
            self.assertTrue(
                code_quality_gate.requires_strict_code_quality(
                    candidate,
                    code_would_commit=True,
                    required_when_missing=True,
                )
            )
            with self.assertRaisesRegex(RuntimeError, "self-owned wrapper"):
                code_quality_gate.code_quality_gate_preview(
                    candidate,
                    code_would_commit=True,
                    required_when_missing=True,
                )

    def test_gate_refuses_to_run_when_the_wrapper_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            worktree = Path(tmp)

            with self.assertRaisesRegex(RuntimeError, "self-owned wrapper is missing"):
                code_quality_gate.run_strict_code_quality_gate(_quality_target(worktree))

    def test_non_dagger_executor_is_refused_by_command_and_policy_builders(self) -> None:
        plan = code_quality_gate.QualityGatePlan(executor="local")

        with self.assertRaisesRegex(ValueError, "pinned Dagger"):
            code_quality_gate._gate_command_parts(plan, "", "closeout")
        with self.assertRaisesRegex(ValueError, "pinned Dagger"):
            code_quality_gate._memory_policy_payload(executor="local")

    def test_host_quality_execution_refuses_before_resolving_or_running_a_wrapper(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            worktree = _checkout_with_wrapper(Path(tmp))
            with self.assertRaisesRegex(RuntimeError, "host quality execution is forbidden"):
                code_quality_gate.run_local_quality_diagnostic(_quality_target(worktree))

    def test_dagger_executor_uses_the_same_staged_candidate_and_never_runs_host_rails(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            worktree = _checkout_with_wrapper(root / "code")
            target = _quality_target(worktree, root / "enclosure")
            with mock.patch.object(
                code_quality_gate,
                "run_clean_quality",
                side_effect=lambda request: _successful_quality_outcome(
                    request,
                    stdout="dagger passed\n",
                ),
            ) as clean:
                result = code_quality_gate.run_strict_code_quality_gate(
                    target,
                    diff_base="abc123",
                    plan=code_quality_gate.QualityGatePlan(
                        mode=code_quality_gate.GATE_FULL,
                        executor="dagger",
                        memory_cap_bytes=2_147_483_648,
                    ),
                )

            request = clean.call_args.args[0]
            self.assertEqual(request.code_worktree, worktree)
            self.assertEqual(request.worktree_group, root / "enclosure")
            self.assertEqual(request.mode, code_quality_gate.GATE_FULL)
            self.assertEqual(request.diff_base, "abc123")
            self.assertEqual(request.memory_cap_bytes, 2_147_483_648)
            self.assertEqual(result["executor"], "dagger")
            self.assertIn("dagger call quality", str(result["command"]))
            memory_cap = result["memoryCap"]
            assert isinstance(memory_cap, dict)
            self.assertEqual(memory_cap["mechanism"], "container-wrapper")
            report = Path(str(result["reportPath"])).read_text(encoding="utf-8")
            self.assertIn("Memory-cap policy: `dagger-inner-wrapper`", report)

    def test_gate_replaces_one_test_report_instead_of_accumulating_runs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            worktree = _checkout_with_wrapper(root / "code")
            worktree_group = root / "enclosure"
            report = worktree_group / "reports" / "test-results.md"
            report.parent.mkdir(parents=True)
            report.write_text("obsolete run\n", encoding="utf-8")

            outputs = iter(("first completed run\n", "second completed run\n"))
            with mock.patch.object(
                code_quality_gate,
                "run_clean_quality",
                side_effect=lambda request: _successful_quality_outcome(
                    request,
                    stdout=next(outputs),
                ),
            ):
                code_quality_gate.run_strict_code_quality_gate(
                    _quality_target(worktree, worktree_group)
                )
                code_quality_gate.run_strict_code_quality_gate(
                    _quality_target(worktree, worktree_group)
                )

            report_text = report.read_text(encoding="utf-8")
            self.assertNotIn("obsolete run", report_text)
            self.assertNotIn("first completed run", report_text)
            self.assertIn("second completed run", report_text)
            self.assertEqual(
                sorted(path.name for path in report.parent.iterdir()),
                ["test-results.md"],
            )

    def test_interrupted_gate_keeps_the_previous_completed_test_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            worktree = _checkout_with_wrapper(root / "code")
            worktree_group = root / "enclosure"
            report = worktree_group / "reports" / "test-results.md"
            report.parent.mkdir(parents=True)
            report.write_text("previous completed run\n", encoding="utf-8")

            with (
                mock.patch.object(
                    code_quality_gate, "run_clean_quality", side_effect=KeyboardInterrupt
                ),
                self.assertRaises(KeyboardInterrupt),
            ):
                code_quality_gate.run_strict_code_quality_gate(
                    _quality_target(worktree, worktree_group)
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
            with mock.patch.object(
                code_quality_gate,
                "run_clean_quality",
                side_effect=_successful_quality_outcome,
            ) as clean:
                result = code_quality_gate.run_strict_code_quality_gate(
                    _quality_target(worktree),
                    diff_base="c1dc5056",
                )

            self.assertEqual(clean.call_args.args[0].diff_base, "c1dc5056")
            self.assertEqual(result["diffBase"], "c1dc5056")
            self.assertIn("--diff-base=c1dc5056", str(result["command"]))

    def test_gate_preview_reports_the_diff_base_it_will_use(self) -> None:
        """The preview names the exact command, so a reader can rerun what will run."""
        with tempfile.TemporaryDirectory() as tmp:
            worktree = _checkout_with_wrapper(Path(tmp))
            preview = code_quality_gate.code_quality_gate_preview(
                worktree, code_would_commit=True, diff_base="c1dc5056"
            )
            self.assertEqual(preview["diffBase"], "c1dc5056")
            self.assertIn("--mode=targeted --diff-base=c1dc5056", str(preview["command"]))

    def test_gate_command_refuses_unknown_modes(self) -> None:
        with self.assertRaisesRegex(ValueError, "unknown quality gate mode"):
            code_quality_gate._gate_command("", mode="bogus")
        with self.assertRaisesRegex(ValueError, "pinned Dagger graph"):
            code_quality_gate._gate_command("", executor="docker")

    def test_preview_and_run_refuse_unknown_executors_at_the_public_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            worktree = _checkout_with_wrapper(Path(tmp))
            invalid = code_quality_gate.QualityGatePlan(executor="docker")
            with self.assertRaisesRegex(ValueError, "pinned Dagger executor"):
                code_quality_gate.code_quality_gate_preview(
                    worktree, code_would_commit=True, plan=invalid
                )
            with self.assertRaisesRegex(ValueError, "pinned Dagger executor"):
                code_quality_gate.run_strict_code_quality_gate(
                    _quality_target(worktree), plan=invalid
                )

    def test_command_planning_covers_reportless_and_minimal_dagger_forms(self) -> None:
        command, invocation = code_quality_gate._gate_command_parts(
            code_quality_gate.QualityGatePlan(),
            "",
            "closeout-staged",
        )
        self.assertEqual(command[-1], "--mode=targeted")
        self.assertEqual(invocation, "closeout-staged")
        self.assertEqual(
            code_quality_gate._dagger_report_command(
                code_quality_gate.QualityGatePlan(executor="dagger"), ""
            ),
            [
                "dagger",
                "call",
                "quality",
                "--source=<exact-staged-candidate>",
                "--repository-bundle=<exact-git-ancestry-bundle>",
                "--mode=targeted",
            ],
        )
        policy = code_quality_gate._memory_policy_payload(executor="dagger")
        self.assertEqual(policy["memoryPolicy"]["mode"], "container-host-managed")  # type: ignore[index]

    def test_dagger_preview_is_symbolic_and_does_not_require_host_tools(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            worktree = _checkout_with_wrapper(Path(tmp))
            preview = code_quality_gate.code_quality_gate_preview(
                worktree,
                code_would_commit=True,
                diff_base="abc123",
                plan=code_quality_gate.QualityGatePlan(
                    mode=code_quality_gate.GATE_FULL,
                    executor="dagger",
                    memory_cap_bytes=4096,
                ),
            )
        self.assertIn("dagger call quality", str(preview["command"]))
        self.assertIn("--repository-bundle", str(preview["command"]))
        memory_cap = preview["memoryCap"]
        assert isinstance(memory_cap, dict)
        self.assertEqual(memory_cap["capBytes"], 4096)

    def test_full_gate_command_is_pinned_dagger_without_an_explicit_cap(self) -> None:
        command = code_quality_gate._gate_command("", mode=code_quality_gate.GATE_FULL)
        self.assertIn("dagger call quality", command)
        self.assertIn("--mode=full", command)

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
                ),
            )
            self.assertEqual(preview["mode"], code_quality_gate.GATE_FULL)
            self.assertIn("--memory-cap-bytes=2147483648", str(preview["command"]))
            memory_cap = preview["memoryCap"]
            assert isinstance(memory_cap, dict)
            self.assertEqual(memory_cap["capBytes"], 2147483648)
            self.assertEqual(memory_cap["policy"], "dagger-inner-wrapper")
            policy = preview["memoryPolicy"]
            assert isinstance(policy, dict)
            self.assertEqual(policy["mode"], "explicit-cap")
            self.assertEqual(policy["pytestProcesses"], "auto")
            self.assertEqual(policy["swap"], "container-host-managed")

    def test_full_gate_preview_without_a_cap_is_container_host_managed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            worktree = _checkout_with_wrapper(Path(tmp))

            preview = code_quality_gate.code_quality_gate_preview(
                worktree,
                code_would_commit=True,
                plan=code_quality_gate.QualityGatePlan(mode=code_quality_gate.GATE_FULL),
            )

            self.assertNotIn("memoryCap", preview)
            policy = preview["memoryPolicy"]
            assert isinstance(policy, dict)
            self.assertEqual(policy["mode"], "container-host-managed")
            self.assertEqual(policy["pytestProcesses"], "auto")
            self.assertEqual(policy["swap"], "container-host-managed")

    def test_full_gate_without_a_cap_uses_container_host_memory_and_swap(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            worktree = _checkout_with_wrapper(Path(tmp))
            with mock.patch.object(
                code_quality_gate,
                "run_clean_quality",
                side_effect=_successful_quality_outcome,
            ) as clean:
                result = code_quality_gate.run_strict_code_quality_gate(
                    _quality_target(worktree),
                    plan=code_quality_gate.QualityGatePlan(mode=code_quality_gate.GATE_FULL),
                )

            request = clean.call_args.args[0]
            self.assertEqual(request.mode, code_quality_gate.GATE_FULL)
            self.assertIsNone(request.memory_cap_bytes)
            self.assertNotIn("memoryCap", result)
            policy = result["memoryPolicy"]
            assert isinstance(policy, dict)
            self.assertEqual(policy["mode"], "container-host-managed")
            report = worktree / "enclosure" / "reports" / "test-results.md"
            report_text = report.read_text(encoding="utf-8")
            self.assertIn("- Memory policy: `container-host-managed`", report_text)
            self.assertIn("- Swap policy: `container-host-managed`", report_text)

    def test_gate_run_refuses_unknown_modes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            worktree = _checkout_with_wrapper(Path(tmp))

            with (
                self.assertRaisesRegex(ValueError, "unknown quality gate mode"),
            ):
                code_quality_gate.run_strict_code_quality_gate(
                    _quality_target(worktree),
                    plan=code_quality_gate.QualityGatePlan(mode="bogus"),
                )

    def test_full_gate_run_uses_the_planned_cap_mechanism(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            worktree = _checkout_with_wrapper(Path(tmp))
            with mock.patch.object(
                code_quality_gate,
                "run_clean_quality",
                side_effect=_successful_quality_outcome,
            ) as clean:
                result = code_quality_gate.run_strict_code_quality_gate(
                    _quality_target(worktree),
                    diff_base="c1dc5056",
                    plan=code_quality_gate.QualityGatePlan(
                        mode=code_quality_gate.GATE_FULL,
                        memory_cap_bytes=1024,
                    ),
                )

            self.assertTrue(result["passed"])
            request = clean.call_args.args[0]
            self.assertEqual(request.memory_cap_bytes, 1024)
            self.assertEqual(result["mode"], code_quality_gate.GATE_FULL)
            memory_cap = result["memoryCap"]
            assert isinstance(memory_cap, dict)
            self.assertEqual(memory_cap["mechanism"], "container-wrapper")

    def test_full_gate_kill_names_the_policy_loudly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            worktree = _checkout_with_wrapper(Path(tmp))

            with (
                mock.patch.object(
                    code_quality_gate,
                    "run_clean_quality",
                    return_value=subprocess.CompletedProcess(["dagger"], 137, stdout=""),
                ),
                self.assertRaisesRegex(RuntimeError, "killed by the memory cap") as caught,
            ):
                code_quality_gate.run_strict_code_quality_gate(
                    _quality_target(worktree),
                    plan=code_quality_gate.QualityGatePlan(
                        mode=code_quality_gate.GATE_FULL,
                        memory_cap_bytes=1024,
                    ),
                )

            self.assertIn("dagger-inner-wrapper", str(caught.exception))

    def test_gate_failure_names_the_cap_when_the_scope_is_sigkilled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            worktree = _checkout_with_wrapper(Path(tmp))

            with (
                mock.patch.object(
                    code_quality_gate,
                    "run_clean_quality",
                    return_value=subprocess.CompletedProcess(["dagger"], -9, stdout=""),
                ),
                self.assertRaisesRegex(RuntimeError, "killed by the memory cap") as caught,
            ):
                code_quality_gate.run_strict_code_quality_gate(
                    _quality_target(worktree),
                    plan=code_quality_gate.QualityGatePlan(
                        mode=code_quality_gate.GATE_FULL,
                        memory_cap_bytes=1024,
                    ),
                )

            self.assertIn("dagger-inner-wrapper", str(caught.exception))

    def test_gate_failure_includes_bounded_wrapper_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            worktree = _checkout_with_wrapper(Path(tmp))

            with (
                mock.patch.object(
                    code_quality_gate,
                    "run_clean_quality",
                    return_value=subprocess.CompletedProcess(
                        ["dagger"],
                        1,
                        stdout="\n".join(f"line-{index}" for index in range(50)),
                    ),
                ),
                self.assertRaisesRegex(
                    RuntimeError, "strict code-quality gate failed before code commit"
                ) as caught,
            ):
                code_quality_gate.run_strict_code_quality_gate(
                    _quality_target(worktree),
                )

            self.assertNotIn("line-0", str(caught.exception))
            self.assertIn("line-49", str(caught.exception))
            report = worktree / "enclosure" / "reports" / "test-results.md"
            self.assertIn(report.as_posix(), str(caught.exception))
            report_text = report.read_text(encoding="utf-8")
            self.assertIn("- Status: **failed**", report_text)
            self.assertIn("    line-0", report_text)
            self.assertIn("    line-49", report_text)
