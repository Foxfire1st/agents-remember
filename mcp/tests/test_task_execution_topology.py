from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import patch

from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig, RepositoryScope
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks import (
    SubTaskRef,
    TaskDocument,
    read_task_doc,
    write_task_doc,
)
from agents_remember.worktrees import task_fact_publication
from agents_remember.worktrees.task_stage import plan_task_stage
from agents_remember.worktrees.worktree_contract import WorktreeContract

REPOSITORY = "agents-remember"
SPRINT = TaskDocumentRef(repository=REPOSITORY, path="sprint/task.json")
MASTER_A = TaskDocumentRef(repository=REPOSITORY, path="master-a/task.json")
MASTER_B = TaskDocumentRef(repository=REPOSITORY, path="master-b/task.json")
MASTER_C = TaskDocumentRef(repository=REPOSITORY, path="master-c/task.json")


def _config(coordination_root: Path, code_repository: Path) -> McpRuntimeConfig:
    scope = RepositoryScope(REPOSITORY, code_repository)
    return cast(
        McpRuntimeConfig,
        SimpleNamespace(
            coordination_root=coordination_root,
            repositories={REPOSITORY: scope},
        ),
    )


def _master(
    *,
    identity: str,
    orchestrates: list[str] | None = None,
    execution_nature: str | None = None,
    execution_graph: dict[str, Any] | None = None,
    title: str | None = None,
) -> TaskDocument:
    return TaskDocument.model_validate(
        {
            "id": identity,
            "slug": identity,
            "title": title or identity,
            "kind": "master",
            "repo": REPOSITORY,
            "type": "Master",
            "createdAt": "2026-08-15T00:00:00+00:00",
            "orchestrates": orchestrates or [],
            "executionNature": execution_nature,
            "executionGraph": execution_graph,
        }
    )


def test_series_stage_publication_mirrors_commanding_sprint_in_one_batch(tmp_path: Path) -> None:

    tasks = tmp_path / "tasks" / REPOSITORY
    reference = MASTER_A
    master = _master(identity="master-a", execution_nature="atomic").model_copy(
        update={"status": "planning", "slug": "task"}
    )
    sprint = _master(
        identity="sprint",
        orchestrates=["master-a"],
        execution_graph={"nodes": [reference.model_dump()], "edges": []},
    ).model_copy(
        update={
            "slug": "task",
            "subTasks": [
                SubTaskRef(number="1", name="Master", status="planning", masterRef=reference)
            ],
        }
    )
    write_task_doc(tasks / "master-a", master)
    write_task_doc(tasks / "sprint", sprint)
    contract = cast(
        WorktreeContract,
        SimpleNamespace(
            kind="series",
            leaf_id="",
            task_root=tasks / "master-a",
            coordination_root=tmp_path,
            repo_name=REPOSITORY,
        ),
    )
    for status in ("inProgress", "abandoned"):
        change = plan_task_stage(contract, status)
        assert len(change.documents) == 2
        # A cross-root batch must retain both native document addresses in the real preview.
        preview = task_fact_publication.preview_closeout_projection_effect
        with patch.object(
            task_fact_publication, "preview_closeout_projection_effect", wraps=preview
        ) as observed:
            effects = task_fact_publication.preview_contract_task_facts(contract, change.candidates)
        assert len(effects) == 1
        overrides = observed.call_args.kwargs["overrides"]
        assert set(overrides) == {SPRINT, MASTER_A}
        assert overrides[MASTER_A].id == master.id
        assert overrides[SPRINT].id == sprint.id
        assert effects[0].sprintTaskDocumentRef == SPRINT
        assert effects[0].rebuild.outcome == "would-publish"
        change.publish()
        assert read_task_doc(tasks / "master-a" / "task.json").status == status
        assert read_task_doc(tasks / "sprint" / "task.json").subTasks[0].status == status
