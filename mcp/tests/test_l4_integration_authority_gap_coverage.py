"""Focused branch forcing for task-derived integration authority."""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks.document_refs import TaskDocumentTopology
from agents_remember.worktrees import integration_branch_authority as authority
from agents_remember.worktrees.integration_branch_types import (
    IntegrationSurfaceSide,
    ProposedWorkBranches,
    RepositoryCheckoutRequest,
    _MasterAuthority,
    _RepositorySide,
)
from integration_branch_authority_test_support import _authority_fixture


def _side(
    repository: Path = Path("/repo"),
    *,
    side: IntegrationSurfaceSide = "code",
    worktree: Path | None = None,
    source: str = "super",
    work: str = "leaf",
) -> _RepositorySide:
    return _RepositorySide(
        side=side,
        repository=repository,
        worktree=worktree or repository,
        source_branch=source,
        work_branch=work,
    )


class IntegrationTargetGapCoverageTests(unittest.TestCase):
    def test_targets_refuse_unsupported_and_standalone_series_contracts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = _authority_fixture(Path(tmp))
            contract = fixture.leaf_contract
            topology = TaskDocumentTopology(contract.coordination_root)
            master_ref = TaskDocumentRef(repository="repo", path="master/task.json")
            standalone = _MasterAuthority(topology, master_ref, None, None, "atomic")
            side = _side(fixture.code_repo, source="feature", work="ar/master")
            with (
                mock.patch.object(authority, "_repository_sides", return_value=(side,)),
                mock.patch.object(authority, "_master_authority", return_value=standalone),
                mock.patch.object(authority, "_side_default_branch", return_value="main"),
                mock.patch.object(
                    authority,
                    "canonical_local_branch",
                    side_effect=lambda _repo, branch: branch,
                ),
                self.assertRaisesRegex(RuntimeError, "standalone atomic series source"),
            ):
                authority.integration_targets(
                    replace(fixture.master_contract, code_source_branch="feature")
                )

            side = replace(side, source_branch="main")
            with (
                mock.patch.object(authority, "_repository_sides", return_value=(side,)),
                mock.patch.object(authority, "_master_authority", return_value=standalone),
                mock.patch.object(authority, "_side_default_branch", return_value="main"),
                mock.patch.object(
                    authority,
                    "canonical_local_branch",
                    side_effect=lambda _repo, branch: branch,
                ),
                self.assertRaisesRegex(RuntimeError, "PR landing plane"),
            ):
                authority.integration_targets(
                    replace(fixture.master_contract, code_source_branch="main")
                )

            with (
                mock.patch.object(authority, "_repository_sides", return_value=(side,)),
                mock.patch.object(authority, "_master_authority", return_value=standalone),
                mock.patch.object(authority, "_side_default_branch", return_value="main"),
                self.assertRaisesRegex(RuntimeError, "leaf or series"),
            ):
                authority.integration_targets(replace(contract, kind="invalid"))

    def test_targets_refuse_repository_default_leaf_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = _authority_fixture(Path(tmp))
            contract = fixture.leaf_contract
            side = _side(fixture.code_repo, source="main")
            with (
                mock.patch.object(authority, "_repository_sides", return_value=(side,)),
                mock.patch.object(
                    authority,
                    "_master_authority",
                    return_value=SimpleNamespace(),
                ),
                mock.patch.object(
                    authority,
                    "_leaf_target",
                    return_value=("sprint-super", "main", "owner"),
                ),
                mock.patch.object(authority, "_side_default_branch", return_value="main"),
                mock.patch.object(
                    authority,
                    "canonical_local_branch",
                    side_effect=lambda _repo, branch: branch,
                ),
                self.assertRaisesRegex(RuntimeError, "repository-default code branch"),
            ):
                authority.integration_targets(contract)

    def test_proposed_external_memory_cannot_alias_code_repository(self) -> None:
        proposal = ProposedWorkBranches(
            Path("/coordination"),
            "repo",
            Path("/coordination/tasks/repo/master"),
            Path("/code"),
            "leaf",
            Path("/memory"),
            "leaf",
        )
        with (
            mock.patch.object(authority, "_repository_identity", return_value=Path("/same")),
            self.assertRaisesRegex(RuntimeError, "must not share"),
        ):
            authority.require_proposed_work_branches(proposal)


class OrdinaryAndTerminalAuthorityGapCoverageTests(unittest.TestCase):
    def test_ordinary_worktree_skips_series_and_refuses_wrong_repo_or_branch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = _authority_fixture(root)
            authority.require_ordinary_worktree(
                fixture.master_contract,
                operation="test",
            )
            checkout = root / "checkout"
            checkout.mkdir()
            side = _side(fixture.code_repo, worktree=checkout)
            with (
                mock.patch.object(authority, "integration_surfaces", return_value=()),
                mock.patch.object(authority, "_repository_sides", return_value=(side,)),
                mock.patch.object(authority, "_require_unprotected_branch"),
                mock.patch.object(
                    authority,
                    "_repository_identity",
                    return_value=Path("/expected"),
                ),
                mock.patch.object(authority, "repository_identity", return_value=Path("/actual")),
                self.assertRaisesRegex(RuntimeError, "does not belong"),
            ):
                authority.require_ordinary_worktree(fixture.leaf_contract, operation="test")

            with (
                mock.patch.object(authority, "integration_surfaces", return_value=()),
                mock.patch.object(authority, "_repository_sides", return_value=(side,)),
                mock.patch.object(authority, "_require_unprotected_branch"),
                mock.patch.object(
                    authority,
                    "_repository_identity",
                    return_value=Path("/same"),
                ),
                mock.patch.object(authority, "repository_identity", return_value=Path("/same")),
                mock.patch.object(authority, "current_branch", return_value="other"),
                mock.patch.object(
                    authority,
                    "canonical_local_branch",
                    side_effect=lambda _repo, branch: branch,
                ),
                self.assertRaisesRegex(RuntimeError, "expected 'leaf'"),
            ):
                authority.require_ordinary_worktree(fixture.leaf_contract, operation="test")

    def test_terminal_worktree_refuses_every_series_identity_violation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = _authority_fixture(Path(tmp))
            series = fixture.master_contract
            with self.assertRaisesRegex(RuntimeError, "unsupported contract kind"):
                authority.require_terminal_worktree(
                    replace(fixture.leaf_contract, kind="invalid"),
                    operation="worktree_cleanup",
                )

            topology = SimpleNamespace(
                resolve=lambda _ref: SimpleNamespace(document=SimpleNamespace(status="inProgress"))
            )
            master_authority = SimpleNamespace(
                topology=topology,
                master_ref=TaskDocumentRef(repository="repo", path="master/task.json"),
                sprint_branch="super",
            )
            with (
                mock.patch.object(
                    authority,
                    "require_series_contract_authority",
                    return_value=master_authority,
                ),
                self.assertRaisesRegex(RuntimeError, "must be Completed"),
            ):
                authority.require_terminal_worktree(series, operation="worktree_cleanup")

            master_authority = SimpleNamespace(
                topology=SimpleNamespace(
                    resolve=lambda _ref: SimpleNamespace(
                        document=SimpleNamespace(status="Completed")
                    )
                ),
                master_ref=TaskDocumentRef(repository="repo", path="master/task.json"),
                sprint_branch="super",
            )
            side = _side(fixture.code_repo, work="alias")
            with (
                mock.patch.object(
                    authority,
                    "require_series_contract_authority",
                    return_value=master_authority,
                ),
                mock.patch.object(authority, "_repository_sides", return_value=(side,)),
                self.assertRaisesRegex(RuntimeError, "exact task-owned spelling"),
            ):
                authority.require_terminal_worktree(series, operation="worktree_cleanup")

            expected = f"ar/{series.task_root.name}"
            side = replace(side, work_branch=expected)
            with (
                mock.patch.object(
                    authority,
                    "require_series_contract_authority",
                    return_value=master_authority,
                ),
                mock.patch.object(authority, "_repository_sides", return_value=(side,)),
                mock.patch.object(
                    authority,
                    "canonical_local_branch",
                    side_effect=["other", expected],
                ),
                self.assertRaisesRegex(RuntimeError, "not its task-owned atomic branch"),
            ):
                authority.require_terminal_worktree(series, operation="worktree_cleanup")

            side = replace(side, work_branch=expected)
            master_authority.sprint_branch = expected
            with (
                mock.patch.object(
                    authority,
                    "require_series_contract_authority",
                    return_value=master_authority,
                ),
                mock.patch.object(authority, "_repository_sides", return_value=(side,)),
                mock.patch.object(
                    authority,
                    "canonical_local_branch",
                    side_effect=lambda _repo, branch: branch,
                ),
                mock.patch.object(authority, "_side_default_branch", return_value="main"),
                self.assertRaisesRegex(RuntimeError, "protected parent ref"),
            ):
                authority.require_terminal_worktree(series, operation="worktree_cleanup")

    def test_series_and_parent_authority_refuse_wrong_shapes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = _authority_fixture(Path(tmp))
            with self.assertRaisesRegex(RuntimeError, "requires an atomic series"):
                authority.require_series_contract_authority(
                    fixture.leaf_contract,
                    operation="test",
                )

            topology = TaskDocumentTopology(fixture.coordination)
            standalone = _MasterAuthority(
                topology,
                TaskDocumentRef(repository="repo", path="master/task.json"),
                None,
                None,
                "atomic",
            )
            with (
                mock.patch.object(authority, "_master_authority", return_value=standalone),
                mock.patch.object(authority, "_require_atomic_master"),
                mock.patch.object(authority, "_require_series_identity"),
                mock.patch.object(
                    authority,
                    "_repository_sides",
                    return_value=(_side(fixture.code_repo, source="feature"),),
                ),
                mock.patch.object(
                    authority,
                    "canonical_local_branch",
                    side_effect=lambda _repo, branch: branch,
                ),
                mock.patch.object(authority, "_side_default_branch", return_value="main"),
                self.assertRaisesRegex(RuntimeError, "standalone atomic code source"),
            ):
                authority.require_series_contract_authority(
                    fixture.master_contract,
                    operation="test",
                )

            missing_parent = replace(
                fixture.leaf_contract,
                parent_contract_path=Path(tmp) / "missing-series.md",
            )
            with (
                mock.patch.object(authority, "_master_authority", return_value=standalone),
                mock.patch.object(authority, "_require_atomic_master"),
                self.assertRaisesRegex(RuntimeError, "exact parent series"),
            ):
                authority.require_parent_series_accepting_leaves(
                    missing_parent,
                    operation="test",
                )


class RepositoryCheckoutGapCoverageTests(unittest.TestCase):
    def test_repository_checkout_refuses_alias_missing_side_and_foreign_checkout(self) -> None:
        request = RepositoryCheckoutRequest(
            Path("/coordination"),
            "repo",
            Path("/code"),
            Path("/memory"),
            Path("/memory"),
            "memory",
            "carryover",
        )
        with (
            mock.patch.object(authority, "_repository_identity", return_value=Path("/same")),
            self.assertRaisesRegex(RuntimeError, "must not share"),
        ):
            authority.require_ordinary_repository_checkout(request)

        missing = replace(request, memory_repository=None)
        with self.assertRaisesRegex(RuntimeError, "cannot resolve"):
            authority.require_ordinary_repository_checkout(missing)

        with (
            mock.patch.object(
                authority,
                "_repository_identity",
                side_effect=[Path("/code"), Path("/memory"), Path("/memory")],
            ),
            mock.patch.object(authority, "repository_identity", return_value=Path("/foreign")),
            self.assertRaisesRegex(RuntimeError, "belongs to another repository"),
        ):
            authority.require_ordinary_repository_checkout(request)
