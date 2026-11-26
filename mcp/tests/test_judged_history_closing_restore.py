"""Contract tests for ``_JudgedHistoryClosing.restore`` and polymorphic ``HistoryClosing`` callers."""

from __future__ import annotations

from pathlib import Path

from agents_remember.models.lifecycles.mutation_evidence import GitMutationSnapshot
from agents_remember.worktrees.direct_landing import _JudgedHistoryClosing
from agents_remember.worktrees.knowledge_gate import HistoryClosing
from agents_remember.worktrees.modules.closeout_external import _restore_preparations


def _snapshot() -> GitMutationSnapshot:
    sha40 = "a" * 40
    sha64 = "b" * 64
    return GitMutationSnapshot(
        headRef="refs/heads/main",
        head=sha40,
        headTree=sha40,
        refLogFingerprint=sha64,
        indexTree=sha40,
        candidateTree=sha40,
        statusFingerprint=sha64,
    )


def _make_closing(
    tmp_path: Path,
    *,
    history_previous: bytes | None = b'{"closed": false}\n',
    history_written: bytes = b'{"closed": true}\n',
    ignore_previous: bytes | None = None,
    ignore_written: bytes = b"/briefs/\n",
) -> tuple[_JudgedHistoryClosing, Path, Path]:
    history = tmp_path / "history.json"
    ignore = tmp_path / ".gitignore"
    history.write_bytes(history_written)
    ignore.write_bytes(ignore_written)
    closing = _JudgedHistoryClosing(
        path=history,
        previous=history_previous,
        written=history_written,
        judged=_snapshot(),
        ignore_before=HistoryClosing(ignore, ignore_previous, ignore_written),
    )
    return closing, history, ignore


def test_restore_returns_true_when_both_preparations_succeed(tmp_path: Path) -> None:
    closing, history, ignore = _make_closing(tmp_path)

    restored = closing.restore()

    assert restored is True
    assert history.read_bytes() == b'{"closed": false}\n'
    assert not ignore.exists()


def test_restore_runs_ignore_restore_without_short_circuit_when_history_changed(
    tmp_path: Path,
) -> None:
    closing, history, ignore = _make_closing(tmp_path, ignore_previous=b"*.tmp\n")
    history.write_bytes(b'{"closed": false, "concurrent": true}\n')

    restored = closing.restore()

    assert restored is False
    assert history.read_bytes() == b'{"closed": false, "concurrent": true}\n'
    assert ignore.read_bytes() == b"*.tmp\n"


def test_restore_returns_false_and_restores_history_when_ignore_changed(tmp_path: Path) -> None:
    closing, history, ignore = _make_closing(tmp_path, ignore_previous=b"*.tmp\n")
    ignore.write_bytes(b"/briefs/\n# concurrent edit\n")

    restored = closing.restore()

    assert restored is False
    assert history.read_bytes() == b'{"closed": false}\n'
    assert ignore.read_bytes() == b"/briefs/\n# concurrent edit\n"


def test_restore_returns_false_when_both_files_changed(tmp_path: Path) -> None:
    closing, history, ignore = _make_closing(tmp_path)
    history.write_bytes(b"concurrent history\n")
    ignore.write_bytes(b"concurrent ignore\n")

    restored = closing.restore()

    assert restored is False
    assert history.read_bytes() == b"concurrent history\n"
    assert ignore.read_bytes() == b"concurrent ignore\n"


def test_restore_preparations_sees_bool_outcome_on_judged_closing(tmp_path: Path) -> None:
    """``closeout_external._restore_preparations`` treats ``True`` as restored and ``False`` as skipped."""

    clean_dir = tmp_path / "clean"
    clean_dir.mkdir()
    clean_closing, clean_history, clean_ignore = _make_closing(clean_dir)

    skipped_clean = _restore_preparations(clean_closing)

    assert skipped_clean == []
    assert clean_history.read_bytes() == b'{"closed": false}\n'
    assert not clean_ignore.exists()

    dirty_history_dir = tmp_path / "dirty_history"
    dirty_history_dir.mkdir()
    dirty_closing, dirty_history, dirty_ignore = _make_closing(
        dirty_history_dir, ignore_previous=b"*.log\n"
    )
    dirty_history.write_bytes(b"concurrent edit\n")

    skipped_dirty_history = _restore_preparations(dirty_closing)

    assert skipped_dirty_history == [dirty_history.as_posix()]
    assert dirty_history.read_bytes() == b"concurrent edit\n"
    assert dirty_ignore.read_bytes() == b"*.log\n"

    dirty_ignore_dir = tmp_path / "dirty_ignore"
    dirty_ignore_dir.mkdir()
    dirty_ignore_closing, restored_history, edited_ignore = _make_closing(
        dirty_ignore_dir, ignore_previous=b"*.log\n"
    )
    edited_ignore.write_bytes(b"/briefs/\n# concurrent\n")

    skipped_dirty_ignore = _restore_preparations(dirty_ignore_closing)

    assert skipped_dirty_ignore == [restored_history.as_posix()]
    assert restored_history.read_bytes() == b'{"closed": false}\n'
    assert edited_ignore.read_bytes() == b"/briefs/\n# concurrent\n"
