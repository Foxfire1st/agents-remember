"""Focused proof for live-leaf topology collision and deleted-owner repair."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest import mock

import pytest
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks.document_refs import TaskDocumentRefError
from agents_remember.worktrees.integration import (
    integration_topology_collisions as collisions,
)
from agents_remember.worktrees.integration import integration_topology_repair as repair
from agents_remember.worktrees.integration.integration_branch_types import (
    IntegrationSurface,
    _RepositorySide,
)


def _value(**fields: object) -> Any:
    return cast(Any, SimpleNamespace(**fields))


def _ref(path: str) -> TaskDocumentRef:
    return TaskDocumentRef(repository="repo", path=path)


def _surface(*, owner: str = "repo/master/task.json", branch: str = "super") -> IntegrationSurface:
    return IntegrationSurface(
        side="code",
        kind="sprint-super",
        repository=Path("/tmp/code"),
        branch=branch,
        owner=owner,
    )


def _side() -> _RepositorySide:
    return _RepositorySide(
        side="code",
        repository=Path("/tmp/code"),
        worktree=Path("/tmp/worktree"),
        source_branch="super",
        work_branch="leaf",
    )


def _membership_error(status: str = "task-execution-graph-membership-invalid") -> RuntimeError:
    try:
        raise TaskDocumentRefError(status, "invalid membership")
    except TaskDocumentRefError as cause:
        try:
            raise RuntimeError("current topology unreadable") from cause
        except RuntimeError as error:
            return error


def test_current_authority_handles_initial_build_repair_and_ordinary_read() -> None:
    owner = _ref("master/task.json")
    authority: collisions._PublicationMasterAuthority = {owner: (cast(Any, _value()), None)}
    request = _value(current=(), repaired_owners=None, scope=_value(repo_name="repo"))
    services = _value()
    assert (
        collisions._current_publication_master_authority(_value(), request, authority, services)
        == {}
    )

    request.current = (_surface(),)
    request.repaired_owners = {owner}
    assert (
        collisions._current_publication_master_authority(_value(), request, authority, services)
        == {}
    )
    assert collisions._authority_without_repaired_owners(authority, {owner}) == {}

    request.repaired_owners = None
    with mock.patch.object(
        collisions, "_publication_master_authority", return_value=authority
    ) as publication:
        assert (
            collisions._current_publication_master_authority(_value(), request, {}, services)
            == authority
        )
    publication.assert_called_once()


def test_commanded_and_standalone_authority_refuses_multiple_sprint_owners() -> None:
    master_ref = _ref("master/task.json")
    sprint_one = _value(ref=_ref("sprint-one/task.json"))
    sprint_two = _value(ref=_ref("sprint-two/task.json"))
    master = _value(ref=master_ref, document=_value(orchestrates=[], executionNature="atomic"))
    authority: dict[TaskDocumentRef, tuple[Any, TaskDocumentRef | None]] = {}
    commanded: set[TaskDocumentRef] = set()
    with mock.patch.object(collisions, "commanded_sprint_masters", return_value=(master,)):
        collisions._record_commanded_authority(_value(), sprint_one, None, authority, commanded)
    assert authority[master_ref] == (master, sprint_one.ref)
    with (
        mock.patch.object(collisions, "commanded_sprint_masters", return_value=(master,)),
        pytest.raises(RuntimeError, match="multiple sprint owners"),
    ):
        collisions._record_commanded_authority(_value(), sprint_two, None, authority, commanded)

    standalone = _value(
        ref=_ref("standalone/task.json"),
        document=_value(orchestrates=[], executionNature="atomic"),
    )
    assert collisions._is_standalone_atomic_master(standalone, set())
    assert not collisions._is_standalone_atomic_master(standalone, {standalone.ref})
    collisions._record_standalone_atomic_authority((standalone,), authority, commanded)
    assert authority[standalone.ref] == (standalone, None)


def test_live_leaf_owner_release_and_sprint_stability_are_explicit() -> None:
    owner = (_value(document=_value(executionNature="organizational")), _ref("sprint/task.json"))
    contract = _value(cleanup="completed", contract_path=Path("/tmp/contract.json"))
    assert collisions._completed_leaf_is_released(contract, None)
    assert collisions._completed_leaf_is_released(contract, owner)
    contract.cleanup = "not-started"
    assert not collisions._completed_leaf_is_released(contract, owner)

    assert collisions._required_live_leaf_owner(contract, owner) == owner
    with pytest.raises(RuntimeError, match="lose its exact owning"):
        collisions._required_live_leaf_owner(contract, None)

    collisions._require_stable_live_leaf_sprint((), None, owner[1], contract)
    collisions._require_stable_live_leaf_sprint((_surface(),), owner, owner[1], contract)
    with pytest.raises(RuntimeError, match="owning sprint would change"):
        collisions._require_stable_live_leaf_sprint((_surface(),), None, owner[1], contract)


def test_workbench_and_source_surfaces_must_keep_one_exact_owner() -> None:
    side = _side()
    key = (side.side, side.repository, side.work_branch)
    services = _value(
        repository_sides=lambda _contract: (side,),
        branch_key=lambda current, branch: (current.side, current.repository, branch),
    )
    contract = _value(contract_path=Path("/tmp/contract.json"))
    context = _value(services=services, candidate_keys=set(), current_keys=set())
    collisions._require_live_leaf_workbench_unprotected(context, contract)
    context.candidate_keys = {key}
    with pytest.raises(RuntimeError, match="live leaf workbench"):
        collisions._require_live_leaf_workbench_unprotected(context, contract)

    source_key = (side.side, side.repository, side.source_branch)
    surface = _surface()
    assert collisions._surface_matches_owner(surface, source_key, "sprint-super", surface.owner)
    assert not collisions._surface_matches_owner(
        surface, source_key, "atomic-integration", surface.owner
    )
    source_context = _value(services=services, candidate=(surface,))
    collisions._require_live_leaf_side_source(
        source_context, contract, side, "sprint-super", surface.owner
    )
    source_context.candidate = ()
    with pytest.raises(RuntimeError, match="source no longer matches"):
        collisions._require_live_leaf_side_source(
            source_context, contract, side, "sprint-super", surface.owner
        )

    empty_context = _value(
        services=_value(repository_sides=lambda _contract: ()),
        candidate=(),
    )
    collisions._require_live_leaf_source_authority(
        empty_context,
        contract,
        _value(ref=_ref("master/task.json")),
        None,
        "atomic",
    )

    context.current_keys = {source_key}
    context.candidate_keys = {source_key}
    collisions._require_preserved_live_leaf_sources(context, contract)
    context.candidate_keys = set()
    with pytest.raises(RuntimeError, match="source authority would be removed"):
        collisions._require_preserved_live_leaf_sources(context, contract)


def test_leaf_document_resolution_and_path_confinement_fail_closed(tmp_path: Path) -> None:
    contract = _value(repo_name="repo", contract_path=tmp_path / "contract.json")
    leaf_ref = _ref("master/leaf.json")
    document = _value(id="leaf")
    topology = _value(resolve=lambda _ref, _overrides: _value(document=document))
    assert collisions._resolved_live_leaf_document(topology, leaf_ref, {}, contract) is document
    topology.resolve = mock.Mock(side_effect=TaskDocumentRefError("task-ref-invalid", "bad ref"))
    with pytest.raises(RuntimeError, match="authority is invalid"):
        collisions._resolved_live_leaf_document(topology, leaf_ref, {}, contract)

    coordination = tmp_path / "coordination"
    task_root = coordination / "tasks" / "repo"
    master_dir = task_root / "master"
    master_dir.mkdir(parents=True)
    topology = _value(coordination_root=coordination)
    master = _value(path=master_dir / "task.json")
    assert collisions._live_leaf_ref(topology, contract, master, "leaf.md") == leaf_ref
    with pytest.raises(RuntimeError, match="repository task tree"):
        collisions._live_leaf_ref(topology, contract, master, "../../../escape.md")
    nested = master_dir / "nested"
    nested.mkdir()
    with pytest.raises(RuntimeError, match="owning master task root"):
        collisions._live_leaf_ref(topology, contract, master, "nested/leaf.md")


def test_deleted_owner_repair_reads_or_filters_one_exact_override_set(tmp_path: Path) -> None:
    owner = _ref("master/task.json")
    kept = _ref("kept/task.json")
    existing = tmp_path / "tasks" / kept.repository / kept.path
    existing.parent.mkdir(parents=True)
    existing.write_text("{}", encoding="utf-8")
    overrides = {owner: cast(Any, _value()), kept: cast(Any, _value())}
    surface = _surface(owner=owner.key)
    kept_surface = _surface(owner=kept.key, branch="kept")

    assert repair.current_surfaces_for_publication(
        (surface,), tmp_path, {}, lambda: (surface,)
    ) == ((surface,), set())
    assert repair._deleted_override_owners(tmp_path, overrides) == {owner}
    repair._require_complete_owner_repair({owner}, {owner: overrides[owner]})
    with pytest.raises(RuntimeError, match="exactly replace deleted owners"):
        repair._require_complete_owner_repair({owner}, overrides)
    assert repair._surfaces_without_owners((surface, kept_surface), {owner}) == (kept_surface,)

    with pytest.raises(RuntimeError, match="current topology unreadable"):
        repair._require_repairable_membership_error(_membership_error("other"))
    repair._require_repairable_membership_error(_membership_error())

    only_deleted = {owner: overrides[owner]}
    candidate, repaired = repair.current_surfaces_for_publication(
        (surface, kept_surface),
        tmp_path,
        only_deleted,
        lambda: (_ for _ in ()).throw(_membership_error()),
    )
    assert repaired == {owner}
    assert candidate == (kept_surface,)
