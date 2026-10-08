"""Public retirement and finalization preserve sprint topology and exact recovery evidence."""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest
from agents_remember.application.task_docs import task_master_retirement as retirement
from agents_remember.application.task_docs import task_retirement_shared as retirement_shared
from agents_remember.application.task_docs import task_retirement_sprint_edit as sprint_edit
from agents_remember.application.task_docs.task_doc_tools import (
    TaskDocCall,
    TaskDocEdit,
    TaskDocTarget,
    task_doc_tool,
)
from agents_remember.mcp.tools.task_doc import task_doc_payload
from agents_remember.models.lifecycles.operation import LifecycleOperationRecord
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks import SubTaskRef, TaskDocument, read_task_doc, write_task_doc
from agents_remember.tasks.document_refs import TaskDocumentRefError, TaskDocumentTopology
from agents_remember.worktrees.worktree_contract import (
    ContractTask,
    RepoBranchPlan,
    default_series_contract,
    write_contract,
)
from test_task_execution_topology import REPOSITORY, _config, _master
from test_task_sprint_linkage import _register_section
from test_worktree_support import git, init_repo

pytestmark = pytest.mark.integration


class World:
    def __init__(self, root: Path) -> None:
        self.coord = root / "coord"
        self.tasks = self.coord / "tasks" / REPOSITORY
        self.code = root / "code"
        self.base = init_repo(self.code)
        git(self.code, "branch", "super")
        self.cfg = _config(self.coord, self.code)
        self.refs = [
            TaskDocumentRef(repository=REPOSITORY, path=f"master-{name}/task.json")
            for name in ("a", "b", "c")
        ]
        for ref in self.refs:
            write_task_doc(
                self.tasks / Path(ref.path).parent,
                _master(
                    identity=Path(ref.path).parent.name,
                    execution_nature="atomic",
                    title=f"Human {Path(ref.path).parent.name}",
                ).model_copy(update={"status": "Completed"}),
            )
        a, b, c = self.refs
        self.edges = [
            {
                "predecessor": c.model_dump(),
                "successor": a.model_dump(),
                "reason": "incoming",
                "judgmentId": "J-1",
            },
            {
                "predecessor": a.model_dump(),
                "successor": b.model_dump(),
                "reason": "outgoing",
                "judgmentId": "J-2",
            },
        ]
        sprint = _master(
            identity="sprint",
            orchestrates=["master-a", "master-b", "master-c"],
            execution_graph={"nodes": [ref.model_dump() for ref in self.refs], "edges": self.edges},
        ).model_copy(
            update={
                "subTasks": [
                    SubTaskRef(
                        number=str(i), name=f"Master {i}", status="inProgress", masterRef=ref
                    )
                    for i, ref in enumerate(self.refs)
                ],
                "sections": [_register_section("J-1", "J-2")],
            }
        )
        write_task_doc(self.tasks / "sprint", sprint)
        self.target = TaskDocTarget(repo_id=REPOSITORY, task_name="sprint")
        self.fields = {"masterRef": a.model_dump(), "reason": "This work is deliberately retired."}

    def retire(self, *, dry_run: bool = False, fields=None):
        return task_doc_payload(
            self.cfg,
            self.target,
            operation="retire_master",
            edit=TaskDocEdit(fields=fields or self.fields),
            call=TaskDocCall(dry_run=dry_run),
        )

    def snapshot(self):
        return {
            path.relative_to(self.tasks).as_posix(): path.read_bytes()
            for path in self.tasks.rglob("*")
            if path.is_file()
        }

    def sprint(self):
        return read_task_doc(self.tasks / "sprint" / "task.json")

    def validate(self):
        return TaskDocumentTopology(self.coord).validate_execution_topology(
            TaskDocumentRef(repository=REPOSITORY, path="sprint/task.json")
        )

    def series(self, name: str = "master-a"):
        git(self.code, "branch", f"ar/{name}")
        contract = default_series_contract(
            ContractTask(
                name=name,
                repo_name=REPOSITORY,
                coordination_root=self.coord,
                workflow_kind="light-task",
                memory_mode="disabled",
            ),
            code=RepoBranchPlan(
                repo_path=self.code,
                source_branch="super",
                work_branch=f"ar/{name}",
                base_commit=self.base,
            ),
        )
        contract = replace(
            contract,
            cleanup="completed",
            closeout_status="completed",
            code_commit=self.base,
            integration_status="completed",
            integrated_code_commit=self.base,
        )
        write_contract(contract.contract_path, contract)
        return contract


@pytest.fixture
def world(tmp_path):
    return World(tmp_path)


def test_retirement_dry_run_lists_exact_edits_and_real_run_keeps_valid_sprint(world):
    before = world.snapshot()
    refs = git(world.code, "show-ref")
    preview = world.retire(dry_run=True)
    assert preview["state"] == "would-retire"
    assert preview["removedOrchestrates"] == ["master-a"]
    assert preview["removedGraphNodes"] == 1
    assert preview["removedEdges"] == world.edges
    assert world.snapshot() == before
    result = world.retire()
    assert result["ok"] and result["state"] == "retired"
    assert len(world.validate()) == 2
    row = world.sprint().subTasks[0]
    assert row.number == "0" and row.name == "Master 0"
    assert row.masterRef is None and row.file == "" and row.retirement is not None
    assert row.retirement.reason == world.fields["reason"]
    assert row.retirement.retiredAt in (world.tasks / "sprint" / "task.md").read_text()
    assert (world.tasks / "0_archive" / "master-a" / "task.json").is_file()
    assert git(world.code, "show-ref") == refs
    preview_graph = preview["documents"][0]["rendered"].split("```mermaid")[1].split("```")[0]
    real_graph = (
        (world.tasks / "sprint" / "task.md").read_text().split("```mermaid")[1].split("```")[0]
    )
    assert preview_graph == real_graph and "Human master-b" in real_graph


def test_unfinished_successor_needs_exact_edge_affirmation(world):
    path = world.tasks / "master-b"
    write_task_doc(
        path, read_task_doc(path / "task.json").model_copy(update={"status": "inProgress"})
    )
    before = world.snapshot()
    with pytest.raises(ValueError, match=r"unfinished successor edges.*removeEdges"):
        world.retire()
    assert world.snapshot() == before
    edge = world.edges[1]
    result = world.retire(
        fields={
            **world.fields,
            "removeEdges": [{key: edge[key] for key in ("predecessor", "successor")}],
        }
    )
    assert result["ok"] and len(world.validate()) == 2


def test_a_pending_cleanup_with_nothing_open_is_a_fact_and_does_not_refuse(world):
    contract = world.series()
    write_contract(contract.contract_path, replace(contract, cleanup="pending"))
    before = world.snapshot()
    preview = world.retire(dry_run=True)
    assert preview["state"] == "would-retire" and world.snapshot() == before
    assert any(
        "the master's own contract (series-contract.md) records cleanup 'pending'; no open work "
        "was found for it" in fact
        for fact in preview["readinessFacts"]
    )


def test_failed_move_restores_exact_sprint_source_pair(world):
    before = world.snapshot()
    with (
        patch.object(Path, "rename", side_effect=OSError("disk refused rename")),
        pytest.raises(ValueError, match=r"sources and task folder restored"),
    ):
        world.retire()
    assert world.snapshot() == before
    assert not (world.tasks / "0_archive").exists()
    assert len(world.validate()) == 3


def test_interrupted_request_resumes_from_typed_row_and_rejects_changed_request(world):
    with (
        patch.object(Path, "rename", side_effect=KeyboardInterrupt),
        pytest.raises(KeyboardInterrupt),
    ):
        world.retire()
    assert len(world.validate()) == 2
    assert (world.tasks / "master-a").is_dir()
    proof = world.sprint().subTasks[0].retirement
    with pytest.raises(ValueError, match=r"differs from its retained proof"):
        world.retire(fields={**world.fields, "reason": "different"})
    result = world.retire()
    assert result["retirementResumed"] and result["ok"]
    assert world.sprint().subTasks[0].retirement == proof
    assert len(world.validate()) == 2
    again = world.retire()
    assert again["ok"] and again["retirementResumed"]


def test_master_source_change_after_interruption_refuses(world):
    with (
        patch.object(Path, "rename", side_effect=KeyboardInterrupt),
        pytest.raises(KeyboardInterrupt),
    ):
        world.retire()
    path = world.tasks / "master-a" / "task.md"
    path.write_text(path.read_text() + "changed\n")
    before = world.snapshot()
    with pytest.raises(ValueError, match=r"source pair changed"):
        world.retire()
    assert world.snapshot() == before


def test_source_pair_cas_refuses_sprint_or_successor_drift_before_publication(world):
    original = retirement._admit

    def drift(request, payload):
        prepared = original(request, payload)
        path = world.tasks / "master-b" / "task.md"
        path.write_text(path.read_text() + "new generation\n")
        return prepared

    with (
        patch.object(retirement, "_admit", side_effect=drift),
        pytest.raises(ValueError, match=r"publication-conflict"),
    ):
        world.retire()
    assert len(world.validate()) == 3
    assert not (world.tasks / "0_archive" / "master-a").exists()
    world.retire()
    with (
        patch.object(retirement, "_admit", side_effect=drift),
        patch.object(retirement_shared, "cleanup_review_artifacts") as cleanup,
        pytest.raises(ValueError, match=r"publication-conflict"),
    ):
        world.retire()
    cleanup.assert_not_called()
    assert len(world.validate()) == 2
    assert (world.tasks / "0_archive" / "master-a").is_dir()


def test_archive_hook_failure_is_truthful_and_same_request_can_retry(world):
    notes = world.tasks / "master-a" / "notes"
    notes.mkdir()
    for name in ("a.sqlite", "b.sqlite"):
        connection = sqlite3.connect(notes / name)
        connection.executescript(
            "CREATE TABLE invariant (id TEXT); CREATE TABLE invariant_revision (id TEXT)"
        )
        connection.close()
    archive = world.tasks / "0_archive" / "master-a"
    deleted, failed = archive / "notes" / "a.sqlite", archive / "notes" / "b.sqlite"
    unlink = Path.unlink
    attempts = []
    fail = True

    def partial_delete(path, *args, **kwargs):
        if path in (deleted, failed):
            attempts.append(path)
        if path == failed and fail:
            raise OSError("dataset is busy")
        return unlink(path, *args, **kwargs)

    with (
        patch.object(Path, "unlink", autospec=True, side_effect=partial_delete),
        patch.object(Path, "rename", autospec=True, side_effect=Path.rename) as move,
        patch.object(
            retirement, "write_task_doc_batch", wraps=retirement.write_task_doc_batch
        ) as write,
        patch.object(sprint_edit, "_detached_data", wraps=sprint_edit._detached_data) as detach,
        patch.object(
            sprint_edit,
            "remove_retired_master_edges",
            wraps=sprint_edit.remove_retired_master_edges,
        ) as edges,
        patch.object(
            retirement,
            "publish_task_doc_transaction_and_refresh",
            wraps=retirement.publish_task_doc_transaction_and_refresh,
        ) as publish,
    ):
        result = world.retire()
        report = result["taskArchive"]["reviewArtifacts"]
        assert not result["ok"] and result["state"] == "retired-with-hook-failures"
        assert result["taskArchive"]["state"] == "archived" and len(world.validate()) == 2
        assert [entry["path"] for entry in report["datasetCopies"]] == [deleted.as_posix()]
        assert report["failures"] == [{"target": failed.as_posix(), "detail": "dataset is busy"}]
        assert attempts == [deleted, failed] and not deleted.exists() and failed.exists()
        pair = {path: path.read_bytes() for path in (world.tasks / "sprint").glob("task.*")}
        proof = world.sprint().subTasks[0].retirement
        events = (publish, write, detach, edges, move)
        assert [event.call_count for event in events] == [1] * len(events)
        with pytest.raises(ValueError, match="differs from its retained proof"):
            world.retire(fields={**world.fields, "reason": "different"})
        for dry_run, blocked in ((True, True), (False, True), (False, False)):
            fail = blocked
            retry = world.retire(dry_run=dry_run)
            cleanup = retry["taskArchive"]["reviewArtifacts"]
            assert retry["retirementResumed"] and retry["taskArchive"]["state"] == "archived"
            assert [event.call_count for event in events] == [1] * len(events)
            assert retry["documents"] == [] and retry["projectionEffects"] == []
            assert retry["retirementRow"] == result["retirementRow"]
            assert all(path.read_bytes() == content for path, content in pair.items())
            assert world.sprint().subTasks[0].retirement == proof and len(world.validate()) == 2
            assert not (world.tasks / "master-a").exists() and not deleted.exists()
            assert [entry["path"] for entry in cleanup["datasetCopies"]] == (
                [] if blocked and not dry_run else [failed.as_posix()]
            )
            assert retry["ok"] == (dry_run or not blocked)
            assert cleanup["state"] == (
                "would-delete" if dry_run else "partial" if blocked else "deleted"
            )
            if blocked and not dry_run:
                assert (
                    retry["state"] == "retired-with-hook-failures"
                    and cleanup["failures"] == report["failures"]
                )
        assert attempts == [deleted, failed, failed, failed] and not failed.exists()


def test_retirement_proof_cannot_be_forged_or_erased_by_generic_task_edit(world):
    world.retire()
    data = world.sprint().model_dump(mode="json")
    data["subTasks"][0]["retirement"] = None
    before = world.snapshot()
    with pytest.raises(
        ValueError, match=r"row .0.*records a master retirement.*only by task_doc.retire_master"
    ):
        task_doc_tool(world.cfg, world.target, operation="replace", edit=TaskDocEdit(fields=data))
    assert world.snapshot() == before


def test_missing_archived_master_refusal_is_shared_by_topology_consumers(world):
    source = world.tasks / "master-a"
    archive = world.tasks / "0_archive" / "master-a"
    archive.parent.mkdir()
    source.rename(archive)
    topology = TaskDocumentTopology(world.coord)
    ref = TaskDocumentRef(repository=REPOSITORY, path="sprint/task.json")
    messages = []
    for consumer in (
        lambda: topology.validate_execution_topology(ref),
        lambda: topology.commanded_masters(topology.resolve(ref)),
        lambda: topology.validate_sprint_linkage(ref),
    ):
        with pytest.raises(TaskDocumentRefError) as refused:
            consumer()
        messages.append(str(refused.value))
    assert len(set(messages)) == 1
    assert (
        "master-a" in messages[0]
        and str(archive / "task.json") in messages[0]
        and "task_doc.retire_master" in messages[0]
    )


def _single_master_sprint(world, *, graph: bool):
    a = world.refs[0]
    data = world.sprint().model_dump(mode="json")
    data["orchestrates"] = ["master-a"]
    data["subTasks"] = [data["subTasks"][0]]
    data["executionGraph"] = {"nodes": [a.model_dump()], "edges": []} if graph else None
    data["sections"] = [] if not graph else data["sections"]
    write_task_doc(world.tasks / "sprint", TaskDocument.model_validate(data))


def test_only_graphed_master_refuses_naming_the_reason_and_the_way_out(world):
    _single_master_sprint(world, graph=True)
    before = world.snapshot()
    with pytest.raises(ValueError, match=r"master-a.*only node.*executionGraph.*attach_master"):
        world.retire()
    assert world.snapshot() == before


def test_last_master_of_a_graphless_sprint_retires_and_retries_its_hook(world):
    """A sprint document with no commanded master validates; its own row keeps the retry route."""
    _single_master_sprint(world, graph=False)
    result = world.retire()
    assert result["ok"] and result["state"] == "retired"
    assert world.sprint().orchestrates == [] and world.sprint().subTasks[0].retirement is not None
    again = world.retire()
    assert again["ok"] and again["retirementResumed"]
    with pytest.raises(ValueError, match=r"does not command master.*master-b.*no sprint commands"):
        world.retire(fields={**world.fields, "masterRef": world.refs[1].model_dump()})


def test_rows_without_documents_do_not_stop_a_retirement_and_a_missing_one_is_a_fact(world):
    contract = world.series()
    master = read_task_doc(contract.task_root / "task.json")
    master.subTasks = [
        SubTaskRef(number="L1", name="Dropped", status="abandoned", file="l1.md"),
        SubTaskRef(number="L2", name="Dropped, never had a document", status="abandoned"),
    ]
    write_task_doc(contract.task_root, master)
    preview = world.retire(dry_run=True)
    assert preview["state"] == "would-retire"
    assert not any(fact.startswith("row ") for fact in preview["readinessFacts"])
    master.subTasks[0].status = "Completed"
    write_task_doc(contract.task_root, master)
    preview = world.retire(dry_run=True)
    gone = (contract.task_root / "l1.json").as_posix()
    assert preview["state"] == "would-retire"
    assert f"row 'L1': its document {gone} is gone" in preview["readinessFacts"]


def test_archive_symlink_escape_refuses_before_publication(world):
    outside = world.coord.parent / "outside-archive"
    outside.mkdir()
    (world.tasks / "0_archive").symlink_to(outside, target_is_directory=True)
    before = world.snapshot()
    with pytest.raises(ValueError, match=r"escapes tasks"):
        world.retire()
    assert world.snapshot() == before and not list(outside.iterdir())


def test_nested_master_is_refused_before_anything_changes(world):
    nested = world.tasks / "group" / "nested"
    write_task_doc(
        nested,
        _master(identity="nested", execution_nature="atomic").model_copy(
            update={"status": "Completed"}
        ),
    )
    ref = TaskDocumentRef(repository=REPOSITORY, path="group/nested/task.json")
    data = world.sprint().model_dump(mode="json")
    data["orchestrates"].append("nested")
    data["subTasks"].append(
        SubTaskRef(number="3", name="Nested", status="inProgress", masterRef=ref).model_dump(
            mode="json", exclude_none=True
        )
    )
    data["executionGraph"]["nodes"].append(ref.model_dump())
    write_task_doc(world.tasks / "sprint", TaskDocument.model_validate(data))
    before = world.snapshot()
    for dry_run in (True, False):
        with pytest.raises(
            ValueError, match=r"group/nested.*nested inside another task.*0_archive/<name>"
        ):
            world.retire(dry_run=dry_run, fields={**world.fields, "masterRef": ref.model_dump()})
        assert world.snapshot() == before
    assert nested.is_dir() and not (world.tasks / "0_archive").exists()


def _historical_operation(contract, status: str) -> LifecycleOperationRecord:
    return LifecycleOperationRecord.model_validate(
        {
            "taskId": contract.task_id,
            "taskName": contract.task_name,
            "contractPath": contract.contract_path.as_posix(),
            "operationKind": "closeout",
            "candidateState": "a" * 64,
            "fingerprint": "b" * 64,
            "operationKey": "c" * 64,
            "generation": 1,
            "taskIntent": {"state": "missing-intent"},
            "input": {
                "kind": "closeout",
                "configPath": (contract.coordination_root / "settings.json").as_posix(),
                "contractPath": contract.contract_path.as_posix(),
                "effectiveInput": {
                    "route": "worktree",
                    "contractKind": "series",
                    "memoryMode": "disabled",
                    "code": {"state": "not-applicable", "reason": "series snapshot"},
                    "memory": {"state": "not-applicable", "reason": "disabled"},
                },
                "approvalNote": "Synthetic retained-operation proof fixture.",
            },
            "status": status,
            "phase": "preflight" if status == "running" else "failed",
            "queuedAt": "2026-10-03T00:00:00+00:00",
            "reportPath": (
                contract.worktree_group / ".lifecycle/closeout-operation.log"
            ).as_posix(),
        }
    )


def test_valid_terminal_historical_generation_is_preserved_and_revalidated(world):
    contract = world.series()
    record = _historical_operation(contract, "failed")
    path = contract.worktree_group / ".lifecycle/closeout-operation.generation-1.json"
    path.parent.mkdir(parents=True)
    raw = record.model_dump_json()
    path.write_text(raw)
    assert world.retire(dry_run=True)["state"] == "would-retire"
    assert world.retire()["state"] == "retired"
    assert path.read_text() == raw and len(world.validate()) == 2
