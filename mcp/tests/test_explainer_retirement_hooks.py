"""The explainer's lifetime in AR's retirement (MIK-R186 rules 8 and 9; MIK-R187 rule 7).

Every case here protects one promise the archive makes about a leaf's explainer environment: a
preview lists it and retires nothing, master retirement and sprint archive retire it and then
reclaim its folder, counter, grades and receipts, a failed retire keeps them for the retry, a
symlink is held rather than followed, leaf finalize leaves it alone, the warmup never resumes it,
and the chat list learns it from its binding.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest import mock

import pytest
from agents_remember.application import review_artifact_cleanup
from agents_remember.application.review_artifact_cleanup import (
    ReviewArtifactCleanup,
    cleanup_review_artifacts,
)
from agents_remember.application.task_docs import task_sprint_archive
from agents_remember.application.task_docs.task_retirement_shared import (
    archive_hook,
    explainer_line,
    hook_failed,
)
from agents_remember.application.task_docs.task_sprint_context import SprintLinkageRequest
from agents_remember.canvas.explainer_archive import (
    ArchiveRun,
    environments,
    remove_environment,
    retire_environments,
)
from agents_remember.canvas.explainer_grades import GradeStore, ScalePoint, grades_path
from agents_remember.canvas.explainer_paths import (
    explainer_task_folder,
    generation_path,
    lock_path,
    retire_receipts_root,
)
from agents_remember.canvas.explainer_state import (
    ExplainerBinding,
    ProcessTree,
    RetireReceipt,
    agent_id,
    read_receipt,
    write_binding,
    write_receipt,
)
from agents_remember.canvas.explainer_workspace import (
    RetirePlace,
    RetireStart,
    retire_workspace,
)
from agents_remember.cli import paseo_warmup, role_document_chats
from agents_remember.models.knowledge.review_frame import FrameBinding
from agents_remember.models.role_launcher import RoleSelection
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.worktrees.services import ReviewArtifactCleanupRequest
from agents_remember_test_support.testing.paseo_warmup import _live
from canvas_host_test_support import (
    LEAF,
    REPO,
    TASK,
    config,
    packet,
)
from canvas_host_test_support import (
    record as comparison,
)

pytestmark = pytest.mark.integration

NOW = "2026-10-22T08:00:00+00:00"
OTHER_LEAF = "260928-MIK-L105"


def _repository(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True, capture_output=True)
    return path


@dataclass(frozen=True)
class World:
    """A coordination root with one task folder, its repositories and its explainer folder."""

    settings: Any
    task_root: Path
    code: Path
    memory: Path

    @property
    def explainer_root(self) -> Path:
        return explainer_task_folder(self.settings.coordination_root, REPO, TASK)

    def request(self, *, dry_run: bool = False) -> SprintLinkageRequest:
        return SprintLinkageRequest(
            coordination_root=self.settings.coordination_root,
            repo_id=REPO,
            code_repository=self.code,
            memory_repository=self.memory,
            task_root=self.task_root,
            slug=None,
            fields={},
            dry_run=dry_run,
            config=self.settings,
        )

    def cleanup_request(self) -> ReviewArtifactCleanupRequest:
        """What a leaf finalize builds: the task, its repositories, and no explainer folder."""

        return ReviewArtifactCleanupRequest(
            task_root=self.task_root,
            task_name=TASK,
            code_repository=self.code,
            memory_repository=self.memory,
            dry_run=False,
        )


@pytest.fixture
def world(tmp_path: Path) -> World:
    return _world(tmp_path)


def _world(root: Path) -> World:
    settings = config(root)
    task_root = root / "tasks" / REPO / TASK
    (task_root / "notes" / "reports").mkdir(parents=True)
    return World(
        settings=settings,
        task_root=task_root,
        code=_repository(root / "code"),
        memory=_repository(root / "memory"),
    )


def environment(world: World, *, leaf: str = LEAF, generation: int = 1) -> Path:
    """One built environment: its workspace, its instructions and its seat-generation counter."""

    return build_environment(world.settings, TASK, leaf=leaf, generation=generation)


def build_environment(
    configuration: Any, task: str, *, leaf: str = LEAF, generation: int = 1
) -> Path:
    """One built environment of one task, ready to be retired (no host, no processes)."""

    workspace = explainer_task_folder(configuration.coordination_root, REPO, task) / leaf
    for name in ("instructions", "scratch", "drafts", "state"):
        (workspace / name).mkdir(parents=True, exist_ok=True)
    binding = FrameBinding.from_packet(packet(comparison(7)).comparison)
    write_binding(
        workspace,
        ExplainerBinding(
            binding=binding,
            snapshot="snapshot-1",
            prepared_at=NOW,
            packet_digest="sha256:" + "0" * 64,
        ),
    )
    counter = generation_path(configuration, REPO, task, leaf)
    counter.parent.mkdir(parents=True, exist_ok=True)
    counter.write_text(f"{generation}\n")
    return workspace


def seeded_grades(world: World, *, leaf: str = LEAF) -> Path:
    store = GradeStore(grades_path(world.task_root, leaf), leaf)
    # `define` takes the category and its scale; the clock is the store's, not a keyword callers pass.
    store.define(
        "risk",
        (
            ScalePoint(grade="high", meaning="it touches the leaf lock"),
            ScalePoint(grade="low", meaning="local to one module"),
        ),
    )
    return store.path


def pending_receipt(
    world: World, workspace: Path, *, leaf: str = LEAF, generation: int = 1
) -> Path:
    """The receipt a failed retire leaves behind: `pending`, naming its seat and its tree."""

    seat = agent_id(workspace, generation)
    receipt = RetireReceipt(
        receipt_id="pending-1",
        leaf_id=leaf,
        agent_id=seat,
        generation=generation,
        tree=ProcessTree(agent_id=seat, workspace=workspace.as_posix()),
        state="pending",
        step="archive",
        detail="the host was unreachable",
        recorded_at=NOW,
    )
    return write_receipt(retire_receipts_root(world.task_root, leaf), receipt)


def test_a_dry_run_lists_every_environment_and_retires_nothing(world: World) -> None:
    workspace = environment(world)
    grades = seeded_grades(world)
    lock = lock_path(world.settings, REPO, TASK, LEAF)
    lock.write_text("")

    report = archive_hook(world.request(dry_run=True), world.task_root)

    assert [entry["state"] for entry in report["environments"]] == ["would-retire"]
    entry = report["environments"][0]
    assert entry["leaf"] == LEAF
    assert entry["agentId"] == agent_id(workspace, 1)
    assert entry["generation"] == 1
    # Nothing was touched: the workspace, its counter, its grades and the host were left alone.
    assert (workspace / "instructions").is_dir()
    assert generation_path(world.settings, REPO, TASK, LEAF).read_text().strip() == "1"
    assert grades.is_file()
    assert not retire_receipts_root(world.task_root, LEAF).exists()
    assert lock.exists()
    # The workspace, its counter and its grades are listed; no receipt exists yet to list.
    assert [item["state"] for item in report["explainerArtifacts"]] == ["would-delete"] * 3
    assert explainer_line(report) is not None


def test_master_retirement_retires_and_reclaims_every_environment(world: World) -> None:
    workspace = environment(world)
    second = environment(world, leaf=OTHER_LEAF, generation=2)
    grades = seeded_grades(world)
    other_grades = seeded_grades(world, leaf=OTHER_LEAF)
    lock = lock_path(world.settings, REPO, TASK, LEAF)
    lock.write_text("")

    report = archive_hook(world.request(), world.task_root)

    assert {entry["leaf"]: entry["state"] for entry in report["environments"]} == {
        LEAF: "retired",
        OTHER_LEAF: "retired",
    }
    for target in (workspace, second):
        assert not target.exists()
    for counter in (LEAF, OTHER_LEAF):
        assert not generation_path(world.settings, REPO, TASK, counter).exists()
    assert not grades.exists() and not grades.parent.exists()
    assert not other_grades.exists()
    assert not retire_receipts_root(world.task_root, LEAF).exists()
    # The lock lies inside the workspace (MIK-R211 v5 rule 12): it goes with it, and nothing stays.
    assert not lock.exists()
    assert list(world.explainer_root.iterdir()) == []
    # The retire removed each workspace and the frames took each counter: grades and receipts stay.
    assert [item["kind"] for item in report["explainerArtifacts"]] == [
        "grades",
        "retire-receipts",
    ] * 2
    assert {item["state"] for item in report["explainerArtifacts"]} == {"deleted"}


def test_a_failed_retire_keeps_the_folder_receipt_and_grades(world: World) -> None:
    workspace = environment(world)
    grades = seeded_grades(world)

    def unreachable(seat: str) -> None:
        raise RuntimeError(f"the host does not answer for {seat}")

    with mock.patch.object(review_artifact_cleanup, "host_seat_archive", return_value=unreachable):
        report = archive_hook(world.request(), world.task_root)

    assert [entry["state"] for entry in report["environments"]] == ["pending"]
    assert hook_failed(report)
    assert workspace.is_dir() and (workspace / "instructions").is_dir()
    assert grades.is_file()
    receipts = retire_receipts_root(world.task_root, LEAF)
    kept = [read_receipt(receipts, path.stem) for path in receipts.glob("*.json")]
    # The counter lies beside the task's revisions and went with its frames; the pending receipt
    # names the seat and the generation the retry ends.
    assert [(item.state, item.generation) for item in kept if item is not None] == [("pending", 1)]
    assert {item["state"] for item in report["explainerArtifacts"]} == {"kept"}


def test_a_pending_receipt_is_finished_by_the_next_archive(world: World) -> None:
    workspace = environment(world)
    grades = seeded_grades(world)
    # The receipt, not the counter, names the seat a retry must end.
    pending_receipt(world, workspace, generation=3)

    report = archive_hook(world.request(), world.task_root)

    entry = report["environments"][0]
    assert entry["state"] == "retired"
    assert entry["generation"] == 3
    assert entry["agentId"] == agent_id(workspace, 3)
    assert not workspace.exists()
    assert not grades.exists()
    assert not retire_receipts_root(world.task_root, LEAF).exists()


def test_an_interrupted_external_deletion_is_attributed_already_absent_from_exact_targets(
    world: World,
) -> None:
    """S22: a workspace and counter deleted externally while an attempt was interrupted are
    attributed ``alreadyAbsent`` on the retry -- from the exact confined explainer targets, and
    never for a path outside the confinement."""

    workspace = environment(world)
    grades = seeded_grades(world)
    counter = generation_path(world.settings, REPO, TASK, LEAF)
    outside = world.settings.coordination_root / "outside"
    outside.mkdir()
    outside_keep = outside / "keep.txt"
    outside_keep.write_text("mine")
    receipts = world.task_root / "notes" / "reports"
    receipts.mkdir(parents=True, exist_ok=True)

    def artifact(path: Path, kind: str) -> dict[str, Any]:
        return {"path": path.as_posix(), "leaf": LEAF, "kind": kind, "state": "would-delete"}

    # The interrupted attempt's receipt, in the shape the ledger itself writes before it deletes
    # anything (`ReceiptLedger.begin`): the planned deletions nested under ``planned``. It had
    # listed the workspace, the counter, the grades and one path outside the explainer's
    # confinement, and never reached its outcome.
    (receipts / "review-artifact-cleanup.json").write_text(
        json.dumps(
            {
                "schema": "ar-review-artifact-cleanup/v1",
                "state": "in-progress",
                "taskId": TASK,
                "reason": "archived",
                "attempt": 1,
                "planned": {
                    "reviewRefs": [],
                    "retainedCodeRefs": [],
                    "releasedGenerations": [],
                    "datasetCopies": [],
                    "arCaches": [],
                    "frameRevisions": [],
                    "environments": [],
                    "explainerArtifacts": [
                        artifact(workspace, "workspace"),
                        artifact(counter, "generation"),
                        artifact(grades.parent, "grades"),
                        artifact(outside_keep, "grades"),
                    ],
                },
            }
        ),
        encoding="utf-8",
    )
    # The external deletion interrupts the attempt: the workspace and its counter are gone, the
    # grades file is still there.
    shutil.rmtree(workspace)
    counter.unlink()

    report = archive_hook(world.request(), world.task_root)

    absent = {row["artifact"]: row for row in report["alreadyAbsent"]}
    assert absent[str(workspace)]["interrupted"] is True
    assert absent[str(workspace)]["kind"] == "explainerArtifacts"
    assert absent[str(counter)]["interrupted"] is True
    # The still-present grades are not claimed gone (this attempt deletes them), and a path outside
    # the confinement is never attributed: it fails the admission, so it reads as still there.
    assert str(grades.parent) not in absent
    assert str(outside_keep) not in absent
    assert outside_keep.read_text() == "mine"
    assert not hook_failed(report)


def test_a_symlink_under_the_explainer_task_root_is_held(world: World) -> None:
    workspace = environment(world)
    outside = world.settings.coordination_root / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("mine")
    (world.explainer_root / "9999-not-a-leaf").symlink_to(outside, target_is_directory=True)

    report = archive_hook(world.request(), world.task_root)

    assert (outside / "keep.txt").read_text() == "mine"
    assert any("symlink" in entry["detail"] for entry in report["failures"])
    # The held path stops nothing else: the real leaf was still retired.
    assert [entry["state"] for entry in report["environments"]] == ["retired"]
    assert not workspace.exists()


def test_a_symlink_inside_a_workspace_holds_the_leaf_and_what_lies_behind_it(
    world: World,
) -> None:
    workspace = environment(world)
    outside = world.settings.coordination_root / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("mine")
    (workspace / "leak").symlink_to(outside, target_is_directory=True)

    report = archive_hook(world.request(), world.task_root)

    assert (outside / "keep.txt").read_text() == "mine"
    assert workspace.joinpath("leak").is_symlink()
    assert any("symlink" in entry["detail"] for entry in report["failures"])


def test_retire_alone_removes_the_workspace_and_keeps_grades_and_receipts(world: World) -> None:
    workspace = environment(world)
    grades = seeded_grades(world)
    receipts = retire_receipts_root(world.task_root, LEAF)

    outcome = retire_workspace(
        RetireStart(
            place=RetirePlace(workspace=workspace, receipts_root=receipts, now=NOW),
            leaf_id=LEAF,
            generation=1,
            receipt_id="retire-1",
        )
    )

    assert outcome.receipt.state == "finished"
    assert not workspace.exists()
    assert sorted(path.name for path in world.explainer_root.iterdir()) == []
    assert grades.is_file()
    assert read_receipt(receipts, "retire-1") is not None


def test_a_leaf_finalize_cleanup_does_not_retire(world: World) -> None:
    workspace = environment(world)
    grades = seeded_grades(world)
    counter = generation_path(world.settings, REPO, TASK, LEAF)
    revision = counter.parent / "revisions" / "1" / "index.html"
    revision.parent.mkdir(parents=True)
    revision.write_text("<p>revision 1</p>\n")

    document = cleanup_review_artifacts(world.cleanup_request())

    assert document["environments"] == []
    assert document["explainerArtifacts"] == []
    assert (workspace / "instructions").is_dir()
    assert grades.is_file()
    assert not retire_receipts_root(world.task_root, LEAF).exists()
    # A finalize is never more destructive than a removal: the explainer's published revisions and
    # the seat counter beside them stay with the task (Manager ruling 20261027T144320Z (2)).
    assert counter.read_text() == "1\n"
    assert revision.read_text() == "<p>revision 1</p>\n"


def test_the_cleanup_port_does_not_retire(world: World) -> None:
    workspace = environment(world)

    document = ReviewArtifactCleanup().cleanup(world.cleanup_request())

    assert document["environments"] == []
    assert workspace.is_dir()


def test_the_cleanup_keeps_a_pending_leaves_own(world: World) -> None:
    workspace = environment(world)
    grades = seeded_grades(world)
    pending_receipt(world, workspace)

    found, held = environments(world.explainer_root, world.task_root)
    assert held == [] and [item.pending for item in found] != []
    entries, failures = remove_environment(
        found[0], task_root=world.task_root, admit=lambda path: None, dry_run=False
    )

    assert failures == []
    assert {entry["state"] for entry in entries} == {"kept"}
    assert workspace.is_dir() and grades.is_file()
    assert generation_path(world.settings, REPO, TASK, LEAF).exists()


def test_the_warmup_never_resumes_an_explainer_seat(world: World, tmp_path: Path) -> None:
    workspace = environment(world)
    seat = agent_id(workspace, 1)
    receipt = _worker_receipt(seat)
    selection = RoleSelection(role="worker", taskDocumentRef=_leaf_ref())

    def ask(config: Any) -> list[str]:
        return paseo_warmup.tree_agent_ids(config, _live("srv-1", [seat]), receipts={})

    with (
        mock.patch.object(paseo_warmup, "tree_selections", return_value=[selection]),
        mock.patch.object(paseo_warmup, "_selection_records", return_value=[receipt]),
    ):
        assert ask(world.settings) == []
        # The same record in a coordination root with no environment is a tree seat, so it is the
        # exclusion that dropped it above.
        bare = _world(tmp_path / "bare")
        assert ask(bare.settings) == [seat]


def test_the_chat_list_lists_the_explainer_after_a_live_seat(world: World) -> None:
    workspace = environment(world)
    other_master = "261001_other-master"
    other_leaf = "261001-OTH-L183"
    build_environment(world.settings, other_master, leaf=other_leaf, generation=1)
    slug_name = "183_the-review-pages-explainer.json"
    (world.task_root / slug_name).write_text(f'{{"id": "{LEAF}"}}\n', encoding="utf-8")
    other_task_root = world.settings.coordination_root / "tasks" / REPO / other_master
    other_task_root.mkdir(parents=True, exist_ok=True)
    (other_task_root / slug_name).write_text(f'{{"id": "{other_leaf}"}}\n', encoding="utf-8")
    slug_ref = TaskDocumentRef(repository=REPO, path=f"{TASK}/{slug_name}")
    worker = _worker_receipt("worker-agent")
    live = SimpleNamespace(reachable=True, found=True, archived=False, unreachable_reason=None)

    with (
        mock.patch.object(role_document_chats, "_records", return_value=[worker]),
        mock.patch.object(role_document_chats, "read_agent", return_value=live),
    ):
        answer = role_document_chats.document_chats(
            world.settings, RoleSelection(role="worker", taskDocumentRef=slug_ref), 0
        )

    assert [row["labels"]["ar.role"] for row in answer["agents"]] == ["worker", "Explainer"]
    assert answer["agents"][1]["agentId"] == agent_id(workspace, 1)
    assert answer["agents"][1]["labels"]["ar.task-ref"] == f"{REPO}/{TASK}/{slug_name}"


def test_the_previews_name_the_environments(world: World) -> None:
    environment(world)

    report = archive_hook(world.request(dry_run=True), world.task_root)
    line = explainer_line(report)
    assert line is not None and LEAF in line and "retire 1" in line
    item = {"report": report, "remainder": 0, "failed": False, "receipt_line": None}
    changes: list[str] = []
    task_sprint_archive._preview_cleanup_changes(
        item, world.task_root, world.task_root, changes, []
    )
    assert any("explainer" in change and LEAF in change for change in changes)

    dry = task_sprint_archive._dry_cleanup(
        world.request(dry_run=True), world.task_root, world.task_root
    )
    assert [entry["state"] for entry in dry["report"]["environments"]] == ["would-retire"]


def test_the_archive_reports_the_environments_in_its_receipt(world: World) -> None:
    workspace = environment(world)
    seeded_grades(world)
    counter = generation_path(world.settings, REPO, TASK, LEAF)
    for number in (1, 2):
        revision = counter.parent / "revisions" / str(number) / "index.html"
        revision.parent.mkdir(parents=True)
        revision.write_text(f"<p>revision {number}</p>\n")

    report = archive_hook(world.request(), world.task_root)

    receipts = world.task_root / "notes" / "reports" / "review-artifact-cleanup.json"
    recorded = receipts.read_text()
    assert agent_id(workspace, 1) in recorded
    # R103 v2: the receipt names what the archive took of the explainer, and why a reopen is gone.
    [named] = json.loads(recorded)["explainerRecords"]
    assert {key: named[key] for key in ("leaf", "revisions", "seatCounter", "generation")} == {
        "leaf": LEAF,
        "revisions": [1, 2],
        "seatCounter": counter.as_posix(),
        "generation": 1,
    }
    assert named["state"] == "reclaimed"
    assert "cannot be reopened" in named["reopen"] and "revisions 1, 2" in named["reopen"]
    assert not counter.exists() and not counter.parent.exists()
    # The retire removed the workspace and the task's frames took its counter beside them.
    assert [entry["kind"] for entry in report["explainerArtifacts"]] == [
        "grades",
        "retire-receipts",
    ]
    assert list(world.explainer_root.iterdir()) == []


def _leaf_ref() -> TaskDocumentRef:
    return TaskDocumentRef(repository=REPO, path=f"{TASK}/{LEAF}.json")


def _worker_receipt(seat: str) -> dict[str, Any]:
    ref = {"repository": REPO, "path": f"{TASK}/{LEAF}.json"}
    return {
        "role": "worker",
        "selection": {
            "role": "worker",
            "sprintDocumentRef": None,
            "masterDocumentRef": None,
            "taskDocumentRef": ref,
        },
        "createdAt": NOW,
        "execution": {"kind": "paseo-agent", "agentId": seat, "workspaceId": "wks-leaf"},
    }


def test_retire_environments_leaves_a_task_without_environments_alone(world: World) -> None:
    report = retire_environments(
        world.explainer_root, ArchiveRun(task_root=world.task_root, now=NOW)
    )
    assert report == {"environments": [], "failures": []}
