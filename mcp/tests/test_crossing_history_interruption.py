"""An interrupted history close retains the open merge for a refused retry."""

import json
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path

import pytest
from agents_remember.application.knowledge_write_route import WriteSubmission, crossing_write_report
from agents_remember.application.knowledge_writer import WriteReport
from agents_remember.models.knowledge_files.documents import history_path
from agents_remember.worktrees import knowledge_crossing
from agents_remember.worktrees.knowledge_crossing import (
    _history_close_checkpoint,
    restore_crossing_history,
)
from agents_remember.worktrees.knowledge_validation import PairedCode
from agents_remember.worktrees.sync_transaction import _finish_retained_merge, _run_side
from agents_remember.worktrees.sync_transaction_git import (
    SyncGitProofError,
    _merge,
    continue_side_merge,
    rollback_side,
)
from agents_remember.worktrees.sync_transaction_state import SyncSideRecord
from knowledge_writer_test_support import (
    BASE_INVARIANT,
    CODE_FILE,
    build_world,
    git,
    read_json,
    write,
)
from test_worktree_sync_crossing_history import CONFLICT, _diverge, _seed_dependent

pytestmark = pytest.mark.integration


class _InterruptedClose(Exception):
    """Validation was interrupted after the history entered the closing context."""


@contextmanager
def _interrupt_after_close(
    side: SyncSideRecord, paired_code: PairedCode, monkeypatch: pytest.MonkeyPatch
) -> Iterator[None]:
    cleanup = ExitStack()
    close = _merge.close_crossing_history

    @contextmanager
    def retain_close(worktree: Path, parents: tuple[str, str]) -> Iterator[tuple[str, ...]]:
        # A retry sees the Git state left by the close without its old caller's cleanup.
        yield cleanup.enter_context(close(worktree, parents))

    def interrupt(**_candidate: object) -> str | None:
        raise _InterruptedClose

    try:
        with monkeypatch.context() as patch:
            patch.setattr(_merge, "close_crossing_history", retain_close)
            patch.setattr(_merge, "memory_commit_refusal", interrupt)
            with pytest.raises(_InterruptedClose):
                continue_side_merge(side, paired_code=paired_code)
        yield
    finally:
        cleanup.__exit__(_InterruptedClose, _InterruptedClose(), None)


def _submission(document: dict[str, object]) -> WriteSubmission:
    return WriteSubmission(
        document=document,
        handoff_path="resolution.json",
        authorization_ref="trial resolution",
        commit=True,
    )


def test_interrupted_close_then_refused_retry_restores_open_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = build_world(tmp_path)
    dependent_id, dependent_path = _seed_dependent(world)
    fixture = _diverge(world)
    record = _run_side(fixture.store, fixture.record, "memory")
    assert record.phase == "memory-resolution-required"
    side = record.memory
    assert side is not None
    path = history_path(fixture.owner)
    entered = crossing_write_report(
        fixture.contract,
        fixture.owner,
        _submission(
            {
                "history": [
                    {
                        "subject": BASE_INVARIANT,
                        "disposition": "no_impact",
                        "reason": "The conflict is outside the paired landing behavior.",
                    }
                ]
            }
        ),
    )
    assert isinstance(entered, WriteReport)
    assert entered.state == "written", entered.render()
    assert [row["subject"] for row in read_json(fixture.memory, path)["rows"]] == [BASE_INVARIANT]

    sidecar = f"onboarding/{CODE_FILE}.json"
    original_card = (fixture.memory / sidecar).read_bytes()
    write(fixture.memory, {CONFLICT: "Resolved line.\n", sidecar: "{}\n"})
    git(fixture.memory, "add", "--", CONFLICT, sidecar, path)
    open_bytes = (fixture.memory / path).read_bytes()
    open_entry = git(fixture.memory, "ls-files", "--stage", "--", path)
    paired_code = PairedCode(world.code, record.code.resultHead)
    with _interrupt_after_close(side, paired_code, monkeypatch):
        checkpoint = _history_close_checkpoint(fixture.memory)
        assert checkpoint.is_file()
        assert read_json(fixture.memory, path)["closed"] is True
        assert '"closed": true' in git(fixture.memory, "show", f":{path}")

        retry = _finish_retained_merge(fixture.contract, fixture.store, record, {})
        assert retry.payload["state"] == "sync-knowledge-validation-refused", retry.payload
        assert "R22.1-shape" in str(retry.payload["summary"])
        assert git(fixture.memory, "rev-parse", "HEAD", "MERGE_HEAD").splitlines() == [
            side.preSyncHead,
            side.sourceCommit,
        ]
        assert (fixture.memory / path).read_bytes() == open_bytes
        assert git(fixture.memory, "ls-files", "--stage", "--", path) == open_entry
        assert not checkpoint.exists()

    write(fixture.memory, {sidecar: original_card})
    git(fixture.memory, "add", "--", sidecar)
    confirmed = crossing_write_report(
        fixture.contract,
        fixture.owner,
        _submission(
            {
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
            }
        ),
    )
    assert isinstance(confirmed, WriteReport)
    assert confirmed.state == "written", confirmed.render()
    assert (
        read_json(fixture.memory, dependent_path)["confirmed"]["commit"] == record.code.resultHead
    )
    git(fixture.memory, "add", "--", path, dependent_path)
    head = continue_side_merge(side, paired_code=paired_code)
    assert not _history_close_checkpoint(fixture.memory).exists()
    committed = read_json(fixture.memory, path)
    assert committed["closed"] is True
    assert {row["subject"] for row in committed["rows"]} == {BASE_INVARIANT, dependent_id}
    assert (
        git(fixture.memory, "show", f"{head}:{path}") == (fixture.memory / path).read_text().strip()
    )
    assert git(fixture.memory, "rev-list", "--parents", "-n", "1", head).split() == [
        head,
        side.preSyncHead,
        side.sourceCommit,
    ]


def _publish_checkpoint_without_mutating(
    worktree: Path,
    side: SyncSideRecord,
    paired_code: PairedCode,
    monkeypatch: pytest.MonkeyPatch,
    *,
    stop_stage: str,
) -> Path:
    """Stop inside close_crossing_history right after checkpoint write or file write."""

    checkpoint = _history_close_checkpoint(worktree)
    real_write = knowledge_crossing.atomic_write_text
    real_git = knowledge_crossing._git

    def stop_after_checkpoint(target: Path, text: str) -> None:
        real_write(target, text)
        if target == checkpoint:
            raise _InterruptedClose

    def stop_before_stage(repo: Path, args: list[str], *, input_text: str | None = None) -> str:
        if args[:2] == ["--literal-pathspecs", "add"]:
            raise _InterruptedClose
        return real_git(repo, args, input_text=input_text)

    def keep_checkpoint(_worktree: Path, _parents: tuple[str, str]) -> None:
        return

    with monkeypatch.context() as patch:
        patch.setattr(knowledge_crossing, "restore_crossing_history", keep_checkpoint)
        if stop_stage == "pre-write":
            patch.setattr(knowledge_crossing, "atomic_write_text", stop_after_checkpoint)
        else:
            patch.setattr(knowledge_crossing, "_git", stop_before_stage)
        with pytest.raises(_InterruptedClose):
            continue_side_merge(side, paired_code=paired_code)
    assert checkpoint.is_file()
    return checkpoint


def test_refused_retry_preserves_later_crossing_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = build_world(tmp_path)
    fixture = _diverge(world)
    record = _run_side(fixture.store, fixture.record, "memory")
    assert record.phase == "memory-resolution-required"
    side = record.memory
    assert side is not None
    path = history_path(fixture.owner)
    sidecar = f"onboarding/{CODE_FILE}.json"
    original_card = (fixture.memory / sidecar).read_bytes()
    write(fixture.memory, {CONFLICT: "Resolved line.\n"})
    git(fixture.memory, "add", "--", CONFLICT)
    paired_code = PairedCode(world.code, record.code.resultHead)

    checkpoint = _publish_checkpoint_without_mutating(
        fixture.memory, side, paired_code, monkeypatch, stop_stage="pre-write"
    )
    assert read_json(fixture.memory, path)["closed"] is False

    entered = crossing_write_report(
        fixture.contract,
        fixture.owner,
        _submission(
            {
                "history": [
                    {
                        "subject": BASE_INVARIANT,
                        "disposition": "no_impact",
                        "reason": "Later crossing row written after close-checkpoint publication.",
                    }
                ]
            }
        ),
    )
    assert isinstance(entered, WriteReport)
    assert entered.state == "written", entered.render()
    git(fixture.memory, "add", "--", path)
    later_bytes = (fixture.memory / path).read_bytes()
    later_entry = git(fixture.memory, "ls-files", "--stage", "--", path)

    write(fixture.memory, {sidecar: "{}\n"})
    git(fixture.memory, "add", "--", sidecar)

    retry = _finish_retained_merge(fixture.contract, fixture.store, record, {})
    assert retry.payload["state"] == "sync-knowledge-validation-refused", retry.payload
    assert "R22.1-shape" in str(retry.payload["summary"])
    assert (fixture.memory / path).read_bytes() == later_bytes
    assert git(fixture.memory, "ls-files", "--stage", "--", path) == later_entry
    assert not checkpoint.exists()

    write(fixture.memory, {sidecar: original_card})
    git(fixture.memory, "add", "--", sidecar)
    head = continue_side_merge(side, paired_code=paired_code)
    assert not checkpoint.exists()
    committed = read_json(fixture.memory, path)
    assert committed["closed"] is True
    assert [row["subject"] for row in committed["rows"]] == [BASE_INVARIANT]
    assert git(fixture.memory, "rev-list", "--parents", "-n", "1", head).split() == [
        head,
        side.preSyncHead,
        side.sourceCommit,
    ]


@pytest.mark.parametrize(
    "case",
    ["half-written", "pre-write-clean", "later-unstaged-edit", "mismatched-parents"],
)
def test_interrupted_close_recovery_respects_checkpoint_ownership(
    tmp_path: Path, case: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = build_world(tmp_path)
    fixture = _diverge(world)
    record = _run_side(fixture.store, fixture.record, "memory")
    side = record.memory
    assert side is not None
    path = history_path(fixture.owner)
    sidecar = f"onboarding/{CODE_FILE}.json"
    write(fixture.memory, {CONFLICT: "Resolved line.\n", sidecar: "{}\n"})
    git(fixture.memory, "add", "--", CONFLICT, sidecar)
    open_bytes = (fixture.memory / path).read_bytes()
    open_entry = git(fixture.memory, "ls-files", "--stage", "--", path)
    parents = (side.preSyncHead, side.sourceCommit)
    paired_code = PairedCode(world.code, record.code.resultHead)

    if case == "half-written":
        checkpoint = _publish_checkpoint_without_mutating(
            fixture.memory, side, paired_code, monkeypatch, stop_stage="half-written"
        )
        assert read_json(fixture.memory, path)["closed"] is True
        assert git(fixture.memory, "ls-files", "--stage", "--", path) == open_entry
        retry = _finish_retained_merge(fixture.contract, fixture.store, record, {})
        assert retry.payload["state"] == "sync-knowledge-validation-refused", retry.payload
        assert (fixture.memory / path).read_bytes() == open_bytes
        assert git(fixture.memory, "ls-files", "--stage", "--", path) == open_entry
        assert not checkpoint.exists()
        return

    if case == "pre-write-clean":
        checkpoint = _publish_checkpoint_without_mutating(
            fixture.memory, side, paired_code, monkeypatch, stop_stage="pre-write"
        )
        retry = _finish_retained_merge(fixture.contract, fixture.store, record, {})
        assert retry.payload["state"] == "sync-knowledge-validation-refused", retry.payload
        assert (fixture.memory / path).read_bytes() == open_bytes
        assert git(fixture.memory, "ls-files", "--stage", "--", path) == open_entry
        assert not checkpoint.exists()
        return

    if case == "later-unstaged-edit":
        with _interrupt_after_close(side, paired_code, monkeypatch):
            checkpoint = _history_close_checkpoint(fixture.memory)
            later_disk = b'{"schema": "ar-history/v1", "owner_edit": true}\n'
            (fixture.memory / path).write_bytes(later_disk)
            staged_before = git(fixture.memory, "ls-files", "--stage", "--", path)
            with pytest.raises(SyncGitProofError, match="unstaged changes"):
                continue_side_merge(side, paired_code=paired_code)
            assert (fixture.memory / path).read_bytes() == later_disk
            assert git(fixture.memory, "ls-files", "--stage", "--", path) == staged_before
            assert not checkpoint.exists()
        return

    checkpoint = _publish_checkpoint_without_mutating(
        fixture.memory, side, paired_code, monkeypatch, stop_stage="half-written"
    )
    payload = json.loads(checkpoint.read_text(encoding="utf-8"))
    payload["parents"] = ["0" * 40, "1" * 40]
    checkpoint.write_text(json.dumps(payload), encoding="utf-8")
    before_bytes = (fixture.memory / path).read_bytes()
    restore_crossing_history(fixture.memory, parents)
    knowledge_crossing.clear_crossing_history_close(fixture.memory, parents)
    assert (fixture.memory / path).read_bytes() == before_bytes
    assert checkpoint.is_file()


def test_rollback_retires_checkpoint_only_after_verified_reset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = build_world(tmp_path)
    fixture = _diverge(world)
    record = _run_side(fixture.store, fixture.record, "memory")
    side = record.memory
    assert side is not None
    write(fixture.memory, {CONFLICT: "Resolved line.\n"})
    git(fixture.memory, "add", "--", CONFLICT)
    paired_code = PairedCode(world.code, record.code.resultHead)

    checkpoint = _publish_checkpoint_without_mutating(
        fixture.memory, side, paired_code, monkeypatch, stop_stage="pre-write"
    )
    wrong_side = side.model_copy(update={"sourceCommit": "0" * 40})
    with pytest.raises(SyncGitProofError, match="outside sync authority"):
        rollback_side(wrong_side)
    assert checkpoint.is_file()

    rollback_side(side)
    assert not checkpoint.exists()
