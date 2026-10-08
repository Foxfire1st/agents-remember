"""MIK-R26 rule 6: the leftover copies of the retired knowledge database, and their one cleanup.

``agents-remember knowledge-copies`` finds every SQLite file under a coordination root that holds
the knowledge schema -- by content, whatever its name -- and names the rule that decides it. The
dry run changes nothing; ``--apply`` deletes exactly the copies rule 6 names. These cases build one
coordination root holding a copy for every rule and measure:

* each copy is listed under its rule, and a dry run leaves every byte and file as it was -- no
  journal, WAL or lock file appears beside a probed file;
* ``--apply`` deletes the provider-runtime, worktree-gone and archived-task-notes copies with the
  files the database route wrote beside them, and nothing else;
* Git-tracked files and derived indexes are skipped **by construction**: a tracked copy inside a
  ``provider-runtime`` directory is still skipped, the index cache directory is never entered even
  when a plain dataset sits in it, and a file with ``ix_*`` tables is never a copy;
* both orders of an archive: a task's copies are left while the task is not archived, fall under
  "notes of archived tasks" once its folder is under ``0_archive``, and are simply absent when the
  archive hook already deleted them;
* a symbolic link is never followed, read or deleted.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import apsw
import pytest
from agents_remember.application import legacy_dataset_copies
from agents_remember.application.legacy_dataset_copies import (
    is_derived_index,
    is_knowledge_dataset,
    plan_cleanup,
    run_cleanup,
    sqlite_table_names,
)
from agents_remember.cli.__main__ import main
from knowledge_rows_test_support import open_knowledge_store

RUNTIME = "provider-runtime/dev-ar-coordination/knowledge"
LIVE_NOTES = "tasks/proj/260101_live-task/notes/comparison-generations/L1/g1/knowledge/before"
DONE_NOTES = "tasks/proj/0_archive/260102_done-task/notes/comparison-generations/L2/g1/knowledge"

# Every copy the fixture root holds, with the rule that decides it.
EXPECTED = {
    f"{LIVE_NOTES}/snapshot.sqlite": "unarchived-task-notes",
    f"{DONE_NOTES}/after/snapshot.sqlite": "archived-task-notes",
    "tasks/proj/260101_live-task/scratch/pub2.sqlite": "other",
    f"worktrees/proj/group-ar/{RUNTIME}/baseline/knowledge-candidate.sqlite": "provider-runtime",
    f"worktrees/proj/group-ar/{RUNTIME}/candidate/knowledge-candidate.sqlite": "provider-runtime",
    "worktrees/proj/gone-ar/head.sqlite": "worktree-gone",
    "worktrees/proj/pruned-ar/checkout/knowledge.sqlite": "worktree-gone",
    "worktrees/proj/live-ar/checkout/knowledge.sqlite": "git-tracked",
    "worktrees/proj/live-ar/checkout/copy.sqlite": "other",
    f"worktrees/proj/live-ar/checkout/{RUNTIME}/tracked.sqlite": "git-tracked",
    "memory-repos/ar-proj/knowledge.sqlite": "git-tracked",
}
DELETED = {
    path
    for path, rule in EXPECTED.items()
    if rule in {"provider-runtime", "worktree-gone", "archived-task-notes"}
}


def _dataset(path: Path) -> Path:
    """One SQLite file holding the knowledge schema, whatever it is called."""

    open_knowledge_store(path, "11111111-1111-4111-8111-111111111111").close()
    return path


def _index(path: Path) -> Path:
    """A knowledge dataset that also holds an ``ix_*`` table: a derived index."""

    _dataset(path)
    connection = apsw.Connection(str(path))
    connection.execute("CREATE TABLE ix_uuid (uuid TEXT PRIMARY KEY, id TEXT, role TEXT)")
    connection.close()
    return path


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def _repository(root: Path, tracked: tuple[str, ...]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "fixture@example.invalid")
    _git(root, "config", "user.name", "cleanup fixture")
    for relative in tracked:
        _dataset(root / relative)
        _git(root, "add", "-f", relative)
    _git(root, "commit", "-q", "-m", "tracked dataset")


def coordination_root(tmp_path: Path) -> Path:
    root = tmp_path / "coordination"
    _dataset(root / LIVE_NOTES / "snapshot.sqlite")
    _dataset(root / DONE_NOTES / "after" / "snapshot.sqlite")
    _dataset(root / "tasks/proj/260101_live-task/scratch/pub2.sqlite")
    group = root / "worktrees/proj/group-ar" / RUNTIME
    baseline = _dataset(group / "baseline/knowledge-candidate.sqlite")
    for name in ("baseline-origin.json", "baseline-generation.json"):
        (baseline.parent / name).write_text("{}", encoding="utf-8")
    (baseline.parent / "knowledge-candidate.sqlite.lock").write_text("", encoding="utf-8")
    (baseline.parent / "notes.txt").write_text("not the database route's file", encoding="utf-8")
    candidate = _dataset(group / "candidate/knowledge-candidate.sqlite")
    (candidate.parent / "candidate-receipt.json").write_text("{}", encoding="utf-8")
    _dataset(root / "worktrees/proj/gone-ar/head.sqlite")
    pruned = root / "worktrees/proj/pruned-ar/checkout"
    _dataset(pruned / "knowledge.sqlite")
    (pruned / ".git").write_text("gitdir: /nowhere/.git/worktrees/pruned\n", encoding="utf-8")
    live = root / "worktrees/proj/live-ar/checkout"
    _repository(live, ("knowledge.sqlite", f"{RUNTIME}/tracked.sqlite"))
    _dataset(live / "copy.sqlite")
    _repository(root / "memory-repos/ar-proj", ("knowledge.sqlite",))
    # Not copies: a derived index in the cache, a plain dataset in the cache directory (never
    # entered), an index outside it, a SQLite file of another schema, text named like a database.
    _index(root / "runtime/knowledge-index/aaaa.sqlite")
    _dataset(root / "runtime/knowledge-index/misplaced.sqlite")
    _index(root / "runtime/elsewhere/index-copy.sqlite")
    other = apsw.Connection(str(root / "worktrees/proj/gone-ar/citations.sqlite3"))
    other.execute("CREATE TABLE source (path TEXT)")
    other.close()
    (root / "tasks/proj/260101_live-task/notes/named.sqlite").write_text("text", encoding="utf-8")
    # A link to a copy the rules would delete: never followed, read or deleted.
    (root / "worktrees/proj/gone-ar/link.sqlite").symlink_to(
        root / "worktrees/proj/gone-ar/head.sqlite"
    )
    return root


def _reviewed(root: Path) -> str:
    """The digest of the dry run a reviewer would have read."""

    return str(plan_cleanup(root)["reviewDigest"])


def tree(root: Path) -> dict[str, bytes | str]:
    """Every file and link under ``root`` outside Git's own directories, with its bytes."""

    found: dict[str, bytes | str] = {}
    for directory, names, files in os.walk(root):
        names[:] = [name for name in names if name != ".git"]
        for name in files:
            path = Path(directory) / name
            relative = path.relative_to(root).as_posix()
            found[relative] = f"-> {os.readlink(path)}" if path.is_symlink() else path.read_bytes()
    return found


def test_the_dry_run_names_the_rule_of_every_copy_and_changes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = coordination_root(tmp_path)
    before = tree(root)

    document = plan_cleanup(root)

    assert {copy["path"]: copy["rule"] for copy in document["copies"]} == EXPECTED
    assert set(document["wouldDelete"]) == DELETED
    assert document["deleted"] == [] and document["mode"] == "dry-run"
    assert document["before"]["files"] == len(EXPECTED) == document["after"]["files"]
    assert document["afterApply"]["files"] == len(EXPECTED) - len(DELETED)
    assert document["before"]["byRule"]["provider-runtime"]["files"] == 2
    assert document["before"]["byOwner"]["260101_live-task"]["files"] == 2
    assert document["before"]["byOwner"]["260102_done-task"]["files"] == 1
    # One index lies outside the cache; the cache directory itself was never entered.
    assert document["derivedIndexes"]["foundOutsideTheCache"] == 1
    assert tree(root) == before  # nothing written: no journal, WAL, lock or report file

    report = tmp_path / "report.json"
    assert (
        main(["knowledge-copies", "--coordination-root", str(root), "--report", str(report)]) == 0
    )
    printed = capsys.readouterr().out
    assert "dry run: nothing is changed" in printed
    for path, rule in EXPECTED.items():
        assert f"[{rule}] {path} " in printed
    assert "deleted: a copy in a provider-runtime directory" in printed
    assert "left: under the notes of a task that is not archived" in printed
    assert json.loads(report.read_text(encoding="utf-8"))["copies"] == document["copies"]
    assert tree(root) == before


def _companion_controls(root: Path) -> tuple[set[str], set[str], dict[str, str]]:
    """Protected and removable classes inside the fixture's existing Git checkout."""

    live = root / "worktrees/proj/live-ar/checkout"
    companion_names = (
        "copy.sqlite.lock",
        "copy.sqlite-wal",
        "copy.sqlite-shm",
        "copy.sqlite-journal",
        "candidate-receipt.json",
        "baseline-origin.json",
        "baseline-generation.json",
    )
    extra_copies: set[str] = set()
    ordinary: set[str] = set()
    protected: dict[str, str] = {}
    # Companion JSON is inert user content, not a fabricated receipt or role authority.
    for kind in ("tracked", "linked", "ordinary"):
        dataset = _dataset(live / RUNTIME / f"{kind}-companions/copy.sqlite")
        extra_copies.add(dataset.relative_to(root).as_posix())
        (dataset.parent / "user-notes.txt").write_text("keep unrelated bytes\n", encoding="utf-8")
        for name in companion_names:
            companion = dataset.parent / name
            relative = companion.relative_to(root).as_posix()
            if kind == "linked":
                target = live / "user-files" / f"{name}.txt"
                target.parent.mkdir(exist_ok=True)
                target.write_text("keep the link target\n", encoding="utf-8")
                companion.symlink_to(target)
                protected[relative] = "the companion is a symbolic link; its deletion was refused"
            else:
                companion.write_text("inert companion bytes\n", encoding="utf-8")
                if kind == "tracked":
                    _git(live, "add", "-f", companion.relative_to(live).as_posix())
                    protected[relative] = "the companion is Git-tracked; its deletion was refused"
                else:
                    ordinary.add(relative)
    _git(live, "commit", "-q", "-m", "protect inert companion files")
    return extra_copies, ordinary, protected


def test_apply_deletes_exactly_the_copies_rule_six_names_with_their_companions(
    tmp_path: Path,
) -> None:
    root = coordination_root(tmp_path)
    extra_copies, ordinary, protected = _companion_controls(root)
    live = root / "worktrees/proj/live-ar/checkout"
    before = tree(root)

    environment = {
        **os.environ,
        "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    command = [sys.executable, "-m", "agents_remember.cli", "knowledge-copies"]
    command += ["--coordination-root", str(root), "--json"]
    reviewed = tmp_path / "dry-run.json"
    dry = subprocess.run(
        [*command, "--report", str(reviewed)],
        capture_output=True,
        text=True,
        check=False,
        env=environment,
    )
    assert dry.returncode == 0 and tree(root) == before
    listed = json.loads(reviewed.read_text(encoding="utf-8"))
    completed = subprocess.run(
        [*command, "--apply", "--reviewed", str(reviewed)],
        capture_output=True,
        text=True,
        check=False,
        env=environment,
    )
    (tmp_path / "apply.stdout.json").write_text(completed.stdout, encoding="utf-8")
    (tmp_path / "apply.stderr.txt").write_text(completed.stderr, encoding="utf-8")
    # The apply did what the reviewed dry run listed, kept companions included: that is no failure.
    assert completed.returncode == 0 and not completed.stderr, completed.stderr
    document = json.loads(completed.stdout)

    assert set(document["deleted"]) == DELETED | extra_copies
    # Exactly what the reviewed dry run listed: the copies, the companions, and the kept ones.
    assert set(listed["wouldDelete"]) == set(document["deleted"])
    assert set(listed["wouldRemoveWithThem"]) == ordinary | {
        path for path in document["removedWithThem"] if not path.endswith("/")
    }
    assert {entry["path"]: entry["detail"] for entry in listed["wouldKeepCompanions"]} == protected
    assert document["reviewDigest"] == listed["reviewDigest"]
    assert {entry["path"]: entry["detail"] for entry in document["keptCompanions"]} == protected
    assert document["failures"] == [] and listed["keptCompanions"] == []
    runtime = f"worktrees/proj/group-ar/{RUNTIME}"
    assert set(document["removedWithThem"]) == ordinary | {
        f"{runtime}/baseline/baseline-origin.json",
        f"{runtime}/baseline/baseline-generation.json",
        f"{runtime}/baseline/knowledge-candidate.sqlite.lock",
        f"{runtime}/candidate/candidate-receipt.json",
        f"{runtime}/candidate/",  # emptied; baseline/ keeps a file the route did not write
        f"{DONE_NOTES}/after/",
        f"{DONE_NOTES}/",
        "tasks/proj/0_archive/260102_done-task/notes/comparison-generations/L2/g1/",
        "tasks/proj/0_archive/260102_done-task/notes/comparison-generations/L2/",
        "tasks/proj/0_archive/260102_done-task/notes/comparison-generations/",
    }
    after = tree(root)
    gone = set(before) - set(after)
    assert gone == DELETED | extra_copies | {
        path for path in document["removedWithThem"] if not path.endswith("/")
    }
    assert {path: after[path] for path in after} == {path: before[path] for path in after}
    assert (root / runtime / "baseline/notes.txt").is_file()
    assert (root / "tasks/proj/0_archive/260102_done-task/notes").is_dir()
    # The link stays, now dangling; its target was deleted as its own file, not through the link.
    assert (root / "worktrees/proj/gone-ar/link.sqlite").is_symlink()
    assert all((root / path).is_symlink() for path in protected if "/linked-companions/" in path)
    assert (
        subprocess.run(
            ["git", "diff", "--name-only", "--diff-filter=D"],
            cwd=live,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        == ""
    )
    assert document["before"]["files"] == len(EXPECTED) + len(extra_copies)
    assert document["after"]["files"] == len(EXPECTED) - len(DELETED)
    assert document["after"] == document["afterApply"]
    assert document["after"]["bytes"] == sum(
        (root / path).stat().st_size for path in EXPECTED if path not in DELETED
    )
    assert set(document["after"]["byRule"]) == {"git-tracked", "other", "unarchived-task-notes"}

    again = run_cleanup(root, _reviewed(root))
    assert again["deleted"] == [] and again["before"] == document["after"]
    assert tree(root) == after


def test_a_tasks_copies_follow_the_archive_in_both_orders(tmp_path: Path) -> None:
    """D17 and rule 6: left while the task is live, deleted once its folder is under ``0_archive``,
    and absent from the list when the archive hook already deleted them."""

    root = coordination_root(tmp_path)
    live = root / "tasks/proj/260101_live-task"
    assert (
        run_cleanup(root, _reviewed(root))["after"]["byRule"]["unarchived-task-notes"]["files"] == 1
    )
    assert (root / LIVE_NOTES / "snapshot.sqlite").is_file()

    # The cleanup ran first; the task is archived later, and the copy is still under its notes.
    archived = root / "tasks/proj/0_archive/260101_live-task"
    live.rename(archived)
    listed = {copy["path"]: copy["rule"] for copy in plan_cleanup(root)["copies"]}
    moved = LIVE_NOTES.replace("proj/260101_live-task", "proj/0_archive/260101_live-task")
    assert listed[f"{moved}/snapshot.sqlite"] == "archived-task-notes"
    assert listed["tasks/proj/0_archive/260101_live-task/scratch/pub2.sqlite"] == "other"
    applied = run_cleanup(root, _reviewed(root))
    assert applied["deleted"] == [f"{moved}/snapshot.sqlite"]
    assert (archived / "scratch/pub2.sqlite").is_file()  # outside the notes: no rule names it

    # The other order: the archive hook already deleted a task's copies; nothing is left to list.
    assert [copy for copy in plan_cleanup(root)["copies"] if "/notes/" in copy["path"]] == []


def test_tracked_files_and_derived_indexes_are_skipped_by_construction(tmp_path: Path) -> None:
    root = coordination_root(tmp_path)
    tracked = f"worktrees/proj/live-ar/checkout/{RUNTIME}/tracked.sqlite"
    misplaced = root / "runtime/knowledge-index/misplaced.sqlite"

    document = run_cleanup(root, _reviewed(root))

    # In a provider-runtime directory, and tracked: tracking is asked first, so it is skipped.
    assert tracked not in document["deleted"]
    assert (root / tracked).is_file()
    # A plain dataset inside the index cache directory is not even listed: nothing there is read.
    assert misplaced.is_file()
    assert all("knowledge-index" not in copy["path"] for copy in document["copies"])
    # An index outside the cache is recognised by its own tables and is never a copy.
    index = root / "runtime/elsewhere/index-copy.sqlite"
    assert is_derived_index(sqlite_table_names(index)) and index.is_file()
    assert is_knowledge_dataset(sqlite_table_names(misplaced))
    assert not is_derived_index(sqlite_table_names(misplaced))


def test_a_file_that_changed_after_it_was_listed_is_re_read_and_left(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The real run reads each file again just before deleting it.

    Between the listing and the deletion, one listed copy is replaced by a derived index, another
    becomes Git-tracked, and a companion the review listed for removal becomes a symbolic link.
    None is deleted; each is named with why, as a failure: the run did not do what was reviewed.
    """

    root = coordination_root(tmp_path)
    replaced = root / "worktrees/proj/gone-ar/head.sqlite"
    live = root / "worktrees/proj/live-ar/checkout"
    newly_tracked = f"{RUNTIME}/later.sqlite"
    _dataset(live / newly_tracked)  # untracked in a provider-runtime directory: listed for deletion
    scan = legacy_dataset_copies._scan
    reviewed = tmp_path / "reviewed.json"
    assert (
        main(["knowledge-copies", "--coordination-root", str(root), "--report", str(reviewed)]) == 0
    )
    capsys.readouterr()

    origin = root / f"worktrees/proj/group-ar/{RUNTIME}/baseline/baseline-origin.json"
    assert origin.is_file()

    def scan_then_change(scanned_root: Path) -> object:
        plan = scan(scanned_root)
        replaced.unlink()
        _index(replaced)
        _git(live, "add", "-f", newly_tracked)
        return plan

    digest = legacy_dataset_copies._review_digest

    def digest_then_link(review: dict[str, Any]) -> str:
        # The companion changes after this run compared its own list with the reviewed one.
        compared = digest(review)
        origin.unlink()
        origin.symlink_to(root / "tasks")
        return compared

    monkeypatch.setattr(legacy_dataset_copies, "_scan", scan_then_change)
    monkeypatch.setattr(legacy_dataset_copies, "_review_digest", digest_then_link)
    report = tmp_path / "cleanup-report.json"
    assert (
        main(
            [
                "knowledge-copies",
                "--coordination-root",
                str(root),
                "--apply",
                "--reviewed",
                str(reviewed),
                "--report",
                str(report),
            ]
        )
        == 1
    )
    document = json.loads(report.read_text(encoding="utf-8"))
    printed = capsys.readouterr().out
    assert f"after this run: {document['after']['files']} copies" in printed
    assert document["after"]["files"] > document["afterApply"]["files"]

    failures = {failure["path"]: failure["detail"] for failure in document["failures"]}
    assert failures == {
        "worktrees/proj/gone-ar/head.sqlite": "the file is no longer a knowledge dataset copy",
        f"worktrees/proj/live-ar/checkout/{newly_tracked}": "the file now falls under git-tracked",
        origin.relative_to(root).as_posix(): (
            "the companion is a symbolic link; its deletion was refused"
        ),
    }
    # A companion that became protected after the review is not the reviewed outcome "kept".
    assert document["keptCompanions"] == [] and origin.is_symlink()
    assert replaced.is_file() and (live / newly_tracked).is_file()
    assert set(document["deleted"]) == DELETED - {"worktrees/proj/gone-ar/head.sqlite"}


def test_the_probe_reads_only_and_the_command_refuses_what_is_not_a_coordination_root(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    dataset = _dataset(tmp_path / "plain" / "knowledge.sqlite")
    before = sorted(path.name for path in dataset.parent.iterdir())
    assert "invariant" in (sqlite_table_names(dataset) or ())
    assert sorted(path.name for path in dataset.parent.iterdir()) == before
    link = tmp_path / "plain" / "link.sqlite"
    link.symlink_to(dataset)
    text = tmp_path / "plain" / "text.sqlite"
    text.write_text("SQLite format 3 but not really", encoding="utf-8")
    assert sqlite_table_names(link) is None
    assert sqlite_table_names(text) is None
    assert sqlite_table_names(tmp_path / "absent.sqlite") is None

    assert main(["knowledge-copies", "--coordination-root", str(tmp_path / "plain")]) == 2
    assert "is not a coordination root" in capsys.readouterr().out
    with pytest.raises(ValueError, match="is not a directory"):
        plan_cleanup(tmp_path / "absent")
    assert dataset.is_file()


def test_an_apply_is_bound_to_the_reviewed_dry_run_and_refuses_when_the_scan_differs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = coordination_root(tmp_path)
    reviewed = tmp_path / "reviewed.json"
    assert (
        main(["knowledge-copies", "--coordination-root", str(root), "--report", str(reviewed)]) == 0
    )
    capsys.readouterr()
    before = tree(root)
    arguments = ["knowledge-copies", "--coordination-root", str(root), "--apply"]

    # No reviewed list, or a report that is not a dry run of this root: nothing is deleted.
    assert main(arguments) == 2
    assert "--apply needs --reviewed" in capsys.readouterr().out
    other = tmp_path / "other.json"
    other.write_text(json.dumps({"mode": "dry-run", "coordinationRoot": "/elsewhere"}), "utf-8")
    assert main([*arguments, "--reviewed", str(other)]) == 2
    assert "dry run of another coordination root" in capsys.readouterr().out
    assert tree(root) == before

    # A new eligible copy appears after the review: the scan differs, and nothing is deleted.
    _dataset(root / f"worktrees/proj/group-ar/{RUNTIME}/late/knowledge-candidate.sqlite")
    late = tree(root)
    assert main([*arguments, "--reviewed", str(reviewed)]) == 2
    assert "differs from the reviewed dry run" in capsys.readouterr().out
    assert tree(root) == late

    # A companion that became a symbolic link after the review changes the list as well.
    fresh = tmp_path / "fresh.json"
    assert main(["knowledge-copies", "--coordination-root", str(root), "--report", str(fresh)]) == 0
    capsys.readouterr()
    lock = root / f"worktrees/proj/group-ar/{RUNTIME}/baseline/knowledge-candidate.sqlite.lock"
    lock.unlink()
    lock.symlink_to(root / "tasks/proj/260101_live-task/scratch/pub2.sqlite")
    changed = tree(root)
    assert main([*arguments, "--reviewed", str(fresh)]) == 2
    assert "differs from the reviewed dry run" in capsys.readouterr().out
    assert tree(root) == changed

    # Reviewed again as it now is, the apply goes through and deletes what that list named.
    lock.unlink()
    lock.write_text("", encoding="utf-8")
    final = tmp_path / "final.json"
    assert main(["knowledge-copies", "--coordination-root", str(root), "--report", str(final)]) == 0
    capsys.readouterr()
    listed = json.loads(final.read_text(encoding="utf-8"))
    assert main([*arguments, "--reviewed", str(final), "--json"]) == 0
    applied = json.loads(capsys.readouterr().out)
    assert applied["deleted"] == listed["wouldDelete"] and len(applied["deleted"]) == 6
