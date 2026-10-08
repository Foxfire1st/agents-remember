"""Wave 2 (L16-R6/R7/R8/R9): branch-addressed direct execution and error dialect.

Covers the policy-gated series-contract binding for ``record_route_review``
(R6), the lock-serialized ``direct_landing`` operation with its pre-commit staged-
candidate gate (R7/R8), and the contract-bound refusal dialect (R9). Uses real
scratch git repos and a synthetic coordination root -- never the live tree.
"""

from __future__ import annotations

import json
import shlex
import subprocess
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from agents_remember.application.knowledge_gate import KnowledgeGate
from agents_remember.application.lifecycle.direct_landing import direct_landing_tool
from agents_remember.application.worktree_services import build_default_worktree_services
from agents_remember.kernel.git_command import run_git
from agents_remember.kernel.memory_cache import derive_memory_ledger
from agents_remember.kernel.memory_ledger import create_initial_ledger, write_ledger
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig, load_config
from agents_remember.models.direct_landing import DirectLandingResponse
from agents_remember.models.lifecycles.direct_landing import DirectLandingOperationInput
from agents_remember.worktrees import direct_landing as direct_landing_owner
from agents_remember.worktrees.direct_landing import (
    DirectLandingError,
    DirectLandingRequest,
)
from agents_remember.worktrees.direct_landing import (
    direct_landing as _production_direct_landing,
)
from agents_remember.worktrees.integration.direct_landing import direct_landing_execution
from agents_remember.worktrees.integration.direct_landing.direct_landing_operation import (
    DirectLandingRuntime,
    direct_landing_store,
)
from agents_remember.worktrees.integration.direct_landing.direct_landing_recovery_state import (
    cancelled_unpublished,
    classify_direct_landing_recovery,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_control_errors import (
    LifecycleControlError,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_location import (
    publish_new_lifecycle_operation_location,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_recovery import (
    recover_direct_landing_under_authority,
)
from agents_remember.worktrees.knowledge_gate import (
    direct_closing_receipt,
    keep_direct_closing,
    settle_direct_closing,
)
from agents_remember.worktrees.modules import git as git_owner
from agents_remember.worktrees.modules.closeout_external import require_gated_recovery
from agents_remember.worktrees.modules.git import (
    head_commit,
    require_git,
)
from agents_remember.worktrees.services import (
    bind_worktree_services,
    reset_worktree_services,
)
from agents_remember.worktrees.worktree_contract import (
    ContractTask,
    RepoBranchPlan,
    default_series_contract,
    write_contract,
)
from test_knowledge_closeout_gate import (
    CODE_A,
    LEAF,
    TRACES,
    A,
    _answered,
    build_gated,
    commit,
    family_row,
    invariant,
    trace_rows,
    write,
)
from test_worktree_support import git, init_repo


def direct_landing(*args, **kwargs):
    """Exercise the production direct-landing path in process."""

    return _production_direct_landing(*args, **kwargs)


def _scratch_config(
    root: Path,
    code: Path,
    memory: Path | None,
    *,
    direct_execution_enabled: bool = True,
) -> McpRuntimeConfig:
    configured_code = root / "repo-a"
    if not configured_code.exists():
        configured_code.symlink_to(code, target_is_directory=True)
    if memory is not None:
        configured_memory = root / "coord" / "memory-repos" / "ar-repo-a"
        configured_memory.parent.mkdir(parents=True, exist_ok=True)
        if not configured_memory.exists():
            configured_memory.symlink_to(memory, target_is_directory=True)
    config_path = root / (
        "settings.json" if direct_execution_enabled else "settings-direct-disabled.json"
    )
    config_path.write_text(
        json.dumps(
            {
                "version": 1,
                "coordinationRoot": (root / "coord").as_posix(),
                "workspaceRoot": root.as_posix(),
                "repositories": {"repo-a": {}},
                "directExecutionEnabled": direct_execution_enabled,
            }
        ),
        encoding="utf-8",
    )
    return load_config(config_path)


def _series_fixture(root: Path, *, code_commit_message: str = "code commit") -> dict:
    """A task-root series contract over a real code + memory repo pair."""
    coord = root / "coord"
    tasks = coord / "tasks" / "repo-a" / "direct-task"
    tasks.mkdir(parents=True)
    code = root / "code"
    memory = coord / "memory-repos" / "ar-repo-a"
    code_base = init_repo(code, "main")
    git(code, "checkout", "-b", "ar/direct-task", "main")
    (code / "feature.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    git(code, "add", "-A")
    git(code, "commit", "-m", code_commit_message)
    code_head = git(code, "rev-parse", "HEAD")
    git(code, "checkout", "main")
    git(code, "branch", "super", "main")

    init_repo(memory, "main")
    git(memory, "checkout", "-b", "ar/direct-task", "main")
    write_ledger(
        memory / "memory.md",
        create_initial_ledger("repo-a", code_base, head_commit(memory)),
    )
    git(memory, "add", "memory.md")
    git(memory, "commit", "-m", "seed ledger")
    memory_base = head_commit(memory)

    task = ContractTask(
        name="direct-task",
        repo_name="repo-a",
        coordination_root=coord,
        workflow_kind="light-task",
        memory_mode="external",
    )
    contract = default_series_contract(
        task,
        code=RepoBranchPlan(
            repo_path=code,
            source_branch="super",
            work_branch="ar/direct-task",
            base_commit=code_base,
        ),
        memory=RepoBranchPlan(
            repo_path=memory,
            source_branch="main",
            work_branch="ar/direct-task",
            base_commit=memory_base,
        ),
    )
    write_contract(contract.contract_path, contract)
    publish_new_lifecycle_operation_location(
        contract,
        contract_text=contract.contract_path.read_text(encoding="utf-8"),
    )
    return {
        "config": _scratch_config(root, code, memory),
        "contract": contract,
        "code": code,
        "memory": memory,
        "code_head": code_head,
        "candidate_tree": require_git(code, ["rev-parse", f"{code_head}^{{tree}}"]),
        "tasks": tasks,
    }


def _byte_tree(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _converted_series_fixture(root: Path) -> dict:
    """A registered direct series over the real converted gate world."""
    world = build_gated(root / "coord")
    tasks = root / "coord" / "tasks" / "repo-a" / "direct-task"
    tasks.mkdir(parents=True)
    world.task_root = tasks
    world.task_document(repo="repo-a")
    contract = default_series_contract(
        ContractTask("direct-task", "repo-a", root / "coord", "light-task", "external"),
        code=RepoBranchPlan(world.code, "main", "leaf", world.code_base),
        memory=RepoBranchPlan(world.memory, "main", "leaf", world.memory_base),
    )
    write_contract(contract.contract_path, contract)
    publish_new_lifecycle_operation_location(
        contract, contract_text=contract.contract_path.read_text(encoding="utf-8")
    )
    return {
        "world": world,
        "config": _scratch_config(root, world.code, world.memory),
        "contract": contract,
        "memory": world.memory,
        "code": world.code,
    }


def _install_failing_commit_hooks(repository: Path, log: Path) -> None:
    """Real ``pre-commit`` and ``commit-msg`` hooks that record their run in ``log`` and fail."""

    hooks = Path(git(repository, "rev-parse", "--path-format=absolute", "--git-path", "hooks"))
    hooks.mkdir(parents=True, exist_ok=True)
    for name in ("pre-commit", "commit-msg"):
        hook = hooks / name
        hook.write_text(
            f"#!/bin/sh\necho {name} >> {shlex.quote(log.as_posix())}\nexit 97\n",
            encoding="utf-8",
        )
        hook.chmod(0o755)
    assert run_git(repository, ["hook", "run", "pre-commit"]).returncode == 97  # the hook is live
    log.unlink()


UNPUBLISHED = {
    # Where a landing is stopped before anything is published, and the status it answers.
    "moved": "direct-landing-memory-publication-refused",
    "moved-late": "direct-landing-memory-publication-refused",
    "switched": "direct-landing-memory-publication-refused",
    # The branch moves after the publication's own tip check: the stricter check sees it.
    "moved-at-staging": "direct-landing-memory-output-ambiguous",
    # The commit object cannot be written (a failing signer): a failure, not a refusal.
    "failed": "direct-landing-interrupted-unpublished",
}
LATE_CHANGES = {
    # A change after the generation is admitted (before its execution begins), after its commit
    # intent is journaled, or after its exact tree is staged: the patched owner and the status.
    "admission": ("execute_or_require_direct_landing_recovery", "memory-evidence-conflict"),
    "intent": ("begin_git_mutation", "memory-output-ambiguous"),
    "staging": ("stage_tree", "memory-output-ambiguous"),
}


def _foreign_commit(memory: Path) -> None:
    """Another session's commit on the memory branch (it keeps the ``Code-Commit`` trailer)."""

    paired = git(memory, "log", "-1", "--format=%B")
    foreign = git(memory, "commit-tree", "HEAD^{tree}", "-p", "HEAD", "-m", paired)
    git(memory, "update-ref", "HEAD", foreign)


def _race_before_publication(case: str, memory: Path) -> mock._patch:
    branch = git(memory, "symbolic-ref", "HEAD")
    published = direct_landing_execution.publish_tree_commit
    fired: list[int] = []

    def before_publication(*args, **kwargs):
        if not fired and case == "moved":
            _foreign_commit(memory)
        elif not fired:
            git(memory, "symbolic-ref", "HEAD", "refs/heads/elsewhere")
        fired.append(1)
        try:
            return published(*args, **kwargs)
        finally:
            git(memory, "symbolic-ref", "HEAD", branch)

    return mock.patch.object(direct_landing_execution, "publish_tree_commit", before_publication)


def _race_at_the_commit_object(case: str, memory: Path) -> mock._patch:
    native = git_owner.run_git
    fired: list[int] = []

    def at_the_commit_object(repo, args, *extra):
        first = args[0] == "commit-tree" and not fired
        if first:
            fired.append(1)
        if first and case == "failed":
            return subprocess.CompletedProcess(args, 128, "", "error: gpg failed to sign")
        result = native(repo, args, *extra)
        if first:  # between the commit object and the ref
            _foreign_commit(memory)
        return result

    return mock.patch.object(git_owner, "run_git", at_the_commit_object)


def _race_after_staging(memory: Path) -> mock._patch:
    staged = direct_landing_execution.stage_tree
    fired: list[int] = []

    def after_staging(*args, **kwargs):
        staged(*args, **kwargs)
        if not fired:  # after the publication's tip check, before its stricter check
            fired.append(1)
            _foreign_commit(memory)

    return mock.patch.object(direct_landing_execution, "stage_tree", after_staging)


def _publication_race(case: str, memory: Path) -> mock._patch:
    """Patch that stops the landing at one seam of its publication (:data:`UNPUBLISHED`)."""

    if case in {"moved-late", "failed"}:
        return _race_at_the_commit_object(case, memory)
    if case == "moved-at-staging":
        return _race_after_staging(memory)
    return _race_before_publication(case, memory)


def _late_change(boundary: str, memory: Path) -> mock._patch:
    """Patch that edits ``onboarding/pkg/a.py.md`` once, at one boundary of :data:`LATE_CHANGES`."""

    module = direct_landing_owner if boundary == "admission" else direct_landing_execution
    owner = LATE_CHANGES[boundary][0]
    original = getattr(module, owner)
    path = memory / "onboarding/pkg/a.py.md"
    fired: list[bool] = []

    def late_change() -> None:
        if fired:
            return
        fired.append(True)
        if boundary == "staging":
            path.write_text("late staged edit after exact staging\n")
            git(memory, "add", "--", "onboarding/pkg/a.py.md")
            path.write_text("# a\n")
        else:
            path.write_text("late worktree edit after admission\n")

    def change_at_boundary(*args, **kwargs):
        if boundary == "admission":
            late_change()
        result = original(*args, **kwargs)
        late_change()
        return result

    return mock.patch.object(module, owner, change_at_boundary)


def _kept_closings(contract) -> list[str]:
    """The closing receipts direct landing still keeps for the series (one per generation)."""

    return sorted(path.name for path in direct_closing_receipt(contract, "").parent.glob("*.json"))


def _converted_request(fixture: dict, code_commit: str) -> DirectLandingRequest:
    return DirectLandingRequest(
        contract_path=fixture["contract"].contract_path.as_posix(),
        code_commit=code_commit,
        candidate_tree=require_git(fixture["code"], ["rev-parse", f"{code_commit}^{{tree}}"]),
        memory_commit_message="publish exact converted tree",
        intent_note="fixture approval",
    )


class DirectLandingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        reset_worktree_services()
        self.temp.cleanup()

    def test_converted_direct_landing_judges_closed_tree_and_refuses_late_memory_change(
        self,
    ) -> None:
        for change in ("worktree", "index", "history"):
            with self.subTest(change=change):
                self._assert_late_memory_change_refused(change)

    def _assert_late_memory_change_refused(self, change: str) -> None:
        fixture = _converted_series_fixture(Path(self.temp.name) / change)
        world = fixture["world"]
        code = commit(world.code, {A: CODE_A.replace("return value", "return -value")})
        _answered(world, world.reanchor("RLZ-A00001"), *trace_rows(*TRACES))
        history = world.memory / f"knowledge/history/{LEAF}.json"
        original_history = history.read_bytes()
        memory_head = head_commit(world.memory)
        index = Path(require_git(world.memory, ["rev-parse", "--git-path", "index"]))
        if not index.is_absolute():
            index = world.memory / index
        original_index = index.read_bytes()
        bind_worktree_services(build_default_worktree_services())
        judge = KnowledgeGate.direct_verdict
        calls = []

        def mutate_after_judgment(self, contract, **kwargs):
            tree = kwargs["memory_tree"]
            closed = json.loads(git(world.memory, "show", f"{tree}:knowledge/history/{LEAF}.json"))
            self_test.assertTrue(closed["closed"])
            self_test.assertIn("/memory.md", git(world.memory, "show", f"{tree}:.gitignore"))
            result = judge(self, contract, **kwargs)
            self_test.assertIsNone(result.refusal)
            calls.append(tree)
            if change == "worktree":
                (world.memory / "onboarding/late.md").write_text("late unjudged bytes\n")
            elif change == "history":
                history.write_bytes(b"late unjudged history edit\n")
            else:
                path = world.memory / "onboarding/pkg/a.py.md"
                path.write_text("index-only unjudged bytes\n")
                git(world.memory, "add", "--", "onboarding/pkg/a.py.md")
                path.write_text("# a\n")
            return result

        self_test = self
        with mock.patch.object(KnowledgeGate, "direct_verdict", mutate_after_judgment):
            result = direct_landing_tool(fixture["config"], _converted_request(fixture, code))
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["status"], "direct-landing-memory-candidate-changed")
        self.assertEqual(len(calls), 1)
        self.assertEqual(head_commit(world.memory), memory_head)
        self.assertEqual(
            history.read_bytes(),
            b"late unjudged history edit\n" if change == "history" else original_history,
        )
        self.assertFalse((world.memory / ".gitignore").exists())
        self.assertIsNone(direct_landing_store(fixture["contract"]).read())
        if change == "worktree":
            self.assertEqual(index.read_bytes(), original_index)
            self.assertEqual(
                (world.memory / "onboarding/late.md").read_text(), "late unjudged bytes\n"
            )
        elif change == "index":
            self.assertEqual(
                git(world.memory, "show", ":onboarding/pkg/a.py.md"), "index-only unjudged bytes"
            )

    def test_converted_direct_landing_commits_the_judged_tree_and_requires_governing_row(
        self,
    ) -> None:
        fixture = _converted_series_fixture(Path(self.temp.name) / "governing")
        world = fixture["world"]
        code = commit(world.code, {A: CODE_A.replace("return value", "return -value")})
        before = world.reanchor("RLZ-A00001")
        write(
            world.memory,
            {
                "knowledge/invariants/INV-AAAAAA-land.json": invariant(
                    "INV-AAAAAA", "Restated guarantee.", revision=2
                )
            },
        )
        row = world.invariant_row("INV-AAAAAA", before, revision=2)
        family = family_row({"INV-AAAAAA": 2, "INV-BBBBBB": 1})
        world.rows(row, family, *trace_rows(*TRACES))
        bind_worktree_services(build_default_worktree_services())
        # No lifecycle publication runs a commit hook, on any route (review R3, note 11 f): the
        # memory repository's failing ``pre-commit`` and ``commit-msg`` hooks are never run, and
        # the landing below succeeds. The checks of a publication are the gate and the validator.
        hook_log = Path(self.temp.name) / "memory-commit-hooks.log"
        _install_failing_commit_hooks(world.memory, hook_log)
        request = _converted_request(fixture, code)
        refused = direct_landing_tool(fixture["config"], request)
        self.assertFalse(refused["ok"], refused)
        self.assertIn("governing row", str(refused["detail"]))
        world.rows(
            {**row, "disposition": "changed", "effect": "clarify"}, family, *trace_rows(*TRACES)
        )
        judge = KnowledgeGate.direct_verdict
        judged = []

        def remember(self, contract, **kwargs):
            result = judge(self, contract, **kwargs)
            judged.append(kwargs["memory_tree"])
            return result

        with mock.patch.object(KnowledgeGate, "direct_verdict", remember):
            landed = direct_landing_tool(fixture["config"], request)
        self.assertTrue(landed["ok"], landed)
        self.assertFalse(hook_log.exists())
        self.assertEqual(len(judged), 1)
        self.assertEqual(git(world.memory, "rev-parse", "HEAD^{tree}"), judged[0])
        record = direct_landing_store(fixture["contract"]).read()
        self.assertIsNotNone(record)
        assert record is not None
        assert isinstance(record.input, DirectLandingOperationInput)
        self.assertEqual(record.input.memoryBefore.candidateTree, judged[0])

    def test_converted_direct_landing_prepares_ignore_rule_before_judging_a_clean_checkout(
        self,
    ) -> None:
        fixture = _converted_series_fixture(Path(self.temp.name) / "clean-open")
        world = fixture["world"]
        world.rows()
        git(world.memory, "add", "-A")
        git(world.memory, "commit", "-m", f"open leaf history\n\nCode-Commit: {world.code_base}")
        git(world.code, "commit", "--allow-empty", "-m", "leaf without code changes")
        code = head_commit(world.code)
        self.assertEqual(git(world.memory, "status", "--porcelain"), "")
        bind_worktree_services(build_default_worktree_services())
        landed = direct_landing_tool(fixture["config"], _converted_request(fixture, code))
        self.assertTrue(landed["ok"], landed)
        self.assertIn("/memory.md", git(world.memory, "show", "HEAD:.gitignore"))
        self.assertTrue(
            json.loads(git(world.memory, "show", f"HEAD:knowledge/history/{LEAF}.json"))["closed"]
        )

    def test_converted_direct_landing_rejects_an_asserted_history_owner(self) -> None:
        fixture = _converted_series_fixture(Path(self.temp.name) / "owner")
        world = fixture["world"]
        code = commit(world.code, {A: CODE_A.replace("return value", "return -value")})
        _answered(world, world.reanchor("RLZ-A00001"), *trace_rows(*TRACES))
        history = world.memory / f"knowledge/history/{LEAF}.json"
        before = history.read_bytes()
        memory_head = head_commit(world.memory)
        bind_worktree_services(build_default_worktree_services())
        judge = KnowledgeGate.direct_verdict

        def asserted_owner(self, contract, **kwargs):
            kwargs["source"] = replace(kwargs["source"], owner="260928-MIK-L98")
            return judge(self, contract, **kwargs)

        with mock.patch.object(KnowledgeGate, "direct_verdict", asserted_owner):
            refused = direct_landing_tool(fixture["config"], _converted_request(fixture, code))
        self.assertFalse(refused["ok"], refused)
        self.assertIn("selected history source", str(refused["detail"]))
        self.assertEqual(head_commit(world.memory), memory_head)
        self.assertEqual(history.read_bytes(), before)
        self.assertIsNone(direct_landing_store(fixture["contract"]).read())

    def test_direct_bound_history_source_cannot_be_downgraded_to_a_clean_replay(self) -> None:
        fixture = _converted_series_fixture(Path(self.temp.name) / "source-head")
        world = fixture["world"]
        code = commit(world.code, {A: CODE_A.replace("return value", "return -value")})
        _answered(world, world.reanchor("RLZ-A00001"), *trace_rows(*TRACES))
        history = world.memory / f"knowledge/history/{LEAF}.json"
        before = history.read_bytes()
        bind_worktree_services(build_default_worktree_services())
        select = KnowledgeGate.direct_source
        changed_head = []

        def move_head_after_selection(self, contract, **kwargs):
            result = select(self, contract, **kwargs)
            assert result.source is not None
            git(world.memory, "add", "-A")
            git(
                world.memory,
                "commit",
                "-m",
                f"concurrent original candidate\n\nCode-Commit: {code}",
            )
            changed_head.append(head_commit(world.memory))
            return result

        with mock.patch.object(KnowledgeGate, "direct_source", move_head_after_selection):
            refused = direct_landing_tool(fixture["config"], _converted_request(fixture, code))
        self.assertFalse(refused["ok"], refused)
        self.assertIn("selected history source", str(refused["detail"]))
        self.assertEqual(head_commit(world.memory), changed_head[0])
        self.assertEqual(history.read_bytes(), before)
        self.assertFalse((world.memory / ".gitignore").exists())
        self.assertIsNone(direct_landing_store(fixture["contract"]).read())

    def test_direct_admission_refuses_a_history_edit_before_its_closing_receipt(self) -> None:
        fixture = _converted_series_fixture(Path(self.temp.name) / "receipt-history")
        world = fixture["world"]
        code = commit(world.code, {A: CODE_A.replace("return value", "return -value")})
        _answered(world, world.reanchor("RLZ-A00001"), *trace_rows(*TRACES))
        history = world.memory / f"knowledge/history/{LEAF}.json"
        memory_head = head_commit(world.memory)
        bind_worktree_services(build_default_worktree_services())

        def change_before_receipt(*args, **kwargs):
            history.write_bytes(b"late history edit before admission\n")
            return keep_direct_closing(*args, **kwargs)

        with mock.patch(
            "agents_remember.worktrees.direct_landing.keep_direct_closing", change_before_receipt
        ):
            refused = direct_landing_tool(fixture["config"], _converted_request(fixture, code))
        self.assertFalse(refused["ok"], refused)
        self.assertIn("changed before admission", str(refused["detail"]))
        self.assertEqual(head_commit(world.memory), memory_head)
        self.assertEqual(history.read_bytes(), b"late history edit before admission\n")
        self.assertIsNone(direct_landing_store(fixture["contract"]).read())

    def test_a_late_change_at_the_commit_boundary_refuses_restores_and_the_request_lands(
        self,
    ) -> None:
        """Review R3, finding 4 (ruling of 2026-10-05). A change the stricter check catches after
        admission, when nothing has been published, restores every preparation and cancels its
        generation; the same request then starts a new generation, judges the tree as it is now,
        and lands. Before the ruling the generation stayed ``input-required`` and only its
        recovery action completed."""

        for boundary in LATE_CHANGES:
            with self.subTest(boundary=boundary):
                self._assert_commit_boundary_change_refused(boundary)

    def _assert_commit_boundary_change_refused(self, boundary: str) -> None:
        fixture = _converted_series_fixture(Path(self.temp.name) / boundary)
        world = fixture["world"]
        code = commit(world.code, {A: CODE_A.replace("return value", "return -value")})
        _answered(world, world.reanchor("RLZ-A00001"), *trace_rows(*TRACES))
        memory_head = head_commit(world.memory)
        history = world.memory / f"knowledge/history/{LEAF}.json"
        opened = history.read_bytes()
        index = git(world.memory, "ls-files", "--stage")
        bind_worktree_services(build_default_worktree_services())
        path = world.memory / "onboarding/pkg/a.py.md"
        request = _converted_request(fixture, code)
        store = direct_landing_store(fixture["contract"])
        with _late_change(boundary, world.memory):
            refused = direct_landing_tool(fixture["config"], request)
            self.assertFalse(refused["ok"], refused)
            self.assertEqual(refused["status"], f"direct-landing-{LATE_CHANGES[boundary][1]}")
            self.assertIn("nothing was published", str(refused["detail"]))
            self.assertEqual(refused["nextAction"], "direct-landing")
            self.assertEqual(head_commit(world.memory), memory_head)
            record = store.read()
            assert record is not None
            self.assertEqual((record.status, record.generation), ("cancelled", 1))
            self.assertTrue(cancelled_unpublished(record))
            # Every preparation is back: the history file byte for byte, the ignore rule, the
            # receipt. The index is given back unless it holds someone else's staged edit.
            self.assertEqual(history.read_bytes(), opened)
            self.assertFalse((world.memory / ".gitignore").exists())
            self.assertEqual(_kept_closings(fixture["contract"]), [])
            staged_edit = git(world.memory, "show", ":onboarding/pkg/a.py.md")
            if boundary == "staging":
                self.assertEqual(staged_edit, "late staged edit after exact staging")
            else:
                self.assertEqual(git(world.memory, "ls-files", "--stage"), index)
                self.assertEqual(path.read_text(), "late worktree edit after admission\n")

            landed = direct_landing_tool(fixture["config"], request)  # the same request
        self.assertTrue(landed["ok"], landed)
        record = store.read()
        assert record is not None
        self.assertEqual((record.status, record.generation), ("completed", 2))
        # The new generation judged the tree as it is now: the late working-tree edit is in the
        # commit, the index-only edit (which the working tree never held) is not.
        late = "# a" if boundary == "staging" else "late worktree edit after admission"
        self.assertEqual(git(world.memory, "show", "HEAD:onboarding/pkg/a.py.md"), late)
        self.assertEqual(git(world.memory, "status", "--porcelain"), "")
        closed = json.loads(git(world.memory, "show", f"HEAD:knowledge/history/{LEAF}.json"))
        self.assertTrue(closed["closed"])

    def test_direct_native_commit_keeps_the_judged_tree_during_a_real_index_edit(self) -> None:
        fixture = _converted_series_fixture(Path(self.temp.name) / "command-running")
        world = fixture["world"]
        code = commit(world.code, {A: CODE_A.replace("return value", "return -value")})
        _answered(world, world.reanchor("RLZ-A00001"), *trace_rows(*TRACES))
        bind_worktree_services(build_default_worktree_services())
        native = git_owner.run_git
        calls = []

        def edit_real_index_before_native_commit(repo, args, *extra):
            if args[0] == "commit-tree":
                record = direct_landing_store(fixture["contract"]).read()
                assert record is not None and isinstance(record.input, DirectLandingOperationInput)
                calls.append(record.input.memoryBefore.candidateTree)
                path = repo / "onboarding/pkg/a.py.md"
                path.write_text("user staged edit while native commit starts\n")
                git(repo, "add", "--", "onboarding/pkg/a.py.md")
                path.write_text("# a\n")
            return native(repo, args, *extra)

        with mock.patch.object(git_owner, "run_git", edit_real_index_before_native_commit):
            landed = direct_landing_tool(fixture["config"], _converted_request(fixture, code))
        self.assertTrue(landed["ok"], landed)
        self.assertEqual(len(calls), 1)
        self.assertEqual(git(world.memory, "rev-parse", "HEAD^{tree}"), calls[0])
        self.assertEqual(git(world.memory, "show", "HEAD:onboarding/pkg/a.py.md"), "# a")
        self.assertEqual(
            git(world.memory, "show", ":onboarding/pkg/a.py.md"),
            "user staged edit while native commit starts",
        )
        self.assertEqual((world.memory / "onboarding/pkg/a.py.md").read_text(), "# a\n")

    def test_converted_direct_publication_refuses_a_moved_ref_or_a_switched_head(self) -> None:
        """The publication is one expected-old move of the admitted branch. A branch that moved, a
        HEAD switched to another branch, or a commit object that cannot be written publishes
        nothing and restores every preparation (the history file byte for byte, the ignore rule,
        the index); the generation is cancelled, and the same request lands once the cause is
        gone. A generation stays in flight only when its commit is, or may be, on the branch
        (``test_converted_direct_retry_keeps_the_judged_generation``)."""

        for case in UNPUBLISHED:
            with self.subTest(case=case):
                self._assert_publication_refused(case)

    def _assert_publication_refused(self, case: str) -> None:
        fixture = _converted_series_fixture(Path(self.temp.name) / f"publish-{case}")
        world = fixture["world"]
        code = commit(world.code, {A: CODE_A.replace("return value", "return -value")})
        _answered(world, world.reanchor("RLZ-A00001"), *trace_rows(*TRACES))
        bind_worktree_services(build_default_worktree_services())
        git(world.memory, "branch", "elsewhere")
        history = world.memory / f"knowledge/history/{LEAF}.json"
        opened = history.read_bytes()
        index = git(world.memory, "ls-files", "--stage")
        request = _converted_request(fixture, code)
        with _publication_race(case, world.memory):
            refused = direct_landing_tool(fixture["config"], request)
        self.assertFalse(refused["ok"], refused)
        self.assertEqual(refused["status"], UNPUBLISHED[case])
        self.assertIn("nothing was published", str(refused["detail"]))
        self.assertEqual(refused["nextAction"], "direct-landing")  # not a recovery, not a decision
        self.assertEqual(
            git(world.memory, "rev-list", "--all", "--count"),
            git(world.memory, "rev-list", "HEAD", "--count"),
        )
        self.assertEqual(history.read_bytes(), opened)
        self.assertFalse((world.memory / ".gitignore").exists())
        self.assertEqual(git(world.memory, "ls-files", "--stage"), index)
        record = direct_landing_store(fixture["contract"]).read()
        assert record is not None
        self.assertTrue(cancelled_unpublished(record))

        landed = direct_landing_tool(fixture["config"], request)  # the cause is gone
        self.assertTrue(landed["ok"], landed)
        closed = json.loads(git(world.memory, "show", f"HEAD:knowledge/history/{LEAF}.json"))
        self.assertTrue(closed["closed"])

    def test_a_request_refused_any_number_of_times_lands_on_the_next_plain_retry(self) -> None:
        """Review R3, finding 1. Every refused publication cancels its own generation and gives
        back every preparation, so the unchanged request starts the next generation each time:
        two refusals of one fingerprint, then the landing, all through the public tool."""

        fixture = _converted_series_fixture(Path(self.temp.name) / "refused-twice")
        world = fixture["world"]
        code = commit(world.code, {A: CODE_A.replace("return value", "return -value")})
        _answered(world, world.reanchor("RLZ-A00001"), *trace_rows(*TRACES))
        bind_worktree_services(build_default_worktree_services())
        git(world.memory, "branch", "elsewhere")
        history = world.memory / f"knowledge/history/{LEAF}.json"
        opened = history.read_bytes()
        index = git(world.memory, "ls-files", "--stage")
        request = _converted_request(fixture, code)
        store = direct_landing_store(fixture["contract"])
        fingerprints = set()
        for generation in (1, 2):
            with _publication_race("switched", world.memory):
                refused = direct_landing_tool(fixture["config"], request)
            self.assertEqual(refused["status"], "direct-landing-memory-publication-refused")
            record = store.read()
            assert record is not None
            self.assertEqual((record.status, record.generation), ("cancelled", generation))
            fingerprints.add(record.fingerprint)
            self.assertEqual(history.read_bytes(), opened)
            self.assertFalse((world.memory / ".gitignore").exists())
            self.assertEqual(git(world.memory, "ls-files", "--stage"), index)
            self.assertEqual(_kept_closings(fixture["contract"]), [])
        self.assertEqual(len(fingerprints), 1)  # the request and the judged tree never changed

        execute = direct_landing_owner.execute_or_require_direct_landing_recovery
        live: list[int] = []

        def replaced_once(contract, runtime):
            # The store's "already replaced" answer is kept for a live successor only: asking
            # again for the replacement that was just published hands back that same record.
            again = store.replace_terminal(runtime.record)
            self.assertEqual(again, store.read())
            live.append(again.generation)
            return execute(contract, runtime)

        with mock.patch.object(
            direct_landing_owner, "execute_or_require_direct_landing_recovery", replaced_once
        ):
            landed = direct_landing_tool(fixture["config"], request)  # the cause is gone
        self.assertTrue(landed["ok"], landed)
        record = store.read()
        assert record is not None
        self.assertEqual((record.status, record.generation), ("completed", 3))
        self.assertEqual(live, [3])
        self.assertEqual(record.predecessorFingerprint, record.fingerprint)
        closed = json.loads(git(world.memory, "show", f"HEAD:knowledge/history/{LEAF}.json"))
        self.assertTrue(closed["closed"])
        self.assertEqual(git(world.memory, "status", "--porcelain"), "")

    def test_a_recovery_that_meets_a_refused_publication_cancels_and_names_the_landing(
        self,
    ) -> None:
        """An in-flight generation (left by an interrupted call) whose recovery action meets a
        refused publication is cancelled and restored like any other, and the refusal names the
        direct landing, not another recovery, as what to repeat."""

        fixture = _converted_series_fixture(Path(self.temp.name) / "recover-refused")
        world = fixture["world"]
        code = commit(world.code, {A: CODE_A.replace("return value", "return -value")})
        _answered(world, world.reanchor("RLZ-A00001"), *trace_rows(*TRACES))
        bind_worktree_services(build_default_worktree_services())
        git(world.memory, "branch", "elsewhere")
        history = world.memory / f"knowledge/history/{LEAF}.json"
        opened = history.read_bytes()
        request = _converted_request(fixture, code)
        contract, store = fixture["contract"], direct_landing_store(fixture["contract"])
        with (
            mock.patch.object(
                direct_landing_owner,
                "execute_or_require_direct_landing_recovery",
                side_effect=OSError("the call ended after admission"),
            ),
            self.assertRaises(OSError),
        ):
            direct_landing(fixture["config"], request, contract)
        admitted = store.read()
        assert admitted is not None
        waiting = DirectLandingRuntime(contract, admitted).require_input(
            status="direct-landing-recovery-required", detail="interrupted after admission"
        )
        self.assertNotEqual(history.read_bytes(), opened)  # the closing is kept while in flight

        with (
            _publication_race("switched", world.memory),
            self.assertRaises(LifecycleControlError) as refused,
        ):
            recover_direct_landing_under_authority(contract, store, waiting)
        self.assertEqual(refused.exception.status, "direct-landing-memory-publication-refused")
        self.assertEqual(refused.exception.next_action, "direct-landing")
        record = store.read()
        assert record is not None
        self.assertTrue(cancelled_unpublished(record))
        self.assertEqual(history.read_bytes(), opened)
        self.assertFalse((world.memory / ".gitignore").exists())

        landed = direct_landing_tool(fixture["config"], request)
        self.assertTrue(landed["ok"], landed)

    def test_a_generation_cancelled_by_control_is_not_replaced_by_its_own_request(self) -> None:
        """Only a generation its own refusal cancelled unpublished is replaced by the same
        request. One cancelled through the control action keeps the base behaviour: the unchanged
        request observes it, starts nothing and leaves the memory checkout as it found it."""

        fixture = _converted_series_fixture(Path(self.temp.name) / "control-cancelled")
        world = fixture["world"]
        code = commit(world.code, {A: CODE_A.replace("return value", "return -value")})
        _answered(world, world.reanchor("RLZ-A00001"), *trace_rows(*TRACES))
        bind_worktree_services(build_default_worktree_services())
        history = world.memory / f"knowledge/history/{LEAF}.json"
        opened, memory_head = history.read_bytes(), head_commit(world.memory)
        request = _converted_request(fixture, code)
        store = direct_landing_store(fixture["contract"])
        with mock.patch.object(
            direct_landing_owner,
            "execute_or_require_direct_landing_recovery",
            side_effect=DirectLandingError("fixture-stop", "stopped after admission"),
        ):
            stopped = direct_landing_tool(fixture["config"], request)
        self.assertEqual(stopped["status"], "fixture-stop")
        admitted = store.read()
        assert admitted is not None
        self.assertEqual(admitted.status, "running")
        # What the control action's cancellation publishes and restores for a direct landing.
        cancelled = store.update(
            lambda record: record.model_copy(
                update={
                    "status": "cancelled",
                    "phase": "cancelled",
                    "finishedAt": record.heartbeatAt,
                    "cancelRequested": True,
                    "generationDisposition": "cancelled",
                }
            )
        )
        settle_direct_closing(fixture["contract"], current=cancelled.fingerprint, state="cancelled")
        self.assertEqual(history.read_bytes(), opened)
        self.assertFalse(cancelled_unpublished(cancelled))

        observed = direct_landing_tool(fixture["config"], request)
        self.assertFalse(observed["ok"], observed)
        self.assertEqual(observed["status"], "direct-landing-operation-action-required")
        self.assertEqual(store.read(), cancelled)
        self.assertEqual(cancelled.fingerprint, admitted.fingerprint)
        self.assertEqual(head_commit(world.memory), memory_head)
        self.assertEqual(history.read_bytes(), opened)
        self.assertFalse((world.memory / ".gitignore").exists())
        self.assertEqual(_kept_closings(fixture["contract"]), [])

    def test_direct_replay_is_admitted_only_on_the_tree_it_was_found_to_replay(self) -> None:
        """A landing the gate finds already published commits nothing, so it is admitted on the
        head's own tree. A file written after that finding is refused, never committed unjudged."""

        fixture = _converted_series_fixture(Path(self.temp.name) / "replay")
        world = fixture["world"]
        code = commit(world.code, {A: CODE_A.replace("return value", "return -value")})
        _answered(world, world.reanchor("RLZ-A00001"), *trace_rows(*TRACES))
        bind_worktree_services(build_default_worktree_services())
        landed = direct_landing_tool(fixture["config"], _converted_request(fixture, code))
        self.assertTrue(landed["ok"], landed)
        published = head_commit(world.memory)
        direct_landing_store(
            fixture["contract"]
        ).path.unlink()  # the same request, a new generation
        select = KnowledgeGate.direct_source

        def write_after_selection(gate, contract, **kwargs):
            result = select(gate, contract, **kwargs)
            assert not result.applies  # the gate found a replay
            (world.memory / "onboarding/pkg/a.py.md").write_text("late unjudged edit\n")
            return result

        with mock.patch.object(KnowledgeGate, "direct_source", write_after_selection):
            refused = direct_landing_tool(fixture["config"], _converted_request(fixture, code))
        self.assertFalse(refused["ok"], refused)
        self.assertEqual(refused["status"], "direct-landing-memory-candidate-changed")
        self.assertEqual(head_commit(world.memory), published)
        self.assertIsNone(direct_landing_store(fixture["contract"]).read())
        self.assertEqual(
            (world.memory / "onboarding/pkg/a.py.md").read_text(), "late unjudged edit\n"
        )

    def test_converted_direct_retry_keeps_the_judged_generation(self) -> None:
        fixture = _converted_series_fixture(Path(self.temp.name) / "retry")
        world = fixture["world"]
        code = commit(world.code, {A: CODE_A.replace("return value", "return -value")})
        _answered(world, world.reanchor("RLZ-A00001"), *trace_rows(*TRACES))
        bind_worktree_services(build_default_worktree_services())
        request = _converted_request(fixture, code)
        judge = KnowledgeGate.direct_verdict
        calls = []

        def remember(self, contract, **kwargs):
            calls.append(kwargs["memory_tree"])
            return judge(self, contract, **kwargs)

        with mock.patch.object(KnowledgeGate, "direct_verdict", remember):
            with mock.patch.object(
                direct_landing_execution,
                "prove_git_commit",
                side_effect=RuntimeError("receipt interrupted"),
            ):
                interrupted = direct_landing_tool(fixture["config"], request)
            self.assertFalse(interrupted["ok"], interrupted)
            store = direct_landing_store(fixture["contract"])
            record = store.read()
            assert record is not None
            committed = head_commit(world.memory)
            self.assertEqual(git(world.memory, "rev-parse", "HEAD^{tree}"), calls[0])
            observed = direct_landing_tool(fixture["config"], request)
            self.assertFalse(observed["ok"], observed)
            observed_record = store.read()
            assert observed_record is not None
            self.assertEqual(observed_record.operationKey, record.operationKey)
            completed = recover_direct_landing_under_authority(fixture["contract"], store, record)
            self.assertEqual(completed.operationKey, record.operationKey)
            self.assertEqual(completed.status, "completed")
        self.assertEqual(len(calls), 1)
        self.assertEqual(head_commit(world.memory), committed)

    def test_recovered_closeout_judges_recorded_governing_rows_instead_of_live_rows(self) -> None:
        fixture = _converted_series_fixture(Path(self.temp.name) / "recovered")
        world = fixture["world"]
        code = commit(world.code, {A: CODE_A.replace("return value", "return -value")})
        before = world.reanchor("RLZ-A00001")
        write(
            world.memory,
            {
                "knowledge/invariants/INV-AAAAAA-land.json": invariant(
                    "INV-AAAAAA", "Restated guarantee.", revision=2
                )
            },
        )
        row = world.invariant_row("INV-AAAAAA", before, revision=2)
        family = family_row({"INV-AAAAAA": 2, "INV-BBBBBB": 1})
        world.rows(row, family, *trace_rows(*TRACES), closed=True)
        git(world.memory, "add", "-A")
        git(world.memory, "commit", "-m", f"invalid recorded rows\n\nCode-Commit: {code}")
        invalid = head_commit(world.memory)
        contract = replace(world.contract, repo_name="repo-a")
        bind_worktree_services(build_default_worktree_services())
        with self.assertRaisesRegex(RuntimeError, "governing row"):
            require_gated_recovery(contract, code, invalid)
        # Correcting the checkout cannot make the already recorded invalid commit pass.
        world.rows(
            {**row, "disposition": "changed", "effect": "clarify"},
            family,
            *trace_rows(*TRACES),
            closed=True,
        )
        with self.assertRaisesRegex(RuntimeError, "governing row"):
            require_gated_recovery(contract, code, invalid)
        git(world.memory, "add", "-A")
        git(world.memory, "commit", "-m", f"valid recorded rows\n\nCode-Commit: {code}")
        valid = head_commit(world.memory)
        require_gated_recovery(contract, code, valid)
        world.rows(row, family, *trace_rows(*TRACES))
        require_gated_recovery(contract, code, valid)
        self.assertEqual(head_commit(world.memory), valid)

    def test_direct_landing_publishes_memory_and_recovers_independently_of_cache(self) -> None:
        root = Path(self.temp.name)
        fixture = _series_fixture(root / "fx")
        config = fixture["config"]
        contract = fixture["contract"]
        memory = fixture["memory"]

        # A mismatch between the requested commit and the series branch HEAD refuses.
        with (
            mock.patch(
                "agents_remember.worktrees.direct_landing.require_git",
                side_effect=AssertionError("foreign commit must not be dereferenced"),
            ) as tree_read,
            self.assertRaisesRegex(DirectLandingError, "not the current series branch HEAD"),
        ):
            direct_landing(
                config,
                DirectLandingRequest(
                    contract_path=contract.contract_path.as_posix(),
                    code_commit="0" * 40,
                    candidate_tree=fixture["candidate_tree"],
                    memory_commit_message="direct memory content",
                    intent_note="approve",
                ),
                contract,
            )
        tree_read.assert_not_called()

        # A malformed cache cannot block preview or become accepted mutation authority.
        (memory / "memory.md").write_text("not a ledger\n", encoding="utf-8")
        before_preview = _byte_tree(root)
        preview = direct_landing(
            config,
            DirectLandingRequest(
                contract_path=contract.contract_path.as_posix(),
                code_commit=fixture["code_head"],
                candidate_tree=fixture["candidate_tree"],
                memory_commit_message="direct memory content",
                intent_note="approve",
                dry_run=True,
            ),
            contract,
        )
        self.assertEqual(preview["state"], "would-land")
        self.assertEqual(DirectLandingResponse.model_validate(preview).state, "would-land")
        self.assertEqual(preview["codeCommit"], fixture["code_head"])
        self.assertEqual(_byte_tree(root), before_preview)
        before = git(memory, "rev-parse", "HEAD")

        # Lose the receipt after the one real content commit; recover it from Git evidence.
        (memory / "onboarding").mkdir(exist_ok=True)
        (memory / "onboarding" / "feature.py.md").write_text("# feature\n", encoding="utf-8")
        with (
            mock.patch(
                "agents_remember.worktrees.integration.direct_landing."
                "direct_landing_execution.prove_git_commit",
                side_effect=RuntimeError("receipt interrupted"),
            ),
            self.assertRaises(DirectLandingError),
        ):
            direct_landing(
                config,
                DirectLandingRequest(
                    contract_path=contract.contract_path.as_posix(),
                    code_commit=fixture["code_head"],
                    candidate_tree=fixture["candidate_tree"],
                    memory_commit_message="direct memory",
                    intent_note="approved by owner",
                ),
                contract,
            )
        landed = self._recover_after_interrupted_receipt(fixture)
        self.assertEqual(landed["state"], "landed")
        self.assertEqual(DirectLandingResponse.model_validate(landed).state, "landed")
        self.assertEqual(landed["codeCommit"], fixture["code_head"])
        self.assertTrue(landed["memoryContentCommit"])
        self.assertNotIn("ledgerCommit", landed)
        after = git(memory, "rev-parse", "HEAD")
        self.assertNotEqual(before, after)
        self.assertEqual(after, landed["memoryContentCommit"])
        self.assertEqual(git(memory, "rev-list", "--count", f"{before}..{after}"), "1")
        self.assertEqual(
            git(memory, "show", "-s", "--format=%s", str(landed["memoryContentCommit"])),
            "direct memory",
        )
        # Direct landing is the branch-addressed closeout route, so its memory-content
        # commit is the memory side of the same pairing: the message body verbatim plus
        # exactly one Code-Commit trailer naming the code commit this landing verified.
        memory_body = git(memory, "show", "-s", "--format=%B", str(landed["memoryContentCommit"]))
        self.assertEqual(memory_body, f"direct memory\n\nCode-Commit: {fixture['code_head']}")
        message_file = root / "direct-memory-message.txt"
        message_file.write_text(f"{memory_body}\n", encoding="utf-8")
        self.assertEqual(
            git(memory, "interpret-trailers", "--parse", message_file.as_posix()),
            f"Code-Commit: {fixture['code_head']}",
        )
        self.assertEqual(git(memory, "ls-files", "memory.md"), "")
        self.assertIn("/memory.md", (memory / ".gitignore").read_text(encoding="utf-8"))
        ledger_text = (memory / "memory.md").read_text(encoding="utf-8")
        self.assertIn(fixture["code_head"], ledger_text)
        self.assertIn(landed["memoryContentCommit"], ledger_text)
        self._assert_clean_memory_reused(root / "reuse")

    def _recover_after_interrupted_receipt(self, fixture: dict) -> dict[str, object]:
        contract = fixture["contract"]
        memory = fixture["memory"]
        store = direct_landing_store(contract)
        record = store.read()
        self.assertIsNotNone(record)
        assert record is not None
        self.assertEqual(set(record.mutationEvidence), {"memory"})
        self.assertEqual(record.mutationEvidence["memory"].state, "mutation-intent")
        committed = head_commit(memory)

        # Actual code/ref/content changes still require a decision, regardless of cache bytes.
        git(fixture["code"], "branch", "-f", contract.code_work_branch, "main")
        self.assertEqual(
            classify_direct_landing_recovery(contract, record).state, "developer-decision"
        )
        git(fixture["code"], "branch", "-f", contract.code_work_branch, fixture["code_head"])
        git(memory, "switch", "main")
        self.assertEqual(
            classify_direct_landing_recovery(contract, record).state, "developer-decision"
        )
        git(memory, "switch", contract.memory_work_branch)
        extra = memory / "unaccepted.md"
        extra.write_text("unaccepted content\n", encoding="utf-8")
        self.assertEqual(
            classify_direct_landing_recovery(contract, record).state, "developer-decision"
        )
        extra.unlink()

        cache = memory / "memory.md"
        cache.unlink()
        self.assertEqual(classify_direct_landing_recovery(contract, record).state, "terminalizable")
        cache.write_text("still not a ledger\n", encoding="utf-8")
        self.assertEqual(classify_direct_landing_recovery(contract, record).state, "terminalizable")
        completed = recover_direct_landing_under_authority(contract, store, record)
        self.assertEqual(head_commit(memory), committed)
        assert completed.recoveryCommits is not None and isinstance(completed.result, dict)
        self.assertEqual(completed.status, "completed")
        self.assertEqual(completed.recoveryCommits.memoryContentCommit, committed)
        self.assertEqual(completed.mutationEvidence["memory"].state, "commit-proven")
        return completed.result

    def _assert_clean_memory_reused(self, root: Path) -> None:
        fixture = _series_fixture(root)
        contract = fixture["contract"]
        memory = fixture["memory"]
        before = head_commit(memory)
        (memory / "memory.md").unlink()
        request = DirectLandingRequest(
            contract_path=contract.contract_path.as_posix(),
            code_commit=fixture["code_head"],
            candidate_tree=fixture["candidate_tree"],
            memory_commit_message="reuse current memory",
            intent_note="approved by owner",
        )
        landed = direct_landing(fixture["config"], request, contract)
        self.assertEqual(landed["state"], "landed")
        self.assertEqual(landed["memoryContentCommit"], before)
        self.assertEqual(head_commit(memory), before)
        # The same request again is the completed generation's replay: a terminal generation is
        # never replaced by its own fingerprint unless its own refusal cancelled it unpublished.
        store = direct_landing_store(contract)
        completed = store.read()
        replayed = direct_landing(fixture["config"], request, contract)
        self.assertEqual(replayed["memoryContentCommit"], before)
        self.assertEqual(store.read(), completed)
        assert completed is not None
        self.assertEqual((completed.status, completed.generation), ("completed", 1))
        self.assertFalse(
            any(
                row.code_commit == fixture["code_head"] for row in derive_memory_ledger(memory).rows
            )
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
