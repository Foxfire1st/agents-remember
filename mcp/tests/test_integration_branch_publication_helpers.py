"""Focused proof for topology-publication branch availability and overrides."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest import mock

import pytest
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.worktrees.integration import integration_branch_authority as authority
from agents_remember.worktrees.integration.integration_branch_types import (
    IntegrationSurface,
    _BranchScope,
)


def _value(**fields: object) -> Any:
    return cast(Any, SimpleNamespace(**fields))


def _surface(
    *,
    kind: str = "atomic-integration",
    branch: str = "ar/master",
    owner: str = "repo/master/task.json",
) -> IntegrationSurface:
    return IntegrationSurface(
        side="code",
        kind=cast(Any, kind),
        repository=Path("/tmp/code"),
        branch=branch,
        owner=owner,
    )


def _scope() -> _BranchScope:
    return _BranchScope(Path("/tmp/coordination"), "repo", Path("/tmp/tasks/repo"), ())


def test_publication_and_migration_authority_apply_their_distinct_current_state() -> None:
    scope = _scope()
    candidate = (_surface(),)
    current = (_surface(kind="repository-default", branch="main", owner="default"),)
    repaired: set[TaskDocumentRef] = set()
    with (
        mock.patch.object(authority, "_publication_scope", return_value=scope),
        mock.patch.object(authority, "_integration_surfaces", return_value=candidate),
        mock.patch.object(
            authority,
            "current_surfaces_for_publication",
            return_value=(current, repaired),
        ),
        mock.patch.object(authority, "_require_stable_surface_owners") as stable,
        mock.patch.object(authority, "require_no_live_leaf_collisions") as collisions,
        mock.patch.object(authority, "_require_new_surface_availability") as availability,
    ):
        authority.require_topology_publication_authority(
            scope.coordination_root,
            scope.repo_name,
            Path("/tmp/code"),
            None,
            {},
        )
    stable.assert_called_once_with(current, candidate)
    assert collisions.call_args.args[0].repaired_owners == repaired
    availability.assert_called_once_with(scope, current, candidate, allow_existing_super=False)

    with (
        mock.patch.object(authority, "_publication_scope", return_value=scope),
        mock.patch.object(authority, "_integration_surfaces", return_value=candidate),
        mock.patch.object(authority, "require_no_live_leaf_collisions") as collisions,
        mock.patch.object(authority, "_require_new_surface_availability") as availability,
    ):
        authority.require_topology_migration_authority(
            scope.coordination_root,
            scope.repo_name,
            Path("/tmp/code"),
            None,
            {},
        )
    assert collisions.call_args.args[0].current == ()
    availability.assert_called_once_with(scope, (), candidate, allow_existing_super=True)


def test_publication_scope_deduplicates_same_repository_memory() -> None:
    code = Path("/tmp/code")
    memory = Path("/tmp/memory")
    with (
        mock.patch.object(authority, "_repository_identity", return_value=code),
        mock.patch.object(authority, "repository_identity", return_value=code),
    ):
        scope = authority._publication_scope(Path("/tmp/coordination"), "repo", code, memory)
    assert [side.side for side in scope.sides] == ["code"]

    with (
        mock.patch.object(authority, "_repository_identity", return_value=code),
        mock.patch.object(authority, "repository_identity", return_value=memory),
    ):
        scope = authority._publication_scope(Path("/tmp/coordination"), "repo", code, memory)
    assert [side.side for side in scope.sides] == ["code", "memory"]


def test_surface_availability_skips_existing_and_checks_new_authority() -> None:
    scope = _scope()
    default = _surface(kind="repository-default", branch="main", owner="default")
    existing = _surface()
    new = _surface(branch="ar/other", owner="repo/other/task.json")
    current_keys = {authority._surface_key(existing)}
    assert not authority._surface_requires_availability(default, current_keys, False)
    assert not authority._surface_requires_availability(existing, current_keys, False)
    assert authority._surface_requires_availability(new, current_keys, False)
    super_surface = _surface(kind="sprint-super", branch="super", owner="repo/sprint/task.json")
    assert not authority._surface_requires_availability(super_surface, set(), True)

    with (
        mock.patch.object(authority, "_require_atomic_surface_available") as atomic,
        mock.patch.object(authority, "_require_surface_not_checked_out") as checkout,
    ):
        authority._require_new_surface_availability(
            scope,
            (existing,),
            (default, existing, new),
            allow_existing_super=False,
        )
    atomic.assert_called_once_with(scope, new)
    checkout.assert_called_once_with(scope, new)


def test_atomic_branch_and_checkout_collisions_require_exact_series_owner() -> None:
    scope = _scope()
    surface = _surface()
    with (
        mock.patch.object(authority, "branch_exists", return_value=True),
        mock.patch.object(authority, "_atomic_surface_has_series", return_value=False),
        pytest.raises(RuntimeError, match="atomic branch already exists"),
    ):
        authority._require_atomic_surface_available(scope, surface)
    with mock.patch.object(authority, "branch_exists", return_value=False):
        authority._require_atomic_surface_available(scope, surface)

    owners = (Path("/tmp/worktree"),)
    with (
        mock.patch.object(authority, "branch_worktree_owners", return_value=owners),
        mock.patch.object(authority, "_atomic_surface_has_series", return_value=False),
        pytest.raises(RuntimeError, match="already checked out"),
    ):
        authority._require_surface_not_checked_out(scope, surface)
    with (
        mock.patch.object(authority, "branch_worktree_owners", return_value=owners),
        mock.patch.object(authority, "_atomic_surface_has_series", return_value=True),
    ):
        authority._require_surface_not_checked_out(scope, surface)


def test_master_override_ignores_foreign_removes_leaf_and_replaces_master(tmp_path: Path) -> None:
    own = TaskDocumentRef(repository="repo", path="master/task.json")
    foreign = TaskDocumentRef(repository="other", path="master/task.json")
    existing = _value(ref=own)
    masters: dict[TaskDocumentRef, Any] = {own: existing}
    root = tmp_path / "tasks" / "repo"

    authority._apply_master_override(masters, root, "repo", foreign, _value(kind="master"))
    assert masters == {own: existing}
    authority._apply_master_override(masters, root, "repo", own, _value(kind="subTask"))
    assert masters == {}
    document = _value(kind="master")
    authority._apply_master_override(masters, root, "repo", own, document)
    assert masters[own].document is document
    assert masters[own].path == root / own.path


def test_repository_master_overrides_are_applied_before_stable_ordering() -> None:
    ref = TaskDocumentRef(repository="repo", path="master/task.json")
    document = _value(kind="master")
    topology = _value(coordination_root=Path("/tmp/coordination"))
    resolved = _value(ref=ref)
    with (
        mock.patch.object(authority, "repository_master_documents", return_value=()),
        mock.patch.object(authority, "_apply_master_override") as apply,
    ):
        apply.side_effect = lambda masters, _root, _repo, own_ref, _document: masters.update(
            {own_ref: resolved}
        )
        assert authority._repository_masters_with_overrides(topology, "repo", {ref: document}) == (
            resolved,
        )
    apply.assert_called_once()
