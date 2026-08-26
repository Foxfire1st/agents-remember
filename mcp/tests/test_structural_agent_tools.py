"""L19 structural agent operations: relationship routing without runtime-id cognition."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.application.structural.agent_tools import (
    StructuralAgentRuntime,
    _curator_route_review_refusal,
    _implementation_series_admission_refusal,
    dispatch_agent_tool,
    message_child_tool,
    message_parent_tool,
)
from agents_remember.controlplane.operator_inbox_store import OperatorInboxStore
from agents_remember.kernel.primitives.observer_paths import observer_root
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig, RepositoryScope
from agents_remember.models.structural.agent import (
    DispatchAgentRequest,
    StructuralMessageRequest,
)
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.models.terminal_catalog import TerminalCatalogEntry
from agents_remember.serving.inbox_delivery import target_session_for_entry
from agents_remember.serving.structural_seats import StructuralSeatError, StructuralSeatResolver
from agents_remember.serving.terminal_catalog import TerminalCatalog, terminal_catalog_path
from agents_remember.tasks import TaskDocument, read_task_doc, write_task_doc
from agents_remember.tasks.document_refs import TaskDocumentTopology
from agents_remember.worktrees.activation.atomic_series_activation import observe_atomic_series
from agents_remember.worktrees.activation.atomic_series_activation_release import (
    release_atomic_series_selection,
)
from agents_remember.worktrees.modules.git import branch_commit, branch_exists
from agents_remember.worktrees.modules.startup import start_contract as start_contract_mod
from agents_remember.worktrees.modules.startup.start_contract import (
    MasterSeriesContractSpec,
    ensure_master_series_contract,
)
from agents_remember.worktrees.route_review import RouteReviewError
from agents_remember.worktrees.source_lineage import lineage_refusal, source_lineage_for_task
from agents_remember.worktrees.task_resolver import series_contract_path
from agents_remember.worktrees.worktree_contract import (
    WorktreeContract,
    load_contract,
    write_contract,
)
from test_worktree_support import git, seed_memory_ledger


class _Host:
    def has_session(self, _tmux_name: str) -> bool:
        return False


def _config(root: Path) -> McpRuntimeConfig:
    return McpRuntimeConfig(
        config_path=root / "settings.json",
        coordination_root=root,
        workspace_root=root,
        transcript_root=root / "logs" / "mcp",
        repositories={"repo": RepositoryScope("repo", root / "workspace" / "repo")},
    )


def _seed_dispatch_memory_source(code_repo: Path, memory_repo: Path) -> None:
    """Give the external-memory super source its canonical code-tip mapping."""

    git(memory_repo, "checkout", "ar/super")
    seed_memory_ledger(memory_repo, "repo", branch_commit(code_repo, "ar/super"))
    git(memory_repo, "checkout", "main")


def _task_doc(**values: object) -> TaskDocument:
    return TaskDocument.model_validate(
        {
            "id": values.pop("id"),
            "slug": values.pop("slug"),
            "title": values.pop("title"),
            "kind": values.pop("kind"),
            "repo": "repo",
            "createdAt": "2026-08-11T00:00",
            **values,
        }
    )


def _write_topology(root: Path) -> tuple[TaskDocumentRef, TaskDocumentRef, TaskDocumentRef]:
    task_root = root / "tasks" / "repo"
    write_task_doc(
        task_root / "sprint",
        _task_doc(
            id="SPRINT",
            slug="sprint",
            title="Sprint",
            kind="master",
            orchestrates=["master"],
            integrationBranch="ar/super",
            executionGraph={
                "nodes": [
                    {"repository": "repo", "path": "master/task.json"},
                ],
                "edges": [],
            },
        ),
    )
    write_task_doc(
        task_root / "master",
        _task_doc(
            id="MASTER",
            slug="master",
            title="Master",
            kind="master",
            executionNature="atomic",
            subTasks=[
                {
                    "number": "leaf-1",
                    "name": "Leaf 1",
                    "file": "leaf-1.md",
                    "status": "inProgress",
                }
            ],
        ),
    )
    write_task_doc(
        task_root / "master",
        _task_doc(
            id="leaf-1",
            slug="leaf-1",
            title="Leaf 1",
            kind="subTask",
            master="task.md",
        ),
    )
    return (
        TaskDocumentRef(repository="repo", path="sprint/task.json"),
        TaskDocumentRef(repository="repo", path="master/task.json"),
        TaskDocumentRef(repository="repo", path="master/leaf-1.json"),
    )


def _seat(
    session_id: str,
    document: TaskDocumentRef,
    role: str,
    *,
    status: str = "running",
) -> TerminalCatalogEntry:
    return TerminalCatalogEntry(
        id=session_id,
        label=session_id,
        kind="harness",
        harness="codex",
        lifecycle_id=None,
        cwd=Path("/workspace"),
        tmux_name=f"ar-{session_id}",
        command=("codex",),
        created_at="2026-08-11T00:00:00+00:00",
        last_attached_at="2026-08-11T00:00:00+00:00",
        status=status,  # type: ignore[arg-type]
        task_document_ref=document,
        seat_role=role,
    )


class StructuralAgentToolTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.sprint, self.master, self.leaf = _write_topology(self.root)
        self.config = _config(self.root)
        self.catalog = TerminalCatalog(terminal_catalog_path(self.root))

    def test_child_to_replacement_parent_is_resolved_by_task_containment(self) -> None:
        self.catalog.upsert(_seat("manager-old", self.master, "manager", status="terminated"))
        self.catalog.upsert(_seat("manager-new", self.master, "manager"))
        self.catalog.upsert(_seat("worker", self.leaf, "worker"))

        result = message_parent_tool(
            self.config,
            StructuralMessageRequest(
                ask="Review my turn report.",
                response="The report is durable.",
                message_kind="turn-report",
            ),
            StructuralAgentRuntime(
                host=_Host(),  # type: ignore[arg-type]
                environ={"AR_HOSTED_SESSION_ID": "worker", "AR_SPAWN_ROLE": "worker"},
            ),
        )

        self.assertEqual(result["taskDocumentRef"], self.master.model_dump())
        self.assertEqual(result["role"], "manager")
        self.assertTrue(
            {"session", "sessionId", "agentId", "lifecycleId", "entryId"}.isdisjoint(result)
        )
        row = next(iter(OperatorInboxStore(observer_root(self.config)).current().values()))
        self.assertEqual(row.agentId, "manager-new")
        self.assertEqual(row.taskDocumentRef, self.master)

    def test_parent_to_replacement_child_is_resolved_by_document_and_role(self) -> None:
        self.catalog.upsert(_seat("manager", self.master, "manager"))
        self.catalog.upsert(_seat("worker-old", self.leaf, "worker", status="terminated"))
        self.catalog.upsert(_seat("worker-new", self.leaf, "worker"))

        result = message_child_tool(
            self.config,
            StructuralMessageRequest(
                ask="Address the review finding.",
                response="Continue on the same leaf.",
                task_document_ref=self.leaf,
                role="worker",
            ),
            StructuralAgentRuntime(
                host=_Host(),  # type: ignore[arg-type]
                environ={"AR_HOSTED_SESSION_ID": "manager", "AR_SPAWN_ROLE": "manager"},
            ),
        )

        self.assertEqual(result["taskDocumentRef"], self.leaf.model_dump())
        self.assertEqual(result["role"], "worker")
        row = next(iter(OperatorInboxStore(observer_root(self.config)).current().values()))
        self.assertEqual(row.taskDocumentRef, self.leaf)
        self.assertEqual(row.recipientRole, "worker")
        self.assertEqual(target_session_for_entry(self.catalog, row).id, "worker-new")  # type: ignore[union-attr]

    def test_duplicate_current_occupants_fail_closed(self) -> None:
        self.catalog.upsert(_seat("manager-a", self.master, "manager"))
        self.catalog.upsert(_seat("manager-b", self.master, "manager"))
        resolver = StructuralSeatResolver(self.catalog, TaskDocumentTopology(self.root))

        with self.assertRaisesRegex(StructuralSeatError, "multiple running occupants"):
            resolver.current(self.master, "manager")

    def test_curator_dispatch_refuses_before_spawn_without_leaf_review_contract(self) -> None:
        self.catalog.upsert(_seat("manager", self.master, "manager"))
        with mock.patch(
            "agents_remember.application.structural.agent_tools.spawn_agent_session_tool"
        ) as spawn:
            result = dispatch_agent_tool(
                self.config,
                DispatchAgentRequest(
                    task_document_ref=self.leaf,
                    role="curator",
                    brief="Curate the reviewed candidate.",
                ),
                StructuralAgentRuntime(
                    environ={"AR_HOSTED_SESSION_ID": "manager", "AR_SPAWN_ROLE": "manager"}
                ),
            )

        self.assertEqual(result["status"], "route-review-contract-ambiguous")
        spawn.assert_not_called()

    def test_first_manager_dispatch_bootstraps_its_series_identity_before_spawn(self) -> None:
        code_repo = self.root / "workspace" / "repo"
        memory_repo = self.root / "memory-repos" / "ar-repo"
        for repo in (code_repo, memory_repo):
            repo.mkdir(parents=True)
            subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
            subprocess.run(
                ["git", "config", "user.email", "test@example.invalid"],
                cwd=repo,
                check=True,
            )
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
            (repo / "base.txt").write_text("base\n", encoding="utf-8")
            subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
            subprocess.run(
                ["git", "commit", "-m", "base"], cwd=repo, check=True, capture_output=True
            )
            subprocess.run(
                ["git", "checkout", "-b", "ar/super"], cwd=repo, check=True, capture_output=True
            )
            (repo / "super.txt").write_text("super\n", encoding="utf-8")
            subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
            subprocess.run(
                ["git", "commit", "-m", "super"], cwd=repo, check=True, capture_output=True
            )
            subprocess.run(["git", "checkout", "main"], cwd=repo, check=True, capture_output=True)
            subprocess.run(
                ["git", "update-ref", "refs/remotes/origin/main", "HEAD"],
                cwd=repo,
                check=True,
            )
            subprocess.run(
                [
                    "git",
                    "symbolic-ref",
                    "refs/remotes/origin/HEAD",
                    "refs/remotes/origin/main",
                ],
                cwd=repo,
                check=True,
            )
        _seed_dispatch_memory_source(code_repo, memory_repo)
        self.config = McpRuntimeConfig(
            config_path=self.root / "settings.json",
            coordination_root=self.root,
            workspace_root=self.root / "workspace",
            transcript_root=self.root / "logs" / "mcp",
            repositories={"repo": RepositoryScope("repo", code_repo, memory_root=memory_repo)},
        )
        self.catalog.upsert(_seat("orchestrator", self.sprint, "orchestrator"))
        contract_path = series_contract_path(self.root / "tasks" / "repo" / "master")

        def spawned_after_manager_activation(*_args: object, **_kwargs: object) -> dict[str, str]:
            self.assertEqual(observe_atomic_series(load_contract(contract_path)).state, "active")
            return {"status": "spawned-unbriefed", "session": "manager-private"}

        with (
            mock.patch(
                "agents_remember.application.structural.agent_tools.spawn_agent_session_tool",
                side_effect=spawned_after_manager_activation,
            ) as spawn,
            mock.patch(
                "agents_remember.application.structural.agent_tools._post_initial_dispatch_brief",
                return_value={
                    "ok": True,
                    "deliveryState": "delivered",
                    "adapterDeliveryState": "accepted",
                },
            ),
        ):
            result = dispatch_agent_tool(
                self.config,
                DispatchAgentRequest(
                    task_document_ref=self.master,
                    role="manager",
                    brief="Manage the master.",
                ),
                StructuralAgentRuntime(
                    environ={
                        "AR_HOSTED_SESSION_ID": "orchestrator",
                        "AR_SPAWN_ROLE": "orchestrator",
                    }
                ),
            )

        self.assertEqual(result["status"], "dispatched")
        spawn.assert_called_once()
        contract = load_contract(contract_path)
        self.assertEqual(contract.code_source_branch, "ar/super")
        self.assertEqual(contract.code_work_branch, "ar/master")
        code_super = subprocess.run(
            ["git", "rev-parse", "ar/super"],
            cwd=code_repo,
            check=True,
            text=True,
            capture_output=True,
        ).stdout.strip()
        memory_super = subprocess.run(
            ["git", "rev-parse", "ar/super"],
            cwd=memory_repo,
            check=True,
            text=True,
            capture_output=True,
        ).stdout.strip()
        self.assertEqual(contract.code_base_commit, code_super)
        self.assertEqual(contract.memory_base_commit, memory_super)
        self.assertTrue(contract_path.is_file())
        self.assertIsNone(lineage_refusal(source_lineage_for_task(self.root, self.master)))

        release_atomic_series_selection(contract)
        self.catalog.upsert(_seat("manager", self.master, "manager"))

        def spawned_after_worker_activation(*_args: object, **_kwargs: object) -> dict[str, str]:
            self.assertEqual(observe_atomic_series(load_contract(contract_path)).state, "active")
            return {"status": "spawned-unbriefed", "session": "worker-private"}

        with (
            mock.patch(
                "agents_remember.application.structural.agent_tools.spawn_agent_session_tool",
                side_effect=spawned_after_worker_activation,
            ) as worker_spawn,
            mock.patch(
                "agents_remember.application.structural.agent_tools._post_initial_dispatch_brief",
                return_value={
                    "ok": True,
                    "deliveryState": "delivered",
                    "adapterDeliveryState": "accepted",
                },
            ),
        ):
            worker_result = dispatch_agent_tool(
                self.config,
                DispatchAgentRequest(
                    task_document_ref=self.leaf,
                    role="worker",
                    brief="Implement the leaf.",
                ),
                StructuralAgentRuntime(
                    environ={
                        "AR_HOSTED_SESSION_ID": "manager",
                        "AR_SPAWN_ROLE": "manager",
                    }
                ),
            )

        self.assertEqual(worker_result["status"], "dispatched")
        worker_spawn.assert_called_once()

        live_memory_worktree = self.root / "live-master-memory"
        subprocess.run(
            ["git", "worktree", "add", "--detach", live_memory_worktree.as_posix(), "ar/master"],
            cwd=memory_repo,
            check=True,
            capture_output=True,
        )
        write_contract(
            contract.contract_path,
            replace(
                contract,
                memory_worktree=live_memory_worktree,
                ledger_path=live_memory_worktree / "memory.md",
            ),
        )
        with (
            mock.patch(
                "agents_remember.application.structural.agent_tools.spawn_agent_session_tool",
                return_value={"status": "spawned-unbriefed", "session": "replacement-manager"},
            ) as replacement_spawn,
            mock.patch(
                "agents_remember.application.structural.agent_tools._post_initial_dispatch_brief",
                return_value={
                    "ok": True,
                    "deliveryState": "delivered",
                    "adapterDeliveryState": "accepted",
                },
            ),
        ):
            replacement = dispatch_agent_tool(
                self.config,
                DispatchAgentRequest(
                    task_document_ref=self.master,
                    role="manager",
                    brief="Replace the manager.",
                ),
                StructuralAgentRuntime(
                    environ={
                        "AR_HOSTED_SESSION_ID": "orchestrator",
                        "AR_SPAWN_ROLE": "orchestrator",
                    }
                ),
            )
        self.assertEqual(replacement["status"], "dispatched")
        replacement_spawn.assert_called_once()

    def test_organizational_manager_dispatch_does_not_bootstrap_a_series(self) -> None:
        master_path = self.root / "tasks" / "repo" / "master" / "task.json"
        master = read_task_doc(master_path)
        write_task_doc(
            master_path.parent,
            master.model_copy(update={"executionNature": "organizational"}),
        )
        self.catalog.upsert(_seat("orchestrator", self.sprint, "orchestrator"))
        with (
            mock.patch(
                "agents_remember.application.structural.agent_tools.spawn_agent_session_tool",
                return_value={"status": "spawned-unbriefed", "session": "manager-private"},
            ) as spawn,
            mock.patch(
                "agents_remember.application.structural.agent_tools._post_initial_dispatch_brief",
                return_value={
                    "ok": True,
                    "deliveryState": "delivered",
                    "adapterDeliveryState": "accepted",
                },
            ),
        ):
            result = dispatch_agent_tool(
                self.config,
                DispatchAgentRequest(
                    task_document_ref=self.master,
                    role="manager",
                    brief="Manage the organizational master.",
                ),
                StructuralAgentRuntime(
                    environ={
                        "AR_HOSTED_SESSION_ID": "orchestrator",
                        "AR_SPAWN_ROLE": "orchestrator",
                    }
                ),
            )

        self.assertEqual(result["status"], "dispatched")
        spawn.assert_called_once()
        self.assertFalse(series_contract_path(self.root / "tasks" / "repo" / "master").exists())

    def _series_bootstrap_repositories(self) -> tuple[Path, Path]:
        code_repo = self.root / "workspace" / "repo"
        memory_repo = self.root / "memory-repos" / "ar-repo"
        for repo in (code_repo, memory_repo):
            repo.mkdir(parents=True)
            subprocess.run(["git", "init", "-b", "ar/super"], cwd=repo, check=True)
            subprocess.run(
                ["git", "config", "user.email", "test@example.invalid"],
                cwd=repo,
                check=True,
            )
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
            (repo / "base.txt").write_text("base\n", encoding="utf-8")
            subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-m", "base"], cwd=repo, check=True)
            subprocess.run(["git", "branch", "main", "HEAD"], cwd=repo, check=True)
            subprocess.run(
                ["git", "update-ref", "refs/remotes/origin/main", "HEAD"],
                cwd=repo,
                check=True,
            )
            subprocess.run(
                [
                    "git",
                    "symbolic-ref",
                    "refs/remotes/origin/HEAD",
                    "refs/remotes/origin/main",
                ],
                cwd=repo,
                check=True,
            )
        seed_memory_ledger(
            memory_repo,
            "repo",
            branch_commit(code_repo, "ar/super"),
        )
        return code_repo, memory_repo

    def _series_bootstrap_spec(
        self, code_repo: Path, memory_repo: Path
    ) -> MasterSeriesContractSpec:
        return MasterSeriesContractSpec(
            coordination_root=self.root,
            repo_name="repo",
            code_repo=code_repo,
            memory_root=memory_repo,
            task_root=self.root / "tasks" / "repo" / "master",
            task_name="master",
            parent_task_name="sprint",
            protected_branch="ar/super",
        )

    def test_series_bootstrap_recovers_both_branches_when_contract_publish_fails(self) -> None:
        code_repo, memory_repo = self._series_bootstrap_repositories()
        spec = self._series_bootstrap_spec(code_repo, memory_repo)
        contract_path = series_contract_path(spec.task_root)

        with (
            mock.patch.object(
                start_contract_mod,
                "publish_new_lifecycle_operation_location",
                side_effect=OSError("publish red"),
            ),
            self.assertRaisesRegex(OSError, "publish red"),
        ):
            ensure_master_series_contract(spec)

        for repo in (code_repo, memory_repo):
            branches = subprocess.run(
                ["git", "branch", "--format=%(refname:short)"],
                cwd=repo,
                check=True,
                text=True,
                capture_output=True,
            ).stdout.splitlines()
            self.assertIn("ar/master", branches)
        self.assertFalse(contract_path.exists())
        self.assertTrue(start_contract_mod._master_series_bootstrap_record_path(spec).is_file())

        contract = ensure_master_series_contract(spec)
        assert isinstance(contract, WorktreeContract)
        self.assertEqual(contract.code_source_branch, "ar/super")
        self.assertTrue(contract_path.is_file())
        self.assertFalse(start_contract_mod._master_series_bootstrap_record_path(spec).exists())

    def test_series_bootstrap_recovers_code_when_memory_branch_create_fails(self) -> None:
        code_repo, memory_repo = self._series_bootstrap_repositories()
        spec = self._series_bootstrap_spec(code_repo, memory_repo)
        require_ref = start_contract_mod._require_bootstrap_ref

        def fail_memory(
            ref,
            *,
            authority: object | None = None,
        ) -> None:
            if ref.repository == memory_repo:
                raise OSError("memory branch red")
            require_ref(ref, authority=authority)

        with (
            mock.patch.object(
                start_contract_mod,
                "_require_bootstrap_ref",
                side_effect=fail_memory,
            ),
            self.assertRaisesRegex(OSError, "memory branch red"),
        ):
            ensure_master_series_contract(spec)

        branches = subprocess.run(
            ["git", "branch", "--format=%(refname:short)"],
            cwd=code_repo,
            check=True,
            text=True,
            capture_output=True,
        ).stdout.splitlines()
        self.assertIn("ar/master", branches)
        self.assertFalse(branch_exists(memory_repo, "ar/master"))
        self.assertTrue(start_contract_mod._master_series_bootstrap_record_path(spec).is_file())

        contract = ensure_master_series_contract(spec)
        assert isinstance(contract, WorktreeContract)
        self.assertTrue(branch_exists(memory_repo, "ar/master"))
        self.assertTrue(contract.contract_path.is_file())
        self.assertFalse(start_contract_mod._master_series_bootstrap_record_path(spec).exists())

    def test_partial_series_bootstrap_restarts_from_fresh_paired_source_tips(self) -> None:
        code_repo, memory_repo = self._series_bootstrap_repositories()
        spec = self._series_bootstrap_spec(code_repo, memory_repo)
        require_ref = start_contract_mod._require_bootstrap_ref
        old_code = branch_commit(code_repo, "ar/super")

        def fail_memory(ref, *, authority: object | None = None) -> None:
            if ref.repository == memory_repo:
                raise OSError("memory branch red")
            require_ref(ref, authority=authority)

        with (
            mock.patch.object(
                start_contract_mod,
                "_require_bootstrap_ref",
                side_effect=fail_memory,
            ),
            self.assertRaisesRegex(OSError, "memory branch red"),
        ):
            ensure_master_series_contract(spec)

        self.assertEqual(branch_commit(code_repo, "ar/master"), old_code)
        for repository, filename in (
            (code_repo, "fresh-code.txt"),
            (memory_repo, "fresh-memory.txt"),
        ):
            (repository / filename).write_text("fresh\n", encoding="utf-8")
            subprocess.run(["git", "add", filename], cwd=repository, check=True)
            subprocess.run(
                ["git", "commit", "-m", f"Advance {filename}"],
                cwd=repository,
                check=True,
            )
        fresh_code = branch_commit(code_repo, "ar/super")
        seed_memory_ledger(memory_repo, "repo", fresh_code)
        fresh_memory = branch_commit(memory_repo, "ar/super")

        contract = ensure_master_series_contract(spec)
        assert isinstance(contract, WorktreeContract)

        self.assertEqual(branch_commit(code_repo, "ar/master"), fresh_code)
        self.assertEqual(branch_commit(memory_repo, "ar/master"), fresh_memory)
        self.assertEqual(contract.code_base_commit, fresh_code)
        self.assertEqual(contract.memory_base_commit, fresh_memory)
        self.assertFalse(start_contract_mod._master_series_bootstrap_record_path(spec).exists())

    def test_series_branch_creation_and_preflight_guards_fail_closed(self) -> None:
        code_repo, memory_repo = self._series_bootstrap_repositories()
        subprocess.run(["git", "branch", "ar/orphan", "ar/super"], cwd=code_repo, check=True)

        with self.assertRaisesRegex(RuntimeError, "source branch does not exist"):
            start_contract_mod._validate_new_code_series_branch(code_repo, "missing", "ar/new")
        with self.assertRaisesRegex(RuntimeError, "without its task-bound contract"):
            start_contract_mod._validate_new_code_series_branch(code_repo, "ar/super", "ar/orphan")
        with self.assertRaisesRegex(RuntimeError, "memory series source branch does not exist"):
            start_contract_mod._validate_new_memory_series_branch(memory_repo, "missing", "ar/new")
        subprocess.run(["git", "branch", "ar/orphan", "ar/super"], cwd=memory_repo, check=True)
        with self.assertRaisesRegex(RuntimeError, "memory series branch exists"):
            start_contract_mod._validate_new_memory_series_branch(
                memory_repo, "ar/super", "ar/orphan"
            )
        wrong = "f" * 40
        with self.assertRaisesRegex(RuntimeError, "journaled bootstrap capability"):
            start_contract_mod._require_bootstrap_ref(
                start_contract_mod._BootstrapRef(
                    repository=code_repo,
                    branch="ar/orphan",
                    commit=wrong,
                    source_branch="ar/super",
                    source_commit=wrong,
                )
            )

    def test_series_bootstrap_dry_run_does_not_create_refs_or_journal(self) -> None:
        code_repo, memory_repo = self._series_bootstrap_repositories()
        spec = self._series_bootstrap_spec(code_repo, memory_repo)

        contract = ensure_master_series_contract(spec, dry_run=True)
        assert isinstance(contract, WorktreeContract)

        self.assertEqual(contract.code_work_branch, "ar/master")
        self.assertFalse(branch_exists(code_repo, "ar/master"))
        self.assertFalse(branch_exists(memory_repo, "ar/master"))
        self.assertFalse(start_contract_mod._master_series_bootstrap_record_path(spec).exists())

    def test_series_publish_failure_before_branch_creation_is_retryable(self) -> None:
        code_repo, memory_repo = self._series_bootstrap_repositories()
        spec = self._series_bootstrap_spec(code_repo, memory_repo)
        with (
            mock.patch.object(
                start_contract_mod,
                "_require_bootstrap_ref",
                side_effect=OSError("code branch red"),
            ),
            self.assertRaisesRegex(OSError, "code branch red"),
        ):
            ensure_master_series_contract(spec)
        self.assertFalse(branch_exists(code_repo, "ar/master"))
        self.assertFalse(branch_exists(memory_repo, "ar/master"))
        self.assertTrue(start_contract_mod._master_series_bootstrap_record_path(spec).is_file())

        contract = ensure_master_series_contract(spec)
        assert isinstance(contract, WorktreeContract)
        self.assertTrue(contract.contract_path.is_file())
        self.assertFalse(start_contract_mod._master_series_bootstrap_record_path(spec).exists())

    def test_existing_series_contract_must_match_the_declared_super(self) -> None:
        code_repo, memory_repo = self._series_bootstrap_repositories()
        spec = self._series_bootstrap_spec(code_repo, memory_repo)
        contract = ensure_master_series_contract(spec)
        assert isinstance(contract, WorktreeContract)

        with self.assertRaisesRegex(RuntimeError, "does not match the sprint integrationBranch"):
            ensure_master_series_contract(replace(spec, protected_branch="main"))

        write_contract(
            contract.contract_path,
            replace(
                contract,
                memory_source_branch="wrong-super",
                memory_work_branch="wrong-master",
            ),
        )
        with self.assertRaisesRegex(RuntimeError, "repository memory edge"):
            ensure_master_series_contract(spec)

    def test_existing_series_contract_binds_git_and_task_edge_identity(self) -> None:
        code_repo, memory_repo = self._series_bootstrap_repositories()
        spec = self._series_bootstrap_spec(code_repo, memory_repo)
        contract = ensure_master_series_contract(spec)
        assert isinstance(contract, WorktreeContract)
        sibling_code = self.root / "sibling-code"
        sibling_memory = self.root / "sibling-memory"
        for repo, sibling in ((code_repo, sibling_code), (memory_repo, sibling_memory)):
            subprocess.run(
                ["git", "worktree", "add", "--detach", sibling.as_posix(), "ar/super"],
                cwd=repo,
                check=True,
                capture_output=True,
            )
        sibling_contract = replace(
            contract,
            code_repo_path=sibling_code,
            code_worktree=sibling_code,
            memory_repo_path=sibling_memory,
            memory_worktree=sibling_memory,
            ledger_path=sibling_memory / "memory.md",
        )
        write_contract(contract.contract_path, sibling_contract)
        adopted = ensure_master_series_contract(spec)
        assert isinstance(adopted, WorktreeContract)
        self.assertEqual(adopted.code_repo_path, sibling_code)
        self.assertEqual(adopted.code_worktree, sibling_code)
        self.assertEqual(adopted.memory_repo_path, sibling_memory)
        self.assertEqual(adopted.memory_worktree, sibling_memory)
        self.assertEqual(adopted.ledger_path, sibling_memory / "memory.md")

        nested_code = sibling_code / "nested"
        nested_memory = sibling_memory / "onboarding"
        nested_code.mkdir()
        nested_memory.mkdir()

        foreign_code = self.root / "foreign-code"
        foreign_memory = self.root / "foreign-memory"
        for repo in (foreign_code, foreign_memory):
            repo.mkdir()
            subprocess.run(["git", "init", "-b", "ar/super"], cwd=repo, check=True)
            subprocess.run(
                ["git", "config", "user.email", "test@example.invalid"],
                cwd=repo,
                check=True,
            )
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
            (repo / "base.txt").write_text("base\n", encoding="utf-8")
            subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-m", "base"], cwd=repo, check=True)

        identity_variants = (
            {"task_id": "OTHER"},
            {"task_name": "other-master"},
            {"repo_name": "other-repo"},
            {"workflow_kind": "chat-task"},
            {"coordination_root": self.root / "other-coordination"},
            {"task_root": self.root / "tasks" / "repo" / "other-master"},
            {"contract_path": self.root / "tasks" / "repo" / "other-master" / "series-contract.md"},
            {"task_artifact": self.root / "other" / "task.md"},
            {"worktree_group": self.root / "other" / "enclosures"},
            {"parent_task_name": "other-sprint"},
            {"parent_contract_path": self.root / "other" / "series-contract.md"},
            {"code_repo_path": foreign_code},
            {"code_worktree": foreign_code},
            {"code_repo_path": nested_code},
            {"code_worktree": nested_code},
            {"memory_repo_path": foreign_memory},
            {"memory_worktree": foreign_memory},
            {"ledger_path": foreign_memory / "memory.md"},
            {"memory_repo_path": nested_memory},
            {"memory_worktree": nested_memory},
            {"ledger_path": nested_memory / "memory.md"},
            {"ledger_path": sibling_memory / "renamed-ledger.md"},
            {"leaf_id": "other-leaf"},
            {"lifecycle_id": "other-lifecycle"},
        )
        for changes in identity_variants:
            with self.subTest(changes=changes):
                write_contract(
                    contract.contract_path,
                    replace(sibling_contract, **changes),
                )
                with self.assertRaisesRegex(RuntimeError, "does not match the commanding sprint"):
                    ensure_master_series_contract(spec)

    def test_repository_root_rejects_missing_and_non_repository_paths(self) -> None:
        self.assertIsNone(start_contract_mod._repository_root(None))
        non_repository = self.root / "not-a-repository"
        non_repository.mkdir()
        self.assertIsNone(start_contract_mod._repository_root(non_repository))

    def test_concurrent_series_bootstrap_adopts_the_winner_without_rollback(self) -> None:
        code_repo, memory_repo = self._series_bootstrap_repositories()
        spec = self._series_bootstrap_spec(code_repo, memory_repo)
        publish_entered = threading.Event()
        allow_publish = threading.Event()
        real_publish = start_contract_mod.publish_new_lifecycle_operation_location
        writes = 0

        def paused_publish(contract: WorktreeContract, *, contract_text: str):
            nonlocal writes
            writes += 1
            publish_entered.set()
            self.assertTrue(allow_publish.wait(timeout=5))
            return real_publish(contract, contract_text=contract_text)

        with (
            mock.patch.object(
                start_contract_mod,
                "publish_new_lifecycle_operation_location",
                side_effect=paused_publish,
            ),
            ThreadPoolExecutor(max_workers=2) as pool,
        ):
            first = pool.submit(ensure_master_series_contract, spec)
            self.assertTrue(publish_entered.wait(timeout=5))
            second = pool.submit(ensure_master_series_contract, spec)
            self.assertFalse(second.done())
            allow_publish.set()
            first_contract = first.result(timeout=5)
            second_contract = second.result(timeout=5)

        self.assertEqual(writes, 1)
        self.assertEqual(first_contract, second_contract)
        assert isinstance(first_contract, WorktreeContract)
        self.assertTrue(first_contract.contract_path.is_file())
        for repo in (code_repo, memory_repo):
            branches = subprocess.run(
                ["git", "branch", "--format=%(refname:short)"],
                cwd=repo,
                check=True,
                text=True,
                capture_output=True,
            ).stdout.splitlines()
            self.assertEqual(branches.count("ar/master"), 1)

    def test_declared_integration_source_refuses_incomplete_task_topology(self) -> None:
        context = SimpleNamespace(
            coordination_root=self.root,
            code_repository_name="repo",
            code_repository_root=self.root,
        )
        with self.assertRaisesRegex(RuntimeError, "cannot resolve the commanding sprint"):
            start_contract_mod._declared_integration_source_branch(
                context, self.root / "tasks" / "repo" / "missing-master"
            )

        orphan_root = self.root / "tasks" / "repo" / "orphan-master"
        write_task_doc(
            orphan_root,
            _task_doc(
                id="ORPHAN",
                slug="task",
                title="Orphan Master",
                kind="master",
                executionNature="organizational",
            ),
        )
        with self.assertRaisesRegex(RuntimeError, "cannot resolve the commanding sprint"):
            start_contract_mod._declared_integration_source_branch(context, orphan_root)

        write_task_doc(
            self.root / "tasks" / "repo" / "blank-sprint",
            _task_doc(
                id="BLANK-SPRINT",
                slug="task",
                title="Blank Sprint",
                kind="master",
                orchestrates=["blank-master"],
            ),
        )
        blank_master_root = self.root / "tasks" / "repo" / "blank-master"
        write_task_doc(
            blank_master_root,
            _task_doc(
                id="BLANK-MASTER",
                slug="task",
                title="Blank Master",
                kind="master",
            ),
        )
        with self.assertRaisesRegex(RuntimeError, "must declare integrationBranch"):
            start_contract_mod._declared_integration_source_branch(context, blank_master_root)

    def test_manager_dispatch_names_the_missing_sprint_field_recovery_before_spawn(self) -> None:
        sprint_root = self.root / "tasks" / "repo" / "sprint"
        sprint = read_task_doc(sprint_root / "task.json")
        payload = sprint.model_dump(by_alias=True)
        payload["integrationBranch"] = None
        write_task_doc(sprint_root, TaskDocument.model_validate(payload))
        self.catalog.upsert(_seat("orchestrator", self.sprint, "orchestrator"))

        with mock.patch(
            "agents_remember.application.structural.agent_tools.spawn_agent_session_tool"
        ) as spawn:
            result = dispatch_agent_tool(
                self.config,
                DispatchAgentRequest(
                    task_document_ref=self.master,
                    role="manager",
                    brief="Manage the master.",
                ),
                StructuralAgentRuntime(
                    environ={
                        "AR_HOSTED_SESSION_ID": "orchestrator",
                        "AR_SPAWN_ROLE": "orchestrator",
                    }
                ),
            )

        self.assertEqual(result["status"], "series-admission-refused")
        self.assertIn("sprint/task.json", result["detail"])
        self.assertIn("integrationBranch", result["detail"])
        self.assertIn("task_doc(operation='set_field'", result["detail"])
        spawn.assert_not_called()

    def test_manager_bootstrap_refuses_invalid_altitude_and_missing_repository(self) -> None:
        topology = TaskDocumentTopology(self.root)
        invalid_altitude = _implementation_series_admission_refusal(
            self.config,
            topology.resolve(self.sprint),
            "manager",
        )
        assert invalid_altitude is not None
        self.assertEqual(invalid_altitude.status, "series-admission-refused")
        assert invalid_altitude.detail is not None
        self.assertIn("canonical master", invalid_altitude.detail)

        self.catalog.upsert(_seat("orchestrator", self.sprint, "orchestrator"))
        with mock.patch(
            "agents_remember.application.structural.agent_tools.spawn_agent_session_tool"
        ) as spawn:
            refused = dispatch_agent_tool(
                self.config,
                DispatchAgentRequest(
                    task_document_ref=self.master,
                    role="manager",
                    brief="Manage the master.",
                ),
                StructuralAgentRuntime(
                    environ={
                        "AR_HOSTED_SESSION_ID": "orchestrator",
                        "AR_SPAWN_ROLE": "orchestrator",
                    }
                ),
            )

        self.assertEqual(refused["status"], "series-admission-refused")
        spawn.assert_not_called()

    def test_curator_review_admission_distinguishes_review_and_contract_refusals(self) -> None:
        topology = TaskDocumentTopology(self.root)
        current = topology.resolve(self.leaf)
        payload = current.document.model_dump(by_alias=True)
        payload["enclosures"] = [
            {
                "leafId": "leaf-1",
                "enclosurePath": "tasks/repo/master/enclosures/leaf-1/missing.md",
            }
        ]
        leaf = TaskDocument.model_validate(payload)
        write_task_doc(current.path.parent, leaf)
        resolved = TaskDocumentTopology(self.root).resolve(self.leaf)

        invalid = _curator_route_review_refusal(self.config, resolved)
        assert invalid is not None
        self.assertEqual(invalid.status, "route-review-contract-invalid")

        contract_path = (self.root / "tasks/repo/master/enclosures/leaf-1/missing.md").resolve()
        contract = SimpleNamespace(
            task_root=resolved.path.parent,
            leaf_id="leaf-1",
            contract_path=contract_path,
        )
        canonical_leaf = (resolved.path, resolved.document)

        with (
            mock.patch(
                "agents_remember.application.structural.agent_tools.load_contract",
                return_value=contract,
            ),
            mock.patch(
                "agents_remember.application.structural.agent_tools.resolve_terminal_leaf_doc",
                return_value=None,
            ),
        ):
            mismatched = _curator_route_review_refusal(self.config, resolved)
        assert mismatched is not None
        self.assertEqual(mismatched.status, "route-review-contract-invalid")

        with (
            mock.patch(
                "agents_remember.application.structural.agent_tools.load_contract",
                return_value=contract,
            ),
            mock.patch(
                "agents_remember.application.structural.agent_tools.resolve_terminal_leaf_doc",
                return_value=canonical_leaf,
            ),
            mock.patch(
                "agents_remember.application.structural.agent_tools.require_current_route_review",
                side_effect=RouteReviewError("route-review-required", "review missing"),
            ),
        ):
            refused = _curator_route_review_refusal(self.config, resolved)
        assert refused is not None
        self.assertEqual(refused.status, "route-review-required")

        with (
            mock.patch(
                "agents_remember.application.structural.agent_tools.load_contract",
                return_value=contract,
            ),
            mock.patch(
                "agents_remember.application.structural.agent_tools.resolve_terminal_leaf_doc",
                return_value=canonical_leaf,
            ),
            mock.patch(
                "agents_remember.application.structural.agent_tools.require_current_route_review",
                return_value={"status": "current"},
            ),
        ):
            self.assertIsNone(_curator_route_review_refusal(self.config, resolved))

    def test_dispatch_persistence_failure_retires_the_unbriefed_child_privately(self) -> None:
        self.catalog.upsert(_seat("architect", self.sprint, "architect"))
        with (
            mock.patch(
                "agents_remember.application.structural.agent_tools.spawn_agent_session_tool",
                return_value={"status": "spawned-unbriefed", "session": "private-child-id"},
            ),
            mock.patch(
                "agents_remember.application.structural.agent_tools._post_structural_message",
                side_effect=ValueError("store refused"),
            ),
            mock.patch(
                "agents_remember.application.structural.agent_tools.session_retire_tool",
                return_value={"ok": True, "status": "retired"},
            ) as retire,
        ):
            result = dispatch_agent_tool(
                self.config,
                DispatchAgentRequest(
                    task_document_ref=self.sprint,
                    role="orchestrator",
                    brief="Coordinate the sprint.",
                ),
                StructuralAgentRuntime(
                    environ={
                        "AR_HOSTED_SESSION_ID": "architect",
                        "AR_SPAWN_ROLE": "architect",
                    }
                ),
            )

        self.assertEqual(result["status"], "dispatch-persistence-refused")
        self.assertNotIn("private-child-id", str(result))
        self.assertEqual(retire.call_args.kwargs["session_id"], "private-child-id")

    def test_plane_dispatch_refuses_broken_plane_identity_without_downgrading(self) -> None:
        with mock.patch(
            "agents_remember.application.structural.agent_tools.spawn_agent_session_tool"
        ) as spawn:
            result = dispatch_agent_tool(
                self.config,
                DispatchAgentRequest(
                    task_document_ref=self.sprint,
                    role="architect",
                    brief="Design the sprint.",
                ),
                StructuralAgentRuntime(
                    environ={"AR_HOSTED_SESSION_ID": "ghost", "AR_SPAWN_ROLE": "architect"}
                ),
            )

        self.assertEqual(result["status"], "ambient-seat-stale")
        spawn.assert_not_called()

    def test_plane_dispatch_refuses_an_unauthorized_child_role(self) -> None:
        self.catalog.upsert(_seat("architect", self.sprint, "architect"))
        with mock.patch(
            "agents_remember.application.structural.agent_tools.spawn_agent_session_tool"
        ) as spawn:
            result = dispatch_agent_tool(
                self.config,
                DispatchAgentRequest(
                    task_document_ref=self.sprint,
                    role="system-specialist",
                    brief="Investigate the sprint.",
                ),
                StructuralAgentRuntime(
                    environ={
                        "AR_HOSTED_SESSION_ID": "architect",
                        "AR_SPAWN_ROLE": "architect",
                    }
                ),
            )

        self.assertEqual(result["status"], "structural-child-refused")
        spawn.assert_not_called()


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
