"""MIK-R25 rule 5: the archive hook deletes only the archived task's own review artifacts.

The fixture is a coordination task root with its task document and one leaf enclosure contract, a
code and a memory repository, and nothing converted: the hook reads refs, manifests and files, never
knowledge. Every case asserts what is deleted and, as much, what another task still holds.

The identity rules under test (R1-R5 rulings):

* review refs are ``refs/ar/review/<task directory>/…`` -- nothing read from the task folder names
  that namespace;
* legacy retained-code pins and a manifest's own-leaf check follow the trust line: ``task.json``'s
  ``id`` counts when a leaf contract of this task carries ``<id>-L<n>``;
* every path the hook deletes, releases or writes is physically inside the task (no symlink).
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest import mock

import apsw
import pytest
from agents_remember.application import review_artifact_cleanup, review_artifact_receipts
from agents_remember.application.review_artifact_cleanup import (
    CLEANUP_REPORT_NAME,
    ReviewArtifactCleanup,
    cleanup_review_artifacts,
)
from agents_remember.errors import CodeObjectRetentionError
from agents_remember.worktrees.modules import finalize
from agents_remember.worktrees.services import (
    ReviewArtifactCleanupRequest,
    WorktreeServices,
    bind_worktree_services,
    reset_worktree_services,
)
from agents_remember.worktrees.worktree_contract import load_contract

REPO = "agents-remember"
MASTER = "260101_tree_review"
TASK = "260101-TRV"
LEAF = "260101-TRV-L1"
OWN_PIN = "refs/ar/retained-code/260101-trv-l1/g1"


def git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(root), "GIT_CONFIG_NOSYSTEM": "1"},
    )
    if result.returncode != 0:
        raise AssertionError(f"fixture git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def _repository(root: Path) -> Path:
    root.mkdir(parents=True)
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "fixture@example.invalid")
    git(root, "config", "user.name", "archive fixture")
    (root / "file.txt").write_text("x\n", encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "fixture")
    return root


@dataclass
class Task:
    root: Path
    code: Path
    memory: Path
    task_root: Path

    @property
    def head(self) -> str:
        return git(self.code, "rev-parse", "HEAD")

    @property
    def tree(self) -> str:
        return git(self.memory, "rev-parse", "HEAD^{tree}")

    def leaf_contract(self, leaf_id: str = LEAF) -> Path:
        enclosure = self.task_root / "enclosures" / leaf_id.lower()
        enclosure.mkdir(parents=True, exist_ok=True)
        path = enclosure / "series-contract.md"
        path.write_text(
            "\n".join(
                [
                    "---",
                    "schema: ar-series-contract/v1",
                    "schemaVersion: 1.0",
                    "kind: leaf",
                    "task_id: 260101_TREE-REVIEW",
                    f"task_name: {MASTER}",
                    f"repo_name: {REPO}",
                    "workflow_kind: light-task",
                    "memory_mode: external",
                    "",
                    "coordination:",
                    f"  root: {self.task_root.parents[2]}",
                    f"  task_root: {self.task_root}",
                    f"  task_artifact: {self.task_root / 'task.md'}",
                    f"  worktree_group: {self.root / 'group'}",
                    f"  leaf_id: {leaf_id}",
                    f"  parent_task_name: {MASTER}",
                    "",
                    "code:",
                    f"  repo_path: {self.code}",
                    "  source_branch: main",
                    "  work_branch: leaf",
                    f"  base_commit: {self.head}",
                    f"  worktree: {self.root / 'group' / 'code'}",
                    "",
                    "memory:",
                    "  mode: external",
                    f"  repo_path: {self.memory}",
                    "  source_branch: main",
                    "  work_branch: leaf",
                    f"  base_commit: {git(self.memory, 'rev-parse', 'HEAD')}",
                    f"  worktree: {self.root / 'group' / 'memory'}",
                    f"  ledger: {self.root / 'group' / 'memory' / 'memory.md'}",
                    "---",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        return path

    def pin(self, ref: str, *, memory: bool = True) -> None:
        git(self.code, "update-ref", ref, self.head)
        if memory:
            git(self.memory, "update-ref", ref, self.tree)

    def archive(self, **fields: Any) -> dict[str, Any]:
        request = {
            "task_root": self.task_root,
            "task_name": MASTER,
            "code_repository": self.code,
            "memory_repository": self.memory,
            "dry_run": False,
            **fields,
        }
        return cleanup_review_artifacts(ReviewArtifactCleanupRequest(**request))


@pytest.fixture
def task(tmp_path: Path) -> Task:
    task_root = tmp_path / "coordination" / "tasks" / REPO / MASTER
    task_root.mkdir(parents=True)
    (task_root / "task.json").write_text(json.dumps({"id": TASK}), encoding="utf-8")
    (task_root / "task.md").write_text("# task\n", encoding="utf-8")
    made = Task(
        tmp_path, _repository(tmp_path / "code"), _repository(tmp_path / "memory"), task_root
    )
    made.leaf_contract()
    return made


@pytest.fixture(autouse=True)
def _no_bound_services() -> Iterator[None]:
    yield
    reset_worktree_services()


def _refs(repository: Path) -> dict[str, str]:
    listed = git(repository, "for-each-ref", "--format=%(refname) %(objectname)", "refs/ar/")
    return dict(line.split(" ", 1) for line in listed.splitlines() if line)


def _knowledge_dataset(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = apsw.Connection(str(path))
    connection.execute(
        "CREATE TABLE invariant (id TEXT); CREATE TABLE invariant_revision (id TEXT)"
    )
    connection.close()


def _manifest(
    task: Task, pin: str, *, leaf: str = LEAF, repository: Path | None = None, snapshot: str = ""
) -> SimpleNamespace:
    return SimpleNamespace(
        leaf_id=leaf,
        generation_id="g1",
        source=SimpleNamespace(
            retained=SimpleNamespace(ref=pin),
            code_repository_root=str(repository or task.code),
        ),
        knowledge=(
            (SimpleNamespace(artifact=SimpleNamespace(relative_path=snapshot)),) if snapshot else ()
        ),
    )


def _generation(task: Task, leaf: str = LEAF) -> tuple[Path, Path]:
    generation = task.task_root / "notes/reports/comparison-generations" / leaf.lower() / "g1"
    snapshot = generation / "knowledge" / "after" / "snapshot.sqlite"
    _knowledge_dataset(snapshot)
    (generation / "manifest.json").write_text("{}", encoding="utf-8")
    return generation, snapshot


def _owners(manifest: SimpleNamespace) -> tuple[Any, mock.Mock, mock.Mock]:
    release, discard = mock.Mock(), mock.Mock(return_value=())
    patches = (
        mock.patch.object(review_artifact_cleanup, "read_manifest", return_value=manifest),
        mock.patch.object(review_artifact_cleanup, "release_comparison_code_object", release),
        mock.patch.object(review_artifact_cleanup, "discard_comparison_snapshots", discard),
    )
    return patches, release, discard


# -- what archival deletes, through the finalizer --------------------------------------------------------


def test_archiving_deletes_the_tasks_review_refs_legacy_pins_and_dataset_copies(
    task: Task,
) -> None:
    ours = f"refs/ar/review/{MASTER}/{LEAF}/1"
    task.pin(ours)
    task.pin(f"refs/ar/review/{TASK}/{LEAF}/1")  # the task.json id names no review namespace
    for name in (
        OWN_PIN,
        "refs/ar/retained-code/260101-trvx-l1/g1",
        "refs/ar/retained-code/260101-trv-extra-l01/g1",
        "refs/ar/review/260101-TRV-EXTRA/260101-TRV-EXTRA-L1/1",
    ):
        task.pin(name, memory=False)
    notes = task.task_root / "notes" / "reports"
    copy = notes / "comparison-generations/l1/g1/knowledge/after/snapshot.sqlite"
    renamed = notes / "scratch" / "pub2.sqlite"
    _knowledge_dataset(copy)
    _knowledge_dataset(renamed)
    other = notes / "other.sqlite"
    sqlite3.connect(other).execute("CREATE TABLE t (x)").connection.close()
    preview = task.archive(dry_run=True)
    assert preview["state"] == "would-delete" and copy.exists()
    assert len(preview["reviewRefs"]) == 2 and len(preview["datasetCopies"]) == 2
    bind_worktree_services(
        WorktreeServices(
            provider_lifecycle=mock.Mock(),
            memory_quality=mock.Mock(),
            citation_guard=mock.Mock(),
            review_artifact_cleanup=ReviewArtifactCleanup(),
        )
    )
    archive = finalize._with_review_artifact_cleanup(
        load_contract(task.leaf_contract()),
        {"state": "archived", "archivePath": str(task.task_root)},
        dry_run=False,
    )
    report = archive["reviewArtifacts"]
    assert isinstance(report, dict) and report["state"] == "deleted"
    assert {entry["ref"] for entry in report["reviewRefs"]} == {ours}
    assert [entry["ref"] for entry in report["retainedCodeRefs"]] == [OWN_PIN]
    head = task.head
    assert _refs(task.code) == {
        f"refs/ar/review/{TASK}/{LEAF}/1": head,
        "refs/ar/retained-code/260101-trvx-l1/g1": head,
        "refs/ar/retained-code/260101-trv-extra-l01/g1": head,
        "refs/ar/review/260101-TRV-EXTRA/260101-TRV-EXTRA-L1/1": head,
    }
    assert _refs(task.memory) == {f"refs/ar/review/{TASK}/{LEAF}/1": task.tree}
    assert not copy.exists() and not renamed.exists() and other.exists()
    recorded = json.loads((notes / CLEANUP_REPORT_NAME).read_text())
    assert recorded["reviewRefs"] == report["reviewRefs"] and not recorded["failures"]


@pytest.mark.parametrize(
    ("task_id", "own", "other"),
    [
        ("260906-IAS", "260906-ias-l3", "260906-ias-memory-recovery-l01"),
        ("260928-MIK", "260928-mik-l25", "260928-mik-extra-l01"),
    ],
)
def test_archiving_one_task_never_selects_a_colliding_tasks_pins(
    task: Task, task_id: str, own: str, other: str
) -> None:
    (task.task_root / "task.json").write_text(json.dumps({"id": task_id}), encoding="utf-8")
    task.leaf_contract(f"{task_id}-L3")
    for leaf in (own, other):
        task.pin(f"refs/ar/retained-code/{leaf}/g1", memory=False)
    report = task.archive()
    assert [entry["ref"] for entry in report["retainedCodeRefs"]] == [
        f"refs/ar/retained-code/{own}/g1"
    ]
    assert _refs(task.code) == {f"refs/ar/retained-code/{other}/g1": task.head}


# -- identity: the review-ref namespace and the trust line -----------------------------------------------


def test_a_comparison_record_naming_another_task_never_widens_the_archive(task: Task) -> None:
    ours = f"refs/ar/review/{MASTER}/{LEAF}/1"
    task.pin(ours)
    task.pin("refs/ar/review/OTHER-1/OTHER-1-L1/1")
    planted = task.task_root / "notes/reports/review-comparisons/planted/1.json"
    planted.parent.mkdir(parents=True)
    planted.write_text(json.dumps({"task_id": "OTHER-1", "leaf_id": "OTHER-1-L1"}))
    report = task.archive()
    assert {entry["ref"] for entry in report["reviewRefs"]} == {ours}
    assert _refs(task.code) == {"refs/ar/review/OTHER-1/OTHER-1-L1/1": task.head}
    assert _refs(task.memory) == {"refs/ar/review/OTHER-1/OTHER-1-L1/1": task.tree}


def test_a_task_document_naming_another_task_touches_none_of_its_refs(task: Task) -> None:
    (task.task_root / "task.json").write_text(json.dumps({"id": "OTHER-1"}), encoding="utf-8")
    task.pin("refs/ar/review/OTHER-1/OTHER-1-L1/1")
    task.pin("refs/ar/retained-code/other-1-l1/g1", memory=False)
    report = task.archive()
    assert report["reviewRefs"] == [] and report["retainedCodeRefs"] == []
    assert _refs(task.code) == {
        "refs/ar/review/OTHER-1/OTHER-1-L1/1": task.head,
        "refs/ar/retained-code/other-1-l1/g1": task.head,
    }
    assert _refs(task.memory) == {"refs/ar/review/OTHER-1/OTHER-1-L1/1": task.tree}
    assert any(one["target"] == "task.json" for one in report["failures"])
    assert report["reportPath"] == str(task.task_root / "notes/reports" / CLEANUP_REPORT_NAME)


def test_an_unconfirmed_task_id_is_a_failure_only_while_a_pin_carries_it(task: Task) -> None:
    """A task that never had a leaf enclosure: its id names no pin, so nothing stays untouched."""
    (task.task_root / "task.json").write_text(json.dumps({"id": "OTHER-1"}), encoding="utf-8")
    for enclosure in (task.task_root / "enclosures").iterdir():
        (enclosure / "series-contract.md").unlink()
    clean = task.archive()
    assert clean["failures"] == [] and clean["state"] == "deleted"
    task.pin("refs/ar/retained-code/other-1-l1/g1", memory=False)
    held = task.archive()
    assert [one["target"] for one in held["failures"]] == ["task.json"]
    assert held["state"] == "partial" and held["retainedCodeRefs"] == []
    assert _refs(task.code) == {"refs/ar/retained-code/other-1-l1/g1": task.head}


def test_a_planted_leaf_contract_never_reaches_another_tasks_review_refs(task: Task) -> None:
    """R5 V10: a planted contract confirms the id only along the ruled trust line (legacy pins)."""

    (task.task_root / "task.json").write_text(json.dumps({"id": "OTHER-1"}), encoding="utf-8")
    task.leaf_contract("OTHER-1-L1")
    task.pin("refs/ar/review/OTHER-1/OTHER-1-L1/1")
    task.pin("refs/ar/retained-code/other-1-l1/g1", memory=False)
    report = task.archive()
    # The review-ref namespace is the directory name: nothing under OTHER-1 is reached.
    assert report["reviewRefs"] == []
    assert _refs(task.memory) == {"refs/ar/review/OTHER-1/OTHER-1-L1/1": task.tree}
    assert "refs/ar/review/OTHER-1/OTHER-1-L1/1" in _refs(task.code)
    # The legacy pin follows the accepted trust line: the control documents name it as the task's.
    assert [entry["ref"] for entry in report["retainedCodeRefs"]] == [
        "refs/ar/retained-code/other-1-l1/g1"
    ]


# -- generations the hook must not release ---------------------------------------------------------------


def test_the_archive_hook_never_raises_and_holds_a_generation_its_owner_refused(
    task: Task,
) -> None:
    task.pin(OWN_PIN, memory=False)
    _, snapshot = _generation(task)
    refusal = CodeObjectRetentionError("code-object-ref-moved", "the pin moved")
    with (
        mock.patch.object(
            review_artifact_cleanup, "read_manifest", return_value=_manifest(task, OWN_PIN)
        ),
        mock.patch.object(
            review_artifact_cleanup, "release_comparison_code_object", side_effect=refusal
        ),
    ):
        report = task.archive(memory_repository=task.root / "moved-away")
    # The refused generation's pin and snapshot are both left, and reported.
    assert _refs(task.code) == {OWN_PIN: task.head} and snapshot.exists()
    held = next(one for one in report["failures"] if one.get("ref") == OWN_PIN)
    assert "held, not deleted" in held["detail"] and "the pin moved" in held["detail"]
    # A repository that is gone is a failure entry, not an exception.
    assert {
        "target": (task.root / "moved-away").as_posix(),
        "detail": "the repository does not exist",
    } in report["failures"]
    assert report["state"] == "partial"
    # And the finalizer reports even a hook that raises, after the task was archived.
    broken = mock.Mock()
    broken.cleanup.side_effect = FileNotFoundError("gone")
    bind_worktree_services(
        WorktreeServices(
            provider_lifecycle=mock.Mock(),
            memory_quality=mock.Mock(),
            citation_guard=mock.Mock(),
            review_artifact_cleanup=broken,
        )
    )
    archive = finalize._with_review_artifact_cleanup(
        load_contract(task.leaf_contract()),
        {"state": "archived", "archivePath": str(task.task_root)},
        dry_run=False,
    )
    assert archive["reviewArtifacts"] == {"state": "failed", "detail": "FileNotFoundError: gone"}


@pytest.mark.parametrize(
    "case",
    [
        ("260921-ICR-L48", "260921-icr-l48", False, "", "is not a leaf of task"),
        (LEAF, "260921-icr-l48", False, "", "is not a pin of task"),
        (LEAF, "260101-trv-l1", True, "", "not this task's"),
        (LEAF, "260101-trv-l1", False, "../../../../../x.sqlite", "leaves the generation"),
    ],
)
def test_a_planted_foreign_manifest_releases_nothing(
    task: Task, case: tuple[str, str, bool, str, str]
) -> None:
    leaf, pin_leaf, foreign_repository, snapshot_path, reason = case
    pin = f"refs/ar/retained-code/{pin_leaf}/g1"
    task.pin(pin, memory=False)
    generation, snapshot = _generation(task, leaf)
    manifest = _manifest(
        task,
        pin,
        leaf=leaf,
        repository=task.memory if foreign_repository else None,
        snapshot=snapshot_path,
    )
    patches, release, discard = _owners(manifest)
    with patches[0], patches[1], patches[2]:
        report = task.archive()
    release.assert_not_called()
    discard.assert_not_called()
    assert snapshot.exists()
    held = next(one for one in report["failures"] if one["target"] == generation.as_posix())
    assert reason in held["detail"] and "held, not deleted" in held["detail"]
    # Another task's pin survives; so does this task's own pin when a held generation names it.
    assert _refs(task.code) == {pin: task.head}


# -- physical confinement -----------------------------------------------------------------------------


def test_the_content_scan_never_follows_a_symlink_out_of_the_task(task: Task) -> None:
    outside = task.root / "elsewhere" / "knowledge.sqlite"
    _knowledge_dataset(outside)
    link = task.task_root / "notes" / "reports" / "linked.sqlite"
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(outside)
    report = task.archive()
    assert outside.exists() and link.is_symlink()
    assert report["datasetCopies"] == []


def test_a_symlinked_notes_root_is_neither_scanned_nor_written(task: Task) -> None:
    other = task.root / "other-task" / "notes"
    victim = other / "reports" / "pub2.sqlite"
    _knowledge_dataset(victim)
    (task.task_root / "notes").symlink_to(other)
    report = task.archive()
    assert victim.exists(), "a dataset behind a symlinked notes root was deleted"
    assert not (other / "reports" / CLEANUP_REPORT_NAME).exists()
    assert report["reportPath"] is None and report["datasetCopies"] == []
    assert any("not scanned" in one["detail"] for one in report["failures"])


def test_a_symlinked_generation_directory_releases_nothing(task: Task) -> None:
    task.pin(OWN_PIN, memory=False)
    elsewhere = task.root / "backup" / "g1"
    snapshot = elsewhere / "knowledge" / "after" / "snapshot.sqlite"
    _knowledge_dataset(snapshot)
    (elsewhere / "manifest.json").write_text("{}", encoding="utf-8")
    leaf_root = task.task_root / "notes/reports/comparison-generations/260101-trv-l1"
    leaf_root.mkdir(parents=True)
    (leaf_root / "g1").symlink_to(elsewhere)
    patches, release, discard = _owners(
        _manifest(task, OWN_PIN, snapshot="knowledge/after/snapshot.sqlite")
    )
    with patches[0], patches[1], patches[2]:
        report = task.archive()
    release.assert_not_called()
    discard.assert_not_called()
    assert snapshot.exists() and not (elsewhere / "deletions").exists()
    assert _refs(task.code) == {OWN_PIN: task.head}
    held = next(one for one in report["failures"] if one.get("ref") == OWN_PIN)
    assert "is a symlink" in held["detail"]


def test_a_repeated_attempt_keeps_receipts_never_leaves_the_name_empty_and_reports_absence(
    task: Task,
) -> None:
    reports = task.task_root / "notes/reports"
    task.pin(f"refs/ar/review/{MASTER}/{LEAF}/1")
    first = task.archive()
    assert first["attempt"] == 1 and first["alreadyAbsent"] == []
    canonical = reports / CLEANUP_REPORT_NAME
    kept = canonical.read_bytes()
    # A repeated request that finds nothing to delete or fail writes no new receipt.
    again = task.archive()
    assert again["receipt"] == "unchanged" and again["attempt"] == 1
    assert canonical.read_bytes() == kept and not list(reports.glob("*.attempt-*"))
    assert [e["artifact"] for e in again["alreadyAbsent"]] == [f"refs/ar/review/{MASTER}/{LEAF}/1"]
    # A later attempt with real work keeps the first receipt and replaces the canonical name
    # atomically, twice: with what it is about to delete, then with the outcome.
    task.pin(f"refs/ar/review/{MASTER}/{LEAF}/2")
    seen: list[tuple[bool, str]] = []
    write = review_artifact_receipts.atomic_write_text

    def watching(path: Path, text: str, **kwargs: Any) -> None:
        seen.append((canonical.exists(), json.loads(text)["state"]))
        write(path, text, **kwargs)

    with mock.patch.object(review_artifact_receipts, "atomic_write_text", watching):
        second = task.archive()
    assert second["attempt"] == 2 and seen == [(True, "in-progress"), (True, "deleted")]
    assert (reports / "review-artifact-cleanup.attempt-1.json").read_bytes() == kept
    assert json.loads(canonical.read_text())["attempt"] == 2


def _receipts(task: Task) -> dict[str, dict[str, Any]]:
    reports = task.task_root / "notes/reports"
    return {
        path.name: json.loads(path.read_text())
        for path in sorted(reports.glob("review-artifact-cleanup*.json"))
    }


def test_an_attempt_that_dies_after_deleting_has_left_its_receipt_before_it_deleted(
    task: Task,
) -> None:
    """The receipt is written ahead of the deletions, so a death in between loses no record."""
    first, second = (f"refs/ar/review/{MASTER}/{LEAF}/{n}" for n in (1, 2))
    task.pin(first)
    task.archive()
    task.pin(second)
    copy = task.task_root / "notes" / "left.sqlite"
    _knowledge_dataset(copy)
    with (
        mock.patch.object(
            review_artifact_receipts.ReceiptLedger, "finish", side_effect=KeyboardInterrupt
        ),
        pytest.raises(KeyboardInterrupt),
    ):
        task.archive()
    assert _refs(task.code) == {} and not copy.exists()
    interrupted = _receipts(task)[CLEANUP_REPORT_NAME]
    assert interrupted["state"] == "in-progress" and interrupted["attempt"] == 2
    assert [entry["ref"] for entry in interrupted["planned"]["reviewRefs"]] == [second, second]
    assert [entry["path"] for entry in interrupted["planned"]["datasetCopies"]] == [str(copy)]
    # The next attempt deletes nothing, reports what the dead attempt deleted, and numbers on.
    after = task.archive()
    assert after["attempt"] == 3 and after["reviewRefs"] == [] and after["datasetCopies"] == []
    assert {
        (entry["artifact"], entry["deletedInAttempt"], entry.get("interrupted", False))
        for entry in after["alreadyAbsent"]
    } == {(first, 1, False), (second, 2, True), (str(copy), 2, True)}
    kept = _receipts(task)
    assert sorted(kept) == [
        "review-artifact-cleanup.attempt-1.json",
        "review-artifact-cleanup.attempt-2.json",
        CLEANUP_REPORT_NAME,
    ]
    assert [receipt["attempt"] for receipt in kept.values()] == [1, 2, 3]
    assert kept["review-artifact-cleanup.attempt-2.json"] == interrupted
    # A further repeat has nothing to record.
    assert task.archive()["receipt"] == "unchanged" and _receipts(task) == kept


def test_a_death_between_setting_a_receipt_aside_and_writing_the_next_numbers_no_copy_twice(
    task: Task,
) -> None:
    task.pin(f"refs/ar/review/{MASTER}/{LEAF}/1")
    task.archive()
    task.pin(f"refs/ar/review/{MASTER}/{LEAF}/2")
    with (
        mock.patch.object(
            review_artifact_receipts, "atomic_write_text", side_effect=KeyboardInterrupt
        ),
        pytest.raises(KeyboardInterrupt),
    ):
        task.archive()
    # Receipt 1 was set aside and nothing was deleted, because receipt 2 was never written.
    assert list(_refs(task.code)) == [f"refs/ar/review/{MASTER}/{LEAF}/2"]
    names = ["review-artifact-cleanup.attempt-1.json", CLEANUP_REPORT_NAME]
    assert {name: r["attempt"] for name, r in _receipts(task).items()} == dict.fromkeys(names, 1)
    assert task.archive()["attempt"] == 2
    assert {name: r["attempt"] for name, r in _receipts(task).items()} == dict(
        zip(names, (1, 2), strict=True)
    )


def test_nothing_is_deleted_when_the_receipt_cannot_be_written(task: Task) -> None:
    pin = f"refs/ar/review/{MASTER}/{LEAF}/1"
    task.pin(pin)
    reports = task.task_root / "notes/reports"
    reports.mkdir(parents=True)
    reports.chmod(0o500)
    try:
        refused = task.archive()
    finally:
        reports.chmod(0o700)
    assert refused["state"] == "partial" and refused["reportPath"] is None
    assert refused["reviewRefs"] == [] and list(_refs(task.code)) == [pin]
    assert "nothing was deleted" in refused["failures"][-1]["detail"]
    assert refused["failures"][-1]["target"] == str(reports / CLEANUP_REPORT_NAME)
    done = task.archive()
    assert done["attempt"] == 1 and done["state"] == "deleted" and _refs(task.code) == {}
    assert [entry["ref"] for entry in done["reviewRefs"]] == [pin, pin]


def test_an_artifact_that_failed_and_is_gone_since_gets_a_receipt_once(task: Task) -> None:
    copy = task.task_root / "notes" / "busy.sqlite"
    _knowledge_dataset(copy)
    unlink = Path.unlink

    def busy(path: Path, *args: Any, **kwargs: Any) -> None:
        if path == copy:
            raise OSError("dataset is busy")
        unlink(path, *args, **kwargs)

    with mock.patch.object(Path, "unlink", autospec=True, side_effect=busy):
        failed = task.archive()
    assert failed["state"] == "partial" and failed["attempt"] == 1
    assert failed["failures"] == [{"target": str(copy), "detail": "dataset is busy"}]
    copy.unlink()  # removed by hand between the attempts
    gone = task.archive()
    assert gone["state"] == "deleted" and gone["failures"] == [] and gone["attempt"] == 2
    assert gone["alreadyAbsent"] == [
        {"artifact": str(copy), "kind": "failures", "failedInAttempt": 1}
    ]
    assert "receipt" not in gone and [r["attempt"] for r in _receipts(task).values()] == [1, 2]
    assert _receipts(task)[CLEANUP_REPORT_NAME]["alreadyAbsent"] == gone["alreadyAbsent"]
    again = task.archive()
    assert again["receipt"] == "unchanged" and again["attempt"] == 2
    assert [r["attempt"] for r in _receipts(task).values()] == [1, 2]
