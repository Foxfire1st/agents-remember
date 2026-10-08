"""The retire operation as one state machine: every state is read from disk, never from the route.

One case kills the operation at every transition, with a sprint and without one, and then asks
the same four things of the state it left: does the sprint still resolve every master it names,
is there at most one retirement record, is every other request refused without a change, and does
the same request complete. In every state it also makes the sprint's document unreadable in four
ways (empty, cut before its membership list, an invalid byte in front, no read permission) and
asks that every request is refused by the file's name and changes nothing, and it compares the
files a dry run lists with the files the real run then writes. The hand-made states (a folder
moved back, a master attached again between two attempts) are cases of the same checks.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from agents_remember.application.review_artifact_receipts import ReceiptLedger
from agents_remember.application.task_docs import task_master_retirement as retirement
from agents_remember.application.task_docs.task_doc_tools import (
    TaskDocCall,
    TaskDocEdit,
    TaskDocTarget,
)
from agents_remember.application.task_docs.task_retirement_records import proof_path
from agents_remember.controlplane.closeout_queue_store import CloseoutQueueStore
from agents_remember.mcp.tools.task_doc import task_doc_payload
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks import TaskDocument, read_task_doc, write_task_doc
from agents_remember.worktrees import task_fact_publication
from agents_remember.worktrees.queue.closeout_projection import (
    capture_projection_source,
    now_iso,
)
from agents_remember.worktrees.queue.closeout_projection_publication import (
    refresh_closeout_projection,
)
from test_master_retirement import World
from test_standalone_master_retirement import REASON, _datasets, lone, retire_lone
from test_task_execution_topology import REPOSITORY

pytestmark = pytest.mark.integration

SPRINT = TaskDocumentRef(repository=REPOSITORY, path="sprint/task.json")


@pytest.fixture
def world(tmp_path):
    return World(tmp_path)


class Case:
    """One master on its way out: through the sprint that commands it, or on its own."""

    def __init__(self, world: World, *, sprint: bool) -> None:
        self.world, self.sprint = world, sprint
        if sprint:
            self.name, self.ref = "master-a", world.refs[0]
        else:
            self.name, self.ref = "outside", lone(world)[1]
        self.live = world.tasks / self.name
        self.archive = world.tasks / "0_archive" / self.name
        self.copies = _datasets(self.live)
        refresh_closeout_projection(world.coord, SPRINT)

    def retire(self, *, dry_run: bool = False, **fields: Any) -> dict[str, Any]:
        """The recorded request, sent to the document that owns the record."""
        if self.sprint:
            return self.world.retire(dry_run=dry_run, fields={**self.world.fields, **fields})
        return retire_lone(self.world, self.name, self.ref, dry_run=dry_run, **fields)

    def elsewhere(self, *, dry_run: bool = False) -> dict[str, Any]:
        """The same request, sent to the other document: the master itself, or the sprint."""
        if self.sprint:
            return task_doc_payload(
                self.world.cfg,
                TaskDocTarget(repo_id=REPOSITORY, task_name=self.name),
                operation="retire_master",
                edit=TaskDocEdit(fields=self.world.fields),
                call=TaskDocCall(dry_run=dry_run),
            )
        fields = {"masterRef": self.ref.model_dump(), "reason": REASON}
        return self.world.retire(dry_run=dry_run, fields=fields)

    def records(self) -> list[str]:
        """Every retirement record of the master, wherever one lies."""
        rows = [
            f"row {row.number}"
            for row in self.world.sprint().subTasks
            if row.retirement is not None and row.retirement.masterRef == self.ref
        ]
        return rows + [
            str(proof_path(folder))
            for folder in (self.live, self.archive)
            if proof_path(folder).is_file()
        ]

    def receipts(self) -> list[dict[str, Any]]:
        reports = self.archive / "notes" / "reports"
        return [
            json.loads(path.read_text())
            for path in sorted(reports.glob("review-artifact-cleanup*.json"))
        ]

    def projection_is_current(self) -> bool:
        stored = CloseoutQueueStore(self.world.coord, SPRINT).read_raw(timestamp=now_iso())
        current = capture_projection_source(self.world.coord, SPRINT).identity
        return (
            stored.serviceCondition == "valid-built"
            and stored.sourceFingerprint == current.fingerprint
        )


def _refused_without_change(case: Case, call, match: str) -> None:
    before = case.world.snapshot()
    with pytest.raises(ValueError, match=match):
        call()
    assert case.world.snapshot() == before
    case.world.validate()


def _attach(world: World, ref: TaskDocumentRef, number: str) -> None:
    task_doc_payload(
        world.cfg,
        world.target,
        operation="attach_master",
        edit=TaskDocEdit(fields={"masterRef": ref.model_dump(), "number": number}),
        call=TaskDocCall(),
    )


def _detach(world: World, ref: TaskDocumentRef) -> None:
    task_doc_payload(
        world.cfg,
        world.target,
        operation="detach_master",
        edit=TaskDocEdit(fields={"masterRef": ref.model_dump()}),
        call=TaskDocCall(),
    )


KILLS = {
    # where the process dies: (what to patch, the state the next request finds)
    "before-the-record": ("record", "not-recorded"),
    "after-the-record": ("archive", "recorded"),
    "after-the-archive": ("refresh", "folder-archived"),
    "inside-the-cleanup": ("receipt", "folder-archived"),
}


def _kill(case: Case, seam: str) -> None:
    if seam == "record":
        name = "_record_on_sprint" if case.sprint else "_record_in_folder"
        target = patch.object(retirement, name, side_effect=KeyboardInterrupt)
    elif seam == "archive":
        target = patch.object(retirement, "_archive_folder", side_effect=KeyboardInterrupt)
    elif seam == "refresh" and case.sprint:
        # After the folder has moved and before the sprint's queue projection is invalidated.
        target = patch.object(
            task_fact_publication, "_invalidate_scope", side_effect=KeyboardInterrupt
        )
    elif seam == "refresh":
        target = patch.object(retirement, "_clean_up", side_effect=KeyboardInterrupt)
    else:
        # After the hook has deleted and before it replaces its receipt by the outcome.
        target = patch.object(ReceiptLedger, "finish", side_effect=KeyboardInterrupt)
    with target, pytest.raises(KeyboardInterrupt):
        case.retire()


def _damages(original: bytes) -> dict[str, bytes | None]:
    """The ways a task document stops being readable; ``None`` takes the reader's permission."""
    return {
        "empty": b"",
        "cut before its membership list": original[: original.index(b'"orchestrates"')],
        "an invalid byte in front": b"\xff" + original,
        "unreadable by mode": None,
    }


def _refused_while_unreadable(case: Case, document: Path) -> None:
    """No request does anything while a task document of the repository cannot be read."""
    original, mode = document.read_bytes(), document.stat().st_mode
    before = case.world.snapshot()
    for damage, content in _damages(original).items():
        if content is None and os.geteuid() == 0:
            continue  # nothing is unreadable by mode to the superuser
        if content is None:
            document.chmod(0)
        else:
            document.write_bytes(content)
        try:
            for call in (case.retire, case.elsewhere):
                for dry_run in (True, False):
                    with pytest.raises(ValueError) as refused:
                        call(dry_run=dry_run)
                    said = str(refused.value)
                    assert document.as_posix() in said, (damage, said)
                    assert "cannot be opened, decoded or parsed" in said, (damage, said)
                    assert "repeat task_doc.retire_master" in said, (damage, said)
        finally:
            document.chmod(mode)
            document.write_bytes(original)
        assert case.world.snapshot() == before, damage


def _files_written(case: Case, before: dict[str, bytes], after: dict[str, bytes]) -> set[str]:
    """The files a request created or changed; the moved folder's own files are not written."""

    def home(name: str) -> str:
        return name.removeprefix("0_archive/")

    earlier = {home(name): content for name, content in before.items()}
    return {home(name) for name, content in after.items() if earlier.get(home(name)) != content}


def _files_listed(case: Case, preview: dict[str, Any]) -> set[str]:
    tasks = case.world.tasks
    return {
        Path(name).relative_to(tasks).as_posix().removeprefix("0_archive/")
        for name in preview["wouldWrite"]
    }


@pytest.mark.parametrize("sprint", [True, False], ids=["with-a-sprint", "without-a-sprint"])
@pytest.mark.parametrize("kill", list(KILLS))
def test_a_death_at_any_transition_leaves_a_state_the_same_request_completes(world, kill, sprint):
    case = Case(world, sprint=sprint)
    seam, found = KILLS[kill]
    _kill(case, seam)
    recorded = found != "not-recorded"
    wanted = found if found != "recorded" else "sprint-edited" if sprint else "proof-written"
    # The state the death left: the sprint resolves every master it names, and one record at most.
    masters = len(world.validate())
    assert masters == (2 if sprint and recorded else 3)
    assert len(case.records()) == (1 if recorded else 0)
    assert case.live.is_dir() != case.archive.is_dir()
    assert case.archive.is_dir() == (found == "folder-archived")
    # Any other request is refused and changes nothing.
    if recorded:
        _refused_without_change(
            case, lambda: case.retire(reason="another reason"), r"differs from its retained proof"
        )
        _refused_without_change(
            case,
            lambda: case.retire(masterRef={"repository": "elsewhere", "path": case.ref.path}),
            r"outside the repository",
        )
    if recorded and sprint:
        edge = world.edges[1]
        named = [{key: edge[key] for key in ("predecessor", "successor")}]
        _refused_without_change(
            case, lambda: case.retire(removeEdges=named), r"differs from its retained proof"
        )
    # One route: the same request sent to the other document is refused in every state.
    for dry_run in (True, False):
        _refused_without_change(
            case,
            lambda dry_run=dry_run: case.elsewhere(dry_run=dry_run),
            (
                (r"recorded on row '0' of sprint .*sprint/task.json" if recorded else r"")
                + r".*on that sprint"
            )
            if sprint
            else r"sprint/task.json, which does not command master .*outside.*own task.json",
        )
    # While the sprint's document cannot be read, nothing is decided and nothing changes.
    _refused_while_unreadable(case, world.tasks / "sprint" / "task.json")
    # A dry run of the same request says what state it found and what is left to do.
    preview = case.retire(dry_run=True)
    assert preview["retirementState"] == wanted and preview["dryRun"]
    assert any("move" in step for step in preview["wouldChange"]) == case.live.is_dir()
    assert preview["wouldChange"][-1].startswith("cleanup receipt: write receipt")
    # The same request completes the retirement from wherever the death left it.
    before = world.snapshot()
    done = case.retire()
    # The dry run listed every file the real run wrote. What it listed beyond that was written
    # with the bytes it already had: the sprint's pair of a completing request, and a lock file.
    written, listed = _files_written(case, before, world.snapshot()), _files_listed(case, preview)
    assert written <= listed, (sorted(written - listed), preview["wouldWrite"])
    assert listed - written <= {
        "sprint/task.json",
        "sprint/task.md",
        "sprint/artifacts/closeout-candidates.json.lock",
    }, sorted(listed - written)
    assert done["ok"] and done["state"] == "retired" and done["retirementState"] == "hook-finished"
    assert done["retirementResumed"] == recorded
    assert case.archive.is_dir() and not case.live.exists()
    assert len(world.validate()) == (2 if sprint else 3) and len(case.records()) == 1
    # Every deletion is in a receipt, and the receipts are numbered without a gap.
    receipts = case.receipts()
    assert sorted(receipt["attempt"] for receipt in receipts) == list(range(1, len(receipts) + 1))
    recorded_deletions = {
        entry["path"]
        for receipt in receipts
        for entry in (
            *receipt.get("datasetCopies", []),
            *receipt.get("planned", {}).get("datasetCopies", []),
        )
    }
    assert recorded_deletions == {copy.as_posix() for copy in case.copies}
    assert not any(copy.exists() for copy in case.copies)
    if sprint:
        # The completing request refreshes the sprint's queue projection, as a fresh one does.
        assert case.projection_is_current()
        assert bool(done["projectionEffects"]) == (found != "folder-archived" or seam == "refresh")
    # The completed retirement is as closed to a request it cannot check as every state before.
    _refused_while_unreadable(case, world.tasks / "sprint" / "task.json")
    # A further repeat has nothing to do, and a dry run says so.
    before = world.snapshot()
    again = case.retire()
    assert again["ok"] and again["retirementState"] == "hook-finished"
    assert again["taskArchive"]["reviewArtifacts"]["receipt"] == "unchanged"
    assert again["projectionEffects"] == [] and world.snapshot() == before
    nothing = case.retire(dry_run=True)
    assert nothing["state"] == "retired" and nothing["wouldChange"] == []
    assert nothing["taskArchive"]["state"] == "archived"
    assert "Nothing would change" in nothing["detail"] and world.snapshot() == before


def test_a_half_written_sprint_pair_is_completed_and_rendered_again(world):
    """A death between the sprint's JSON and its Markdown leaves the page behind the document."""
    case = Case(world, sprint=True)
    page = world.tasks / "sprint" / "task.md"
    stale = page.read_bytes()
    _kill(case, "archive")
    page.write_bytes(stale)
    # The dry run lists the rewrite of the page, which the real run performs.
    preview = case.retire(dry_run=True)
    assert preview["retirementState"] == "sprint-edited"
    assert "write its task.json and its rendered task.md again" in preview["wouldChange"][0]
    assert page.as_posix() in preview["wouldWrite"]
    assert (world.tasks / "sprint" / "task.json").as_posix() in preview["wouldWrite"]
    (document,) = preview["documents"]
    assert document["renderedPath"] == page.as_posix() and document["diff"]
    assert page.read_bytes() == stale
    done = case.retire()
    assert done["ok"] and done["retirementResumed"] and len(world.validate()) == 2
    assert "Retired agents-remember/master-a/task.json" in page.read_text()
    assert case.projection_is_current()


@pytest.mark.parametrize("moved_back", [False, True], ids=["interrupted", "moved-back-by-hand"])
def test_a_master_attached_again_between_two_attempts_stays_where_its_sprint_resolves_it(
    world, moved_back
):
    """With a sprint: the repeated request refuses, and the action it names clears the refusal."""
    case = Case(world, sprint=True)
    if moved_back:
        assert case.retire()["ok"]
        case.archive.rename(case.live)
    else:
        _kill(case, "archive")
    _attach(world, case.ref, "9")
    for call in (case.retire, lambda: case.retire(dry_run=True)):
        _refused_without_change(case, call, r"reattached after retirement.*detach_master")
    _refused_without_change(case, case.elsewhere, r"recorded on row '0' of sprint")
    assert len(world.validate()) == 3 and case.live.is_dir()
    _detach(world, case.ref)
    done = case.retire()
    assert done["ok"] and done["retirementResumed"] and len(world.validate()) == 2
    assert case.archive.is_dir() and case.records() == ["row 0"]


@pytest.mark.parametrize("moved_back", [False, True], ids=["interrupted", "moved-back-by-hand"])
@pytest.mark.parametrize("way_out", ["detach", "delete-the-record"])
def test_a_lone_master_that_a_sprint_commands_now_is_not_archived_from_under_it(
    world, moved_back, way_out
):
    """Without a sprint: a record in the master's folder never outranks a sprint that names it."""
    case = Case(world, sprint=False)
    if moved_back:
        assert case.retire()["ok"]
        case.archive.rename(case.live)
    else:
        _kill(case, "archive")
    _attach(world, case.ref, "9")
    stored = proof_path(case.live)
    for call in (case.retire, lambda: case.retire(dry_run=True), case.elsewhere):
        _refused_without_change(
            case, call, rf"{stored}.*sprint .*sprint/task.json commands it now.*detach_master"
        )
    assert len(world.validate()) == 4 and case.live.is_dir() and len(case.records()) == 1
    if way_out == "detach":
        _detach(world, case.ref)
        done = case.retire()
        assert done["retirementResumed"] and case.records() == [str(proof_path(case.archive))]
    else:
        stored.unlink()
        fields = {"masterRef": case.ref.model_dump(), "reason": "retired through its sprint"}
        done = world.retire(fields=fields)
        assert not done["retirementResumed"] and case.records() == ["row 9"]
    assert done["ok"] and case.archive.is_dir() and len(world.validate()) == 3


def test_the_folder_never_moves_from_under_a_sprint_that_took_the_master_meanwhile(world):
    """The last look before the move: an attach between admission and the move stops it."""
    case = Case(world, sprint=False)
    before = world.snapshot()
    admit = retirement._admit

    def attached_meanwhile(request, payload):
        plan = admit(request, payload)
        _attach(world, case.ref, "9")
        return plan

    with (
        patch.object(retirement, "_admit", side_effect=attached_meanwhile),
        pytest.raises(ValueError, match=r"before archival.*sprint/task.json commands master"),
    ):
        case.retire()
    assert case.live.is_dir() and not case.archive.exists() and case.records() == []
    assert len(world.validate()) == 4
    _detach(world, case.ref)
    assert {k: v for k, v in world.snapshot().items() if "artifacts/" not in k} == {
        k: v for k, v in before.items() if "artifacts/" not in k
    }


def test_a_request_on_the_master_itself_writes_no_second_record(world):
    """After an interrupted retirement on the sprint, the master's own address stays closed."""
    case = Case(world, sprint=True)
    _kill(case, "archive")
    row = world.sprint().subTasks[0]
    other = TaskDocEdit(fields={**world.fields, "reason": "another reason, sent to the master"})
    target = TaskDocTarget(repo_id=REPOSITORY, task_name="master-a")
    for edit in (other, TaskDocEdit(fields=world.fields)):
        for dry_run in (True, False):
            _refused_without_change(
                case,
                lambda edit=edit, dry_run=dry_run: task_doc_payload(
                    world.cfg,
                    target,
                    operation="retire_master",
                    edit=edit,
                    call=TaskDocCall(dry_run=dry_run),
                ),
                r"recorded on row '0' of sprint .*repeat task_doc.retire_master on that sprint",
            )
    assert case.records() == ["row 0"] and world.sprint().subTasks[0] == row
    assert case.live.is_dir() and not proof_path(case.live).exists()


def test_a_sprint_is_not_a_master_and_stays_a_sprint_when_it_lost_its_last_master(world):
    sprint_fields = {"masterRef": SPRINT.model_dump(), "reason": "retire the sprint itself"}
    before = world.snapshot()
    for dry_run in (True, False):
        with pytest.raises(ValueError, match=r"is a sprint, not a master.*archive_sprint") as said:
            world.retire(dry_run=dry_run, fields=sprint_fields)
        assert "commands 3 master(s)" in str(said.value) and world.snapshot() == before
    # A graph-less sprint that retired its only master commands nothing, and is a sprint still.
    data = world.sprint().model_dump(mode="json")
    data.update(orchestrates=["master-a"], subTasks=[data["subTasks"][0]], executionGraph=None)
    data["sections"] = []
    write_task_doc(world.tasks / "sprint", TaskDocument.model_validate(data))
    assert world.retire()["ok"] and world.sprint().orchestrates == []
    before = world.snapshot()
    for dry_run in (True, False):
        with pytest.raises(ValueError, match=r"is a sprint, not a master.*archive_sprint") as said:
            world.retire(dry_run=dry_run, fields=sprint_fields)
        assert "records the retirement" in str(said.value) and world.snapshot() == before
    report = task_doc_payload(
        world.cfg, world.target, operation="linkage_report", edit=TaskDocEdit(), call=TaskDocCall()
    )
    assert report["ok"] and report["linkageFacts"] == []
    _attach(world, world.refs[1], "7")
    assert world.sprint().orchestrates == ["master-b"]
    assert world.sprint().subTasks[0].retirement is not None


def test_the_restart_notice_follows_the_record_and_nothing_else(world):
    case = Case(world, sprint=False)
    blank = {**world.fields, "reason": "  "}
    unfinished = world.tasks / "master-b"
    write_task_doc(
        unfinished,
        read_task_doc(unfinished / "task.json").model_copy(update={"status": "inProgress"}),
    )
    # Before anything is recorded: a refusal of the parser and one of the operation carry none.
    for fields, text in ((blank, "invalid retire_master payload"), (world.fields, "unfinished")):
        with pytest.raises(ValueError, match=text) as said:
            world.retire(fields=fields)
        assert "Restart required" not in str(said.value)
    with pytest.raises(ValueError, match=r"differs|removeEdges") as said:
        case.retire(removeEdges=[{"predecessor": case.ref.model_dump(), "successor": "x"}])
    assert "Restart required" not in str(said.value)
    edge = world.edges[1]
    named = {**world.fields, "removeEdges": [{k: edge[k] for k in ("predecessor", "successor")}]}
    assert "Restart required" in world.retire(fields=named, dry_run=True)["detail"]
    assert world.retire(fields=named)["ok"] and case.retire()["ok"]
    # Once a record exists, every refusal carries it: the named master's record ...
    refusals = [
        lambda: world.retire(fields={**named, "reason": "  "}),
        lambda: world.retire(fields={**named, "reason": "another"}),
        lambda: case.retire(reason="  "),
        lambda: case.retire(reason="another"),
        # ... and a record that stands on the addressed sprint, for whichever master.
        lambda: world.retire(fields={**named, "masterRef": world.refs[2].model_dump()}),
        lambda: world.retire(
            fields={**world.fields, "masterRef": {"repository": "other", "path": "x/task.json"}}
        ),
    ]
    for refusal in refusals:
        with pytest.raises(ValueError) as said:
            refusal()
        assert "Restart required" in str(said.value), str(said.value)


def test_a_dry_run_after_a_partial_cleanup_lists_exactly_what_remains(world):
    case = Case(world, sprint=True)
    deleted, failed = case.copies
    unlink = Path.unlink

    def busy(path, *args, **kwargs):
        if path == failed:
            raise OSError("dataset is busy")
        return unlink(path, *args, **kwargs)

    with patch.object(Path, "unlink", autospec=True, side_effect=busy):
        partial = case.retire()
    assert not partial["ok"] and partial["retirementState"] == "hook-failed"
    _refused_while_unreadable(case, world.tasks / "sprint" / "task.json")
    before = world.snapshot()
    preview = case.retire(dry_run=True)
    assert preview["ok"] and preview["state"] == "would-clean-up"
    assert preview["taskArchive"]["state"] == "archived" and preview["documents"] == []
    reports = case.archive / "notes" / "reports"
    assert preview["wouldChange"] == [
        "review artifacts: delete 1 (listed under taskArchive.reviewArtifacts)",
        f"cleanup receipt: write receipt 2 at {reports / 'review-artifact-cleanup.json'}",
    ]
    assert preview["wouldWrite"] == [
        (reports / "review-artifact-cleanup.attempt-1.json").as_posix(),
        (reports / "review-artifact-cleanup.json").as_posix(),
    ]
    # The state is the one of the module's table: the last attempt failed and work is left.
    assert preview["retirementState"] == "hook-failed"
    remaining = preview["taskArchive"]["reviewArtifacts"]
    assert [entry["path"] for entry in remaining["datasetCopies"]] == [failed.as_posix()]
    assert [entry["artifact"] for entry in remaining["alreadyAbsent"]] == [deleted.as_posix()]
    assert world.snapshot() == before


def test_a_master_that_is_missing_is_named_with_an_action_that_works(world):
    ghost = {**world.fields, "masterRef": {"repository": REPOSITORY, "path": "ghost/task.json"}}
    with pytest.raises(ValueError, match=r"ghost/task.json has no task folder: neither .*ghost"):
        world.retire(fields=ghost)
    # A commanded master that was moved under 0_archive by hand: every reader names the repair.
    live, archive = world.tasks / "master-a", world.tasks / "0_archive" / "master-a"
    archive.parent.mkdir()
    live.rename(archive)
    with pytest.raises(ValueError, match=r"0_archive.*task_doc.retire_master"):
        world.validate()
    report = task_doc_payload(
        world.cfg, world.target, operation="linkage_report", edit=TaskDocEdit(), call=TaskDocCall()
    )
    fact = next(f for f in report["linkageFacts"] if f["kind"] == "orchestrates-entry-unresolved")
    assert fact["archivePaths"] == [(archive / "task.json").as_posix()]
    assert "task_doc.retire_master" in fact["detail"] and "0_archive" in fact["detail"]
    with pytest.raises(
        ValueError, match=rf"without a retirement record; restore its folder to {live}"
    ):
        world.retire()
    # The named action: put the folder back, then retire the master through its sprint.
    archive.rename(live)
    assert world.retire()["ok"] and len(world.validate()) == 2 and archive.is_dir()


def test_any_task_document_that_cannot_be_read_stops_a_retirement_and_is_named(world):
    """An archive cannot be undone, so a retirement is stopped by every document it cannot read."""
    elsewhere = world.tasks / "unrelated"
    elsewhere.mkdir()
    for broken in ('{"orchestrates": ["someone-else"', '{"orchestrates": ["master-a"', "[]"):
        (elsewhere / "task.json").write_text(broken)
        before = world.snapshot()
        for dry_run in (True, False):
            with pytest.raises(
                ValueError, match=r"unrelated/task.json cannot be opened, decoded or parsed"
            ) as refused:
                world.retire(dry_run=dry_run)
            assert "Repair or remove that file, then repeat task_doc.retire_master" in str(
                refused.value
            )
        assert world.snapshot() == before
    (elsewhere / "task.json").unlink()
    assert world.retire()["ok"]
