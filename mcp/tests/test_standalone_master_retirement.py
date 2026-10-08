"""Finalization never archives a master; retirement archives one with or without a sprint.

Covers the three finalization outcomes (sprint-commanded master, master no sprint commands,
ordinary standalone task), the retirement of a master that no sprint commands (dry run, real run,
partial hook failure and the same-request retry), the retry refusals for a changed request or
namespace, the archive hook's receipts and its failure handling.
"""

from __future__ import annotations

import contextlib
import json
import sqlite3
from pathlib import Path
from typing import Any, ClassVar, cast
from unittest.mock import patch

import pytest
from agents_remember.application.task_docs import task_master_retirement as retirement
from agents_remember.application.task_docs import task_retirement_shared as retirement_shared
from agents_remember.application.task_docs.task_doc_tools import (
    TaskDocCall,
    TaskDocEdit,
    TaskDocTarget,
)
from agents_remember.mcp.tools.task_doc import task_doc_payload
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.models.task_retirement import MasterRetirementProof
from agents_remember.tasks import TaskDocument, read_task_doc, write_task_doc
from agents_remember.worktrees.integration import terminal_enclosure_evidence as evidence
from agents_remember.worktrees.modules.finalize import FinalizeArgs, finalize_result
from agents_remember.worktrees.task_resolver import archive_completed_root_task
from agents_remember.worktrees.worktree_contract import WorktreeContract, load_contract
from test_master_retirement import World
from test_task_execution_topology import REPOSITORY, _master
from test_worktree_support import git

pytestmark = pytest.mark.integration

REASON = "This lone master is deliberately retired."


@pytest.fixture
def world(tmp_path):
    return World(tmp_path)


def lone(world: World, name: str = "outside") -> tuple[WorktreeContract, TaskDocumentRef]:
    """A completed master that no sprint commands, with its series contract."""
    contract = world.series(name)
    write_task_doc(
        contract.task_root,
        _master(identity=name, execution_nature="atomic").model_copy(
            update={"status": "Completed"}
        ),
    )
    return contract, TaskDocumentRef(repository=REPOSITORY, path=f"{name}/task.json")


def retire_lone(
    world: World, name: str, ref: TaskDocumentRef, *, dry_run: bool = False, **fields: Any
) -> dict[str, Any]:
    return task_doc_payload(
        world.cfg,
        TaskDocTarget(repo_id=REPOSITORY, task_name=name),
        operation="retire_master",
        edit=TaskDocEdit(fields={"masterRef": ref.model_dump(), "reason": REASON, **fields}),
        call=TaskDocCall(dry_run=dry_run),
    )


def archive_state(result: Any) -> dict[str, Any]:
    return cast("dict[str, Any]", result.payload["taskArchive"])


# -- finalization: three cases -----------------------------------------------------------------


def test_finalizing_a_sprint_commanded_master_completes_its_row_and_names_the_sprint(world):
    contract = world.series()
    result = finalize_result(FinalizeArgs(contract_path=contract.contract_path))
    assert result.returncode == 0 and result.payload["state"] == "finalized"
    assert world.sprint().subTasks[0].status == "Completed"
    archive = archive_state(result)
    assert archive["state"] == "skipped" and archive["reason"] == "sprint-commands-master"
    assert archive["sprintTaskDocumentRef"]["path"] == "sprint/task.json"
    assert "sprint agents-remember/sprint/task.json commands this master" in archive["detail"]
    assert "task_doc.retire_master" in archive["detail"]
    assert contract.task_root.is_dir() and len(world.validate()) == 3


def test_finalizing_a_master_no_sprint_commands_keeps_it_and_names_the_retire_route(world):
    contract, _ = lone(world)
    refs = git(world.code, "show-ref")
    result = finalize_result(FinalizeArgs(contract_path=contract.contract_path))
    assert result.returncode == 0 and result.payload["state"] == "finalized"
    archive = archive_state(result)
    assert archive["state"] == "skipped" and archive["reason"] == "master-archived-only-by-retire"
    assert "only by task_doc.retire_master" in archive["detail"]
    assert "reviewArtifacts" not in archive and "archivePath" not in archive
    assert contract.task_root.is_dir() and not (world.tasks / "0_archive").exists()
    assert git(world.code, "show-ref") == refs


def test_finalizing_an_ordinary_standalone_task_keeps_todays_skip(world):
    contract = world.series("plain")
    write_task_doc(
        contract.task_root,
        TaskDocument(
            id="PLAIN",
            slug="plain",
            title="Plain",
            kind="subTask",
            repo=REPOSITORY,
            createdAt="2026-10-02T00:00:00+00:00",
        ),
    )
    result = finalize_result(FinalizeArgs(contract_path=contract.contract_path))
    assert result.returncode == 0 and result.payload["state"] == "finalized"
    assert archive_state(result) == {
        "state": "skipped",
        "reason": "root-series-still-active",
        "taskRoot": contract.task_root.as_posix(),
    }
    assert cast("dict[str, Any]", result.payload["taskUpdates"])["leaf"]["state"] == "skipped"
    assert contract.task_root.is_dir()


def test_the_archive_step_itself_never_archives_a_task_holding_a_series_contract(world):
    contract, _ = lone(world)
    outcome = archive_completed_root_task(
        world.coord, REPOSITORY, contract.task_root, dry_run=False
    )
    assert outcome["state"] == "skipped" and contract.task_root.is_dir()


# -- retirement of a master that no sprint commands --------------------------------------------


def test_lone_master_dry_run_and_real_run_edit_no_sprint(world):
    contract, ref = lone(world)
    before = world.snapshot()
    refs = git(world.code, "show-ref")
    preview = retire_lone(world, "outside", ref, dry_run=True)
    assert preview["ok"] and preview["state"] == "would-retire"
    assert preview["taskArchive"]["state"] == "would-archive"
    assert preview["removedOrchestrates"] == [] and preview["removedEdges"] == []
    assert world.snapshot() == before
    result = retire_lone(world, "outside", ref)
    assert result["ok"] and result["state"] == "retired" and not result["retirementResumed"]
    archive = world.tasks / "0_archive" / "outside"
    assert result["taskArchive"]["archivePath"] == archive.as_posix()
    assert not contract.task_root.exists() and (archive / "task.json").is_file()
    proof = MasterRetirementProof.model_validate_json(
        (archive / "notes/reports/master-retirement.json").read_bytes()
    )
    assert proof.reason == REASON and proof.masterRef == ref and proof.removedEdges == []
    assert result["taskId"] == "outside" and result["retirementProof"]["reason"] == REASON
    sprint_files = {k: v for k, v in before.items() if k.startswith("sprint/")}
    assert {k: v for k, v in world.snapshot().items() if k.startswith("sprint/")} == sprint_files
    assert len(world.validate()) == 3 and git(world.code, "show-ref") == refs


def test_lone_master_refuses_what_a_sprint_or_its_arguments_make_wrong(world):
    contract, ref = lone(world)
    member = world.refs[1]
    before = world.snapshot()
    with pytest.raises(ValueError, match=r"commanded by sprint.*sprint/task.json.*on that sprint"):
        retire_lone(world, "master-b", member)
    with pytest.raises(ValueError, match=r"outside/task.json, but master.*master-b.*sprint/task"):
        retire_lone(world, "outside", member)
    edge = {"predecessor": ref.model_dump(), "successor": member.model_dump()}
    with pytest.raises(ValueError, match=r"commanded by no sprint.*without removeEdges"):
        retire_lone(world, "outside", ref, removeEdges=[edge])
    assert world.snapshot() == before and contract.task_root.is_dir()


def test_lone_master_with_open_work_refuses_without_writes_and_a_pending_cleanup_does_not(world):
    from dataclasses import replace  # noqa: PLC0415

    from agents_remember.worktrees.worktree_contract import write_contract  # noqa: PLC0415

    contract, ref = lone(world)
    write_contract(contract.contract_path, replace(contract, cleanup="pending"))
    assert retire_lone(world, "outside", ref, dry_run=True)["state"] == "would-retire"
    leaf_worktree = world.coord.parent / "leaf-worktree"
    leaf_worktree.mkdir()
    leaf = replace(
        contract,
        kind="leaf",
        leaf_id="L1",
        contract_path=contract.task_root / "enclosures" / "l1" / "series-contract.md",
        parent_contract_path=contract.contract_path,
        code_source_branch=contract.code_work_branch,
        code_work_branch="leaf-l1",
        code_worktree=leaf_worktree,
    )
    write_contract(leaf.contract_path, leaf)
    before = world.snapshot()
    with pytest.raises(ValueError, match=r"has open work.*worktree directory .*leaf-worktree"):
        retire_lone(world, "outside", ref)
    assert world.snapshot() == before
    leaf_worktree.rmdir()
    assert retire_lone(world, "outside", ref)["state"] == "retired"


def test_lone_master_source_drift_before_publication_refuses_before_any_hook(world):
    contract, ref = lone(world)
    original = retirement._admit

    def drift(request, payload):
        prepared = original(request, payload)
        page = contract.task_root / "task.md"
        page.write_text(page.read_text() + "new generation\n")
        return prepared

    with (
        patch.object(retirement, "_admit", side_effect=drift),
        patch.object(retirement_shared, "cleanup_review_artifacts") as cleanup,
        pytest.raises(ValueError, match=r"changed before publication"),
    ):
        retire_lone(world, "outside", ref)
    cleanup.assert_not_called()
    assert contract.task_root.is_dir() and not (world.tasks / "0_archive").exists()
    assert not (contract.task_root / "notes/reports/master-retirement.json").exists()


def test_lone_master_failed_move_restores_folder_and_removes_its_proof(world):
    contract, ref = lone(world)
    before = world.snapshot()
    with (
        patch.object(Path, "rename", side_effect=OSError("disk refused rename")),
        pytest.raises(ValueError, match=r"task folder and proof were restored"),
    ):
        retire_lone(world, "outside", ref)
    assert world.snapshot() == before and not (world.tasks / "0_archive").exists()
    assert not (contract.task_root / "notes/reports/master-retirement.json").exists()


def test_lone_master_interrupted_move_resumes_only_for_the_same_request(world):
    contract, ref = lone(world)
    with (
        patch.object(Path, "rename", side_effect=KeyboardInterrupt),
        pytest.raises(KeyboardInterrupt),
    ):
        retire_lone(world, "outside", ref)
    proof = contract.task_root / "notes/reports/master-retirement.json"
    kept = proof.read_bytes()
    with pytest.raises(ValueError, match=r"differs from its retained proof"):
        retire_lone(world, "outside", ref, reason="another reason")
    assert contract.task_root.is_dir() and proof.read_bytes() == kept
    result = retire_lone(world, "outside", ref)
    assert result["ok"] and result["retirementResumed"] and not contract.task_root.exists()
    assert (
        world.tasks / "0_archive/outside/notes/reports/master-retirement.json"
    ).read_bytes() == kept


def _datasets(folder: Path) -> tuple[Path, Path]:
    notes = folder / "notes"
    notes.mkdir(exist_ok=True)
    for name in ("a.sqlite", "b.sqlite"):
        connection = sqlite3.connect(notes / name)
        connection.executescript(
            "CREATE TABLE invariant (id TEXT); CREATE TABLE invariant_revision (id TEXT)"
        )
        connection.close()
    archive = folder.parent / "0_archive" / folder.name / "notes"
    return archive / "a.sqlite", archive / "b.sqlite"


def test_lone_master_partial_hook_failure_keeps_the_retirement_and_retries_cleanup_only(world):
    contract, ref = lone(world)
    deleted, failed = _datasets(contract.task_root)
    unlink = Path.unlink
    fail = True

    def partial_delete(path, *args, **kwargs):
        if path == failed and fail:
            raise OSError("dataset is busy")
        return unlink(path, *args, **kwargs)

    archive = world.tasks / "0_archive" / "outside"
    with (
        patch.object(Path, "unlink", autospec=True, side_effect=partial_delete),
        patch.object(Path, "rename", autospec=True, side_effect=Path.rename) as move,
    ):
        result = retire_lone(world, "outside", ref)
        assert not result["ok"] and result["state"] == "retired-with-hook-failures"
        assert "same masterRef and reason" in result["nextAction"]
        assert result["taskArchive"]["state"] == "archived" and move.call_count == 1
        report = result["taskArchive"]["reviewArtifacts"]
        assert [e["path"] for e in report["datasetCopies"]] == [deleted.as_posix()]
        assert report["failures"][0]["target"] == failed.as_posix()
        proof = (archive / "notes/reports/master-retirement.json").read_bytes()
        first_receipt = (archive / "notes/reports/review-artifact-cleanup.json").read_bytes()
        changes: list[dict[str, Any]] = [
            {"reason": "another"},
            {"masterRef": world.refs[1].model_dump()},
        ]
        for changed in changes:
            with pytest.raises(ValueError):
                retire_lone(world, "outside", ref, **changed)
        retry = retire_lone(world, "outside", ref)
        assert not retry["ok"] and retry["retirementResumed"] and move.call_count == 1
        assert [e["path"] for e in retry["taskArchive"]["reviewArtifacts"]["datasetCopies"]] == []
        absent = retry["taskArchive"]["reviewArtifacts"]["alreadyAbsent"]
        assert [(e["artifact"], e["deletedInAttempt"]) for e in absent] == [(deleted.as_posix(), 1)]
        fail = False
        final = retire_lone(world, "outside", ref)
    assert final["ok"] and final["state"] == "retired" and final["retirementResumed"]
    assert [e["path"] for e in final["taskArchive"]["reviewArtifacts"]["datasetCopies"]] == [
        failed.as_posix()
    ]
    assert not deleted.exists() and not failed.exists() and move.call_count == 1
    assert (archive / "notes/reports/master-retirement.json").read_bytes() == proof
    reports = archive / "notes/reports"
    assert (reports / "review-artifact-cleanup.attempt-1.json").read_bytes() == first_receipt
    assert (reports / "review-artifact-cleanup.attempt-2.json").is_file()
    assert json.loads((reports / "review-artifact-cleanup.json").read_text())["attempt"] == 3


# -- retry refusals on an already archived sprint retirement -----------------------------------


def test_retry_with_other_edge_arguments_is_refused_without_changes(world):
    world.retire()
    before = world.snapshot()
    edge = world.edges[1]
    other = {**world.fields, "removeEdges": [{k: edge[k] for k in ("predecessor", "successor")}]}
    for dry_run in (True, False):
        with pytest.raises(ValueError, match=r"differs from its retained proof"):
            world.retire(dry_run=dry_run, fields=other)
        assert world.snapshot() == before


def test_retry_with_a_mismatched_namespace_is_refused_without_changes(world):
    world.retire()
    sprint_json = world.tasks / "sprint" / "task.json"
    stored = json.loads(sprint_json.read_text())
    stored["subTasks"][0]["retirement"]["archiveRef"]["path"] = "0_archive/elsewhere/task.json"
    sprint_json.write_text(json.dumps(stored))
    before = world.snapshot()
    with pytest.raises(ValueError, match=r"differs from its retained proof"):
        world.retire()
    foreign = {
        **world.fields,
        "masterRef": {"repository": "other-repo", "path": "master-a/task.json"},
    }
    with pytest.raises(ValueError, match=r"outside the repository"):
        world.retire(fields=foreign)
    assert world.snapshot() == before
    assert (world.tasks / "0_archive" / "master-a" / "task.json").is_file()


# -- the archive hook: failure handling and receipts -------------------------------------------


@pytest.mark.parametrize("error", [KeyError("gone"), AttributeError("no port"), TypeError("bad")])
def test_any_hook_exception_ends_as_a_reported_failure_and_the_same_request_recovers(world, error):
    contract, ref = lone(world)
    with patch.object(retirement_shared, "cleanup_review_artifacts", side_effect=error):
        result = retire_lone(world, "outside", ref)
        sprint_result = world.retire()
    for answer in (result, sprint_result):
        assert not answer["ok"] and answer["state"] == "retired-with-hook-failures"
        assert answer["taskArchive"]["state"] == "archived"
        assert type(error).__name__ in answer["taskArchive"]["reviewArtifacts"]["detail"]
    assert not contract.task_root.exists() and len(world.validate()) == 2
    assert retire_lone(world, "outside", ref)["ok"] and world.retire()["ok"]


def test_a_repeated_completed_request_writes_no_new_receipt(world):
    world.retire()
    reports = world.tasks / "0_archive/master-a/notes/reports"
    first = (reports / "review-artifact-cleanup.json").read_bytes()
    for _ in range(2):
        again = world.retire()
        assert again["ok"] and again["taskArchive"]["reviewArtifacts"]["receipt"] == "unchanged"
    assert (reports / "review-artifact-cleanup.json").read_bytes() == first
    assert not list(reports.glob("review-artifact-cleanup.attempt-*"))


def test_a_retired_lone_master_is_still_readable_where_it_was_archived(world):
    _, ref = lone(world)
    retire_lone(world, "outside", ref)
    archived = read_task_doc(world.tasks / "0_archive/outside/task.json")
    assert (
        archived.id == "outside"
        and load_contract(world.tasks / "0_archive/outside/series-contract.md").cleanup
        == "completed"
    )


def test_finalize_refuses_a_broken_sprint_linkage_and_changes_nothing(world):
    """Same as a leaf with a broken master link: refused before cleanup, in a dry run too.

    Two typed rows for one master contradict each other. A sprint without a typed row for the
    master is no broken linkage; ``test_untyped_sprint_rows`` covers that it finalizes.
    """
    contract = world.series()
    data = world.sprint().model_dump(mode="json")
    data["subTasks"].append({**data["subTasks"][0], "number": "9"})
    write_task_doc(world.tasks / "sprint", TaskDocument.model_validate(data))
    before = world.snapshot()
    contract_bytes = contract.contract_path.read_bytes()
    for dry_run in (True, False):
        result = finalize_result(
            FinalizeArgs(contract_path=contract.contract_path, dry_run=dry_run)
        )
        assert result.payload["state"] == "task-document-resolution-blocked"
        blockers = " ".join(cast("list[str]", result.payload["blockers"]))
        assert "task-sprint-linkage-row-duplicate" in blockers
        assert "repair sprint linkage" in blockers and "attach_master" not in blockers
        assert world.snapshot() == before
        assert contract.contract_path.read_bytes() == contract_bytes


def test_organizational_master_no_sprint_commands_finalizes_and_keeps_its_folder(world):
    """The base finalized it; ``topology.parent`` would refuse it as parentless."""
    contract = world.series("org")
    write_task_doc(
        contract.task_root,
        _master(identity="org", execution_nature="organizational").model_copy(
            update={"status": "Completed"}
        ),
    )
    result = finalize_result(FinalizeArgs(contract_path=contract.contract_path))
    assert result.returncode == 0 and result.payload["state"] == "finalized"
    assert archive_state(result)["reason"] == "master-archived-only-by-retire"
    assert (
        contract.task_root.is_dir()
        and read_task_doc(contract.task_root / "task.json").status == "Completed"
    )


def test_finalize_refuses_an_unresolvable_sprint_member_and_changes_nothing(world):
    contract = world.series()
    data = world.sprint().model_dump(mode="json")
    data["orchestrates"].append("ghost-master")
    write_task_doc(world.tasks / "sprint", TaskDocument.model_validate(data))
    before = world.snapshot()
    for dry_run in (True, False):
        result = finalize_result(
            FinalizeArgs(contract_path=contract.contract_path, dry_run=dry_run)
        )
        assert result.payload["state"] == "task-document-resolution-blocked"
        blockers = " ".join(cast("list[str]", result.payload["blockers"]))
        assert "ghost-master" in blockers and "repair sprint linkage" in blockers
        assert world.snapshot() == before


def test_evidence_change_between_admission_and_publication_refuses_on_the_sprint_route(world):
    sprint_route = retirement

    contract = world.series()
    original = sprint_route._admit

    def drift(request, payload):
        prepared = original(request, payload)
        contract.contract_path.write_text(contract.contract_path.read_text() + "\n<!-- drift -->\n")
        return prepared

    before = {k: v for k, v in world.snapshot().items() if k.startswith("sprint/")}
    with (
        patch.object(sprint_route, "_admit", side_effect=drift),
        patch.object(retirement_shared, "cleanup_review_artifacts") as cleanup,
        pytest.raises(ValueError, match=r"enclosure/operation source changed before publication"),
    ):
        world.retire()
    cleanup.assert_not_called()
    assert {k: v for k, v in world.snapshot().items() if k.startswith("sprint/")} == before
    assert contract.task_root.is_dir() and not (world.tasks / "0_archive").exists()


@pytest.mark.parametrize("error", [RuntimeError("boom"), KeyError("boom")])
def test_any_exception_before_archival_ends_restored_on_both_routes(world, error):
    contract, ref = lone(world)
    before = world.snapshot()
    with (
        patch.object(Path, "rename", side_effect=error),
        pytest.raises(ValueError, match=r"before archival"),
    ):
        world.retire()
    assert world.snapshot() == before and len(world.validate()) == 3
    with (
        patch.object(Path, "rename", side_effect=error),
        pytest.raises(ValueError, match=r"before archival"),
    ):
        retire_lone(world, "outside", ref)
    assert world.snapshot() == before and contract.task_root.is_dir()


def test_an_old_build_is_told_to_restart_and_a_new_one_says_so_in_every_answer(world):
    from agents_remember.tasks import serving_preflight  # noqa: PLC0415

    class Predating:
        model_fields: ClassVar[dict[str, object]] = {}

    with (
        patch.object(serving_preflight, "SubTaskRef", Predating),
        pytest.raises(ValueError, match=r"restart required.*predates master retirement"),
    ):
        world.retire()
    assert len(world.validate()) == 3
    result = world.retire()
    assert "Restart required" in result["detail"] and "dashboard" in result["detail"]


def test_a_record_that_does_not_match_its_canonical_file_name_is_refused(world):
    from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_location_errors import (  # noqa: PLC0415
        LifecycleOperationLocationError,
    )
    from agents_remember.worktrees.integration.terminal_enclosure_evidence import (  # noqa: PLC0415
        terminal_operation_evidence,
    )
    from test_master_retirement import _historical_operation  # noqa: PLC0415

    contract = world.series()
    record = _historical_operation(contract, "failed")  # a closeout record
    lifecycle = contract.worktree_group / ".lifecycle"
    lifecycle.mkdir(parents=True)
    (lifecycle / "integrate-operation.json").write_text(record.model_dump_json())
    with pytest.raises(LifecycleOperationLocationError, match=r"integrate-operation.json"):
        terminal_operation_evidence(contract.worktree_group)
    before = world.snapshot()
    with pytest.raises(ValueError, match=r"integrate-operation.json"):
        world.retire()
    assert world.snapshot() == before


def _generic(world, operation, **edit):
    return task_doc_payload(
        world.cfg, world.target, operation=operation, edit=TaskDocEdit(**edit), call=TaskDocCall()
    )


def test_every_generic_route_to_a_retired_row_refuses_naming_the_row(world):
    """Routes checked: replace, set_subtask, remove_subtask (plain, keep_file, discard-unstarted),
    set_field, create of a leaf whose id is the row number (master sync), and the sprint linkage
    operations (which must leave the row alone)."""
    world.retire()
    row = world.sprint().subTasks[0]
    assert row.retirement is not None and row.number == "0"
    before = world.snapshot()
    pattern = r"row '0'.*records a master retirement.*only by task_doc.retire_master"
    data = world.sprint().model_dump(mode="json")
    data["subTasks"][0]["retirement"]["reason"] = "forged"
    attempts: list[tuple[str, dict[str, Any]]] = [
        ("replace", {"fields": data}),
        ("set_subtask", {"subtask": {"number": "0", "name": "Renamed", "status": "abandoned"}}),
        ("set_subtask", {"subtask": {"number": "0", "status": "Completed"}}),
        ("set_subtask", {"subtask": {"number": "0", "file": "zero.md"}}),
        ("set_subtask", {"subtask": {"number": "0", "scope": "another scope"}}),
        ("remove_subtask", {"subtask": {"number": "0"}}),
        ("remove_subtask", {"subtask": {"number": "0", "keep_file": True}}),
        (
            "remove_subtask",
            {"subtask": {"number": "0", "disposition": "discard-unstarted", "reason": "x"}},
        ),
    ]
    for operation, edit in attempts:
        with pytest.raises(ValueError, match=pattern):
            _generic(world, operation, **edit)
        assert world.snapshot() == before, operation
    # set_field cannot reach the rows at all: the field is refused or ignored, never applied.
    with contextlib.suppress(ValueError):
        _generic(world, "set_field", fields={"subTasks": []})
    assert world.sprint().subTasks[0].retirement == row.retirement
    # a leaf document whose id is the retired row's number would overwrite it through master sync
    leaf = TaskDocument(
        id="0",
        slug="zero",
        title="Zero",
        kind="subTask",
        repo=REPOSITORY,
        createdAt="2026-10-04T00:00:00+00:00",
    )
    with pytest.raises(ValueError, match=pattern):
        task_doc_payload(
            world.cfg,
            TaskDocTarget(repo_id=REPOSITORY, task_name="sprint", slug="zero"),
            operation="create",
            edit=TaskDocEdit(fields=leaf.model_dump(mode="json", by_alias=True)),
            call=TaskDocCall(),
        )
    assert world.sprint().subTasks[0].retirement == row.retirement
    # the linkage operations leave the retirement row in place
    task_doc_payload(
        world.cfg,
        world.target,
        operation="detach_master",
        edit=TaskDocEdit(fields={"masterRef": world.refs[2].model_dump()}),
        call=TaskDocCall(),
    )
    assert world.sprint().subTasks[0].retirement == row.retirement


def test_every_successful_retire_answer_carries_the_restart_notice_and_refusals_none(world):
    _, ref = lone(world)
    answers = [
        world.retire(dry_run=True),
        world.retire(),
        world.retire(),
        world.retire(dry_run=True),
        retire_lone(world, "outside", ref, dry_run=True),
        retire_lone(world, "outside", ref),
        retire_lone(world, "outside", ref),
    ]
    for answer in answers:
        assert answer["ok"] and "Restart required" in answer["detail"]
    with patch.object(retirement_shared, "cleanup_review_artifacts", side_effect=KeyError("x")):
        failed = world.retire(fields={**world.fields, "masterRef": world.refs[1].model_dump()})
    assert not failed["ok"] and "Restart required" in failed["detail"]
    with pytest.raises(ValueError) as recorded:
        world.retire(fields={**world.fields, "reason": "different"})
    assert "differs from its retained proof" in str(recorded.value)
    assert "Restart required" in str(recorded.value)


def test_a_series_task_with_no_task_document_keeps_todays_skip(world):
    contract = world.series("nodoc")
    result = finalize_result(FinalizeArgs(contract_path=contract.contract_path))
    assert result.returncode == 0
    assert archive_state(result)["reason"] == "root-series-still-active"
    assert "detail" not in archive_state(result)


def test_a_refusal_before_anything_is_recorded_carries_no_restart_notice(world):
    before = world.snapshot()
    with pytest.raises(ValueError) as refused:
        world.retire(
            fields={**world.fields, "removeEdges": [{"predecessor": "x", "successor": "y"}]}
        )
    assert "Restart required" not in str(refused.value)
    assert world.snapshot() == before


def test_an_unreadable_master_document_refuses_finalize_and_names_the_file(world):
    contract = world.series("broken")
    (contract.task_root / "task.json").write_text("{broken")
    before = {p: p.read_bytes() for p in contract.task_root.glob("task.*")}
    result = finalize_result(FinalizeArgs(contract_path=contract.contract_path))
    assert result.payload["state"] == "task-document-resolution-blocked"
    assert str(contract.task_root / "task.json") in " ".join(
        cast("list[str]", result.payload["blockers"])
    )
    assert {p: p.read_bytes() for p in contract.task_root.glob("task.*")} == before


def test_finalize_and_reopen_refuse_a_leaf_whose_id_is_a_retired_rows_number(world):
    from agents_remember.worktrees.modules.finalize_task_documents import (  # noqa: PLC0415
        FinalizeTaskDocumentError,
        _parent_completion_candidate,
    )

    world.retire()
    sprint = world.sprint()
    with pytest.raises(FinalizeTaskDocumentError, match=r"row '0'.*only by task_doc.retire_master"):
        _parent_completion_candidate(sprint, "0")
    from agents_remember.worktrees import reopen  # noqa: PLC0415

    contract = world.series("sprint")
    leaf = TaskDocument(
        id="0",
        slug="zero",
        title="Zero",
        kind="subTask",
        repo=REPOSITORY,
        createdAt="2026-10-04T00:00:00+00:00",
        master="task.json",
    )
    with pytest.raises(
        reopen.ReopenTaskDocumentError, match=r"row '0'.*only by task_doc.retire_master"
    ):
        reopen._plan_master_index_reset(contract, world.tasks / "sprint" / "zero.json", leaf)


def test_archive_evidence_refusals_name_the_path_and_the_action(tmp_path):
    real = tmp_path / "real.json"
    real.write_text("{}")
    link = tmp_path / "link.json"
    link.symlink_to(real)
    with pytest.raises(RuntimeError, match=rf"{link}.*regular non-symlink file.*then retry"):
        evidence._read_regular_file(link, owner="canonical lifecycle artifact link.json")
    locked = tmp_path / "locked.json"
    locked.write_text("{}")
    locked.chmod(0)
    try:
        with pytest.raises(RuntimeError, match=rf"{locked}.*is unreadable.*retry"):
            evidence._read_regular_file(locked, owner="canonical lifecycle artifact locked.json")
    finally:
        locked.chmod(0o600)
    with pytest.raises(RuntimeError, match=rf"{tmp_path / 'nolife'}.*is unreadable.*retry"):
        evidence._lifecycle_paths(tmp_path / "nolife")
