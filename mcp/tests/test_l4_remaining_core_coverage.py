"""Cover remaining L4 topology, bootstrap, queue, and recovery decisions."""

from __future__ import annotations

import tempfile
import unittest
from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest import mock

from agents_remember.models.lifecycles.operation import (
    IntegrationConflictTransaction,
    IntegrationOperationAuthority,
    LifecycleOperationRecoveryCommits,
)
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks import TaskDocument
from agents_remember.tasks.document_refs import (
    TaskDocumentRefError,
    TaskDocumentTopology,
)
from agents_remember.worktrees import (
    closeout_queue_lifecycle,
)
from agents_remember.worktrees import (
    integration_branch_authority as authority,
)
from agents_remember.worktrees.integration_branch_types import (
    IntegrationSurface,
    _BranchScope,
    _MasterAuthority,
    _RepositorySide,
)
from agents_remember.worktrees.integration_ref_transaction import IntegratedCommits
from agents_remember.worktrees.modules import integrate, start_contract
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.models import WorktreeCommandResult
from integration_branch_authority_test_support import _authority_fixture


def _side(repository: Path, *, side: str = "code", source: str = "main") -> _RepositorySide:
    return _RepositorySide(
        side=cast(Any, side),
        repository=repository,
        worktree=repository,
        source_branch=source,
        work_branch="ar/master",
    )


def _task_ref_error(status: str) -> RuntimeError:
    try:
        raise RuntimeError("topology failed") from TaskDocumentRefError(status, status)
    except RuntimeError as error:
        return error


def _conflict_authority(contract) -> IntegrationOperationAuthority:
    code_source = "1" * 40
    code_candidate = "2" * 40
    return IntegrationOperationAuthority(
        targetKind="sprint-super",
        codeRepository=contract.code_repo_path.as_posix(),
        codeSourceBranch="super",
        codeSourceRef="refs/heads/super",
        codeSourceCommit=code_source,
        codeCandidateCommit=code_candidate,
        conflictTransaction=IntegrationConflictTransaction(
            codeReplayRequired=True,
            memoryReplayRequired=False,
            codeSourceRef="refs/heads/super",
            codeSourceCommit=code_source,
            codeCandidateCommit=code_candidate,
            codeWorktree=contract.code_worktree.resolve().as_posix(),
            memoryWorktree=(
                contract.memory_worktree.resolve().as_posix()
                if contract.memory_worktree is not None
                else ""
            ),
        ),
    )


class IntegrationBranchAuthorityRemainderTests(unittest.TestCase):
    def test_publication_rethrows_unrelated_and_incomplete_repair_errors(self) -> None:
        scope = _BranchScope(Path("/coordination"), "repo", Path("/tasks/repo"), ())
        with (
            mock.patch.object(authority, "_publication_scope", return_value=scope),
            mock.patch.object(
                authority,
                "_integration_surfaces",
                side_effect=[(), RuntimeError("unrelated")],
            ),
            self.assertRaisesRegex(RuntimeError, "unrelated"),
        ):
            authority.require_topology_publication_authority(
                Path("/coordination"), "repo", Path("/code"), None, {}
            )

        ref = TaskDocumentRef(repository="repo", path="master/task.json")
        with (
            mock.patch.object(authority, "_publication_scope", return_value=scope),
            mock.patch.object(
                authority,
                "_integration_surfaces",
                side_effect=[(), _task_ref_error("task-execution-graph-membership-invalid")],
            ),
            mock.patch.object(Path, "is_file", return_value=True),
            self.assertRaisesRegex(RuntimeError, "topology failed"),
        ):
            authority.require_topology_publication_authority(
                Path("/coordination"),
                "repo",
                Path("/code"),
                None,
                {ref: cast(TaskDocument, SimpleNamespace())},
            )

    def test_surface_resolution_wraps_master_error_and_requires_super_branch(self) -> None:
        scope = _BranchScope(Path("/coordination"), "repo", Path("/tasks/repo"), ())
        broken = SimpleNamespace(
            repository_masters=mock.Mock(side_effect=TaskDocumentRefError("broken", "bad"))
        )
        with (
            mock.patch.object(authority, "TaskDocumentTopology", return_value=broken),
            self.assertRaisesRegex(RuntimeError, "broken"),
        ):
            authority._integration_surfaces(scope)

        sprint = SimpleNamespace(
            ref=TaskDocumentRef(repository="repo", path="sprint/task.json"),
            document=SimpleNamespace(orchestrates=["master"], integrationBranch=None),
        )
        topology = SimpleNamespace(
            coordination_root=Path("/coordination"),
            repository_masters=lambda _repo: (sprint,),
            validate_execution_topology=lambda *_args, **_kwargs: (),
        )
        with (
            mock.patch.object(authority, "TaskDocumentTopology", return_value=topology),
            self.assertRaisesRegex(RuntimeError, "declare integrationBranch"),
        ):
            authority._integration_surfaces(scope)

    def test_foreign_override_is_ignored(self) -> None:
        topology = SimpleNamespace(
            coordination_root=Path("/coordination"), repository_masters=lambda _repo: ()
        )
        foreign = TaskDocumentRef(repository="other", path="master/task.json")
        document = cast(TaskDocument, SimpleNamespace(kind="master"))
        self.assertEqual(
            authority._repository_masters_with_overrides(
                cast(TaskDocumentTopology, topology), "repo", {foreign: document}
            ),
            (),
        )

    def test_live_leaf_census_keeps_cleaned_atomic_and_refuses_sprint_reassignment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = _authority_fixture(Path(tmp))
            cleaned = replace(fixture.leaf_contract, cleanup="completed")
            scope = authority._scope(cleaned)
            master_ref = TaskDocumentRef(repository="repo", path="master/task.json")
            sprint_one = TaskDocumentRef(repository="repo", path="sprint/task.json")
            sprint_two = TaskDocumentRef(repository="repo", path="other/task.json")
            master = TaskDocumentTopology(fixture.coordination).resolve(master_ref)

            with (
                mock.patch.object(
                    authority,
                    "iter_leaf_enclosure_contracts",
                    return_value=(cleaned.contract_path,),
                ),
                mock.patch.object(authority, "_load_series", return_value=cleaned),
                mock.patch.object(authority, "_publication_master_authority", return_value={}),
                mock.patch.object(
                    authority, "_current_publication_master_authority", return_value={}
                ),
            ):
                authority._require_no_live_leaf_collisions(scope, (), (), {})

            surface = IntegrationSurface(
                side="code",
                kind="sprint-super",
                repository=Path("/repo"),
                branch="super",
                owner=sprint_one.key,
            )
            with (
                mock.patch.object(
                    authority,
                    "iter_leaf_enclosure_contracts",
                    return_value=(cleaned.contract_path,),
                ),
                mock.patch.object(authority, "_load_series", return_value=cleaned),
                mock.patch.object(
                    authority,
                    "_publication_master_authority",
                    return_value={master_ref: (master, sprint_one)},
                ),
                mock.patch.object(
                    authority,
                    "_current_publication_master_authority",
                    return_value={master_ref: (master, sprint_two)},
                ),
                mock.patch.object(authority, "_require_live_leaf_task_identity"),
                self.assertRaisesRegex(RuntimeError, "owning sprint would change"),
            ):
                authority._require_no_live_leaf_collisions(scope, (surface,), (surface,), {})

    def test_publication_master_authority_refuses_multiple_sprint_owners(self) -> None:
        master_ref = TaskDocumentRef(repository="repo", path="master/task.json")
        sprint_one = SimpleNamespace(
            ref=TaskDocumentRef(repository="repo", path="s1/task.json"),
            document=SimpleNamespace(orchestrates=["master"]),
        )
        sprint_two = SimpleNamespace(
            ref=TaskDocumentRef(repository="repo", path="s2/task.json"),
            document=SimpleNamespace(orchestrates=["master"]),
        )
        master = SimpleNamespace(ref=master_ref)
        topology = SimpleNamespace(validate_execution_topology=lambda *_args, **_kwargs: (master,))
        with (
            mock.patch.object(
                authority,
                "_repository_masters_with_overrides",
                return_value=(sprint_one, sprint_two),
            ),
            self.assertRaisesRegex(RuntimeError, "multiple sprint owners"),
        ):
            authority._publication_master_authority(
                cast(TaskDocumentTopology, topology), "repo", None
            )

    def test_live_leaf_identity_refuses_changed_document(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = _authority_fixture(Path(tmp))
            topology = TaskDocumentTopology(fixture.coordination)
            master_ref = TaskDocumentRef(repository="repo", path="master/task.json")
            master = topology.resolve(master_ref)
            with (
                mock.patch.object(
                    topology,
                    "resolve_candidate",
                    return_value=SimpleNamespace(
                        document=SimpleNamespace(id="wrong", kind="subTask")
                    ),
                ),
                self.assertRaisesRegex(RuntimeError, "task identity changed"),
            ):
                authority._require_live_leaf_task_identity(
                    topology,
                    fixture.leaf_contract,
                    master,
                    TaskDocumentRef(repository="repo", path="sprint/task.json"),
                    {},
                )

    def test_master_surface_and_authority_refuse_invalid_nature_graph_and_branch(self) -> None:
        scope = _BranchScope(Path("/coordination"), "repo", Path("/tasks/repo"), ())
        master_ref = TaskDocumentRef(repository="repo", path="master/task.json")
        master = SimpleNamespace(
            ref=master_ref,
            path=Path("/coordination/tasks/repo/master/task.json"),
            document=SimpleNamespace(executionNature=None),
        )
        with self.assertRaisesRegex(RuntimeError, "unsupported executionNature"):
            authority._master_integration_surfaces(scope, cast(Any, master), "super")

        sprint_ref = TaskDocumentRef(repository="repo", path="sprint/task.json")
        topology = SimpleNamespace(
            canonical_ref=lambda *_args: master_ref,
            resolve=lambda ref: (
                master
                if ref == master_ref
                else SimpleNamespace(
                    ref=sprint_ref, document=SimpleNamespace(integrationBranch="super")
                )
            ),
            parent=lambda _ref: sprint_ref,
            validate_execution_topology=mock.Mock(
                side_effect=TaskDocumentRefError("graph-invalid", "invalid graph")
            ),
        )
        with (
            mock.patch.object(authority, "TaskDocumentTopology", return_value=topology),
            self.assertRaisesRegex(RuntimeError, "graph-invalid"),
        ):
            authority._master_authority(scope)

        topology.validate_execution_topology = mock.Mock(return_value=())
        topology.resolve = lambda ref: (
            master
            if ref == master_ref
            else SimpleNamespace(ref=sprint_ref, document=SimpleNamespace(integrationBranch=None))
        )
        with (
            mock.patch.object(authority, "TaskDocumentTopology", return_value=topology),
            self.assertRaisesRegex(RuntimeError, "declare integrationBranch"),
        ):
            authority._master_authority(scope)

        with self.assertRaisesRegex(RuntimeError, "executionNature='atomic'"):
            authority._require_atomic_master(
                _MasterAuthority(cast(TaskDocumentTopology, topology), master_ref, None, None, None)
            )

    def test_repository_and_series_shape_refusals(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = _authority_fixture(root, external_memory=True)
            with self.assertRaisesRegex(RuntimeError, "requires a repository path"):
                authority._repository_sides(replace(fixture.leaf_contract, memory_repo_path=None))
            with self.assertRaisesRegex(RuntimeError, "requires a worktree path"):
                authority._repository_sides(replace(fixture.leaf_contract, memory_worktree=None))
            invalid = root / "invalid-series.md"
            invalid.write_text("invalid\n", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "cannot resolve"):
                authority._load_series(invalid)

            scope = authority._scope(fixture.master_contract)
            with self.assertRaisesRegex(RuntimeError, "does not match commanded task"):
                authority._require_series_contract_shape(
                    scope,
                    replace(fixture.master_contract, repo_name="wrong"),
                    fixture.master_contract.task_root.resolve(),
                    "ar/master",
                )
            with self.assertRaisesRegex(RuntimeError, "code source does not match"):
                authority._require_series_code_source(
                    replace(fixture.master_contract, code_source_branch="wrong"),
                    fixture.master_contract.task_root,
                    "super",
                )
            with self.assertRaisesRegex(RuntimeError, "memory target does not match"):
                authority._require_series_memory_identity(
                    replace(fixture.master_contract, memory_work_branch="wrong"),
                    fixture.master_contract.task_root,
                    "ar/master",
                    "super",
                )
            with self.assertRaisesRegex(RuntimeError, "memory source does not match"):
                authority._require_series_memory_identity(
                    replace(fixture.master_contract, memory_source_branch="wrong"),
                    fixture.master_contract.task_root,
                    "ar/master",
                    "super",
                )
            with self.assertRaisesRegex(RuntimeError, "not the repository default"):
                authority._require_series_memory_identity(
                    replace(fixture.master_contract, memory_source_branch="super"),
                    fixture.master_contract.task_root,
                    "ar/master",
                    None,
                )

        with (
            mock.patch.object(authority, "repository_identity", return_value=None),
            self.assertRaisesRegex(RuntimeError, "cannot resolve code"),
        ):
            authority._repository_identity(Path("/missing"), "code")

    def test_standalone_series_default_loop_accepts_exact_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = _authority_fixture(Path(tmp))
            topology = TaskDocumentTopology(fixture.coordination)
            standalone = _MasterAuthority(
                topology,
                TaskDocumentRef(repository="repo", path="master/task.json"),
                None,
                None,
                "atomic",
            )
            side = _side(fixture.code_repo)
            memory_side = _side(fixture.code_repo, side="memory")
            with (
                mock.patch.object(authority, "_master_authority", return_value=standalone),
                mock.patch.object(authority, "_require_series_identity"),
                mock.patch.object(authority, "_repository_sides", return_value=(side, memory_side)),
                mock.patch.object(
                    authority, "canonical_local_branch", side_effect=lambda _repo, branch: branch
                ),
                mock.patch.object(authority, "_side_default_branch", return_value="main"),
            ):
                self.assertIs(
                    authority.require_series_contract_authority(
                        fixture.master_contract, operation="test"
                    ),
                    standalone,
                )


class QueueLifecycleRemainderTests(unittest.TestCase):
    def test_terminal_publication_refuses_wrong_kind_nature_and_accepts_standalone(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = _authority_fixture(Path(tmp))
            with self.assertRaisesRegex(RuntimeError, "requires a series"):
                closeout_queue_lifecycle._atomic_series_terminal_publication(
                    fixture.leaf_contract, lambda: None
                )

            master_ref = TaskDocumentRef(repository="repo", path="master/task.json")
            topology = SimpleNamespace(
                canonical_ref=lambda *_args: master_ref,
                resolve=lambda _ref: SimpleNamespace(
                    document=SimpleNamespace(executionNature="organizational")
                ),
                parent=lambda _ref: None,
            )
            with (
                mock.patch.object(
                    closeout_queue_lifecycle, "TaskDocumentTopology", return_value=topology
                ),
                self.assertRaisesRegex(RuntimeError, "executionNature='atomic'"),
            ):
                closeout_queue_lifecycle._atomic_series_terminal_publication(
                    fixture.master_contract, lambda: None
                )

            topology.resolve = lambda _ref: SimpleNamespace(
                document=SimpleNamespace(executionNature="atomic")
            )
            with mock.patch.object(
                closeout_queue_lifecycle, "TaskDocumentTopology", return_value=topology
            ):
                self.assertEqual(
                    closeout_queue_lifecycle._atomic_series_terminal_publication(
                        fixture.master_contract, lambda: "published"
                    ),
                    "published",
                )

    def test_terminal_publication_refuses_graph_move_and_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = _authority_fixture(Path(tmp))
            master_ref = TaskDocumentRef(repository="repo", path="master/task.json")
            sprint_ref = TaskDocumentRef(repository="repo", path="sprint/task.json")
            topology = SimpleNamespace(
                canonical_ref=lambda *_args: master_ref,
                resolve=lambda _ref: SimpleNamespace(
                    document=SimpleNamespace(executionNature="atomic")
                ),
                parent=lambda _ref: sprint_ref,
            )

            def inspect(_initial, action):
                return action(
                    SimpleNamespace(
                        activeBarrier=None,
                        candidates={
                            "leaf": SimpleNamespace(
                                owningMaster=master_ref,
                                taskDocumentRef=TaskDocumentRef(
                                    repository="repo", path="master/leaf.json"
                                ),
                            )
                        },
                    )
                )

            store = SimpleNamespace(inspect=inspect)
            with (
                mock.patch.object(
                    closeout_queue_lifecycle, "TaskDocumentTopology", return_value=topology
                ),
                mock.patch.object(
                    closeout_queue_lifecycle,
                    "_graph_context",
                    side_effect=[SimpleNamespace(revision="1"), SimpleNamespace(revision="2")],
                ),
                mock.patch.object(
                    closeout_queue_lifecycle, "_initial_state", return_value=object()
                ),
                mock.patch.object(
                    closeout_queue_lifecycle, "CloseoutQueueStore", return_value=store
                ),
                self.assertRaisesRegex(
                    closeout_queue_lifecycle.CloseoutQueueError, "graph changed"
                ),
            ):
                closeout_queue_lifecycle._atomic_series_terminal_publication(
                    fixture.master_contract, lambda: None
                )

            with (
                mock.patch.object(
                    closeout_queue_lifecycle, "TaskDocumentTopology", return_value=topology
                ),
                mock.patch.object(
                    closeout_queue_lifecycle,
                    "_graph_context",
                    return_value=SimpleNamespace(revision="1"),
                ),
                mock.patch.object(
                    closeout_queue_lifecycle, "_initial_state", return_value=object()
                ),
                mock.patch.object(
                    closeout_queue_lifecycle, "CloseoutQueueStore", return_value=store
                ),
                self.assertRaisesRegex(closeout_queue_lifecycle.CloseoutQueueError, "candidates"),
            ):
                closeout_queue_lifecycle._atomic_series_terminal_publication(
                    fixture.master_contract, lambda: None
                )

    def test_deactivating_unknown_permit_is_idempotent(self) -> None:
        permit = closeout_queue_lifecycle.AtomicSeriesTerminalPermit(
            Path("/contract"),
            "worktree_cleanup",
            closeout_queue_lifecycle._ATOMIC_SERIES_TERMINAL_CAPABILITY,
        )
        closeout_queue_lifecycle._deactivate_atomic_series_terminal_permit(permit)
        self.assertFalse(closeout_queue_lifecycle._atomic_series_terminal_permit_is_active(permit))

    def test_conflict_contract_requires_transaction_accepts_reset_and_refuses_mismatch(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = _authority_fixture(Path(tmp))
            contract = fixture.leaf_contract
            authority_record = _conflict_authority(contract)
            with self.assertRaisesRegex(
                closeout_queue_lifecycle.CloseoutQueueError, "transaction-required"
            ):
                closeout_queue_lifecycle._conflict_resolution_contract(
                    contract, authority_record.model_copy(update={"conflictTransaction": None})
                )
            self.assertIs(
                closeout_queue_lifecycle._conflict_resolution_contract(contract, authority_record),
                contract,
            )
            closed = replace(
                contract,
                closeout_status="completed",
                approved_for_commit=True,
                code_commit="wrong",
            )
            with self.assertRaisesRegex(
                closeout_queue_lifecycle.CloseoutQueueError, "contract-mismatch"
            ):
                closeout_queue_lifecycle._conflict_resolution_contract(closed, authority_record)

    def test_prepare_conflict_resolution_covers_unbound_missing_state_and_candidate_refusals(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = _authority_fixture(Path(tmp))
            contract = fixture.leaf_contract
            authority_record = _conflict_authority(contract)
            reset = replace(contract, cleanup="reopened")
            changed = replace(contract, cleanup="completed")
            with (
                mock.patch.object(
                    closeout_queue_lifecycle,
                    "_conflict_resolution_contract",
                    return_value=reset,
                ),
                mock.patch.object(
                    closeout_queue_lifecycle, "contract_queue_binding", return_value=None
                ),
                mock.patch.object(
                    closeout_queue_lifecycle,
                    "integration_authority_lock",
                    return_value=nullcontext(),
                ),
                mock.patch.object(
                    closeout_queue_lifecycle,
                    "load_contract",
                    side_effect=[reset, reset, changed],
                ),
                mock.patch.object(
                    closeout_queue_lifecycle,
                    "_conflict_reset_is_complete",
                    side_effect=[True, False],
                ),
            ):
                self.assertIs(
                    closeout_queue_lifecycle.prepare_queue_candidate_conflict_resolution(
                        contract,
                        operation_key="a" * 64,
                        authority=authority_record,
                    ),
                    reset,
                )
                with self.assertRaisesRegex(
                    closeout_queue_lifecycle.CloseoutQueueError,
                    "changed before conflict resolution",
                ):
                    closeout_queue_lifecycle.prepare_queue_candidate_conflict_resolution(
                        contract,
                        operation_key="a" * 64,
                        authority=authority_record,
                    )

            binding = closeout_queue_lifecycle.QueueBinding(
                TaskDocumentRef(repository="repo", path="sprint/task.json"),
                TaskDocumentRef(repository="repo", path="master/leaf.json"),
            )

            def run_state(state):
                store = SimpleNamespace(
                    transact_with_publication=lambda **kwargs: (
                        kwargs["transform"](state),
                        kwargs["publication"](),
                    )
                )
                patches = (
                    mock.patch.object(
                        closeout_queue_lifecycle,
                        "_conflict_resolution_contract",
                        return_value=reset,
                    ),
                    mock.patch.object(
                        closeout_queue_lifecycle,
                        "contract_queue_binding",
                        return_value=binding,
                    ),
                    mock.patch.object(
                        closeout_queue_lifecycle,
                        "TaskDocumentTopology",
                        return_value=SimpleNamespace(),
                    ),
                    mock.patch.object(
                        closeout_queue_lifecycle,
                        "_graph_context",
                        return_value=SimpleNamespace(revision="1"),
                    ),
                    mock.patch.object(
                        closeout_queue_lifecycle, "_initial_state", return_value=object()
                    ),
                    mock.patch.object(
                        closeout_queue_lifecycle, "CloseoutQueueStore", return_value=store
                    ),
                    mock.patch.object(
                        closeout_queue_lifecycle,
                        "integration_authority_lock",
                        return_value=nullcontext(),
                    ),
                    mock.patch.object(
                        closeout_queue_lifecycle, "load_contract", return_value=reset
                    ),
                    mock.patch.object(
                        closeout_queue_lifecycle,
                        "_conflict_reset_is_complete",
                        return_value=True,
                    ),
                )
                with (
                    patches[0],
                    patches[1],
                    patches[2],
                    patches[3],
                    patches[4],
                    patches[5],
                    patches[6],
                    patches[7],
                    patches[8],
                ):
                    return closeout_queue_lifecycle.prepare_queue_candidate_conflict_resolution(
                        contract,
                        operation_key="a" * 64,
                        authority=authority_record,
                    )

            self.assertIs(
                run_state(SimpleNamespace(candidates={})),
                reset,
            )
            uncertified = SimpleNamespace(
                state="selected",
                closeoutCodeCommit=authority_record.codeCandidateCommit,
                closeoutMemoryContentCommit=None,
                closeoutLedgerCommit=None,
            )
            with self.assertRaisesRegex(
                closeout_queue_lifecycle.CloseoutQueueError, "not-certified"
            ):
                run_state(SimpleNamespace(candidates={binding.candidate_ref.key: uncertified}))
            mismatch = SimpleNamespace(
                state="certified",
                closeoutCodeCommit="wrong",
                closeoutMemoryContentCommit=None,
                closeoutLedgerCommit=None,
            )
            with self.assertRaisesRegex(
                closeout_queue_lifecycle.CloseoutQueueError, "candidate-mismatch"
            ):
                run_state(SimpleNamespace(candidates={binding.candidate_ref.key: mismatch}))


class BootstrapRemainderTests(unittest.TestCase):
    def test_master_nature_and_commanded_bootstrap_refusals(self) -> None:
        with (
            mock.patch.object(
                start_contract,
                "read_task_doc",
                return_value=SimpleNamespace(kind="master", executionNature=None),
            ),
            mock.patch.object(Path, "is_file", return_value=True),
            self.assertRaisesRegex(RuntimeError, "requires executionNature"),
        ):
            start_contract._master_execution_nature(Path("/task"))

        spec = start_contract.MasterSeriesContractSpec(
            Path("/coordination"),
            "repo",
            Path("/code"),
            None,
            Path("/coordination/tasks/repo/master"),
            "master",
            "",
            "feature",
        )
        master_ref = TaskDocumentRef(repository="repo", path="master/task.json")
        master = SimpleNamespace(ref=master_ref, document=SimpleNamespace(executionNature="atomic"))
        standalone = SimpleNamespace(
            canonical_ref=lambda *_args: master_ref,
            resolve=lambda _ref: master,
            parent=lambda _ref: None,
        )
        with (
            mock.patch.object(start_contract, "TaskDocumentTopology", return_value=standalone),
            mock.patch.object(start_contract, "repository_default_branch", return_value="main"),
            self.assertRaisesRegex(RuntimeError, "repository-default"),
        ):
            start_contract._require_commanded_atomic_master(spec)

        sprint_ref = TaskDocumentRef(repository="repo", path="sprint/task.json")
        sprint = SimpleNamespace(
            ref=sprint_ref, document=SimpleNamespace(integrationBranch="feature")
        )
        commanded = SimpleNamespace(
            canonical_ref=lambda *_args: master_ref,
            resolve=lambda ref: master if ref == master_ref else sprint,
            parent=lambda _ref: sprint_ref,
            validate_execution_topology=lambda _ref: (),
        )
        with (
            mock.patch.object(start_contract, "TaskDocumentTopology", return_value=commanded),
            self.assertRaisesRegex(RuntimeError, "not commanded"),
        ):
            start_contract._require_commanded_atomic_master(spec)

    def test_bootstrap_record_refuses_missing_repository_identities(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = _authority_fixture(Path(tmp), external_memory=True)
            spec = start_contract.MasterSeriesContractSpec(
                fixture.coordination,
                "repo",
                fixture.code_repo,
                fixture.master_contract.memory_repo_path,
                fixture.master_contract.task_root,
                "master",
                "sprint",
                "super",
            )
            with (
                mock.patch.object(start_contract, "repository_identity", return_value=None),
                self.assertRaisesRegex(RuntimeError, "code repository identity"),
            ):
                start_contract._bootstrap_record(spec, fixture.master_contract)
            with (
                mock.patch.object(
                    start_contract,
                    "repository_identity",
                    side_effect=[Path("/code"), None],
                ),
                self.assertRaisesRegex(RuntimeError, "memory repository identity"),
            ):
                start_contract._bootstrap_record(spec, fixture.master_contract)

    def test_bootstrap_memory_edges_and_source_authority_refuse_missing_facts(self) -> None:
        record = start_contract._SeriesBootstrapRecord(
            contractPath="/contract",
            codeRepository="/code",
            codeSourceBranch="super",
            codeWorkBranch="ar/master",
            codeBaseCommit="1" * 40,
            memoryRepository="/memory",
            memorySourceBranch="super",
            memoryWorkBranch="ar/master",
            memoryBaseCommit="2" * 40,
        )
        spec = start_contract.MasterSeriesContractSpec(
            Path("/coordination"),
            "repo",
            Path("/code"),
            None,
            Path("/coordination/tasks/repo/master"),
            "master",
            "sprint",
            "super",
        )
        with (
            mock.patch.object(
                start_contract,
                "_contract_from_bootstrap_record",
                return_value=cast(Any, SimpleNamespace(contract_path=Path("/contract"))),
            ),
            mock.patch.object(start_contract, "_bootstrap_ref_creation_started", return_value=True),
            mock.patch.object(start_contract, "_require_bootstrap_ref"),
            self.assertRaisesRegex(RuntimeError, "requires the external memory repository"),
        ):
            start_contract._finish_master_series_bootstrap(spec, record)

        with (
            mock.patch.object(start_contract, "branch_commit", return_value=record.codeBaseCommit),
            self.assertRaisesRegex(RuntimeError, "requires the external memory repository"),
        ):
            start_contract._require_current_bootstrap_sources(spec, record)

        memory_spec = replace(spec, memory_root=Path("/memory"))
        with (
            mock.patch.object(
                start_contract,
                "branch_commit",
                side_effect=[record.codeBaseCommit, "wrong"],
            ),
            self.assertRaisesRegex(RuntimeError, "memory source moved"),
        ):
            start_contract._require_current_bootstrap_sources(memory_spec, record)

        ref = start_contract._BootstrapRef(Path("/repo"), "ar/master", "1" * 40, "", "")
        with (
            mock.patch.object(start_contract, "branch_exists", return_value=False),
            self.assertRaisesRegex(RuntimeError, "exact journaled source authority"),
        ):
            start_contract._require_bootstrap_ref(
                ref, authority=start_contract._BOOTSTRAP_REF_AUTHORITY
            )

    def test_bootstrap_recovery_and_existing_contract_mismatch_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            spec = start_contract.MasterSeriesContractSpec(
                root,
                "repo",
                root / "code",
                None,
                root / "tasks" / "repo" / "master",
                "master",
                "sprint",
                "super",
            )
            record = start_contract._SeriesBootstrapRecord(
                contractPath=(root / "contract.md").as_posix(),
                codeRepository=(root / "code").as_posix(),
                codeSourceBranch="super",
                codeWorkBranch="ar/master",
                codeBaseCommit="1" * 40,
                memoryRepository="",
                memorySourceBranch="",
                memoryWorkBranch="",
                memoryBaseCommit="",
            )
            journal = root / "journal.json"
            journal.write_text(record.model_dump_json(), encoding="utf-8")
            contract_path = root / "contract.md"
            contract_path.write_text("published", encoding="utf-8")
            contract = SimpleNamespace(contract_path=contract_path)
            with (
                mock.patch.object(
                    start_contract,
                    "_master_series_bootstrap_record_path",
                    return_value=journal,
                ),
                mock.patch.object(
                    start_contract,
                    "_contract_from_bootstrap_record",
                    return_value=contract,
                ),
                mock.patch.object(start_contract, "load_contract", return_value=contract),
            ):
                self.assertIs(start_contract._recover_master_series_bootstrap(spec), contract)
            self.assertFalse(journal.exists())

            journal.write_text(record.model_dump_json(), encoding="utf-8")
            with (
                mock.patch.object(
                    start_contract,
                    "_contract_from_bootstrap_record",
                    return_value=contract,
                ),
                mock.patch.object(
                    start_contract, "_bootstrap_ref_creation_started", return_value=True
                ),
                mock.patch.object(start_contract, "_require_bootstrap_ref"),
                mock.patch.object(start_contract, "load_contract", return_value=object()),
                self.assertRaisesRegex(RuntimeError, "published with different"),
            ):
                start_contract._finish_master_series_bootstrap(spec, record)

            with (
                mock.patch.object(
                    start_contract,
                    "_contract_from_bootstrap_record",
                    return_value=contract,
                ),
                mock.patch.object(
                    start_contract, "_bootstrap_ref_creation_started", return_value=True
                ),
                mock.patch.object(start_contract, "_require_bootstrap_ref"),
                mock.patch.object(start_contract, "load_contract", return_value=contract),
                mock.patch.object(
                    start_contract,
                    "_master_series_bootstrap_record_path",
                    return_value=journal,
                ),
            ):
                self.assertIs(
                    start_contract._finish_master_series_bootstrap(spec, record), contract
                )


class IntegrationRecoveryRemainderTests(unittest.TestCase):
    def test_external_recovery_refuses_missing_repo_and_unfinished_memory_cas(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = _authority_fixture(Path(tmp), external_memory=True)
            contract = fixture.leaf_contract
            commits = LifecycleOperationRecoveryCommits(
                codeCommit="2" * 40,
                memoryContentCommit="3" * 40,
                ledgerCommit="4" * 40,
            )
            operation_authority = SimpleNamespace(
                codeSourceBranch="ar/master",
                codeSourceCommit="1" * 40,
                memorySourceBranch="ar/master",
                memorySourceCommit="5" * 40,
            )
            with (
                mock.patch.object(integrate, "branch_commit", return_value="1" * 40),
                self.assertRaisesRegex(RuntimeError, "requires a memory repo"),
            ):
                integrate._recover_landed_refs(
                    replace(contract, memory_repo_path=None),
                    WorktreeArgs(),
                    commits,
                    cast(Any, operation_authority),
                )

            with mock.patch.object(
                integrate,
                "branch_commit",
                side_effect=["1" * 40, "5" * 40],
            ):
                self.assertFalse(
                    integrate._recover_landed_refs(
                        contract,
                        WorktreeArgs(),
                        commits,
                        cast(Any, operation_authority),
                    )
                )

            with self.assertRaisesRegex(RuntimeError, "recorded external-memory commits"):
                integrate._recover_landed_refs(
                    replace(contract, memory_mode="disabled", memory_repo_path=None),
                    WorktreeArgs(),
                    commits,
                    cast(Any, operation_authority),
                )

            with (
                mock.patch.object(
                    integrate,
                    "branch_commit",
                    side_effect=["2" * 40, "5" * 40, "5" * 40],
                ),
                mock.patch.object(integrate, "require_integrated_ledger_mapping"),
                mock.patch.object(integrate, "recover_integration_ref", return_value=False),
                self.assertRaisesRegex(RuntimeError, "could not finish"),
            ):
                integrate._recover_landed_refs(
                    contract,
                    WorktreeArgs(),
                    commits,
                    cast(Any, operation_authority),
                )

    def test_external_recovery_proof_refuses_unreadable_ledger_and_wrong_code_head(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = _authority_fixture(Path(tmp), external_memory=True)
            contract = replace(fixture.master_contract, kind="series")
            commits = LifecycleOperationRecoveryCommits(
                codeCommit="2" * 40,
                memoryContentCommit="3" * 40,
                ledgerCommit="4" * 40,
            )
            with (
                mock.patch.object(integrate, "branch_commit", return_value="4" * 40),
                mock.patch.object(
                    integrate,
                    "run_git",
                    return_value=SimpleNamespace(returncode=1, stdout="", stderr="missing"),
                ),
                self.assertRaisesRegex(RuntimeError, "no readable memory.md"),
            ):
                integrate._prove_external_memory_recovery(contract, commits)

            with (
                mock.patch.object(integrate, "_recover_landed_refs", return_value=True),
                mock.patch.object(integrate, "branch_commit", return_value="wrong"),
                self.assertRaisesRegex(RuntimeError, "found task HEAD"),
            ):
                integrate._prove_integration_recovery_commits(
                    contract,
                    WorktreeArgs(),
                    commits,
                    cast(Any, SimpleNamespace()),
                )

    def test_recovery_finalization_and_completed_descendant_refusals(self) -> None:
        self.assertIsNone(
            integrate._recover_integration_finalization(
                cast(Any, SimpleNamespace()),
                WorktreeArgs(),
                cast(Any, SimpleNamespace()),
            )
        )
        with mock.patch.object(integrate, "_recover_landed_refs", return_value=False):
            self.assertIsNone(
                integrate._prove_integration_recovery_commits(
                    cast(Any, SimpleNamespace()),
                    WorktreeArgs(),
                    LifecycleOperationRecoveryCommits(
                        codeCommit="2" * 40,
                        memoryContentCommit="",
                        ledgerCommit="",
                    ),
                    cast(Any, SimpleNamespace()),
                )
            )
        commits = LifecycleOperationRecoveryCommits(
            codeCommit="2" * 40,
            memoryContentCommit="",
            ledgerCommit="",
        )
        contract = SimpleNamespace(
            integrated_code_commit=commits.codeCommit,
            integrated_memory_content_commit="",
            integrated_ledger_commit="",
            code_repo_path=Path("/code"),
            memory_mode="disabled",
        )
        operation_authority = SimpleNamespace(
            codeCandidateCommit=commits.codeCommit,
            memoryContentCommit="",
            ledgerCommit="",
        )
        target = SimpleNamespace(side="code", branch="super")
        with (
            mock.patch.object(integrate, "integration_targets", return_value=(target,)),
            mock.patch.object(integrate, "branch_commit", return_value="tip"),
            mock.patch.object(integrate, "is_ancestor", return_value=False),
            self.assertRaisesRegex(RuntimeError, "not reachable"),
        ):
            integrate._prove_completed_integration_descendant(
                cast(Any, contract), commits, cast(Any, operation_authority)
            )

        memory_commits = commits.model_copy(
            update={"memoryContentCommit": "3" * 40, "ledgerCommit": "4" * 40}
        )
        external = SimpleNamespace(
            integrated_code_commit=memory_commits.codeCommit,
            integrated_memory_content_commit=memory_commits.memoryContentCommit,
            integrated_ledger_commit=memory_commits.ledgerCommit,
            code_repo_path=Path("/code"),
            memory_repo_path=Path("/memory"),
            memory_mode="external",
        )
        memory_authority = SimpleNamespace(
            codeCandidateCommit=memory_commits.codeCommit,
            memoryContentCommit=memory_commits.memoryContentCommit,
            ledgerCommit=memory_commits.ledgerCommit,
        )
        targets = (
            SimpleNamespace(side="code", branch="super"),
            SimpleNamespace(side="memory", branch="super"),
        )
        with (
            mock.patch.object(integrate, "integration_targets", return_value=targets),
            mock.patch.object(integrate, "branch_commit", return_value="tip"),
            mock.patch.object(integrate, "is_ancestor", side_effect=[True, False]),
            self.assertRaisesRegex(RuntimeError, "ledger is not reachable"),
        ):
            integrate._prove_completed_integration_descendant(
                cast(Any, external), memory_commits, cast(Any, memory_authority)
            )

    def test_completed_result_requires_authority_and_exact_recovery_input(self) -> None:
        contract = SimpleNamespace(integration_status="completed")
        commits = LifecycleOperationRecoveryCommits(
            codeCommit="2" * 40, memoryContentCommit="", ledgerCommit=""
        )
        operation = SimpleNamespace(recoveryCommits=commits, integrationAuthority=None)
        with self.assertRaisesRegex(RuntimeError, "no immutable integration authority"):
            integrate._completed_integration_result(
                cast(Any, contract),
                WorktreeArgs(recovery_commits=commits),
                cast(Any, operation),
            )

        operation.integrationAuthority = SimpleNamespace()
        with self.assertRaisesRegex(RuntimeError, "does not match"):
            integrate._completed_integration_result(
                cast(Any, contract), WorktreeArgs(), cast(Any, operation)
            )

    def test_recovery_publication_and_apply_recheck_current_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = _authority_fixture(Path(tmp))
            contract = fixture.leaf_contract
            with (
                mock.patch.object(
                    integrate, "load_contract", return_value=replace(contract, cleanup="completed")
                ),
                mock.patch.object(
                    integrate,
                    "publish_queue_candidate_integration_under_authority",
                    side_effect=lambda _contract, publication, **_kwargs: publication(),
                ),
                self.assertRaisesRegex(RuntimeError, "changed before recovery finalization"),
            ):
                integrate._recover_integration_under_authority(
                    contract,
                    WorktreeArgs(operation_key="a" * 64),
                    cast(
                        Any,
                        SimpleNamespace(
                            codeCandidateCommit="code",
                            memoryContentCommit="",
                            ledgerCommit="",
                        ),
                    ),
                )

            blocker = WorktreeCommandResult(2, {"state": "blocked"})
            series = fixture.master_contract
            with (
                mock.patch.object(
                    integrate,
                    "_prepare_integration_commits",
                    return_value=(IntegratedCommits("code", "", ""), {}),
                ),
                mock.patch.object(integrate, "load_contract", return_value=series),
                mock.patch.object(integrate, "require_series_contract_authority") as require,
                mock.patch.object(
                    integrate, "_integration_source_state_block", return_value=blocker
                ),
                mock.patch.object(
                    integrate,
                    "publish_series_integration_under_authority",
                    side_effect=lambda _contract, publication: publication(),
                ),
            ):
                self.assertIs(
                    integrate._apply_integration(
                        series,
                        WorktreeArgs(),
                        cast(Any, SimpleNamespace()),
                        handover_warning=None,
                    ),
                    blocker,
                )
            require.assert_called_once()

    def test_ff_only_replay_is_structurally_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = _authority_fixture(Path(tmp))
            blocker = WorktreeCommandResult(2, {"state": "blocked"})
            with mock.patch.object(integrate, "_blocked_non_ff_result", return_value=blocker):
                self.assertIs(
                    integrate._continue_integration(
                        fixture.leaf_contract,
                        WorktreeArgs(strategy="ff-only"),
                        cast(Any, SimpleNamespace(replay_required=True)),
                        None,
                    ),
                    blocker,
                )


if __name__ == "__main__":
    unittest.main()
