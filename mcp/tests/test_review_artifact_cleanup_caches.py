"""MIK-R82 rule 3 (hook side) and rule 6: scratch caches go with the archived task.

When a task is archived the hook deletes every ``ar-cache-*`` directory under the task folder
and lists each with its size in ``arCaches``; a symlink is never followed or deleted. The dry
run lists without deleting.

The same file also carries the rule 6 decision: both trust statements are stated in the
module text of ``application/review_artifact_cleanup.py`` -- (1) the hook guards against
accidents, not against a second writer with the same rights, and (2) the legacy cleanup
trusts the control documents the product itself writes -- and that text never uses the word
``accepted``.
"""

from __future__ import annotations

import json
from pathlib import Path

from agents_remember.application.review_artifact_cleanup import cleanup_review_artifacts
from agents_remember.worktrees.services import ReviewArtifactCleanupRequest

TASK = "260101_tree_review"
MODULE = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "agents_remember"
    / "application"
    / "review_artifact_cleanup.py"
)


def task_root(tmp_path: Path) -> Path:
    root = tmp_path / "coordination" / "tasks" / "agents-remember" / TASK
    root.mkdir(parents=True)
    (root / "task.json").write_text(json.dumps({"id": "260101-TRV"}), encoding="utf-8")
    first = root / "notes" / "ar-cache-1000"
    (first / "slot-0").mkdir(parents=True)
    (first / "slot-0" / "index.sqlite3").write_bytes(b"0" * 64)
    (first / "manifest.json").write_text("{}", encoding="utf-8")
    second = root / "scratch" / "ar-cache-1001"
    (second / "nested").mkdir(parents=True)
    (second / "nested" / "payload.bin").write_bytes(b"1" * 128)
    (root / "ar-cache-link").symlink_to(first, target_is_directory=True)
    (root / "ar-cache-notes.txt").write_text("a file, not a cache directory\n", encoding="utf-8")
    return root


def _request(root: Path, *, dry_run: bool) -> ReviewArtifactCleanupRequest:
    return ReviewArtifactCleanupRequest(
        task_root=root,
        task_name=TASK,
        code_repository=None,
        memory_repository=None,
        dry_run=dry_run,
    )


def _sizes(root: Path) -> dict[str, int]:
    return {
        (root / "notes" / "ar-cache-1000").as_posix(): 64 + 2,
        (root / "scratch" / "ar-cache-1001").as_posix(): 128,
    }


def test_the_dry_run_lists_each_cache_with_its_size_and_changes_nothing(tmp_path: Path) -> None:
    root = task_root(tmp_path)

    preview = cleanup_review_artifacts(_request(root, dry_run=True))

    assert preview["state"] == "would-delete"
    assert preview["arCaches"] == [
        {"path": path, "bytes": size} for path, size in sorted(_sizes(root).items())
    ]
    assert (root / "notes" / "ar-cache-1000").is_dir()
    assert (root / "scratch" / "ar-cache-1001").is_dir()
    assert any(
        entry["target"] == (root / "ar-cache-link").as_posix() for entry in preview["failures"]
    )


def test_archiving_deletes_the_cache_directories_and_keeps_the_link_target_policy(
    tmp_path: Path,
) -> None:
    root = task_root(tmp_path)

    report = cleanup_review_artifacts(_request(root, dry_run=False))

    assert report["state"] == "partial"  # the symlinked cache is held, everything else went
    assert report["arCaches"] == [
        {"path": path, "bytes": size} for path, size in sorted(_sizes(root).items())
    ]
    assert not (root / "notes" / "ar-cache-1000").exists()
    assert not (root / "scratch" / "ar-cache-1001").exists()
    assert (root / "ar-cache-link").is_symlink()
    assert (root / "ar-cache-notes.txt").is_file()
    assert any(
        entry["target"] == (root / "ar-cache-link").as_posix() for entry in report["failures"]
    )

    again = cleanup_review_artifacts(_request(root, dry_run=False))
    assert again["arCaches"] == []
    assert any(
        entry["target"] == (root / "ar-cache-link").as_posix() for entry in again["failures"]
    )


def test_the_module_text_states_both_trust_statements_without_the_forbidden_word() -> None:
    """Rule 6: the hook's two trust statements, and never the word they were once given."""

    text = " ".join(MODULE.read_text(encoding="utf-8").split())

    assert "accepted" not in text.lower()
    assert "the hook guards against accidents, not against a second writer" in text
    assert "already has the right to delete the target itself" in text
    assert "trusts the control documents the product itself writes" in text
    assert "can delete the refs with Git directly" in text
