"""Wave 2 (L16-R6/R7/R8/R9): branch-addressed direct execution and error dialect.

Covers the grant-gated series-contract binding for ``record_route_review``
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

from agents_remember.application import worktree_tools
from agents_remember.application.direct_execution_grants import (
    give_grant,
    open_direct_sibling,
    revoke_grant,
)
from agents_remember.application.knowledge_gate import KnowledgeGate
from agents_remember.application.lifecycle.direct_landing import direct_landing_tool
from agents_remember.application.role_launch_context import RoleLaunchContext
from agents_remember.application.worktree_services import build_default_worktree_services
from agents_remember.cli.role_launch_workspace import _resolve_workspace
from agents_remember.errors import RolePreparationError
from agents_remember.kernel.direct_execution_grants import (
    DirectExecutionGrant,
    grant_path_for,
    write_grant_file,
)
from agents_remember.kernel.git_command import run_git
from agents_remember.kernel.memory_cache import derive_memory_ledger
from agents_remember.kernel.memory_ledger import create_initial_ledger, write_ledger
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig, load_config
from agents_remember.models.direct_landing import DirectLandingResponse
from agents_remember.models.knowledge_files import canonical_text
from agents_remember.models.lifecycles.direct_landing import DirectLandingOperationInput
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.models.task_intent import MissingTaskIntent
from agents_remember.tasks import (
    SubTaskRef,
    TaskDocument,
    TaskExecutionRegistration,
    read_task_doc,
    write_task_doc,
)
from agents_remember.tasks.document_refs import TaskDocumentTopology
from agents_remember.worktrees import direct_landing as direct_landing_owner
from agents_remember.worktrees.direct_landing import (
    DIRECT_CREATE_PRECODE_CENSUS,
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
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_control_projection import (
    legal_operation_controls,
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
from agents_remember.worktrees.modules.closeout import _validate_closeout_source_state
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
    _edit,
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
) -> McpRuntimeConfig:
    configured_code = root / "repo-a"
    if not configured_code.exists():
        configured_code.symlink_to(code, target_is_directory=True)
    if memory is not None:
        configured_memory = root / "coord" / "memory-repos" / "ar-repo-a"
        configured_memory.parent.mkdir(parents=True, exist_ok=True)
        if not configured_memory.exists():
            configured_memory.symlink_to(memory, target_is_directory=True)
    config_path = root / "settings.json"
    config_path.write_text(
        json.dumps(
            {
                "version": 1,
                "coordinationRoot": (root / "coord").as_posix(),
                "workspaceRoot": root.as_posix(),
                "repositories": {"repo-a": {}},
            }
        ),
        encoding="utf-8",
    )
    return load_config(config_path)


def _grant_direct_leaf(tasks: Path, config: McpRuntimeConfig) -> TaskDocumentRef:
    """Publish a canonical master + leaf pair and give the leaf its grant.

    The fixtures land a leaf the knowledge gate infers from its history file;
    the grant names that same leaf by its canonical task reference.
    """
    master = TaskDocument.model_validate(
        {
            "id": "TEST-DIRECT-MASTER",
            "slug": "direct-task",
            "title": "Direct master",
            "kind": "master",
            "status": "inProgress",
            "repo": "repo-a",
            "createdAt": "2026-10-06T00:00:00+00:00",
            "executionNature": "atomic",
            "subTasks": [
                SubTaskRef(
                    number=LEAF,
                    name="Direct leaf",
                    file="leaf.md",
                    status="planning",
                ).model_dump(mode="json"),
            ],
        }
    )
    write_task_doc(tasks, master)
    leaf = TaskDocument.model_validate(
        {
            "id": LEAF,
            "slug": "leaf",
            "title": "Direct leaf",
            "kind": "subTask",
            "status": "planning",
            "repo": "repo-a",
            "createdAt": "2026-10-06T00:00:00+00:00",
            "objective": "Exercise the direct landing.",
            "requirements": ["The fixture leaf lands directly."],
        }
    )
    write_task_doc(tasks, leaf)
    ref = TaskDocumentRef(repository="repo-a", path="direct-task/leaf.json")
    give_grant(config, ref, granted_by="architect", granter_agent_id="fixture")
    return ref


def _series_fixture(
    root: Path, *, code_commit_message: str = "code commit", lifecycle_id: str = ""
) -> dict:
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
    memory_source = git(memory, "rev-parse", "main")

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
            base_commit=memory_source,
        ),
    )
    if lifecycle_id:
        contract = replace(contract, lifecycle_id=lifecycle_id)
    write_contract(contract.contract_path, contract)
    publish_new_lifecycle_operation_location(
        contract,
        contract_text=contract.contract_path.read_text(encoding="utf-8"),
    )
    config = _scratch_config(root, code, memory)
    return {
        "config": config,
        "contract": contract,
        "code": code,
        "memory": memory,
        "code_head": code_head,
        "candidate_tree": require_git(code, ["rev-parse", f"{code_head}^{{tree}}"]),
        "tasks": tasks,
        "leaf_ref": _grant_direct_leaf(tasks, config),
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
    config = _scratch_config(root, world.code, world.memory)
    return {
        "world": world,
        "config": config,
        "contract": contract,
        "memory": world.memory,
        "code": world.code,
        "leaf_ref": _grant_direct_leaf(tasks, config),
    }


def _install_passing_commit_hooks(repository: Path, log: Path) -> None:
    """Real ``pre-commit`` and ``commit-msg`` hooks that record their run and pass."""

    hooks = Path(git(repository, "rev-parse", "--path-format=absolute", "--git-path", "hooks"))
    hooks.mkdir(parents=True, exist_ok=True)
    for name in ("pre-commit", "commit-msg"):
        hook = hooks / name
        hook.write_text(
            f"#!/bin/sh\necho {name} >> {shlex.quote(log.as_posix())}\nexit 0\n",
            encoding="utf-8",
        )
        hook.chmod(0o755)
    assert run_git(repository, ["hook", "run", "pre-commit"]).returncode == 0  # live
    log.unlink()


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
        leaf_task_document_ref=fixture["leaf_ref"],
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

    def test_direct_landing_without_grant_names_leaf_and_givers(self) -> None:
        fixture = _converted_series_fixture(Path(self.temp.name) / "nogrant")
        world = fixture["world"]
        code = commit(world.code, {A: CODE_A.replace("return value", "return -value")})
        revoke_grant(fixture["config"], fixture["leaf_ref"])
        refused = direct_landing_tool(fixture["config"], _converted_request(fixture, code))
        self.assertFalse(refused["ok"])
        self.assertEqual(refused["status"], "direct-landing-leaf-without-grant")
        detail = refused["detail"]
        assert isinstance(detail, str)
        self.assertIn(fixture["leaf_ref"].path, detail)
        self.assertIn("dashboard", detail)
        self.assertIn("direct_execution_grant", detail)
        self.assertNotIn("directExecutionEnabled", detail)

    def test_direct_landing_with_unreadable_grant_refuses(self) -> None:
        fixture = _converted_series_fixture(Path(self.temp.name) / "badgrant")
        world = fixture["world"]
        code = commit(world.code, {A: CODE_A.replace("return value", "return -value")})
        grant_path_for(fixture["config"].coordination_root, fixture["leaf_ref"]).write_text(
            "{not json", encoding="utf-8"
        )
        refused = direct_landing_tool(fixture["config"], _converted_request(fixture, code))
        self.assertFalse(refused["ok"])
        self.assertEqual(refused["status"], "direct-landing-grant-unreadable")

    def _create_fixture(self, root: Path) -> dict:
        fixture = _series_fixture(root, lifecycle_id="direct-create-test")
        code = fixture["code"]
        git(code, "checkout", fixture["contract"].code_work_branch)
        (code / "feature.py").write_text("def f():\n    return 2\n", encoding="utf-8")
        (code / "extra.py").write_text("def g():\n    return 3\n", encoding="utf-8")
        memory = fixture["memory"]
        (memory / "onboarding").mkdir(exist_ok=True)
        (memory / "onboarding" / "feature.py.md").write_text("# feature\n", encoding="utf-8")
        return fixture

    def _create_request(self, fixture: dict, *, dry_run: bool) -> DirectLandingRequest:
        return DirectLandingRequest(
            contract_path=fixture["contract"].contract_path.as_posix(),
            code_commit=None,
            leaf_task_document_ref=fixture["leaf_ref"],
            code_commit_message="direct work",
            memory_commit_message="direct memory",
            intent_note="approve",
            dry_run=dry_run,
        )

    def _converted_create_fixture(self, root: Path, *, answer: bool = True) -> dict:
        """A converted-memory fixture with an answered, uncommitted code change to create."""

        fixture = _converted_series_fixture(root)
        world = fixture["world"]
        if answer:
            _edit(world, CODE_A.replace("return value", "return -value"))
            _answered(world, world.reanchor("RLZ-A00001"), *trace_rows(world, *TRACES))
        bind_worktree_services(build_default_worktree_services())
        fixture["code_head"] = git(fixture["code"], "rev-parse", "HEAD")
        fixture["tasks"] = fixture["contract"].task_root
        return fixture

    def _converted_create_request(self, fixture: dict, *, dry_run: bool) -> DirectLandingRequest:
        return DirectLandingRequest(
            contract_path=fixture["contract"].contract_path.as_posix(),
            code_commit=None,
            leaf_task_document_ref=fixture["leaf_ref"],
            code_commit_message="direct work",
            memory_commit_message="direct memory",
            intent_note="approve",
            dry_run=dry_run,
        )

    def test_direct_create_preview_lists_paths_and_names_tree(self) -> None:
        root = Path(self.temp.name)
        fixture = self._create_fixture(root / "create-preview")
        code = fixture["code"]
        head_before = git(code, "rev-parse", "HEAD")
        preview = direct_landing_tool(
            fixture["config"], self._create_request(fixture, dry_run=True)
        )
        self.assertTrue(preview["ok"])
        self.assertEqual(preview["state"], "would-land")
        self.assertEqual(preview["codeCommit"], "")
        self.assertEqual(preview["codeCommitMessage"], "direct work")
        self.assertTrue(preview["candidateTree"])
        self.assertEqual(preview["candidatePaths"], ["README.md", "extra.py", "feature.py"])
        self.assertEqual(DirectLandingResponse.model_validate(preview).state, "would-land")
        # The preview commits nothing: the branch and the files are as before.
        self.assertEqual(git(code, "rev-parse", "HEAD"), head_before)
        self.assertTrue(git(code, "status", "--porcelain"))

    def test_direct_create_apply_lands_code_memory_and_ledger(self) -> None:
        root = Path(self.temp.name)
        fixture = self._converted_create_fixture(root / "create-apply")
        config = fixture["config"]
        code = fixture["code"]
        memory = fixture["memory"]
        code_base = fixture["code_head"]
        preview = direct_landing_tool(config, self._converted_create_request(fixture, dry_run=True))
        apply_request = self._converted_create_request(fixture, dry_run=False)
        apply_request = replace(apply_request, candidate_tree=preview["candidateTree"])
        landed = direct_landing_tool(config, apply_request)
        self.assertEqual(landed["state"], "landed")
        self.assertEqual(DirectLandingResponse.model_validate(landed).state, "landed")
        self.assertEqual(landed["candidateTree"], preview["candidateTree"])
        created = landed["codeCommit"]
        assert isinstance(created, str)
        self.assertTrue(created)
        self.assertNotEqual(created, code_base)
        self.assertEqual(git(code, "rev-parse", "HEAD"), created)
        self.assertEqual(git(code, "rev-parse", f"{created}^{{tree}}"), preview["candidateTree"])
        self.assertEqual(
            git(code, "show", "-s", "--format=%B", created),
            f"direct work\nCommit-Check-Pass: tree={preview['candidateTree']} hook=none",
        )
        self.assertEqual(git(code, "status", "--porcelain"), "")
        memory_commit = landed["memoryContentCommit"]
        self.assertTrue(memory_commit)
        memory_body = git(memory, "show", "-s", "--format=%B", str(memory_commit))
        memory_tree = git(memory, "rev-parse", f"{memory_commit}^{{tree}}")
        self.assertEqual(
            memory_body,
            f"direct memory\n\nCode-Commit: {created}\n"
            f"Commit-Check-Pass: tree={memory_tree} hook=none",
        )
        ledger_text = (memory / "memory.md").read_text(encoding="utf-8")
        self.assertIn(created, ledger_text)
        self.assertIn(str(memory_commit), ledger_text)
        # F01: the admitted generation persists the create's code facts before
        # publication: message, tree/ref snapshot, and the exact tree.
        contract = fixture["contract"]
        record = direct_landing_store(contract).read()
        assert record is not None
        accepted = DirectLandingOperationInput.model_validate(record.input.model_dump(mode="json"))
        self.assertEqual(accepted.codeCommit, created)
        self.assertEqual(accepted.codeCommitMessage, "direct work")
        self.assertEqual(accepted.candidateTree, preview["candidateTree"])
        assert accepted.codeBefore is not None
        self.assertEqual(accepted.codeBefore.head, code_base)
        self.assertEqual(accepted.codeBefore.headRef, f"refs/heads/{contract.code_work_branch}")

    def test_direct_create_apply_refuses_before_code_with_state_preserved(self) -> None:
        root = Path(self.temp.name)
        fixture = self._converted_create_fixture(root / "create-precode-refusal")
        config = fixture["config"]
        contract = fixture["contract"]
        world = fixture["world"]
        code = world.code
        memory = world.memory
        preview = direct_landing_tool(config, self._converted_create_request(fixture, dry_run=True))
        request = self._converted_create_request(fixture, dry_run=False)
        request = replace(request, candidate_tree=preview["candidateTree"])
        head_before = git(code, "rev-parse", "HEAD")
        porcelain_before = git(code, "status", "--porcelain")
        cached_before = git(code, "diff", "--cached", "--name-only")
        history = memory / f"knowledge/history/{LEAF}.json"
        history_before = history.read_bytes()
        memory_porcelain_before = git(memory, "status", "--porcelain")
        memory_before = git(memory, "rev-parse", "HEAD")
        store = direct_landing_store(contract)
        with (
            mock.patch.object(
                direct_landing_owner,
                "direct_gate_source",
                return_value=direct_landing_owner.DirectGateVerdict(
                    True, None, "probe refuses before any code publication"
                ),
            ),
            self.assertRaisesRegex(DirectLandingError, "direct-landing-knowledge-gate-refused"),
        ):
            direct_landing_owner.direct_landing(config, request, contract)
        # F01: a pre-code refusal publishes nothing and preserves everything:
        # branch, files, index, history, and no admitted generation.
        self.assertEqual(git(code, "rev-parse", "HEAD"), head_before)
        self.assertEqual(git(code, "status", "--porcelain"), porcelain_before)
        self.assertEqual(git(code, "diff", "--cached", "--name-only"), cached_before)
        self.assertTrue((code / A).is_file())
        self.assertEqual(history.read_bytes(), history_before)
        self.assertEqual(git(memory, "status", "--porcelain"), memory_porcelain_before)
        self.assertEqual(git(memory, "rev-parse", "HEAD"), memory_before)
        self.assertIsNone(store.read())

    def test_direct_create_apply_on_converted_memory_judges_before_code(self) -> None:
        root = Path(self.temp.name)
        fixture = self._converted_create_fixture(root / "create-converted")
        config = fixture["config"]
        contract = fixture["contract"]
        world = fixture["world"]
        code = world.code
        memory = world.memory
        code_base = git(code, "rev-parse", "HEAD")
        preview = direct_landing_tool(config, self._converted_create_request(fixture, dry_run=True))
        request = self._converted_create_request(fixture, dry_run=False)
        request = replace(request, candidate_tree=preview["candidateTree"])
        landed = direct_landing_tool(config, request)
        self.assertEqual(landed["state"], "landed")
        created = landed["codeCommit"]
        assert isinstance(created, str)
        self.assertTrue(created)
        self.assertNotEqual(created, code_base)
        self.assertEqual(git(code, "rev-parse", "HEAD"), created)
        self.assertEqual(git(code, "rev-parse", f"{created}^{{tree}}"), preview["candidateTree"])
        memory_commit = landed["memoryContentCommit"]
        self.assertTrue(memory_commit)
        memory_body = git(memory, "show", "-s", "--format=%B", str(memory_commit))
        self.assertIn(f"Code-Commit: {created}", memory_body)
        # The gate closed this leaf's history file.
        closed = json.loads((memory / f"knowledge/history/{LEAF}.json").read_text(encoding="utf-8"))
        self.assertTrue(closed["closed"])
        self.assertEqual(closed["leaf"], LEAF)
        # F01: the admitted generation persists the create's code facts.
        record = direct_landing_store(contract).read()
        assert record is not None
        accepted = DirectLandingOperationInput.model_validate(record.input.model_dump(mode="json"))
        self.assertEqual(accepted.codeCommit, created)
        self.assertEqual(accepted.codeCommitMessage, "direct work")
        self.assertEqual(accepted.candidateTree, preview["candidateTree"])
        assert accepted.codeBefore is not None
        self.assertEqual(accepted.codeBefore.head, code_base)

    def _cut_create_before_memory(self, fixture: dict) -> tuple[DirectLandingRequest, str]:
        """Preview, then cut the apply before any memory commit is published."""

        config = fixture["config"]
        code = fixture["code"]
        preview = direct_landing_tool(config, self._create_request(fixture, dry_run=True))
        request = self._create_request(fixture, dry_run=False)
        request = replace(request, candidate_tree=preview["candidateTree"])
        with (
            mock.patch(
                "agents_remember.worktrees.integration.direct_landing."
                "direct_landing_execution._publish_judged_tree",
                side_effect=RuntimeError("cut before memory publication"),
            ),
        ):
            cut = direct_landing_tool(config, request)
        self.assertEqual(cut["state"], "refused")
        created = git(code, "rev-parse", "HEAD")
        self.assertNotEqual(created, fixture["code_head"])
        return request, created

    def _cut_create_at_memory_receipt(self, fixture: dict) -> tuple[DirectLandingRequest, str, str]:
        """Preview, then cut the apply after the memory commit but before its receipt."""

        config = fixture["config"]
        contract = fixture["contract"]
        code = fixture["code"]
        memory = fixture["memory"]
        preview = direct_landing_tool(config, self._create_request(fixture, dry_run=True))
        request = self._create_request(fixture, dry_run=False)
        request = replace(request, candidate_tree=preview["candidateTree"])
        memory_before = git(memory, "rev-parse", "HEAD")
        with (
            mock.patch(
                "agents_remember.worktrees.integration.direct_landing."
                "direct_landing_execution.prove_git_commit",
                side_effect=RuntimeError("receipt interrupted"),
            ),
        ):
            try:
                direct_landing(config, request, contract)
            except DirectLandingError as error:
                self.assertEqual(error.status, "direct-landing-recovery-required")
            else:
                self.fail("the cut call must refuse")
        created = git(code, "rev-parse", "HEAD")
        self.assertNotEqual(created, fixture["code_head"])
        # The cut lands after the memory publication: the memory commit is on
        # the branch; only its receipt and the ledger row are missing.
        cut_memory = git(memory, "rev-parse", "HEAD")
        self.assertNotEqual(cut_memory, memory_before)
        return request, created, cut_memory

    def _cut_create_at_ledger_write(self, fixture: dict) -> tuple[DirectLandingRequest, str, str]:
        """Preview, then cut the apply after the memory commit but before the ledger row."""

        config = fixture["config"]
        contract = fixture["contract"]
        code = fixture["code"]
        memory = fixture["memory"]
        preview = direct_landing_tool(config, self._create_request(fixture, dry_run=True))
        request = self._create_request(fixture, dry_run=False)
        request = replace(request, candidate_tree=preview["candidateTree"])
        with (
            mock.patch(
                "agents_remember.worktrees.integration.direct_landing."
                "direct_landing_execution.refresh_memory_cache",
                side_effect=RuntimeError("ledger interrupted"),
            ),
        ):
            try:
                direct_landing_owner.direct_landing(config, request, contract)
            except DirectLandingError as error:
                self.assertEqual(error.status, "direct-landing-recovery-required")
            else:
                self.fail("the cut call must refuse")
        created = git(code, "rev-parse", "HEAD")
        self.assertNotEqual(created, fixture["code_head"])
        # The cut lands after the memory commit: it is on the branch, but the
        # ledger row was never written. The ledger file is a derived view: on
        # a converted fixture the first landing creates it, so a file that
        # does not exist yet counts as 'no row', like a file without the row.
        cut_memory = git(memory, "rev-parse", "HEAD")
        ledger_file = memory / "memory.md"
        ledger_text = ledger_file.read_text(encoding="utf-8") if ledger_file.is_file() else ""
        self.assertNotIn(created, ledger_text)
        return request, created, cut_memory

    def test_direct_create_apply_completes_after_a_cut_memory_commit(self) -> None:
        root = Path(self.temp.name)
        fixture = self._converted_create_fixture(root / "create-resume")
        config = fixture["config"]
        code = fixture["code"]
        memory = fixture["memory"]
        request, created, cut_memory = self._cut_create_at_memory_receipt(fixture)
        # The same call again completes the admitted pair: no second code
        # commit, the same memory commit, then the ledger row.
        again = direct_landing_tool(config, request)
        self.assertEqual(again["state"], "landed")
        self.assertEqual(again["codeCommit"], created)
        self.assertEqual(git(code, "rev-parse", "HEAD"), created)
        self.assertEqual(git(code, "rev-list", "--count", f"{fixture['code_head']}..HEAD"), "1")
        self.assertTrue(again["memoryContentCommit"])
        self.assertEqual(git(memory, "rev-parse", "HEAD"), cut_memory)
        self.assertEqual(again["memoryContentCommit"], cut_memory)

    def test_recover_control_completes_a_cut_memory_commit(self) -> None:
        # The recover control completes the same cut on a fresh pair with no
        # second code commit.
        root = Path(self.temp.name)
        fixture = self._converted_create_fixture(root / "create-recover")
        config = fixture["config"]
        contract = fixture["contract"]
        code = fixture["code"]
        memory = fixture["memory"]
        preview = direct_landing_tool(config, self._converted_create_request(fixture, dry_run=True))
        request = self._converted_create_request(fixture, dry_run=False)
        request = replace(request, candidate_tree=preview["candidateTree"])
        store = direct_landing_store(contract)
        memory_before = git(memory, "rev-parse", "HEAD")
        with (
            mock.patch(
                "agents_remember.worktrees.integration.direct_landing."
                "direct_landing_execution.prove_git_commit",
                side_effect=RuntimeError("receipt interrupted"),
            ),
            self.assertRaises(DirectLandingError),
        ):
            direct_landing(config, request, contract)
        created = git(code, "rev-parse", "HEAD")
        cut_memory = git(memory, "rev-parse", "HEAD")
        self.assertNotEqual(cut_memory, memory_before)
        record = store.read()
        assert record is not None
        completed = recover_direct_landing_under_authority(contract, store, record)
        assert isinstance(completed.result, dict)
        self.assertEqual(completed.result["codeCommit"], created)
        self.assertEqual(git(code, "rev-parse", "HEAD"), created)
        self.assertEqual(git(code, "rev-list", "--count", f"{fixture['code_head']}..HEAD"), "1")
        self.assertTrue(completed.result["memoryContentCommit"])
        self.assertEqual(git(memory, "rev-parse", "HEAD"), cut_memory)

    def test_direct_create_apply_completes_after_a_cut_before_memory(self) -> None:
        # Sealed F01/F04: a cut after code publication but before any memory
        # commit keeps generation1 in flight (it owns an irreversible output),
        # and the exact same call resumes and completes that generation with
        # the same code commit — no generation2, no second code commit.
        root = Path(self.temp.name)
        fixture = self._converted_create_fixture(root / "create-code-cut")
        config = fixture["config"]
        contract = fixture["contract"]
        code = fixture["code"]
        memory = fixture["memory"]
        memory_before = git(memory, "rev-parse", "HEAD")
        request, created = self._cut_create_before_memory(fixture)
        # No memory commit was published by the cut.
        self.assertEqual(git(memory, "rev-parse", "HEAD"), memory_before)
        store = direct_landing_store(contract)
        record = store.read()
        assert record is not None
        cut_generation = record.generation
        self.assertNotEqual(record.status, "completed")
        # The exact same call completes the admitted generation: same code
        # commit, one code commit total, memory and ledger follow.
        again = direct_landing_tool(config, request)
        self.assertEqual(again["state"], "landed")
        self.assertEqual(again["codeCommit"], created)
        successor = store.read()
        assert successor is not None
        self.assertEqual(successor.generation, cut_generation)
        self.assertEqual(successor.status, "completed")
        self.assertEqual(git(code, "rev-parse", "HEAD"), created)
        self.assertEqual(git(code, "rev-list", "--count", f"{fixture['code_head']}..HEAD"), "1")
        self.assertTrue(again["memoryContentCommit"])
        self.assertNotEqual(git(memory, "rev-parse", "HEAD"), memory_before)

    def test_cut_generation_carries_code_leg_and_boundary(self) -> None:
        # Sealed F01: the post-code/pre-memory generation journals the
        # per-leg code intent through to commit proof and enters the
        # irreversible boundary: enabled code leg, proven code cell, flag set.
        root = Path(self.temp.name)
        fixture = self._converted_create_fixture(root / "create-code-leg")
        contract = fixture["contract"]
        _request, created = self._cut_create_before_memory(fixture)
        record = direct_landing_store(contract).read()
        assert record is not None
        op_input = record.input
        assert op_input.kind == "direct-landing"
        self.assertEqual(op_input.effectiveInput.code.state, "enabled")
        self.assertEqual(set(record.mutationEvidence), {"code", "memory"})
        code_evidence = record.mutationEvidence["code"]
        self.assertEqual(code_evidence.state, "commit-proven")
        self.assertEqual(code_evidence.commit, created)
        self.assertTrue(record.irreversibleBoundaryEntered)

    def test_moved_code_branch_keeps_generation_ambiguous(self) -> None:
        # Sealed F01-F04-moved-code: the branch advances past the admitted
        # code commit (same tree) before the memory cut. The published output
        # stays reachable, so the generation is not unpublished and is never
        # cancelled as such: it stays in flight with an explicit
        # developer-decision refusal, and the repeat refuses the same way.
        root = Path(self.temp.name)
        fixture = self._converted_create_fixture(root / "create-moved-code")
        config = fixture["config"]
        contract = fixture["contract"]
        code = fixture["code"]
        preview = direct_landing_tool(config, self._converted_create_request(fixture, dry_run=True))
        request = self._converted_create_request(fixture, dry_run=False)
        request = replace(request, candidate_tree=preview["candidateTree"])
        published: dict[str, str] = {}

        def cut_after_code(*args, **kwargs):
            published["code"] = git(code, "rev-parse", "HEAD")
            tree = git(code, "rev-parse", "HEAD^{tree}")
            published["descendant"] = git(
                code, "commit-tree", tree, "-p", published["code"], "-m", "advance"
            )
            git(code, "update-ref", "HEAD", published["descendant"], published["code"])
            raise RuntimeError("cut before memory with advanced code branch")

        with (
            mock.patch(
                "agents_remember.worktrees.integration.direct_landing."
                "direct_landing_execution._publish_judged_tree",
                side_effect=cut_after_code,
            ),
        ):
            result = direct_landing_tool(config, request)
        self.assertEqual(result["state"], "refused")
        self.assertNotEqual(published["code"], fixture["code_head"])
        store = direct_landing_store(contract)
        record = store.read()
        assert record is not None
        self.assertNotEqual(record.status, "cancelled")
        self.assertEqual(git(code, "merge-base", "--is-ancestor", published["code"], "HEAD"), "")
        classification = classify_direct_landing_recovery(contract, record)
        self.assertEqual(classification.state, "developer-decision")
        again = direct_landing_tool(config, request)
        self.assertEqual(again["state"], "refused")
        successor = store.read()
        assert successor is not None
        self.assertEqual(successor.generation, record.generation)
        self.assertNotEqual(successor.status, "cancelled")
        self.assertNotEqual(successor.status, "completed")

    def test_advanced_source_refuses_create(self) -> None:
        # Sealed F01: the direct route proves the same source lineage as the
        # enclosure closeout. A source that moved after admission refuses the
        # preview and the apply with the lineage words.
        root = Path(self.temp.name)
        fixture = self._create_fixture(root / "create-stale-source")
        config = fixture["config"]
        contract = fixture["contract"]
        code = fixture["code"]
        source = git(code, "rev-parse", contract.code_source_branch)
        tree = git(code, "rev-parse", f"{source}^{{tree}}")
        advanced = git(code, "commit-tree", tree, "-p", source, "-m", "source advance")
        git(code, "update-ref", f"refs/heads/{contract.code_source_branch}", advanced, source)
        with self.assertRaises(Exception) as ordinary:
            _validate_closeout_source_state(contract, dry_run=True)
        self.assertIn("stale", str(ordinary.exception))
        preview = direct_landing_tool(config, self._create_request(fixture, dry_run=True))
        self.assertFalse(preview["ok"])
        self.assertEqual(preview["status"], "direct-landing-source-lineage-refused")
        self.assertEqual(git(code, "rev-parse", "HEAD"), fixture["code_head"])
        apply = direct_landing_tool(config, self._create_request(fixture, dry_run=False))
        self.assertFalse(apply["ok"])
        self.assertEqual(apply["status"], "direct-landing-source-lineage-refused")

    def test_public_recover_control_completes_cut_generation(self) -> None:
        # Sealed F04: the public worktree_operation_control recover call
        # executes the advertised action on a mechanically recoverable cut
        # instead of refusing it.
        root = Path(self.temp.name)
        fixture = self._converted_create_fixture(root / "create-public-recover")
        config = fixture["config"]
        contract = fixture["contract"]
        code = fixture["code"]
        _request, created = self._cut_create_before_memory(fixture)
        store = direct_landing_store(contract)
        record = store.read()
        assert record is not None
        controls = legal_operation_controls(contract, record)
        self.assertIn("recover", [item["action"] for item in controls])
        result = worktree_tools.worktree_operation_control_tool(
            config,
            worktree_tools.OperationControlRequest(
                contract_path=str(contract.contract_path),
                operation_kind="direct-landing",
                action="recover",
                expected_generation=record.generation,
                intent_note="Recover the sealed code cut.",
            ),
        )
        self.assertTrue(result.get("ok"), result)
        successor = store.read()
        assert successor is not None
        self.assertEqual(successor.status, "completed")
        self.assertEqual(successor.generation, record.generation)
        self.assertEqual(git(code, "rev-list", "--count", f"{fixture['code_head']}..HEAD"), "1")
        _ = created

    def test_create_precode_census_pins_each_row(self) -> None:
        # Sealed F01: the durable ordinary-closeout census. Each row's
        # disposition is pinned behaviorally on one fixture: same-check rows
        # refuse or judge identically, ruled-out rows hold structurally.
        self.assertEqual(
            [row.id for row in DIRECT_CREATE_PRECODE_CENSUS],
            [
                "entry-opt-out",
                "source-lineage",
                "plan-contract-identity",
                "commit-messages",
                "previewed-tree-equality",
                "branch-checkout",
                "review-claim",
                "invariant-knowledge-judgment",
                "generation-admission",
                "code-leg-intent-proof",
                "closeout-door",
                "task-intent-binding",
                "grant-and-bound-leaf",
            ],
        )
        self.assertEqual(
            [row.direct_disposition for row in DIRECT_CREATE_PRECODE_CENSUS],
            ["same"] * 10 + ["ruled-out", "ruled-out", "direct-only"],
        )
        root = Path(self.temp.name)
        fixture = self._converted_create_fixture(root / "create-census", answer=False)
        config = fixture["config"]
        contract = fixture["contract"]
        # Grant row: without the grant the create refuses at entry.
        revoke_grant(config, fixture["leaf_ref"])
        ungranted = direct_landing_tool(
            config, self._converted_create_request(fixture, dry_run=True)
        )
        self.assertFalse(ungranted["ok"])
        self.assertEqual(ungranted["status"], "direct-landing-leaf-without-grant")
        give_grant(config, fixture["leaf_ref"], granted_by="architect", granter_agent_id="census")
        # Source-bound trace computation materializes the enclosure: only after grant controls.
        world = fixture["world"]
        _edit(world, CODE_A.replace("return value", "return -value"))
        _answered(world, world.reanchor("RLZ-A00001"), *trace_rows(world, *TRACES))
        # Message row: without the create message the apply refuses up front.
        no_message = replace(
            self._converted_create_request(fixture, dry_run=False), code_commit_message=" "
        )
        refused = direct_landing_tool(config, no_message)
        self.assertFalse(refused["ok"])
        self.assertEqual(refused["status"], "direct-landing-code-message-required")
        # Tree row: a stale previewed tree refuses with nothing committed.
        stale = replace(
            self._converted_create_request(fixture, dry_run=True), candidate_tree="0" * 40
        )
        moved = direct_landing_tool(config, stale)
        self.assertFalse(moved["ok"])
        self.assertEqual(moved["status"], "direct-landing-candidate-tree-moved")
        # Door ruled out, code leg in: a real apply admits a generation with
        # no door publication and an enabled, proven code leg.
        preview = direct_landing_tool(config, self._converted_create_request(fixture, dry_run=True))
        request = self._converted_create_request(fixture, dry_run=False)
        request = replace(request, candidate_tree=preview["candidateTree"])
        landed = direct_landing_tool(config, request)
        self.assertEqual(landed["state"], "landed")
        record = direct_landing_store(contract).read()
        assert record is not None
        self.assertIsNone(record.doorPublication)
        op_input = record.input
        assert op_input.kind == "direct-landing"
        self.assertEqual(op_input.leafTaskDocumentRef, fixture["leaf_ref"])
        assert isinstance(record.taskIntent, MissingTaskIntent)
        self.assertEqual(record.taskIntent.state, "missing-intent")
        self.assertEqual(op_input.effectiveInput.code.state, "enabled")
        self.assertEqual(record.mutationEvidence["code"].state, "commit-proven")
        self.assertTrue(record.irreversibleBoundaryEntered)

    def test_recover_for_another_operations_record_is_refused(self) -> None:
        # Sealed F01 (L94-THE-TWO-CHECKS ruling, door stand-in): a recovery
        # acts only on its own operation. Asked for another operation's record
        # (a generation that is not the admitted one), the public recover
        # control refuses with lifecycle-generation-changed, and the admitted
        # generation still recovers afterwards.
        root = Path(self.temp.name)
        fixture = self._converted_create_fixture(root / "create-foreign-recover")
        config = fixture["config"]
        contract = fixture["contract"]
        _request, _created = self._cut_create_before_memory(fixture)
        store = direct_landing_store(contract)
        record = store.read()
        assert record is not None
        foreign = worktree_tools.worktree_operation_control_tool(
            config,
            worktree_tools.OperationControlRequest(
                contract_path=str(contract.contract_path),
                operation_kind="direct-landing",
                action="recover",
                expected_generation=record.generation + 1,
                intent_note="Recover another operation's record.",
            ),
        )
        self.assertFalse(foreign.get("ok"))
        self.assertEqual(foreign.get("status"), "lifecycle-generation-changed")
        result = worktree_tools.worktree_operation_control_tool(
            config,
            worktree_tools.OperationControlRequest(
                contract_path=str(contract.contract_path),
                operation_kind="direct-landing",
                action="recover",
                expected_generation=record.generation,
                intent_note="Recover the sealed code cut.",
            ),
        )
        self.assertTrue(result.get("ok"), result)
        successor = store.read()
        assert successor is not None
        self.assertEqual(successor.status, "completed")

    def test_direct_landing_without_a_bound_leaf_is_refused(self) -> None:
        # Sealed F01 (L94-THE-TWO-CHECKS ruling, binding stand-in): an
        # operation belongs to exactly one leaf task. A landing that names a
        # granted leaf bound to another master — i.e. no leaf bound to this
        # series contract — is refused before any durable effect.
        root = Path(self.temp.name)
        fixture = self._create_fixture(root / "create-unbound-leaf")
        config = fixture["config"]
        code = fixture["code"]
        memory = fixture["memory"]
        head_before = git(code, "rev-parse", "HEAD")
        memory_before = git(memory, "rev-parse", "HEAD")
        unbound = self._foreign_leaf_ref(config)
        request = replace(
            self._create_request(fixture, dry_run=False),
            leaf_task_document_ref=unbound,
        )
        refused = direct_landing_tool(config, request)
        self.assertFalse(refused["ok"])
        self.assertEqual(refused["status"], "direct-landing-foreign-leaf")
        self.assertEqual(git(code, "rev-parse", "HEAD"), head_before)
        self.assertEqual(git(memory, "rev-parse", "HEAD"), memory_before)

    def _second_granted_leaf(self, fixture: dict) -> TaskDocumentRef:
        """A second planning leaf of the same master with its own grant."""
        config = fixture["config"]
        tasks = fixture["tasks"]
        sibling = TaskDocument.model_validate(
            {
                "id": "TEST-DIRECT-SECOND",
                "slug": "second",
                "title": "Second direct leaf",
                "kind": "subTask",
                "status": "planning",
                "repo": "repo-a",
                "createdAt": "2026-10-06T00:00:00+00:00",
                "objective": "Exercise the sibling guard.",
                "requirements": ["The second leaf stays out until the first lands."],
            }
        )
        write_task_doc(tasks, sibling)
        master = read_task_doc(tasks / "task.json")
        write_task_doc(
            tasks,
            master.model_copy(
                update={
                    "subTasks": [
                        *master.subTasks,
                        SubTaskRef(
                            number="TEST-DIRECT-SECOND",
                            name="Second direct leaf",
                            file="second.md",
                            status="planning",
                        ),
                    ]
                }
            ),
        )
        second_ref = TaskDocumentRef(repository="repo-a", path="direct-task/second.json")
        give_grant(config, second_ref, granted_by="architect", granter_agent_id="fixture")
        return second_ref

    def _mark_completed_started(self, fixture: dict) -> None:
        """Mark the first leaf Completed with a worker start registration."""
        tasks = fixture["tasks"]
        first = read_task_doc(tasks / "leaf.json")
        write_task_doc(
            tasks,
            first.model_copy(
                update={
                    "status": "Completed",
                    "executionRegistrations": [
                        TaskExecutionRegistration(
                            sourceKind="terminal-catalog-seat",
                            role="worker",
                            sourceId="first-worker",
                            observedAt="2026-10-11T00:00:00Z",
                        )
                    ],
                }
            ),
        )

    def _second_workspace_context(
        self, fixture: dict, second_ref: TaskDocumentRef
    ) -> RoleLaunchContext:
        config = fixture["config"]
        topology = TaskDocumentTopology(config.coordination_root)
        master_ref = TaskDocumentRef(repository="repo-a", path="direct-task/task.json")
        return RoleLaunchContext(
            role="worker",
            sprint=topology.resolve(master_ref),
            master=topology.resolve(master_ref),
            task=topology.resolve(second_ref),
            effective_task=None,
        )

    def test_direct_create_apply_completes_after_a_cut_ledger_write(self) -> None:
        root = Path(self.temp.name)
        fixture = self._converted_create_fixture(root / "create-ledger-cut")
        config = fixture["config"]
        code = fixture["code"]
        memory = fixture["memory"]
        request, created, cut_memory = self._cut_create_at_ledger_write(fixture)
        # The same call again completes the admitted pair: the same commits,
        # then the ledger row, with one code commit total.
        again = direct_landing_tool(config, request)
        self.assertEqual(again["state"], "landed")
        self.assertEqual(again["codeCommit"], created)
        self.assertEqual(again["memoryContentCommit"], cut_memory)
        self.assertEqual(git(code, "rev-parse", "HEAD"), created)
        self.assertEqual(git(code, "rev-list", "--count", f"{fixture['code_head']}..HEAD"), "1")
        self.assertEqual(git(memory, "rev-parse", "HEAD"), cut_memory)
        ledger_text = (memory / "memory.md").read_text(encoding="utf-8")
        self.assertIn(created, ledger_text)
        self.assertIn(cut_memory, ledger_text)

    def test_recover_control_completes_a_cut_ledger_write(self) -> None:
        # The recover control completes the same cut on a fresh pair.
        root = Path(self.temp.name)
        fixture = self._converted_create_fixture(root / "create-ledger-recover")
        config = fixture["config"]
        contract = fixture["contract"]
        code = fixture["code"]
        memory = fixture["memory"]
        preview = direct_landing_tool(config, self._converted_create_request(fixture, dry_run=True))
        request = self._converted_create_request(fixture, dry_run=False)
        request = replace(request, candidate_tree=preview["candidateTree"])
        store = direct_landing_store(contract)
        with (
            mock.patch(
                "agents_remember.worktrees.integration.direct_landing."
                "direct_landing_execution.refresh_memory_cache",
                side_effect=RuntimeError("ledger interrupted"),
            ),
            self.assertRaises(DirectLandingError),
        ):
            direct_landing_owner.direct_landing(config, request, contract)
        created = git(code, "rev-parse", "HEAD")
        cut_memory = git(memory, "rev-parse", "HEAD")
        record = store.read()
        assert record is not None
        completed = recover_direct_landing_under_authority(contract, store, record)
        assert isinstance(completed.result, dict)
        self.assertEqual(completed.result["codeCommit"], created)
        self.assertEqual(completed.result["memoryContentCommit"], cut_memory)
        self.assertEqual(git(code, "rev-parse", "HEAD"), created)
        self.assertEqual(git(memory, "rev-parse", "HEAD"), cut_memory)
        self.assertEqual(git(code, "rev-list", "--count", f"{fixture['code_head']}..HEAD"), "1")

    def test_completed_leaf_with_dirty_memory_guards_sibling(self) -> None:
        # Sealed F05: the R2 counterexample. A cut publishes the first leaf's
        # code (clean) with memory dirty and no pair result; marking it
        # Completed does not release the guard. Both consumers refuse.
        root = Path(self.temp.name)
        fixture = self._converted_create_fixture(root / "sibling-memory-dirt")
        config = fixture["config"]
        code = fixture["code"]
        memory = fixture["memory"]
        _request, _created = self._cut_create_before_memory(fixture)
        self.assertEqual(git(code, "status", "--porcelain"), "")
        self.assertTrue(git(memory, "status", "--porcelain"))
        self._mark_completed_started(fixture)
        second_ref = self._second_granted_leaf(fixture)
        found = open_direct_sibling(config, second_ref)
        self.assertIsNotNone(found)
        assert found is not None
        self.assertEqual(found[0], fixture["leaf_ref"])
        refused = direct_landing_tool(
            config,
            replace(
                self._converted_create_request(fixture, dry_run=True),
                leaf_task_document_ref=second_ref,
            ),
        )
        self.assertFalse(refused["ok"])
        self.assertEqual(refused["status"], "direct-landing-sibling-open")
        detail = refused["detail"]
        assert isinstance(detail, str)
        self.assertIn(fixture["leaf_ref"].path, detail)
        with self.assertRaises(RolePreparationError) as raised:
            _resolve_workspace(config, self._second_workspace_context(fixture, second_ref))
        self.assertIn("direct-grant-sibling-open", str(raised.exception))

    def test_completed_leaf_with_clean_pair_and_no_proof_guards_sibling(self) -> None:
        # Sealed F05: a cut leaves code published and clean; cleaning memory
        # too still guards, because the incomplete generation is no landing
        # proof and completion is not landing.
        root = Path(self.temp.name)
        fixture = self._converted_create_fixture(root / "sibling-cancelled-pair")
        config = fixture["config"]
        code = fixture["code"]
        memory = fixture["memory"]
        _request, _created = self._cut_create_before_memory(fixture)
        # Converted memory tracks its onboarding sidecars: cleaning restores
        # the checkout and drops untracked rows instead of deleting content.
        git(memory, "checkout", "--", ".")
        git(memory, "clean", "-fd")
        self.assertEqual(git(code, "status", "--porcelain"), "")
        self.assertEqual(git(memory, "status", "--porcelain"), "")
        self._mark_completed_started(fixture)
        second_ref = self._second_granted_leaf(fixture)
        found = open_direct_sibling(config, second_ref)
        self.assertIsNotNone(found)
        assert found is not None
        self.assertEqual(found[0], fixture["leaf_ref"])

    def test_landed_leaf_with_completed_document_releases_sibling(self) -> None:
        # Sealed F05: a completed generation with the published pair releases
        # the guard even after the document is marked Completed.
        root = Path(self.temp.name)
        fixture = self._converted_create_fixture(root / "sibling-landed")
        config = fixture["config"]
        preview = direct_landing_tool(config, self._converted_create_request(fixture, dry_run=True))
        request = self._converted_create_request(fixture, dry_run=False)
        request = replace(request, candidate_tree=preview["candidateTree"])
        landed = direct_landing_tool(config, request)
        self.assertEqual(landed["state"], "landed")
        self._mark_completed_started(fixture)
        second_ref = self._second_granted_leaf(fixture)
        self.assertIsNone(open_direct_sibling(config, second_ref))

    def test_direct_create_apply_refuses_a_conflicting_generation(self) -> None:
        root = Path(self.temp.name)
        fixture = self._converted_create_fixture(root / "create-conflict")
        config = fixture["config"]
        contract = fixture["contract"]
        code = fixture["code"]
        memory = fixture["memory"]
        preview = direct_landing_tool(config, self._converted_create_request(fixture, dry_run=True))
        request = self._converted_create_request(fixture, dry_run=False)
        request = replace(request, candidate_tree=preview["candidateTree"])
        store = direct_landing_store(contract)
        with (
            mock.patch(
                "agents_remember.worktrees.integration.direct_landing."
                "direct_landing_execution.prove_git_commit",
                side_effect=RuntimeError("receipt interrupted"),
            ),
            self.assertRaises(DirectLandingError),
        ):
            direct_landing_owner.direct_landing(config, request, contract)
        first = store.read()
        assert first is not None
        created = git(code, "rev-parse", "HEAD")
        cut_memory = git(memory, "rev-parse", "HEAD")
        # A different request while the first generation is in flight refuses
        # as a conflict, leaving the admitted pair untouched.
        other = replace(request, intent_note="another approval")
        with self.assertRaisesRegex(DirectLandingError, "direct-landing-input-conflict"):
            direct_landing_owner.direct_landing(config, other, contract)
        self.assertEqual(git(code, "rev-parse", "HEAD"), created)
        self.assertEqual(git(memory, "rev-parse", "HEAD"), cut_memory)
        after = store.read()
        assert after is not None
        self.assertEqual(after.fingerprint, first.fingerprint)
        self.assertEqual(after.status, first.status)

    def test_direct_create_apply_refuses_a_moved_checkout(self) -> None:
        root = Path(self.temp.name)
        fixture = self._create_fixture(root / "create-moved")
        config = fixture["config"]
        preview = direct_landing_tool(config, self._create_request(fixture, dry_run=True))
        code = fixture["code"]
        head_before = git(code, "rev-parse", "HEAD")
        (code / "late.py").write_text("late\n", encoding="utf-8")
        request = self._create_request(fixture, dry_run=False)
        request = replace(request, candidate_tree=preview["candidateTree"])
        with self.assertRaisesRegex(DirectLandingError, "no longer matches"):
            direct_landing(config, request, fixture["contract"])
        # The refusal came before the code commit: nothing is committed on either
        # side and the checkout's files are as before.
        self.assertEqual(git(code, "rev-parse", "HEAD"), head_before)
        self.assertTrue((code / "late.py").is_file())
        self.assertTrue((code / "extra.py").is_file())

    def test_direct_create_apply_requires_the_previewed_tree(self) -> None:
        root = Path(self.temp.name)
        fixture = self._create_fixture(root / "create-no-tree")
        config = fixture["config"]
        code = fixture["code"]
        memory = fixture["memory"]
        head_before = git(code, "rev-parse", "HEAD")
        memory_before = git(memory, "rev-parse", "HEAD")
        preview = direct_landing_tool(config, self._create_request(fixture, dry_run=True))
        self.assertTrue(preview["candidateTree"])
        with self.assertRaisesRegex(DirectLandingError, "candidate-tree-required"):
            direct_landing(
                config, self._create_request(fixture, dry_run=False), fixture["contract"]
            )
        # Nothing was committed on either side and the files are as previewed.
        self.assertEqual(git(code, "rev-parse", "HEAD"), head_before)
        self.assertEqual(git(memory, "rev-parse", "HEAD"), memory_before)
        self.assertTrue((code / "extra.py").is_file())
        # F02: malformed and stale assertions refuse the same way, unchanged.
        for bad_tree in ("not-a-tree", "0" * 40):
            bad = replace(self._create_request(fixture, dry_run=False), candidate_tree=bad_tree)
            with self.assertRaisesRegex(DirectLandingError, "candidate-tree-moved"):
                direct_landing(config, bad, fixture["contract"])
        self.assertEqual(git(code, "rev-parse", "HEAD"), head_before)
        self.assertEqual(git(memory, "rev-parse", "HEAD"), memory_before)
        self.assertTrue((code / "extra.py").is_file())

    def _foreign_leaf_ref(self, config: McpRuntimeConfig) -> TaskDocumentRef:
        """A granted leaf of another master, for foreign-authority refusals."""

        foreign = TaskDocumentRef(repository="repo-a", path="foreign-master/leaf.json")
        foreign_tasks = config.coordination_root / "tasks" / "repo-a" / "foreign-master"
        foreign_tasks.mkdir(parents=True, exist_ok=True)
        write_task_doc(
            foreign_tasks,
            TaskDocument.model_validate(
                {
                    "id": "TEST-FOREIGN-LEAF",
                    "slug": "leaf",
                    "title": "Foreign leaf",
                    "kind": "subTask",
                    "status": "planning",
                    "repo": "repo-a",
                    "createdAt": "2026-10-06T00:00:00+00:00",
                    "objective": "Belongs to another master.",
                    "requirements": ["The foreign leaf stays foreign."],
                }
            ),
        )
        write_grant_file(
            grant_path_for(config.coordination_root, foreign),
            DirectExecutionGrant(
                leaf=foreign,
                granted_by="architect",
                granted_at="2026-10-06T00:00:00+00:00",
                granter_agent_id="fixture",
            ),
        )
        return foreign

    def test_direct_landing_refuses_a_foreign_leaf_grant(self) -> None:
        root = Path(self.temp.name)
        fixture = self._create_fixture(root / "create-foreign")
        config = fixture["config"]
        code = fixture["code"]
        memory = fixture["memory"]
        head_before = git(code, "rev-parse", "HEAD")
        memory_before = git(memory, "rev-parse", "HEAD")
        foreign = self._foreign_leaf_ref(config)
        preview = direct_landing_tool(config, self._create_request(fixture, dry_run=True))
        request = replace(
            self._create_request(fixture, dry_run=False),
            leaf_task_document_ref=foreign,
            candidate_tree=preview["candidateTree"],
        )
        with self.assertRaisesRegex(DirectLandingError, "direct-landing-foreign-leaf"):
            direct_landing(config, request, fixture["contract"])
        refused = direct_landing_tool(config, replace(request, dry_run=True))
        self.assertFalse(refused["ok"])
        self.assertEqual(refused["status"], "direct-landing-foreign-leaf")
        # Nothing was committed on either side.
        self.assertEqual(git(code, "rev-parse", "HEAD"), head_before)
        self.assertEqual(git(memory, "rev-parse", "HEAD"), memory_before)

    def test_direct_verify_refuses_a_foreign_leaf_grant(self) -> None:
        root = Path(self.temp.name)
        fixture = _converted_series_fixture(root / "verify-foreign")
        world = fixture["world"]
        config = fixture["config"]
        code = commit(world.code, {A: CODE_A.replace("return value", "return -value")})
        _answered(world, world.reanchor("RLZ-A00001"), *trace_rows(world, *TRACES))
        bind_worktree_services(build_default_worktree_services())
        memory_before = git(world.memory, "rev-parse", "HEAD")
        foreign = self._foreign_leaf_ref(config)
        request = replace(_converted_request(fixture, code), leaf_task_document_ref=foreign)
        with self.assertRaisesRegex(DirectLandingError, "direct-landing-foreign-leaf"):
            direct_landing_owner.direct_landing(config, request, fixture["contract"])
        # Nothing was committed on either side.
        self.assertEqual(git(world.code, "rev-parse", "HEAD"), code)
        self.assertEqual(git(world.memory, "rev-parse", "HEAD"), memory_before)

    def test_direct_verify_refuses_a_mismatched_history_owner(self) -> None:
        root = Path(self.temp.name)
        fixture = _converted_series_fixture(root / "verify-owner")
        world = fixture["world"]
        config = fixture["config"]
        code = commit(world.code, {A: CODE_A.replace("return value", "return -value")})
        _answered(world, world.reanchor("RLZ-A00001"), *trace_rows(world, *TRACES))
        # The bound leaf's file stays open, but another leaf's open history is
        # the one the gate selects: the landing must not close a foreign file.
        other = {"schema": "ar-history/v1", "leaf": "260928-MIK-L96", "closed": False, "rows": []}
        (world.memory / "knowledge/history/260928-MIK-L96.json").write_text(
            canonical_text(other), encoding="utf-8"
        )
        (world.memory / f"knowledge/history/{LEAF}.json").unlink()
        bind_worktree_services(build_default_worktree_services())
        memory_before = git(world.memory, "rev-parse", "HEAD")
        request = _converted_request(fixture, code)
        # L52 now refuses first at the gate with the leaf named (land with its
        # own leaf_id); the property holds: refused, foreign file unclosed.
        with self.assertRaisesRegex(
            DirectLandingError, "direct-landing-knowledge-gate-refused"
        ) as refused:
            direct_landing_owner.direct_landing(config, request, fixture["contract"])
        self.assertIn("260928-MIK-L96", str(refused.exception))
        self.assertEqual(git(world.code, "rev-parse", "HEAD"), code)
        self.assertEqual(git(world.memory, "rev-parse", "HEAD"), memory_before)

    def test_direct_verify_refuses_a_sibling_leaf_history(self) -> None:
        root = Path(self.temp.name)
        fixture = _converted_series_fixture(root / "verify-sibling")
        world = fixture["world"]
        config = fixture["config"]
        tasks = fixture["contract"].task_root
        sibling = TaskDocument.model_validate(
            {
                "id": "TEST-DIRECT-SIBLING",
                "slug": "sibling",
                "title": "Direct sibling",
                "kind": "subTask",
                "status": "planning",
                "repo": "repo-a",
                "createdAt": "2026-10-06T00:00:00+00:00",
                "objective": "Exercise the sibling refusal.",
                "requirements": ["The sibling leaf stays out of this landing."],
            }
        )
        write_task_doc(tasks, sibling)
        master = TaskDocument.model_validate(
            {
                "id": "TEST-DIRECT-MASTER",
                "slug": "direct-task",
                "title": "Direct master",
                "kind": "master",
                "status": "inProgress",
                "repo": "repo-a",
                "createdAt": "2026-10-06T00:00:00+00:00",
                "executionNature": "atomic",
                "subTasks": [
                    SubTaskRef(
                        number=LEAF, name="Direct leaf", file="leaf.md", status="planning"
                    ).model_dump(mode="json"),
                    SubTaskRef(
                        number="TEST-DIRECT-SIBLING",
                        name="Direct sibling",
                        file="sibling.md",
                        status="planning",
                    ).model_dump(mode="json"),
                ],
            }
        )
        write_task_doc(tasks, master)
        sibling_ref = TaskDocumentRef(repository="repo-a", path="direct-task/sibling.json")
        give_grant(config, sibling_ref, granted_by="architect", granter_agent_id="fixture")
        code = commit(world.code, {A: CODE_A.replace("return value", "return -value")})
        _answered(world, world.reanchor("RLZ-A00001"), *trace_rows(world, *TRACES))
        bind_worktree_services(build_default_worktree_services())
        memory_before = git(world.memory, "rev-parse", "HEAD")
        request = replace(_converted_request(fixture, code), leaf_task_document_ref=sibling_ref)
        # The sibling passes the entry authority (it is a master row), but the
        # gate's open history belongs to this series' leaf, not to the sibling.
        # L52 now refuses first at the gate with the leaf named (land with its
        # own leaf_id); the property holds: refused, foreign file unclosed.
        with self.assertRaisesRegex(
            DirectLandingError, "direct-landing-knowledge-gate-refused"
        ) as refused:
            direct_landing_owner.direct_landing(config, request, fixture["contract"])
        self.assertIn("TEST-DIRECT-SIBLING", str(refused.exception))
        self.assertEqual(git(world.code, "rev-parse", "HEAD"), code)
        self.assertEqual(git(world.memory, "rev-parse", "HEAD"), memory_before)

    def test_direct_landing_refuses_an_unknown_repository_leaf(self) -> None:
        root = Path(self.temp.name)
        fixture = self._create_fixture(root / "create-unknown-repo")
        config = fixture["config"]
        unknown = TaskDocumentRef(repository="repo-b", path="other-master/leaf.json")
        write_grant_file(
            grant_path_for(config.coordination_root, unknown),
            DirectExecutionGrant(
                leaf=unknown,
                granted_by="architect",
                granted_at="2026-10-06T00:00:00+00:00",
                granter_agent_id="fixture",
            ),
        )
        request = replace(
            self._create_request(fixture, dry_run=False),
            leaf_task_document_ref=unknown,
            candidate_tree="0" * 40,
        )
        # The grant exists, but the leaf resolves to no canonical task document.
        with self.assertRaisesRegex(DirectLandingError, "direct-landing-leaf-missing"):
            direct_landing_owner.direct_landing(config, request, fixture["contract"])

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
        _answered(world, world.reanchor("RLZ-A00001"), *trace_rows(world, *TRACES))
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
        world.rows(row, family, *trace_rows(world, *TRACES))
        bind_worktree_services(build_default_worktree_services())
        # MIK-R152: the publication runs the repository's commit check. The failing
        # hooks refuse the landing below; the checks of a publication are the gate,
        # the validator and the hook.
        hook_log = Path(self.temp.name) / "memory-commit-hooks.log"
        _install_failing_commit_hooks(world.memory, hook_log)
        request = _converted_request(fixture, code)
        refused = direct_landing_tool(fixture["config"], request)
        self.assertFalse(refused["ok"], refused)
        self.assertIn("governing row", str(refused["detail"]))
        world.rows(
            {**row, "disposition": "changed", "effect": "clarify"},
            family,
            *trace_rows(world, *TRACES),
        )
        judge = KnowledgeGate.direct_verdict
        judged = []

        def remember(self, contract, **kwargs):
            result = judge(self, contract, **kwargs)
            judged.append(kwargs["memory_tree"])
            return result

        with mock.patch.object(KnowledgeGate, "direct_verdict", remember):
            landed = direct_landing_tool(fixture["config"], request)
        # The failing hooks refuse at publication: nothing published, hooks ran.
        self.assertFalse(landed["ok"], landed)
        self.assertIn("commit check", str(landed["detail"]), landed)
        self.assertEqual(hook_log.read_text(encoding="utf-8").splitlines(), ["pre-commit"])
        # Passing hooks land the same call with the judged tree.
        _install_passing_commit_hooks(world.memory, hook_log)
        with mock.patch.object(KnowledgeGate, "direct_verdict", remember):
            landed = direct_landing_tool(fixture["config"], request)
        self.assertTrue(landed["ok"], landed)
        self.assertIn("pre-commit", hook_log.read_text(encoding="utf-8").splitlines())
        self.assertNotIn("commit-msg", hook_log.read_text(encoding="utf-8").splitlines())
        # The gate judged once per attempt: the refused attempt and the landing.
        self.assertEqual(len(judged), 2)
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
        _answered(world, world.reanchor("RLZ-A00001"), *trace_rows(world, *TRACES))
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
        _answered(world, world.reanchor("RLZ-A00001"), *trace_rows(world, *TRACES))
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
        _answered(world, world.reanchor("RLZ-A00001"), *trace_rows(world, *TRACES))
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
        _answered(world, world.reanchor("RLZ-A00001"), *trace_rows(world, *TRACES))
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
        _answered(world, world.reanchor("RLZ-A00001"), *trace_rows(world, *TRACES))
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
        _answered(world, world.reanchor("RLZ-A00001"), *trace_rows(world, *TRACES))
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
        _answered(world, world.reanchor("RLZ-A00001"), *trace_rows(world, *TRACES))
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
        _answered(world, world.reanchor("RLZ-A00001"), *trace_rows(world, *TRACES))
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
        _answered(world, world.reanchor("RLZ-A00001"), *trace_rows(world, *TRACES))
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
        _answered(world, world.reanchor("RLZ-A00001"), *trace_rows(world, *TRACES))
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
        _answered(world, world.reanchor("RLZ-A00001"), *trace_rows(world, *TRACES))
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
        world.rows(row, family, *trace_rows(world, *TRACES), closed=True)
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
            *trace_rows(world, *TRACES),
            closed=True,
        )
        with self.assertRaisesRegex(RuntimeError, "governing row"):
            require_gated_recovery(contract, code, invalid)
        git(world.memory, "add", "-A")
        git(world.memory, "commit", "-m", f"valid recorded rows\n\nCode-Commit: {code}")
        valid = head_commit(world.memory)
        require_gated_recovery(contract, code, valid)
        world.rows(row, family, *trace_rows(world, *TRACES))
        require_gated_recovery(contract, code, valid)
        self.assertEqual(head_commit(world.memory), valid)

    def test_direct_landing_publishes_memory_and_recovers_independently_of_cache(self) -> None:
        root = Path(self.temp.name)
        fixture = _converted_series_fixture(root / "fx")
        config = fixture["config"]
        contract = fixture["contract"]
        memory = fixture["memory"]
        code = fixture["code"]
        code_base = git(code, "rev-parse", "HEAD")
        candidate_tree = require_git(code, ["rev-parse", f"{code_base}^{{tree}}"])
        fixture["code_head"] = code_base
        fixture["candidate_tree"] = candidate_tree

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
                    leaf_task_document_ref=fixture["leaf_ref"],
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
                leaf_task_document_ref=fixture["leaf_ref"],
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
                    leaf_task_document_ref=fixture["leaf_ref"],
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
        # exactly one Code-Commit trailer naming the code commit this landing verified,
        # and the Commit-Check-Pass trailer for its tree (MIK-R152).
        memory_body = git(memory, "show", "-s", "--format=%B", str(landed["memoryContentCommit"]))
        memory_tree = git(memory, "rev-parse", f"{landed['memoryContentCommit']}^{{tree}}")
        self.assertEqual(
            memory_body,
            f"direct memory\n\nCode-Commit: {fixture['code_head']}\n"
            f"Commit-Check-Pass: tree={memory_tree} hook=none",
        )
        message_file = root / "direct-memory-message.txt"
        message_file.write_text(f"{memory_body}\n", encoding="utf-8")
        self.assertEqual(
            git(memory, "interpret-trailers", "--parse", message_file.as_posix()),
            f"Code-Commit: {fixture['code_head']}\nCommit-Check-Pass: tree={memory_tree} hook=none",
        )
        self.assertEqual(git(memory, "ls-files", "memory.md"), "")
        self.assertIn("/memory.md", (memory / ".gitignore").read_text(encoding="utf-8"))
        ledger_text = (memory / "memory.md").read_text(encoding="utf-8")
        self.assertIn(fixture["code_head"], ledger_text)
        self.assertIn(landed["memoryContentCommit"], ledger_text)

    def test_converted_direct_landing_reuses_clean_memory_and_replays_terminal(self) -> None:
        """B06 port: the retired clean-memory reuse/replay/attribution guarantees.

        The retired ``_assert_clean_memory_reused`` helper ran on unconverted
        memory, which cannot land anymore; these are its non-format guarantees,
        mapped onto converted mechanics (a converted direct landing always
        publishes exactly one content commit pairing the code commit -- the
        message plus exactly one ``Code-Commit`` trailer -- so "reuse" is one
        paired commit for the clean tree, never zero and never two):
        one paired commit and nothing else moves; the completed generation is
        terminal (generation 1); repeating the request never replaces it (the
        product refuses the fresh generation naming the accepted fingerprint,
        store byte-identical, no new commit); and no further attribution is
        invented by the refusals.
        """
        root = Path(self.temp.name)
        fixture = _converted_series_fixture(root / "reuse")
        world = fixture["world"]
        config = fixture["config"]
        contract = fixture["contract"]
        memory = fixture["memory"]
        code = commit(world.code, {A: CODE_A.replace("return value", "return -value")})
        _answered(world, world.reanchor("RLZ-A00001"), *trace_rows(world, *TRACES))
        bind_worktree_services(build_default_worktree_services())
        request = _converted_request(fixture, code)
        leaf_before = git(memory, "rev-parse", "leaf")
        landed = direct_landing(config, request, contract)
        self.assertEqual(landed["state"], "landed")
        head = head_commit(memory)
        self.assertEqual(landed["memoryContentCommit"], head)
        # Exactly one paired commit for the clean tree: the message verbatim plus
        # exactly one Code-Commit trailer naming the landed code commit.
        self.assertEqual(git(memory, "rev-list", "--count", f"{leaf_before}..{head}"), "1")
        self.assertEqual(
            git(memory, "show", "-s", "--format=%s", head), "publish exact converted tree"
        )
        # Tree reuse: the pairing commit publishes the judged tree with only the
        # first-landing scaffolding (ignore rules, the leaf history file) and the
        # gate-authored sidecar for the changed file -- no other content invented.
        self.assertEqual(
            git(memory, "diff-tree", "--no-commit-id", "--name-only", "-r", leaf_before, head),
            ".gitignore\nknowledge/history/260928-MIK-L97.json\nonboarding/pkg/a.py.json",
        )
        landed_tree = git(memory, "rev-parse", f"{head}^{{tree}}")
        self.assertEqual(
            git(memory, "show", "-s", "--format=%B", head),
            f"publish exact converted tree\n\nCode-Commit: {code}\n"
            f"Commit-Check-Pass: tree={landed_tree} hook=none",
        )
        store = direct_landing_store(contract)
        completed = store.read()
        assert completed is not None
        self.assertEqual((completed.status, completed.generation), ("completed", 1))
        attributed = git(memory, "log", "--all", "--format=%H", f"--grep=Code-Commit: {code}")
        # Terminal replay: the same request is the completed generation's own
        # fingerprint, so it is never replaced -- the refusal names the accepted
        # fingerprint, and nothing moves (no commit, store byte-identical).
        with self.assertRaises(DirectLandingError) as replay_refused:
            direct_landing(config, request, contract)
        self.assertEqual(replay_refused.exception.status, "direct-landing-input-conflict")
        self.assertEqual(
            replay_refused.exception.expected.get("acceptedFingerprint"), completed.fingerprint
        )
        self.assertEqual(head_commit(memory), head)
        self.assertEqual(store.read(), completed)
        # Losing the cache file changes the admitted identity the same way: the
        # same refusal, still no commit and no invented attribution.
        (memory / "memory.md").unlink()
        with self.assertRaises(DirectLandingError) as reuse_refused:
            direct_landing(config, request, contract)
        self.assertEqual(reuse_refused.exception.status, "direct-landing-input-conflict")
        self.assertEqual(head_commit(memory), head)
        self.assertEqual(store.read(), completed)
        self.assertEqual(
            git(memory, "log", "--all", "--format=%H", f"--grep=Code-Commit: {code}"),
            attributed,
        )

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
        # (The converted world's leaf branch is checked out, so move it with switch -C.)
        # The converted work branch starts at the base: advance main first so
        # the move is a real divergence, like the old backward move to base.
        code_repo = fixture["code"]
        advance = git(
            code_repo, "commit-tree", git(code_repo, "rev-parse", "HEAD^{tree}"), "-m", "advance"
        )
        git(code_repo, "update-ref", "refs/heads/main", advance)
        git(fixture["code"], "switch", "-q", "-C", contract.code_work_branch, "main")
        self.assertEqual(
            classify_direct_landing_recovery(contract, record).state, "developer-decision"
        )
        git(fixture["code"], "switch", "-q", "-C", contract.code_work_branch, fixture["code_head"])
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
            leaf_task_document_ref=fixture["leaf_ref"],
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
