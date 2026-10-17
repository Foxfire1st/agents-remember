"""The task entry owns stage, activity, membership and counts (MIK-R167)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from itertools import permutations
from pathlib import Path

import pytest
from agents_remember.observer.host_activity import (
    HostActivitySnapshot,
    PublishedAgentActivity,
    TaskAgentActivity,
)
from agents_remember.observer.task_entries import build_task_entries, task_counts, task_state
from agents_remember.serving.projections.snapshots_impl._task_documents import read_task_sources
from agents_remember.tasks import TaskDocument, write_task_doc

NOW = datetime(2026, 10, 15, tzinfo=UTC)


@pytest.mark.parametrize(
    "status,expected",
    [
        ("planning", "planned"),
        ("inProgress", "started"),
        ("abandoned", "aborted"),
        ("Completed", "completed"),
    ],
)
@pytest.mark.parametrize("known", [False, True])
def test_stage_has_priority_over_host_activity(status: str, expected: str, known: bool) -> None:
    state = task_state(
        status, [PublishedAgentActivity(state="in-turn", eventAt=NOW)], host_known=known
    )
    assert state.stage == expected
    assert "ageSeconds" not in state.model_dump()
    if expected != "started":
        assert (state.activity, state.eventAt) == (None, None)
    else:
        assert state.activity == ("works" if known else "unknown")


def test_work_and_permission_win_and_event_time_comes_from_a_contributor_in_every_order() -> None:
    for working in ("in-turn", "permission"):
        records = [
            PublishedAgentActivity(state=working, eventAt=NOW - timedelta(seconds=9)),
            PublishedAgentActivity(state="idle", eventAt=NOW - timedelta(seconds=1)),
            PublishedAgentActivity(state="ended", eventAt=NOW),
        ]
        for order in permutations(records):
            state = task_state("inProgress", order, host_known=True)
            assert (state.activity, state.eventAt) == (
                "works",
                (NOW - timedelta(seconds=9)).isoformat(),
            )
    paused = task_state("inProgress", records[1:], host_known=True)
    assert (paused.activity, paused.eventAt) == ("paused", NOW.isoformat())
    assert task_state("inProgress", [], host_known=True).activity == "paused"
    assert (
        task_state("inProgress", [PublishedAgentActivity(state="idle")], host_known=True).eventAt
        is None
    )
    assert (
        task_state(
            "inProgress",
            [PublishedAgentActivity(state="in-turn", eventAt=NOW + timedelta(seconds=62))],
            host_known=True,
        ).eventAt
        == (NOW + timedelta(seconds=62)).isoformat()
    )


def test_one_declared_child_without_a_document_gets_one_entry_and_the_same_counts(
    tmp_path: Path,
) -> None:
    root = tmp_path / "tasks" / "repo" / "master"
    write_task_doc(
        root,
        TaskDocument.model_validate(
            {
                "id": "MASTER",
                "slug": "task",
                "kind": "master",
                "repo": "repo",
                "title": "Master",
                "createdAt": NOW.isoformat(),
                "status": "inProgress",
                "subTasks": [
                    {
                        "number": "L1",
                        "name": "Not authored",
                        "file": "01_leaf.md",
                        "status": "planning",
                        "scope": "Read [the plan](https://example.org/plan)",
                    }
                ],
            }
        ),
    )
    result = build_task_entries(read_task_sources(tmp_path, now=NOW), [], HostActivitySnapshot())
    assert len(result.tasks) == 2
    master, child = result.tasks
    assert master.children == [child.key]
    assert child.reference is not None
    assert child.reference.key == "repo/master/01_leaf.json"
    assert (
        child.id,
        child.title,
        child.stage,
        child.summary,
        child.stepsDone,
        child.stepsTotal,
    ) == ("L1", "Not authored", "planned", None, 0, 0)
    assert child.scope == "Read [the plan](https://example.org/plan)"
    assert master.counts.planned == 1
    assert result.counts.planned == 1

    write_task_doc(
        root,
        TaskDocument.model_validate(
            {
                "id": "L1",
                "slug": "01_leaf",
                "kind": "subTask",
                "repo": "repo",
                "title": "Authored",
                "createdAt": NOW.isoformat(),
                "status": "inProgress",
                "master": "task.md",
                "steps": [{"id": "S1", "title": "Work", "status": "pending"}],
            }
        ),
    )
    host = HostActivitySnapshot(
        known=True,
        agents=(
            TaskAgentActivity(
                child.reference.key, PublishedAgentActivity(state="permission", eventAt=NOW)
            ),
        ),
    )
    result = build_task_entries(read_task_sources(tmp_path, now=NOW), [], host)
    by_id = {task.id: task for task in result.tasks}
    child = by_id["L1"]
    assert len(result.tasks) == 2
    assert (child.stage, child.activity, child.stepsDone, child.stepsTotal) == (
        "started",
        "works",
        0,
        1,
    )
    assert by_id["MASTER"].counts.works == 1
    assert by_id["MASTER"].counts.planned == 0
    assert child.scope == "Read [the plan](https://example.org/plan)"
    assert child.summary is not None
    assert (
        not {"id", "title", "status", "stepsDone", "stepsTotal", "subTasks"}
        & child.summary.model_dump().keys()
    )


@pytest.mark.parametrize("parent_exists", [False, True])
def test_only_declared_reciprocal_membership_sets_parent_and_all_tasks_are_reachable(
    tmp_path: Path, parent_exists: bool
) -> None:
    def document(folder: str, slug: str, id: str, **fields) -> None:
        write_task_doc(
            tmp_path / "tasks" / "repo" / folder,
            TaskDocument.model_validate(
                {
                    "id": id,
                    "slug": slug,
                    "kind": "master" if slug == "task" else "subTask",
                    "repo": "repo",
                    "title": id,
                    "createdAt": NOW.isoformat(),
                    "status": "inProgress",
                    **fields,
                }
            ),
        )

    if parent_exists:
        document("sprint", "task", "SPRINT")
    document(
        "master",
        "task",
        "MASTER",
        master="../sprint/task.md",
        subTasks=[
            {"number": "L2", "name": "Second", "file": "02_leaf.md", "status": "planning"},
            {"number": "L1", "name": "First", "file": "01_leaf.md", "status": "Completed"},
        ],
    )
    document("master", "01_leaf", "L1", master="task.md", status="Completed")
    document("master", "02_leaf", "L2", master="task.md", status="planning")
    document("unlisted", "task", "UNLISTED", master="../master/task.md")
    result = build_task_entries(
        read_task_sources(tmp_path, now=NOW), [], HostActivitySnapshot(known=True)
    )
    tasks = {task.key: task for task in result.tasks}
    by_id = {task.id: task for task in result.tasks}
    assert by_id["MASTER"].parent is None
    assert by_id["UNLISTED"].parent is None
    assert by_id["MASTER"].children == [by_id["L2"].key, by_id["L1"].key]
    assert by_id["MASTER"].counts.planned == by_id["MASTER"].counts.completed == 1
    if parent_exists:
        assert by_id["SPRINT"].children == []
        assert by_id["SPRINT"].counts.started == 0
    reached = []

    def walk(task) -> None:
        reached.append(task)
        for ref in task.children:
            assert tasks[ref].parent == task.key
            walk(tasks[ref])

    for task in result.tasks:
        if task.parent is None:
            walk(task)
    assert len(reached) == len({task.key for task in reached}) == len(result.tasks)
    assert task_counts(reached) == result.counts


def test_a_record_naming_no_master_belongs_to_the_listing_master_and_disagreements_are_served(
    tmp_path: Path,
) -> None:
    def document(folder: str, slug: str, id: str, **fields) -> None:
        write_task_doc(
            tmp_path / "tasks" / "repo" / folder,
            TaskDocument.model_validate(
                {
                    "id": id,
                    "slug": slug,
                    "kind": "master" if slug == "task" else "subTask",
                    "repo": "repo",
                    "title": id,
                    "createdAt": NOW.isoformat(),
                    "status": "Completed",
                    **fields,
                }
            ),
        )

    def row(name: str, file: str, number: str | None = None) -> dict[str, str]:
        return {"number": number or name, "name": name, "file": file, "status": "Completed"}

    elsewhere = row("elsewhere", "03_elsewhere.md")
    rows = [row("old", "01_old.md"), row("named", "02_named.md"), elsewhere, elsewhere]
    rows += [row("absolute", "06_absolute.md"), row("escaping", "07_escaping.md")]
    old_twice = row("old", "../m/01_old.md")
    document("m", "task", "M", subTasks=rows)
    document(
        "other", "task", "OTHER", subTasks=[old_twice, old_twice, {**old_twice, "number": "o2"}]
    )
    document("m", "01_old", "old")
    document("m", "02_named", "named", master="task.md")
    document("m", "03_elsewhere", "elsewhere", master="../other/task.md")
    document("m", "04_stray", "stray")
    document("m", "05_unlisted", "unlisted", master="task.md")
    # A master that is present but resolves to no task is not an absent one (L168-R14-F02).
    document("m", "06_absolute", "absolute", master="/other/task.md")
    document("m", "07_escaping", "escaping", master="../../other/task.md")
    document("m", "08_unlisted_escaping", "unlisted-escaping", master="../../m/task.md")
    result = build_task_entries(read_task_sources(tmp_path, now=NOW), [], HostActivitySnapshot())
    by_id = {task.id: task for task in result.tasks}
    master = by_id["M"]
    assert master.children == [by_id["old"].key, by_id["named"].key]
    assert by_id["old"].parent == by_id["named"].parent == master.key
    assert by_id["elsewhere"].parent is by_id["stray"].parent is by_id["unlisted"].parent is None
    assert by_id["absolute"].parent is by_id["escaping"].parent is None
    assert master.counts.completed == 2

    def served(entry):
        return [
            (
                item.kind,
                item.row,
                item.record.key,
                item.recordMaster and item.recordMaster.key,
                item.masterLiteral,
                item.parent,
            )
            for item in entry.disagreements or []
        ]

    # A list that repeats a conflicting row reports the conflict once (L168-R14-F03); another
    # row of the same record is another dispute and stays.
    assert served(master) == [
        (
            "names-another-master",
            "elsewhere",
            "repo/m/03_elsewhere.json",
            "repo/other/task.json",
            None,
            None,
        ),
        (
            "unresolvable-master",
            "absolute",
            "repo/m/06_absolute.json",
            None,
            "/other/task.md",
            None,
        ),
        (
            "unresolvable-master",
            "escaping",
            "repo/m/07_escaping.json",
            None,
            "../../other/task.md",
            None,
        ),
        ("not-listed", None, "repo/m/04_stray.json", None, None, None),
        ("not-listed", None, "repo/m/05_unlisted.json", "repo/m/task.json", None, None),
        (
            "unresolvable-master",
            None,
            "repo/m/08_unlisted_escaping.json",
            None,
            "../../m/task.md",
            None,
        ),
    ]
    assert served(by_id["OTHER"]) == [
        ("listed-twice", "old", "repo/m/01_old.json", None, None, master.key),
        ("listed-twice", "o2", "repo/m/01_old.json", None, None, master.key),
    ]
    assert "disagreements" not in by_id["old"].model_dump(exclude_none=True)


def test_addressless_declared_row_has_one_opaque_identity_and_no_document(tmp_path: Path) -> None:
    root = tmp_path / "tasks" / "repo" / "master"
    write_task_doc(
        root,
        TaskDocument.model_validate(
            {
                "id": "MASTER",
                "slug": "task",
                "kind": "master",
                "repo": "repo",
                "title": "Master",
                "createdAt": NOW.isoformat(),
                "status": "inProgress",
                "subTasks": [
                    {"number": "L1", "name": "Only a plan", "file": "", "status": "planning"}
                ],
            }
        ),
    )
    result = build_task_entries(read_task_sources(tmp_path, now=NOW), [], HostActivitySnapshot())
    master, child = result.tasks
    assert len(result.tasks) == 2
    assert child.key == master.key + "#L1"
    assert child.reference is child.summary is None
    assert child.stage == "planned"
    assert child.parent == master.key
    assert master.children == [child.key]
    assert master.counts.planned == result.counts.planned == 1


@pytest.mark.parametrize("relation", ["two-parents", "cycle", "no-parent", "unlisted-parent"])
def test_conflicting_membership_is_deterministic_reciprocal_and_reachable(
    tmp_path: Path, relation: str
) -> None:
    for folder, target in (
        ("a", "b" if relation == "cycle" else "leaf"),
        ("b", "a" if relation == "cycle" else "leaf"),
    ):
        write_task_doc(
            tmp_path / "tasks" / "repo" / folder,
            TaskDocument.model_validate(
                {
                    "id": folder,
                    "slug": "task",
                    "kind": "master",
                    "repo": "repo",
                    "title": folder,
                    "createdAt": NOW.isoformat(),
                    "status": "inProgress",
                    "subTasks": [
                        {
                            "number": target,
                            "name": target,
                            "file": f"../{target}/task.md",
                            "status": "planning",
                        }
                    ],
                }
            ),
        )
    if relation != "cycle":
        write_task_doc(
            tmp_path / "tasks" / "repo" / "leaf",
            TaskDocument.model_validate(
                {
                    "id": "leaf",
                    "slug": "task",
                    "kind": "subTask",
                    "repo": "repo",
                    "title": "leaf",
                    "master": None
                    if relation == "no-parent"
                    else "../c/task.md"
                    if relation == "unlisted-parent"
                    else "../b/task.md",
                    "createdAt": NOW.isoformat(),
                    "status": "Completed",
                }
            ),
        )
    sources = read_task_sources(tmp_path, now=NOW)
    result = build_task_entries(sources, [], HostActivitySnapshot())
    reversed_result = build_task_entries(list(reversed(sources)), [], HostActivitySnapshot())
    assert {task.key: (task.parent, task.children) for task in result.tasks} == {
        task.key: (task.parent, task.children) for task in reversed_result.tasks
    }
    tasks = {task.key: task for task in result.tasks}
    reached = []
    pending = [task for task in result.tasks if task.parent is None]
    while pending:
        task = pending.pop()
        assert task.key not in reached
        reached.append(task.key)
        for key in task.children:
            assert tasks[key].parent == task.key
            pending.append(tasks[key])
        assert task.counts == task_counts(tasks[key] for key in task.children)
    assert set(reached) == tasks.keys()
    assert task_counts(tasks[key] for key in reached) == result.counts
    assert sum(len(task.children) for task in result.tasks) == (
        0 if relation == "unlisted-parent" else 1
    )
    by_id = {task.id: task for task in result.tasks}
    reported = {
        task.id: [(item.kind, item.parent) for item in task.disagreements or []]
        for task in result.tasks
        if task.disagreements
    }
    if relation == "two-parents":
        assert by_id["leaf"].parent == by_id["b"].key
        assert by_id["b"].children == [by_id["leaf"].key]
        assert reported == {"a": [("names-another-master", None)]}
    if relation == "no-parent":
        # Naming no master, the leaf belongs to the first list; the second list says so.
        assert by_id["leaf"].parent == by_id["a"].key
        assert reported == {"b": [("listed-twice", by_id["a"].key)]}
    if relation == "cycle":
        assert reported == {}
    if relation == "unlisted-parent":
        assert reported == {
            "a": [("names-another-master", None)],
            "b": [("names-another-master", None)],
        }
