"""Master memory resolution opens history that only its merge commit closes."""

from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

import pytest
from agents_remember.application.knowledge_gate.landing import landing_refusal
from agents_remember.application.knowledge_write_route import WriteSubmission, crossing_write_report
from agents_remember.application.knowledge_writer import Owner, WriteReport, WriteRequest
from agents_remember.kernel.memory_attribution import render_memory_content_message
from agents_remember.models.knowledge_files.documents import history_path
from agents_remember.tasks.document import SubTaskRef, TaskDocument
from agents_remember.tasks.store import write_task_doc
from agents_remember.worktrees import knowledge_crossing
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_location import (
    publish_new_lifecycle_operation_location,
)
from agents_remember.worktrees.knowledge_validation import PairedCode
from agents_remember.worktrees.services import LandingGateRequest
from agents_remember.worktrees.sync_transaction import _run_side
from agents_remember.worktrees.sync_transaction_authority import side_record
from agents_remember.worktrees.sync_transaction_git import (
    SyncKnowledgeValidationError,
    continue_side_merge,
    park_worktree_wip,
    path_fingerprint,
    start_side_merge,
)
from agents_remember.worktrees.sync_transaction_git import _merge as memory_merge
from agents_remember.worktrees.sync_transaction_state import SyncOperationRecord
from agents_remember.worktrees.worktree_contract import write_contract
from knowledge_writer_test_support import (
    CODE_FILE,
    LEAF_ID,
    TASK_ID,
    TEST_FILE,
    CrossingWorld,
    World,
    build_crossing_world,
    build_world,
    commit_all,
    git,
    read_json,
    write,
)
from writer_worklist_test_support import write_knowledge

pytestmark = pytest.mark.integration
CONFLICT = "notes.txt"


def _diverge(world: World, *, plain: bool = False, conflict: bool = True) -> CrossingWorld:
    if plain:
        git(world.memory, "rm", "knowledge/layout.json")
    write(world.memory, {CONFLICT: "common line\n"})
    commit_all(world.memory, "Common memory baseline")
    task_root = world.root / "tasks" / "agents-remember" / "landing"
    fixture = build_crossing_world(replace(world, task_root=task_root))
    (fixture.memory / history_path(fixture.owner)).unlink()
    write(fixture.memory, {CONFLICT: "own line\n"})
    commit_all(fixture.memory, "Own memory change")
    incoming_path = CONFLICT if conflict else "other.txt"
    write(world.memory, {incoming_path: "incoming line\n"})
    incoming = commit_all(
        world.memory,
        render_memory_content_message("Incoming memory change", fixture.record.code.resultHead),
    )
    side = side_record(fixture.contract, "memory", incoming)
    record = fixture.record.model_copy(update={"phase": "running-memory", "memory": side})
    fixture.store.write(record)
    return replace(fixture, record=record)


def _seed_dependent(world: World) -> tuple[str, str]:
    report = write_knowledge(
        WriteRequest(
            memory_root=world.memory,
            code_root=world.code,
            owner=Owner(task=TASK_ID, kind="leaf", id=LEAF_ID),
            handoff_path="dependent.json",
            document={
                "records": [
                    {
                        "key": "dependent",
                        "kind": "dependent",
                        "slug": "dependent",
                        "fields": {
                            "document": TEST_FILE,
                            "stands_on": [{"kind": "file", "path": CODE_FILE}],
                            "confirmed": {
                                "task": LEAF_ID,
                                "role": "curator",
                                "statement": "The test exercises the landing function.",
                            },
                        },
                    }
                ],
            },
            commit=True,
            worklist={"items": []},
        )
    )
    assert report.state == "written", report.render()
    (record,) = report.records
    commit_all(world.memory, "Seed the dependent")
    return record.id, record.path


def _confirm_dependent(fixture: CrossingWorld, dependent_id: str) -> object:
    return crossing_write_report(
        fixture.contract,
        fixture.owner,
        WriteSubmission(
            document={
                "records": [
                    {
                        "key": "dependent",
                        "kind": "dependent",
                        "id": dependent_id,
                        "fields": {
                            "confirmed": {
                                "task": fixture.contract.task_id,
                                "role": "curator",
                                "statement": "The merged test still exercises paired landing.",
                            }
                        },
                    }
                ],
                "history": [
                    {
                        "subject": dependent_id,
                        "disposition": "changed",
                        "reason": "Confirmed on the sync's paired code.",
                        "effect": "clarify",
                    }
                ],
            },
            handoff_path="resolution.json",
            authorization_ref="trial resolution",
            commit=True,
        ),
    )


def test_refused_continue_reopens_the_history_of_a_non_record_park(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = build_world(tmp_path)
    dependent_id, dependent_path = _seed_dependent(world)
    fixture = _diverge(world)
    record = _run_side(fixture.store, fixture.record, "memory")
    assert record.phase == "memory-resolution-required"
    assert record.memory is not None
    side = record.memory
    assert side.conflictFiles == (CONFLICT,)
    assert side.crossingHistoryOwner == fixture.owner
    path = history_path(fixture.owner)
    assert read_json(fixture.memory, path)["closed"] is False
    assert path in side.retainedTreeDigests
    before = (fixture.memory / path).read_bytes(), git(fixture.memory, "ls-files", "-s", path)

    write(fixture.memory, {CONFLICT: "Resolved line.\n"})
    sidecar = f"onboarding/{CODE_FILE}.json"
    stage_original = (fixture.memory / sidecar).read_bytes()
    write(fixture.memory, {sidecar: "{}\n"})
    git(fixture.memory, "add", "--", CONFLICT, sidecar)
    paired = PairedCode(world.code, record.code.resultHead)
    validate = memory_merge.memory_commit_refusal

    def observe_closed(
        *,
        memory_repository: Path,
        candidate_tree: str,
        bases: Sequence[str],
        paired_code: PairedCode | None,
    ) -> str | None:
        assert read_json(fixture.memory, path)["closed"] is True
        assert '"closed": true' in git(fixture.memory, "show", f":{path}")
        return validate(
            memory_repository=memory_repository,
            candidate_tree=candidate_tree,
            bases=bases,
            paired_code=paired_code,
        )

    with monkeypatch.context() as patch:
        patch.setattr(memory_merge, "memory_commit_refusal", observe_closed)
        with pytest.raises(SyncKnowledgeValidationError):
            continue_side_merge(side, paired_code=paired)
    assert before == (
        (fixture.memory / path).read_bytes(),
        git(fixture.memory, "ls-files", "-s", path),
    )
    write(fixture.memory, {sidecar: stage_original})
    git(fixture.memory, "add", "--", sidecar)

    report = _confirm_dependent(fixture, dependent_id)
    assert isinstance(report, WriteReport)
    assert report.state == "written", report.render()
    assert (
        read_json(fixture.memory, dependent_path)["confirmed"]["commit"] == record.code.resultHead
    )
    git(fixture.memory, "add", "--", path, dependent_path)
    head = continue_side_merge(side, paired_code=paired)
    assert read_json(fixture.memory, path)["closed"] is True
    assert (
        git(fixture.memory, "show", f"{head}:{path}") == (fixture.memory / path).read_text().strip()
    )
    assert git(fixture.memory, "rev-list", "--parents", "-n", "1", head).split() == [
        head,
        side.preSyncHead,
        side.sourceCommit,
    ]


class _InterruptedOpen(Exception):
    """Stop a task-local merge at the open-history write or stage seam."""


def _interrupt_history_open(
    fixture: CrossingWorld, path: str, pending: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    git_before_stage = knowledge_crossing._git

    def stop_before_write(*_args: object, **_kwargs: object) -> str:
        raise _InterruptedOpen

    def stop_before_stage(worktree: Path, args: list[str], *, input_text: str | None = None) -> str:
        if worktree == fixture.memory and args == ["add", "--", path]:
            raise _InterruptedOpen
        return git_before_stage(worktree, args, input_text=input_text)

    with monkeypatch.context() as patch:
        if pending == "before-write":
            patch.setattr(memory_merge, "open_crossing_history", stop_before_write)
        else:
            patch.setattr(knowledge_crossing, "_git", stop_before_stage)
        with pytest.raises(_InterruptedOpen):
            _run_side(fixture.store, fixture.record, "memory")


@pytest.mark.parametrize("pending", ["before-write", "before-stage", "owner-edit"])
def test_resume_completes_only_the_exact_interrupted_open(
    tmp_path: Path, pending: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = build_world(tmp_path)
    dependent_id, dependent_path = _seed_dependent(world)
    fixture = _diverge(world)
    path = history_path(fixture.owner)
    target = fixture.memory / path
    _interrupt_history_open(fixture, path, pending, monkeypatch)

    persisted = fixture.store.read()
    assert isinstance(persisted, SyncOperationRecord) and persisted.phase == "running-memory"
    assert persisted.memory is not None
    parents = (persisted.memory.preSyncHead, persisted.memory.sourceCommit)
    assert git(fixture.memory, "rev-parse", "HEAD", "MERGE_HEAD").splitlines() == list(parents)
    assert target.is_file() is (pending != "before-write")
    assert not git(fixture.memory, "ls-files", "--stage", "--", path)
    owner_bytes = b'{"owner edit":true}\n'
    if pending == "owner-edit":
        target.write_bytes(owner_bytes)

    resumed = _run_side(fixture.store, persisted, "memory")
    assert resumed.phase == "memory-resolution-required"
    assert resumed.memory is not None
    side = resumed.memory
    if pending == "owner-edit":
        assert not side.crossingHistoryOwner
        assert not side.retainedTreeDigests
        assert target.read_bytes() == owner_bytes
        assert not git(fixture.memory, "ls-files", "--stage", "--", path)
        return

    assert side.crossingHistoryOwner == fixture.owner
    assert side.retainedTreeDigests[path] == path_fingerprint(side, path)
    assert read_json(fixture.memory, path) == {
        "schema": "ar-history/v1",
        "crossing": fixture.owner,
        "closed": False,
        "rows": [],
    }
    assert git(fixture.memory, "show", f":{path}") == target.read_text().strip()
    assert git(fixture.memory, "ls-files", "--stage", "--", path).split()[2] == "0"
    report = _confirm_dependent(fixture, dependent_id)
    assert isinstance(report, WriteReport)
    assert report.state == "written", report.render()
    assert (
        read_json(fixture.memory, dependent_path)["confirmed"]["commit"] == resumed.code.resultHead
    )
    write(fixture.memory, {CONFLICT: "Resolved line.\n"})
    git(fixture.memory, "add", "--", CONFLICT, path, dependent_path)
    head = continue_side_merge(side, paired_code=PairedCode(world.code, resumed.code.resultHead))
    assert read_json(fixture.memory, path)["closed"] is True
    assert git(fixture.memory, "show", f"{head}:{path}") == target.read_text().strip()
    assert git(fixture.memory, "rev-list", "--parents", "-n", "1", head).split() == [head, *parents]


def test_replay_recovers_owner_and_empty_history_closes_for_landing(tmp_path: Path) -> None:
    fixture = _diverge(build_world(tmp_path))
    side = fixture.record.memory
    assert side is not None
    paired = PairedCode(Path(fixture.record.code.repository), fixture.record.code.resultHead)
    started = start_side_merge(
        side, paired_code=paired, crossing_owner=("master", fixture.contract.task_id)
    )
    assert started.state == "resolution-required"
    assert started.crossing_history_owner == fixture.owner
    # The merge was applied but the running operation had yet to persist its park.
    resumed = _run_side(fixture.store, fixture.record, "memory")
    assert resumed.memory is not None
    assert resumed.memory.crossingHistoryOwner == fixture.owner
    path = history_path(fixture.owner)
    assert path in resumed.memory.retainedTreeDigests
    assert read_json(fixture.memory, path)["rows"] == []
    write(fixture.memory, {CONFLICT: "Resolved line.\n"})
    git(fixture.memory, "add", "--", CONFLICT)
    head = continue_side_merge(resumed.memory, paired_code=paired)
    assert read_json(fixture.memory, path) == {
        "schema": "ar-history/v1",
        "crossing": fixture.owner,
        "closed": True,
        "rows": [],
    }
    series = replace(fixture.contract, sync_log=({"memoryHeadTo": head},))
    write_contract(series.contract_path, series)
    write_task_doc(
        series.task_root,
        TaskDocument(
            id=series.task_id,
            slug="landing",
            title="Landing",
            kind="master",
            repo=series.repo_name,
            createdAt="2026-01-01T00:00:00Z",
            executionNature="atomic",
            subTasks=[SubTaskRef(number="unused", name="Unused", status="abandoned")],
        ),
    )
    publish_new_lifecycle_operation_location(
        series, contract_text=series.contract_path.read_text(encoding="utf-8")
    )
    request = LandingGateRequest(
        memory_repository=Path(side.repository),
        memory_commit=head,
        memory_bases=(side.preSyncHead, side.sourceCommit),
        code_repository=Path(fixture.record.code.repository),
        code_commit=fixture.record.code.resultHead,
        series_contract=str(series.contract_path),
    )
    assert landing_refusal(request) is None


@pytest.mark.parametrize("plain", [True, False], ids=["plain-park", "clean-structural-merge"])
def test_history_is_opened_only_for_a_structural_merge_park(tmp_path: Path, plain: bool) -> None:
    fixture = _diverge(build_world(tmp_path), plain=plain, conflict=plain)
    record = _run_side(fixture.store, fixture.record, "memory")
    assert record.phase == ("memory-resolution-required" if plain else "finalizing")
    assert record.memory is not None
    assert not record.memory.crossingHistoryOwner
    assert not (fixture.memory / history_path(fixture.owner)).exists()


def test_post_commit_memory_restore_conflict_has_no_crossing_history(tmp_path: Path) -> None:
    fixture = _diverge(build_world(tmp_path), conflict=False)
    side = fixture.record.memory
    assert side is not None
    write(fixture.memory, {"other.txt": "Parked bytes for the incoming path.\n"})
    git(fixture.memory, "add", "--", "other.txt")
    stash = park_worktree_wip(side, message="Parked memory")
    parked = side.model_copy(update={"wipState": "parked", "wipStash": stash})
    record = fixture.record.model_copy(update={"memory": parked})
    fixture.store.write(record)

    resolved = _run_side(fixture.store, record, "memory")

    assert resolved.phase == "memory-resolution-required"
    assert resolved.memory is not None
    assert resolved.memory.wipState == "restore-conflict"
    assert resolved.memory.resultHead == git(fixture.memory, "rev-parse", "HEAD")
    assert not resolved.memory.crossingHistoryOwner
    assert not (fixture.memory / history_path(fixture.owner)).exists()
