"""Idle projection work is proportional to change: stat witnesses before any re-read.

A configuration digest, a packed-reference parse, a clean index trust-flag scan, an execution
receipt parse and the field-effect taxonomy walk are each redone only when their input moved.
The witness is the full file identity (device, inode, size, mtime and ctime): a same-size
rewrite whose writer restores mtime still moves ctime, so it is read again, and so is any real
change.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest
from agents_remember.application import master_architect_record
from agents_remember.application.master_architect_record import (
    EXECUTIONS_DIRECTORY,
    leaf_execution_receipts,
)
from agents_remember.kernel import git_reference_files
from agents_remember.kernel.git_reference_files import packed_references
from agents_remember.serving import changeset_generation
from agents_remember.serving.changeset_generation import configuration_identity, repository_layout
from agents_remember.serving.projections.snapshots_impl import _closeout_queue as queue_module
from agents_remember.tasks import document_field_effects
from agents_remember_test_support.testing.waits import wait_until
from pydantic import BaseModel
from test_worktree_support import git, init_repo

pytestmark = pytest.mark.evidence_unit


def _rewrite_keeping_mtime(path: Path, data: bytes) -> None:
    """Rewrite ``path`` in place with same-size bytes and restore its mtime.

    The wait lets the filesystem clock pass the file's ctime first, so the rewrite's ctime
    is a later one; only ctime then tells the two contents apart.
    """
    before = path.stat()
    assert len(data) == before.st_size
    probe = path.with_name(path.name + ".clock")

    def clock_moved() -> bool:
        probe.write_bytes(b"")
        return probe.stat().st_ctime_ns > before.st_ctime_ns

    wait_until(clock_moved, "the filesystem clock to pass the file's ctime")
    probe.unlink()
    with path.open("r+b") as stream:
        stream.write(data)
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    after = path.stat()
    assert (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) == (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
    )
    assert after.st_ctime_ns != before.st_ctime_ns


def _count_calls(monkeypatch, owner, name: str) -> list[object]:
    calls: list[object] = []
    original = getattr(owner, name)

    def counted(*args, **kwargs):
        calls.append(args)
        return original(*args, **kwargs)

    monkeypatch.setattr(owner, name, counted)
    return calls


def test_an_unchanged_configuration_file_is_not_hashed_again(tmp_path, monkeypatch) -> None:
    path = tmp_path / "config"
    path.write_bytes(b"[core]\n\tbare = false\n")
    hashed = _count_calls(monkeypatch, changeset_generation, "_file_digest")

    first = configuration_identity(path)
    second = configuration_identity(path)

    assert first == second == hashlib.sha256(path.read_bytes()).hexdigest()
    assert len(hashed) == 1


def test_a_same_size_rewrite_with_restored_mtime_is_hashed_again(tmp_path) -> None:
    path = tmp_path / "config"
    path.write_bytes(b"[core]\n\tbare = false\n")
    before = configuration_identity(path)

    _rewrite_keeping_mtime(path, b"[core]\n\tbare = fals3\n")

    assert configuration_identity(path) != before
    assert configuration_identity(path) == hashlib.sha256(path.read_bytes()).hexdigest()


def test_a_changed_configuration_file_is_hashed_again(tmp_path) -> None:
    path = tmp_path / "config"
    path.write_bytes(b"one\n")
    first = configuration_identity(path)
    replacement = tmp_path / "config.new"
    replacement.write_bytes(b"two, longer\n")
    replacement.replace(path)

    assert configuration_identity(path) == hashlib.sha256(b"two, longer\n").hexdigest() != first
    path.unlink()
    assert configuration_identity(path) == "absent"
    path.write_bytes(b"one\n")
    assert configuration_identity(path) == first


def test_an_unchanged_repository_layout_hashes_no_configuration(tmp_path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    init_repo(repo)
    layout = repository_layout(repo)
    hashed = _count_calls(monkeypatch, changeset_generation, "_file_digest")

    assert layout.current()
    assert layout.current()
    assert hashed == []

    config = repo / ".git" / "config"
    text = config.read_bytes()
    _rewrite_keeping_mtime(config, text.replace(b"Tests", b"Testz"))
    assert not layout.current()


def _packed_repository(tmp_path: Path) -> tuple[Path, str, str]:
    repo = tmp_path / "repo"
    init_repo(repo)
    first = git(repo, "rev-parse", "HEAD")
    git(repo, "commit", "--allow-empty", "-m", "second")
    second = git(repo, "rev-parse", "HEAD")
    git(repo, "branch", "-f", "source", first)
    git(repo, "pack-refs", "--all")
    assert not (repo / ".git" / "refs" / "heads" / "source").exists()
    return repo, first, second


def test_unchanged_packed_references_are_not_parsed_again(tmp_path, monkeypatch) -> None:
    repo, first, _ = _packed_repository(tmp_path)
    reads: list[Path] = []
    original = Path.read_text

    def read_text(self: Path, *args, **kwargs):
        if self.name == "packed-refs":
            reads.append(self)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read_text)

    answer = packed_references(repo / ".git")
    answer["refs/heads/source"] = "edited by a caller"
    again = packed_references(repo / ".git")

    assert again["refs/heads/source"] == first
    assert len(reads) == 1


def test_a_packed_tip_rewrite_with_restored_mtime_moves_the_live_tip(tmp_path) -> None:
    repo, first, second = _packed_repository(tmp_path)
    assert queue_module._branch_tip_parts(repo, "source") == f"tip\0{first}\0"
    packed = repo / ".git" / "packed-refs"
    text = packed.read_bytes()
    rewritten = text.replace(
        f"{first} refs/heads/source".encode(), f"{second} refs/heads/source".encode()
    )
    assert rewritten != text

    _rewrite_keeping_mtime(packed, rewritten)

    assert queue_module._branch_tip_parts(repo, "source") == f"tip\0{second}\0"
    git(repo, "branch", "-f", "source", first)
    assert queue_module._branch_tip_parts(repo, "source") == f"tip\0{first}\0"


def test_a_clean_index_is_scanned_once_while_unchanged(tmp_path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    init_repo(repo)
    scans = _count_calls(monkeypatch, queue_module, "_index_copy_has_trust_flags")

    first = queue_module._worktree_live_parts(repo, "code")
    second = queue_module._worktree_live_parts(repo, "code")

    assert first is not None
    assert second == first
    assert len(scans) == 1

    git(repo, "update-index", "--assume-unchanged", "README.md")
    assert queue_module._worktree_live_parts(repo, "code") is None
    assert queue_module._worktree_live_parts(repo, "code") is None
    assert len(scans) == 3


def test_an_edit_still_moves_the_worktree_live_parts(tmp_path) -> None:
    repo = tmp_path / "repo"
    init_repo(repo)
    clean = queue_module._worktree_live_parts(repo, "code")
    (repo / "README.md").write_text("# Edited\n", encoding="utf-8")

    edited = queue_module._worktree_live_parts(repo, "code")

    assert edited is not None
    assert edited != clean


def test_the_taxonomy_is_walked_once_and_a_refusal_repeats(monkeypatch) -> None:
    monkeypatch.setattr(document_field_effects, "_validated_roots", set(), raising=False)
    walks = _count_calls(monkeypatch, document_field_effects, "task_document_schema_models")

    document_field_effects.validate_task_document_field_effects()
    document_field_effects.validate_task_document_field_effects()

    assert len(walks) == 1

    class Unclassified(BaseModel):
        value: int = 0

    for _ in range(2):
        with pytest.raises(document_field_effects.TaskDocumentFieldEffectTaxonomyError):
            document_field_effects.validate_task_document_field_effects(root_model=Unclassified)
    assert len(walks) == 3


def _receipt(agent: str) -> bytes:
    return json.dumps(
        {
            "agentId": agent,
            "role": "worker",
            "selection": {"taskDocumentRef": {"path": "master/L1.json"}},
        }
    ).encode()


def test_unchanged_execution_receipts_are_not_parsed_again(tmp_path, monkeypatch) -> None:
    folder = tmp_path / "notes" / "reports" / EXECUTIONS_DIRECTORY
    folder.mkdir(parents=True)
    (folder / "worker-L1.json").write_bytes(_receipt("agent-1"))
    (folder / "worker-L2.json").write_bytes(_receipt("agent-2").replace(b"L1", b"L2"))
    parsed = _count_calls(monkeypatch, master_architect_record, "read_execution_receipt")

    first = leaf_execution_receipts(tmp_path, "L1")
    first[0]["agentId"] = "edited by a caller"
    second = leaf_execution_receipts(tmp_path, "L1")
    leaf_execution_receipts(tmp_path, "L2")

    assert [receipt["agentId"] for receipt in second] == ["agent-1"]
    assert len(parsed) == 2


def test_a_receipt_rewrite_with_restored_mtime_is_parsed_again(tmp_path) -> None:
    folder = tmp_path / "notes" / "reports" / EXECUTIONS_DIRECTORY
    folder.mkdir(parents=True)
    path = folder / "worker-L1.json"
    path.write_bytes(_receipt("agent-1"))
    assert [receipt["agentId"] for receipt in leaf_execution_receipts(tmp_path, "L1")] == [
        "agent-1"
    ]

    _rewrite_keeping_mtime(path, _receipt("agent-9"))

    assert [receipt["agentId"] for receipt in leaf_execution_receipts(tmp_path, "L1")] == [
        "agent-9"
    ]
    path.unlink()
    assert leaf_execution_receipts(tmp_path, "L1") == ()


def _bounded(monkeypatch, owner, name: str, capacity: int) -> None:
    """Replace one module memo with an empty one of ``capacity`` entries for this test."""
    from agents_remember.kernel.witnessed_memo import WitnessedMemo  # noqa: PLC0415

    monkeypatch.setattr(owner, name, WitnessedMemo(capacity))


def test_an_evicted_configuration_digest_is_hashed_again_to_the_same_answer(
    tmp_path, monkeypatch
) -> None:
    _bounded(monkeypatch, changeset_generation, "_configuration_digests", 1)
    first, second = tmp_path / "first", tmp_path / "second"
    first.write_bytes(b"[core]\n")
    second.write_bytes(b"[user]\n")
    hashed = _count_calls(monkeypatch, changeset_generation, "_file_digest")

    answer = configuration_identity(first)
    assert configuration_identity(first) == answer
    configuration_identity(second)
    assert configuration_identity(first) == answer

    assert hashed == [(first,), (second,), (first,)]


def test_an_evicted_packed_reference_parse_is_read_again_to_the_same_answer(
    tmp_path, monkeypatch
) -> None:
    _bounded(monkeypatch, git_reference_files, "_parsed", 1)
    repo, first, _ = _packed_repository(tmp_path)
    other = tmp_path / "other"
    init_repo(other)
    git(other, "pack-refs", "--all")
    reads: list[Path] = []
    original = Path.read_text

    def read_text(self: Path, *args, **kwargs):
        if self.name == "packed-refs":
            reads.append(self.parent)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read_text)

    answer = packed_references(repo / ".git")
    assert packed_references(repo / ".git") == answer
    packed_references(other / ".git")
    assert packed_references(repo / ".git") == answer
    assert answer["refs/heads/source"] == first

    assert reads == [repo / ".git", other / ".git", repo / ".git"]


def test_an_evicted_clean_index_is_scanned_again_to_the_same_answer(tmp_path, monkeypatch) -> None:
    _bounded(monkeypatch, queue_module, "_clean_index_stamps", 1)
    first, second = tmp_path / "first", tmp_path / "second"
    init_repo(first)
    init_repo(second)
    scans = _count_calls(monkeypatch, queue_module, "_index_copy_has_trust_flags")

    answer = queue_module._worktree_live_parts(first, "code")
    assert queue_module._worktree_live_parts(first, "code") == answer
    queue_module._worktree_live_parts(second, "code")
    assert queue_module._worktree_live_parts(first, "code") == answer

    assert answer is not None
    assert len(scans) == 3


def test_an_evicted_receipt_is_parsed_again_to_the_same_answer(tmp_path, monkeypatch) -> None:
    _bounded(monkeypatch, master_architect_record, "_parsed_receipts", 1)
    folder = tmp_path / "notes" / "reports" / EXECUTIONS_DIRECTORY
    folder.mkdir(parents=True)
    (folder / "worker-L1.json").write_bytes(_receipt("agent-1"))
    (folder / "worker-L2.json").write_bytes(_receipt("agent-2").replace(b"L1", b"L2"))
    parsed = _count_calls(monkeypatch, master_architect_record, "read_execution_receipt")

    answer = leaf_execution_receipts(tmp_path, "L1")
    assert leaf_execution_receipts(tmp_path, "L1") == answer

    assert [receipt["agentId"] for receipt in answer] == ["agent-1"]
    assert len(parsed) == 4
