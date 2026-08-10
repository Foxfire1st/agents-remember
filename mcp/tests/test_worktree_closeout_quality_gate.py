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

from agents_remember.code_quality import check as quality_check
from agents_remember.kernel.git_command import GIT_REPOSITORY_SELECTOR_ENV
from agents_remember.worktrees import git_worktree_manager as worktree_manager
from agents_remember.worktrees.modules import closeout as closeout_module
from agents_remember.worktrees.modules import code_quality_gate
from agents_remember.worktrees.modules.git import commit_if_dirty
from agents_remember.worktrees.worktree_contract import (
    ContractTask,
    LeafIdentity,
    RepoBranchPlan,
    default_contract,
    default_series_contract,
    load_contract,
    write_contract,
)
from test_worktree_support import (
    closeout_args,
    dirty_open_external_contract_fixture,
    git,
    init_repo,
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
                    worktree, diff_base="c1dc5056", runner=runner
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
                    worktree,
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
                    worktree,
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
                    worktree,
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
                    worktree,
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
                    worktree,
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


class CloseoutCodeQualityGateTests(unittest.TestCase):
    def test_memory_preflight_failure_never_starts_the_code_quality_gate(self) -> None:
        """A broken sidecar must abort before Ruff, Pyright, or pytest can start."""
        with tempfile.TemporaryDirectory() as tmp:
            contract = dirty_open_external_contract_fixture(Path(tmp))
            failed_quality = {
                "ok": False,
                "findingCount": 1,
                "findings": [
                    {
                        "code": "citation_claim_reopened",
                        "path": "feature.txt.md",
                        "message": "stale claim",
                    }
                ],
            }

            with (
                mock.patch.object(
                    closeout_module, "requires_strict_code_quality", return_value=True
                ),
                mock.patch.object(
                    closeout_module.worktree_services().memory_quality,
                    "run_check",
                    return_value=failed_quality,
                ),
                mock.patch.object(closeout_module, "run_strict_code_quality_gate") as gate,
                self.assertRaisesRegex(RuntimeError, "citation_claim_reopened"),
            ):
                worktree_manager.command_closeout(closeout_args(contract))

            gate.assert_not_called()
            self.assertEqual(
                git(contract.code_worktree, "rev-parse", "HEAD"), contract.code_base_commit
            )

    def test_preview_advertises_memory_preflight_before_code_quality(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            contract = dirty_open_external_contract_fixture(Path(tmp))
            _checkout_with_wrapper(contract.code_worktree)
            output = io.StringIO()

            with redirect_stdout(output):
                self.assertEqual(
                    worktree_manager.command_closeout(closeout_args(contract, dry_run=True)),
                    0,
                )

            payload = json.loads(output.getvalue())
            order = payload["closeout_order"]
            self.assertLess(
                order.index("run-working-tree-memory-quality-preflight-before-code-quality"),
                order.index("run-strict-code-quality-over-that-staged-content"),
            )
            self.assertIn("before Pyright or pytest", payload["summary"])

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
            gate_run.assert_called_once_with(
                contract.code_worktree,
                diff_base=contract.code_base_commit,
                plan=code_quality_gate.QualityGatePlan(mode=code_quality_gate.GATE_TARGETED),
            )

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

            def run_gate(
                _worktree: Path,
                *,
                diff_base: str = "",
                plan: code_quality_gate.QualityGatePlan | None = None,
            ) -> dict[str, object]:
                events.append("quality")
                return {
                    "required": True,
                    "passed": True,
                    "command": "python -m agents_remember.code_quality.check --targeted",
                    "diffBase": diff_base,
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


CREATED_FILE = "pkg/leaf_addition.py"


def _gate_scope_contract_fixture(root: Path):
    """A leaf whose closeout exercises exactly one thing: what the gate is shown.

    Internal memory mode keeps the sidecar, ledger and memory-quality machinery out of
    the way. The base commit already carries everything ``derive_scope`` needs to answer
    -- a tracked top-level package, a ``pyproject.toml`` declaring ``testpaths``, and the
    quality wrapper whose presence is what makes the gate mandatory -- so the only thing
    that differs between the base commit and the closeout is the file the leaf creates.
    """
    code_repo = root / "repo-a"
    init_repo(code_repo, "main")
    _checkout_with_wrapper(code_repo)
    (code_repo / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n', encoding="utf-8"
    )
    (code_repo / "pkg").mkdir()
    (code_repo / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (code_repo / "pkg" / "existing.py").write_text("VALUE = 1\n", encoding="utf-8")
    git(code_repo, "add", "-A")
    git(code_repo, "commit", "-m", "Add package, quality wrapper and pytest config")
    contract = default_contract(
        ContractTask(
            name="Gate Scope Thing",
            repo_name="repo-a",
            coordination_root=root / "ar-coordination",
            workflow_kind="chat-task",
            memory_mode="internal",
        ),
        leaf=LeafIdentity(worktree_name="gate-scope-thing"),
        code=RepoBranchPlan(
            repo_path=code_repo,
            source_branch="main",
            work_branch="ar/gate-scope-thing",
            base_commit=git(code_repo, "rev-parse", "HEAD"),
        ),
    )
    git(
        code_repo,
        "worktree",
        "add",
        "-b",
        contract.code_work_branch,
        str(contract.code_worktree),
        "main",
    )
    write_contract(contract.contract_path, contract)
    return contract


class _ScopeRecordingGate:
    """The wrapper's own scope derivation, handed to the wrapper's own first rail.

    ``derive_scope`` plus ``ruff check <lint_paths>`` is literally the pair
    ``quality_steps`` builds, so this stands in for the whole wrapper without paying for
    pyright and a full pytest run on a throwaway repository. Substituting anything less
    real would miss the defect entirely: it was never in ruff, it was in which files ruff
    was handed, and only the real ``derive_scope`` can be wrong about that.
    """

    def __init__(self) -> None:
        self.lint_paths: list[str] = []

    def __call__(
        self,
        worktree: Path,
        *,
        diff_base: str = "",
        plan: code_quality_gate.QualityGatePlan | None = None,
    ) -> dict[str, object]:
        del plan
        self.lint_paths = quality_check.posix_args(quality_check.derive_scope(worktree).lint_paths)
        completed = subprocess.run(
            [sys.executable, "-m", "ruff", "check", "--no-cache", *self.lint_paths],
            cwd=worktree,
            text=True,
            capture_output=True,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                "strict code-quality gate failed before code commit with exit code "
                f"{completed.returncode}.\nQuality output tail:\n{completed.stdout}"
            )
        return {"required": True, "passed": True, "command": "ruff", "diffBase": diff_base}


class CloseoutGateSeesCreatedFilesTests(unittest.TestCase):
    """A file the leaf creates must be inspected, not just the ones it edits.

    ``derive_scope`` picks what ruff and pyright are given with ``git ls-files``, which
    reads the index; ``diff_coverage`` diffs the base against the tracked tree, which is
    blind to the same files; and closeout commits with ``git add -A``. Everything in that
    gap -- every path the task created and never staged -- used to go into the commit with
    no rail of the gate having read a line of it, while the gate reported green. Leaf 3's
    ``abc7cbcc`` shipped four files that way. These tests fail against that closeout.
    """

    def test_a_created_file_carrying_a_lint_error_fails_the_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            contract = _gate_scope_contract_fixture(Path(tmp))
            (contract.code_worktree / CREATED_FILE).write_text("import os\n", encoding="utf-8")
            gate = _ScopeRecordingGate()

            with (
                mock.patch.object(
                    closeout_module, "run_strict_code_quality_gate", side_effect=gate
                ),
                self.assertRaises(RuntimeError) as caught,
            ):
                worktree_manager.command_closeout(closeout_args(contract))

            message = str(caught.exception)
            self.assertIn("strict code-quality gate failed before code commit", message)
            # The gate did not merely fail: it failed *on this file*, having read it.
            self.assertIn(CREATED_FILE, gate.lint_paths)
            self.assertIn(f"{CREATED_FILE}:1:", message)
            self.assertIn("F401", message)
            self.assertEqual(
                git(contract.code_worktree, "rev-parse", "HEAD"), contract.code_base_commit
            )
            self.assertEqual(load_contract(contract.contract_path).closeout_status, "not-started")

    def test_a_refused_gate_commits_nothing_and_leaves_the_worktree_staged(self) -> None:
        """The promise is "nothing was committed", not "nothing was staged".

        Closeout stages the task's own worktree and does not put it back, so this asserts
        the end state the documentation now describes rather than the rollback an earlier
        attempt tried to guarantee. What has to hold is that no commit was created and the
        contract did not advance -- and that the staging left behind is exactly the content
        a retry would stage anyway, which is what makes leaving it harmless.
        """
        with tempfile.TemporaryDirectory() as tmp:
            contract = _gate_scope_contract_fixture(Path(tmp))
            (contract.code_worktree / CREATED_FILE).write_text("import os\n", encoding="utf-8")
            (contract.code_worktree / "pkg" / "existing.py").write_text(
                "VALUE = 2\n", encoding="utf-8"
            )

            with (
                mock.patch.object(
                    closeout_module,
                    "run_strict_code_quality_gate",
                    side_effect=_ScopeRecordingGate(),
                ),
                self.assertRaises(RuntimeError),
            ):
                worktree_manager.command_closeout(closeout_args(contract))

            self.assertEqual(
                git(contract.code_worktree, "rev-parse", "HEAD"), contract.code_base_commit
            )
            self.assertEqual(load_contract(contract.contract_path).closeout_status, "not-started")
            staged = git(contract.code_worktree, "write-tree")
            self.assertIn(CREATED_FILE, git(contract.code_worktree, "ls-files"))
            # A further `add -A` reaches the same tree, so `commit_if_dirty`'s own add adds
            # nothing to what the gate certified. That a *retry* reaches the same tree is a
            # stronger claim, it does not follow from this one, and it is what
            # RetryStagesWhatAFirstRunWouldTests below establishes.
            git(contract.code_worktree, "add", "-A")
            self.assertEqual(git(contract.code_worktree, "write-tree"), staged)

    def test_the_gates_scope_is_the_commits_content(self) -> None:
        """The invariant the fix establishes, asserted as an equality rather than trusted.

        The deletion is here because the index cut both ways. A path the leaf *removed*
        stayed in ``git ls-files`` until something staged the removal, so the pre-fix gate
        handed ruff a file that no longer existed and took an ``E902 No such file or
        directory`` for it -- a failure with nothing wrong behind it, the exact mirror of
        the created file it never looked at. One equality covers both directions.
        """
        with tempfile.TemporaryDirectory() as tmp:
            contract = _gate_scope_contract_fixture(Path(tmp))
            (contract.code_worktree / CREATED_FILE).write_text("VALUE = 2\n", encoding="utf-8")
            (contract.code_worktree / "pkg" / "existing.py").unlink()
            gate = _ScopeRecordingGate()

            with (
                mock.patch.object(
                    closeout_module, "run_strict_code_quality_gate", side_effect=gate
                ),
                redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(worktree_manager.command_closeout(closeout_args(contract)), 0)

            committed = git(
                contract.code_worktree, "ls-tree", "-r", "--name-only", "HEAD"
            ).splitlines()
            self.assertIn(CREATED_FILE, committed)
            self.assertNotIn("pkg/existing.py", committed)
            self.assertEqual(
                sorted(gate.lint_paths),
                sorted(path for path in committed if path.endswith(".py")),
            )


GATE_REFUSAL = "strict code-quality gate failed before code commit with exit code 1"


def _refusing_gate(message: str = GATE_REFUSAL):
    return mock.patch.object(
        closeout_module, "run_strict_code_quality_gate", side_effect=RuntimeError(message)
    )


def _task_worktree(root: Path) -> tuple[Path, Path]:
    """A repository and a linked worktree off it, which is the shape closeout runs in.

    ``(repository checkout, task worktree)``. Both are real: the precondition under test
    is git's own distinction between the two, so a fixture that faked it would be testing
    the fixture.
    """
    repo = root / "repo"
    init_repo(repo, "main")
    (repo / "tracked.txt").write_text("one\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "Add a tracked file")
    worktree = root / "task-worktree"
    git(repo, "worktree", "add", "-b", "ar/task", str(worktree), "main")
    return repo, worktree


def _conflicted_task_worktree(root: Path) -> Path:
    repo, worktree = _task_worktree(root)
    git(repo, "checkout", "-b", "side")
    (repo / "tracked.txt").write_text("side\n", encoding="utf-8")
    git(repo, "commit", "-am", "Change tracked.txt on side")
    git(repo, "checkout", "main")
    (worktree / "tracked.txt").write_text("task\n", encoding="utf-8")
    git(worktree, "commit", "-am", "Change tracked.txt on the task branch")
    subprocess.run(
        ["git", "merge", "side"], cwd=worktree, capture_output=True, text=True, check=False
    )
    return worktree


class TaskWorktreePreconditionTests(unittest.TestCase):
    """Closeout stages, so it must first establish that staging here is free.

    Staging is safe in a task worktree because that checkout is disposable scratch space
    with nobody in it -- ``worktree_start`` makes it and ``lifecycle_finalize_task``
    destroys it. It is not safe in a repository's own checkout, and closeout can be handed
    one: ``default_series_contract`` records ``code_worktree=code.repo_path`` for a
    ``kind: "series"`` contract, and nothing else on the apply path would stop it.

    The guard tests git's own definition of a linked worktree -- ``--git-dir`` differing
    from ``--git-common-dir`` -- rather than the contract's ``kind``, because that is the
    property the safety argument actually rests on. ``kind`` is a label beside the path;
    the git-dir comparison constrains the path that is about to be written.
    """

    def test_the_repositorys_own_checkout_is_refused_before_anything_is_staged(self) -> None:
        """Asserted as the damage that does not happen, not merely as a message.

        Measured on git 2.43 with this guard removed: ``git add -A`` here rewrites the
        staged ``t.txt`` from the ``add -p`` selection (``one\\ntwo``) to the working-tree
        version, and stages ``secret.env`` -- writing a durable blob for a file the person
        deliberately left untracked. Both are unrecoverable from git alone.
        """
        with tempfile.TemporaryDirectory() as tmp:
            repo, _worktree = _task_worktree(Path(tmp))
            # A partial `git add -p` selection: index and working tree deliberately differ.
            (repo / "tracked.txt").write_text("one\ntwo\n", encoding="utf-8")
            git(repo, "add", "tracked.txt")
            (repo / "tracked.txt").write_text("one\ntwo\nthree\n", encoding="utf-8")
            (repo / "secret.env").write_text("TOKEN=REAL\n", encoding="utf-8")
            status_before = git(repo, "status", "--porcelain")

            with (
                mock.patch.object(closeout_module, "run_strict_code_quality_gate") as gate,
                self.assertRaises(RuntimeError) as caught,
            ):
                closeout_module._gate_staged_code(repo, diff_base="HEAD")

            # The selection survives, and the untracked secret is still untracked with no
            # object written for it.
            self.assertEqual(git(repo, "show", ":tracked.txt"), "one\ntwo")
            self.assertEqual(git(repo, "ls-files", "--", "secret.env"), "")
            self.assertEqual(git(repo, "status", "--porcelain"), status_before)
            gate.assert_not_called()
            message = str(caught.exception)
            self.assertIn("is not a task worktree", message)
            self.assertIn("Nothing was staged and nothing was committed", message)

    def test_a_series_contracts_code_worktree_is_exactly_that_checkout(self) -> None:
        """The refusal above is aimed at a contract the system really can produce.

        Without this, the guard is a guess about a shape nobody builds. ``kind: "series"``
        records the repository path itself, so it is the concrete way a closeout could have
        reached a checkout a person works in.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo, _worktree = _task_worktree(root)
            series = default_series_contract(
                ContractTask(
                    name="Series Thing",
                    repo_name="repo",
                    coordination_root=root / "ar-coordination",
                    workflow_kind="chat-task",
                    memory_mode="internal",
                ),
                code=RepoBranchPlan(
                    repo_path=repo,
                    source_branch="main",
                    work_branch="ar/series-thing",
                    base_commit=git(repo, "rev-parse", "HEAD"),
                ),
            )

            self.assertEqual(series.kind, "series")
            self.assertEqual(series.code_worktree, repo)
            with self.assertRaises(RuntimeError) as caught:
                closeout_module._gate_staged_code(series.code_worktree, diff_base="HEAD")
            self.assertIn("is not a task worktree", str(caught.exception))

    def test_a_task_worktree_passes_the_precondition_and_is_staged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _repo, worktree = _task_worktree(Path(tmp))
            (worktree / "created.py").write_text("VALUE = 1\n", encoding="utf-8")
            verdict = {"required": True, "passed": True}

            with mock.patch.object(
                closeout_module, "run_strict_code_quality_gate", return_value=verdict
            ):
                self.assertEqual(
                    closeout_module._gate_staged_code(worktree, diff_base="HEAD"), verdict
                )

            self.assertIn("created.py", git(worktree, "ls-files"))

    def test_a_refused_gate_leaves_the_task_worktree_staged(self) -> None:
        """No rollback, stated as a test rather than left to be discovered.

        An earlier attempt saved the index file aside and copied it back. That machinery is
        gone: there is no snapshot to orphan, no ``index.lock`` to leave stale, and nothing
        that has to run at exit for the checkout to be in a sane state.
        """
        with tempfile.TemporaryDirectory() as tmp:
            _repo, worktree = _task_worktree(Path(tmp))
            (worktree / "created.py").write_text("VALUE = 1\n", encoding="utf-8")

            with _refusing_gate(), self.assertRaises(RuntimeError):
                closeout_module._gate_staged_code(worktree, diff_base="HEAD")

            self.assertIn("created.py", git(worktree, "ls-files"))
            git_dir = Path(git(worktree, "rev-parse", "--path-format=absolute", "--git-dir"))
            self.assertFalse((git_dir / "index.lock").exists())
            self.assertEqual(sorted(git_dir.glob("ar-closeout-index-*")), [])


class ConflictedIndexTests(unittest.TestCase):
    """A conflicted worktree fails cleanly instead of committing the markers.

    ``git add -A`` over an unmerged index does not refuse -- it resolves every conflict to
    whatever the working tree holds, markers included, and closeout then commits that. The
    refusal is deliberate, is checked before anything is staged, and says what state the
    checkout is in rather than reporting plumbing from a command nobody ran.
    """

    def test_a_conflicted_worktree_is_refused_before_anything_is_staged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            worktree = _conflicted_task_worktree(Path(tmp))
            head_before = git(worktree, "rev-parse", "HEAD")
            status_before = git(worktree, "status", "--porcelain")
            self.assertIn("<<<<<<<", (worktree / "tracked.txt").read_text(encoding="utf-8"))

            with (
                mock.patch.object(closeout_module, "run_strict_code_quality_gate") as gate,
                self.assertRaises(RuntimeError) as caught,
            ):
                closeout_module._gate_staged_code(worktree, diff_base="HEAD")

            message = str(caught.exception)
            self.assertIn("closeout cannot stage the code worktree", message)
            self.assertIn("unmerged path", message)
            self.assertIn("tracked.txt", message)
            self.assertIn("conflict markers", message)
            gate.assert_not_called()
            self.assertEqual(git(worktree, "rev-parse", "HEAD"), head_before)
            self.assertEqual(git(worktree, "status", "--porcelain"), status_before)

    def test_the_reset_runs_after_the_conflict_check_not_before_it(self) -> None:
        """Order, asserted through what survives rather than through call bookkeeping.

        A mixed reset drops the unmerged index entries and removes ``MERGE_HEAD``. Run
        before the check, it would leave ``diff --diff-filter=U`` with nothing to report,
        the refusal would never fire again, and ``add -A`` would go on to stage the
        ``<<<<<<<`` markers. So the merge being intact after the refusal is the property
        that says the reset has not run yet -- and it is the property that keeps the
        refusal above from quietly becoming unreachable.
        """
        with tempfile.TemporaryDirectory() as tmp:
            worktree = _conflicted_task_worktree(Path(tmp))
            git_dir = Path(git(worktree, "rev-parse", "--path-format=absolute", "--git-dir"))
            self.assertTrue((git_dir / "MERGE_HEAD").exists())

            with (
                mock.patch.object(closeout_module, "run_strict_code_quality_gate") as gate,
                self.assertRaises(RuntimeError),
            ):
                closeout_module._gate_staged_code(worktree, diff_base="HEAD")

            gate.assert_not_called()
            self.assertTrue((git_dir / "MERGE_HEAD").exists())
            self.assertEqual(git(worktree, "diff", "--name-only", "--diff-filter=U"), "tracked.txt")


DROPPED_TOOL_ARTEFACT = ".dmypy.json"


class RetryStagesWhatAFirstRunWouldTests(unittest.TestCase):
    """A refused attempt must not decide what the next attempt commits.

    ``git add -A`` applies ignore rules only to paths git does not already track or hold
    staged. A file staged by a refused gate therefore stays staged after the leaf adds it to
    ``.gitignore``, and the retry commits it -- which is exactly how a ``.dmypy.json`` a type
    checker had dropped in the worktree got into this leaf's own first commit. The mixed
    reset is what removes that path dependence.

    The property is asserted as an equality of committed trees rather than as the presence
    of a ``reset`` call: what has to hold is that a retry commits what a worktree that never
    saw the refusal commits. Both sides run the same closeout steps against the same end
    state on disk, so the only thing that can make the trees differ is history the index
    carried across attempts.
    """

    @staticmethod
    def _end_state(worktree: Path) -> None:
        """The files as the leaf leaves them once it has ignored the tool's artefact."""
        (worktree / "a.py").write_text("VALUE = 2\n", encoding="utf-8")
        (worktree / DROPPED_TOOL_ARTEFACT).write_text('{"pid": 1}\n', encoding="utf-8")
        (worktree / ".gitignore").write_text(f"{DROPPED_TOOL_ARTEFACT}\n", encoding="utf-8")

    def _gate_then_commit(self, worktree: Path, message: str) -> str:
        with mock.patch.object(
            closeout_module,
            "run_strict_code_quality_gate",
            return_value={"required": True, "passed": True},
        ):
            closeout_module._gate_staged_code(worktree, diff_base="HEAD")
        commit_if_dirty(worktree, message)
        return git(worktree, "rev-parse", "HEAD^{tree}")

    def test_a_retry_commits_the_tree_a_first_run_would(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo, retried = _task_worktree(root)
            fresh = root / "fresh-worktree"
            git(repo, "worktree", "add", "-b", "ar/fresh", str(fresh), "main")

            # Attempt one: the artefact is on disk and not yet ignored, and the gate refuses.
            (retried / "a.py").write_text("VALUE = 2\n", encoding="utf-8")
            (retried / DROPPED_TOOL_ARTEFACT).write_text('{"pid": 1}\n', encoding="utf-8")
            with _refusing_gate(), self.assertRaises(RuntimeError):
                closeout_module._gate_staged_code(retried, diff_base="HEAD")
            self.assertIn(DROPPED_TOOL_ARTEFACT, git(retried, "ls-files"))

            # The leaf adds the ignore rule and retries, against a worktree still staged.
            self._end_state(retried)
            retried_tree = self._gate_then_commit(retried, "Closeout on the retry")

            # The same end state, closed out once, in a worktree that never saw the refusal.
            self._end_state(fresh)
            fresh_tree = self._gate_then_commit(fresh, "Closeout on a first run")

            self.assertEqual(retried_tree, fresh_tree)
            self.assertNotIn(
                DROPPED_TOOL_ARTEFACT,
                git(retried, "ls-tree", "-r", "--name-only", "HEAD").splitlines(),
            )
