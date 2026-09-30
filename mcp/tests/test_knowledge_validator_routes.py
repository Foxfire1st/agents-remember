"""MIK-R22 rule 8: where the validator runs -- the Git inputs, the commit-route port, the managed
sync after a merge, and the curator's standalone command -- with a refusal test at each point.

Every route validates only when a side of the commit holds the layout marker, so the unconverted
memory of today's production commits exactly as before; the existing sync tests prove that path.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from agents_remember.cli.__main__ import main as cli_main
from agents_remember.kernel.git_command import read_git_blobs_bytes
from agents_remember.kernel.memory_attribution import render_memory_content_message
from agents_remember.memory_quality.knowledge_validator import (
    knowledge_tree_from_directory,
    knowledge_tree_from_git,
)
from agents_remember.memory_quality.knowledge_validator.commit_route import (
    GitKnowledgeValidation,
)
from agents_remember.worktrees.knowledge_validation import PairedCode, memory_commit_refusal
from agents_remember.worktrees.services import bind_worktree_services
from agents_remember.worktrees.services import worktree_services as bound_worktree_services
from knowledge_validator_test_support import (
    CODE_PATHS,
    INTEGRATE,
    INTEGRATE_CARD,
    INTEGRATE_SIDECAR,
    INVARIANT,
    LEGACY_COUNT,
    edit_json,
    encode,
    fixture_tree_files,
    invariant_document,
    invariant_path,
    write_tree,
)
from test_worktree_sync import SyncFixture

LEFT_DUPLICATE = invariant_path("INV-D0P111", "left")
RIGHT_DUPLICATE = invariant_path("INV-D0P111", "right")


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=repo, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout)
    return result.stdout.strip()


def _repository(path: Path, files: dict[str, bytes]) -> str:
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-q", "-b", "main")
    git(path, "config", "user.email", "agents-remember@example.invalid")
    git(path, "config", "user.name", "Agents Remember")
    return _commit(path, files, "seed")


def _commit(path: Path, files: dict[str, bytes], message: str) -> str:
    write_tree(path, files)
    git(path, "add", "-A")
    git(path, "commit", "-q", "--allow-empty", "-m", message)
    return git(path, "rev-parse", "HEAD")


@pytest.fixture
def code_repository(tmp_path: Path) -> tuple[Path, str]:
    root = tmp_path / "code"
    return root, _repository(root, {path: b"# code\n" for path in CODE_PATHS})


def test_git_trees_are_read_exactly_and_the_route_index_cache_is_ignored(tmp_path: Path) -> None:
    memory = tmp_path / "memory"
    files = {
        **fixture_tree_files(),
        INVARIANT: fixture_tree_files()[INVARIANT].replace(b"\n", b"\r\n"),
        "onboarding/mcp/overview.index.json": b"{}",
        "README.md": b"# not knowledge\n",
        "onboarding/.git-blame-ignore-revs.md": b"# dot-named card\n",
        "onboarding/.ar-index/cache.json": b"{}",
    }
    commit = _repository(memory, files)

    snapshot = knowledge_tree_from_git(memory, commit)

    assert snapshot.files[INVARIANT] == files[INVARIANT]
    assert "onboarding/mcp/overview.index.json" not in snapshot.files
    assert "README.md" not in snapshot.files
    assert "onboarding/.git-blame-ignore-revs.md" in snapshot.files
    assert "onboarding/.ar-index/cache.json" not in snapshot.files
    assert knowledge_tree_from_directory(memory).files == snapshot.files
    assert snapshot.converted
    blob = git(memory, "rev-parse", f"{commit}:{INVARIANT}")
    assert read_git_blobs_bytes(memory, [blob, blob]) == {blob: files[INVARIANT]}


def test_the_commit_route_adapter_refuses_naming_every_violation(
    tmp_path: Path, code_repository: tuple[Path, str]
) -> None:
    code_root, code_commit = code_repository
    memory = tmp_path / "memory"
    unconverted = _repository(memory, {"onboarding/x.md": b"# x [1]\n"})
    git(memory, "rm", "-q", "onboarding/x.md")
    base = _commit(memory, fixture_tree_files(), "convert")
    broken = {**fixture_tree_files(), INTEGRATE_CARD: b"# integrate.py\n\nSee [4].\n"}
    _commit(memory, broken, "hand edit")
    broken_tree = git(memory, "rev-parse", "HEAD^{tree}")
    validation = GitKnowledgeValidation()

    def refusal(tree: str, bases: list[str]) -> str | None:
        return validation.refusal(
            memory_repository=memory,
            candidate_tree=tree,
            bases=bases,
            code_repository=code_root,
            code_commit=code_commit,
        )

    assert refusal(unconverted, [unconverted]) is None
    assert refusal(base, [unconverted]) is not None  # the conversion commit needs its conversion
    assert refusal(base, [base]) is None
    assert "cannot read" in (refusal(base, ["no-such-ref"]) or "")
    refused = refusal(broken_tree, [base])
    assert refused is not None
    assert f"{INTEGRATE_CARD}: line 3: [R22.3-markers] marker [4] has no reference" in refused


def test_the_worktree_route_never_commits_converted_memory_unvalidated(
    tmp_path: Path, code_repository: tuple[Path, str], worktree_services: None
) -> None:
    code_root, code_commit = code_repository
    memory = tmp_path / "memory"
    unconverted = _repository(memory, {"onboarding/x.md": b"# x [1]\n"})
    git(memory, "rm", "-q", "onboarding/x.md")
    converted = _commit(memory, fixture_tree_files(), "convert")
    paired = PairedCode(code_root, code_commit)
    bound = bound_worktree_services()

    def refusal(tree: str, code: PairedCode | None) -> str | None:
        return memory_commit_refusal(
            memory_repository=memory, candidate_tree=tree, bases=[tree], paired_code=code
        )

    assert refusal(converted, paired) is None
    assert "cannot read" in (refusal("0" * 40, paired) or "")  # a Git failure fails closed
    assert "paired code commit is unknown" in (refusal(converted, None) or "")
    try:
        bind_worktree_services(replace(bound, knowledge_validation=None))
        assert refusal(unconverted, None) is None
        assert "not bound" in (refusal(converted, paired) or "")
    finally:
        bind_worktree_services(bound)


def test_the_managed_sync_refuses_a_merge_with_duplicate_ids_until_it_is_repaired(
    tmp_path: Path, worktree_services: None
) -> None:
    """Rule 2 and rule 8: after the merge, a duplicate ID is a conflict naming both files."""

    fixture = SyncFixture(tmp_path)
    worktree = fixture.contract.memory_worktree
    assert worktree is not None
    work = {**fixture_tree_files(), LEFT_DUPLICATE: encode(invariant_document("INV-D0P111"))}
    before = _commit(worktree, work, "Work: mint an invariant")
    code_tip = fixture.move_official_code()
    source = {**fixture_tree_files(), RIGHT_DUPLICATE: encode(invariant_document("INV-D0P111"))}
    _commit(fixture.memory_repo, source, render_memory_content_message("Source mint", code_tip))
    source_tip = git(fixture.memory_repo, "rev-parse", "HEAD")

    refused = fixture.sync(memory_sync_choice="merge-memory")

    assert refused.payload["state"] == "sync-knowledge-validation-refused", refused.payload
    summary = str(refused.payload["summary"])
    assert "merge conflict: ID INV-D0P111" in summary
    assert LEFT_DUPLICATE in summary and RIGHT_DUPLICATE in summary
    assert git(worktree, "rev-parse", "HEAD") == before
    assert git(worktree, "rev-parse", "MERGE_HEAD") == source_tip
    assert "then rerun worktree_sync; or cancel the sync with resolution_action='cancel'" in summary

    git(worktree, "rm", "-q", "-f", RIGHT_DUPLICATE)
    repaired = fixture.sync()

    assert repaired.payload["state"] == "synced", repaired.payload
    assert git(worktree, "rev-list", "--parents", "-n", "1", "HEAD").split()[1:] == [
        before,
        source_tip,
    ]


def test_parallel_minted_ids_sync_cleanly(tmp_path: Path, worktree_services: None) -> None:
    """Packet conforming example: two parallel leaves each mint an invariant."""

    fixture = SyncFixture(tmp_path)
    worktree = fixture.contract.memory_worktree
    assert worktree is not None
    left = invariant_path("INV-AAAAAA")
    right = invariant_path("INV-BBBBBB")
    _commit(worktree, {**fixture_tree_files(), left: encode(invariant_document("INV-AAAAAA"))}, "w")
    code_tip = fixture.move_official_code()
    source = {**fixture_tree_files(), right: encode(invariant_document("INV-BBBBBB"))}
    _commit(fixture.memory_repo, source, render_memory_content_message("Source mint", code_tip))

    result = fixture.sync(memory_sync_choice="merge-memory")

    assert result.payload["state"] == "synced", result.payload
    assert (worktree / left).is_file() and (worktree / right).is_file()


LEAF_HISTORY = "knowledge/history/260928-MIK-L97.json"


def _moved(document: dict[str, Any]) -> None:
    """The official line re-anchors the integrate route's entry (another leaf edited the code)."""

    moved = {"blob": "1" * 40, "content": "sha256:" + "2" * 64}
    document["realizes"][0]["anchor"] = {**document["realizes"][0]["anchor"], **moved}


@pytest.mark.parametrize("own_row", ["consistent", "already wrong"])
def test_a_sync_merge_refuses_a_history_row_only_when_the_leaf_s_own_side_had_it_wrong(
    tmp_path: Path, worktree_services: None, own_row: str
) -> None:
    """MIK-R09 at a sync: a mismatch the merge caused surfaces at the gate, not at the sync.

    The leaf's open row covers the integrate route's entry; the official line then re-anchors that
    entry. A row that agreed with the leaf's own side syncs (``R09-history-rows-merged`` reports it);
    a row that already disagreed there is refused by ``R09-history-rows``.
    """

    fixture = SyncFixture(tmp_path)
    worktree = fixture.contract.memory_worktree
    assert worktree is not None
    files = fixture_tree_files()
    code_tip = fixture.move_official_code()
    _commit(fixture.memory_repo, files, render_memory_content_message("Convert", code_tip))
    assert fixture.sync(memory_sync_choice="merge-memory").payload["state"] == "synced"

    entry = json.loads(files[INTEGRATE_SIDECAR])["realizes"][0]
    anchor = {**entry["anchor"], "path": INTEGRATE}
    after = anchor if own_row == "consistent" else {**anchor, "blob": "3" * 40}
    row = {
        "id": "ROW-000000",
        "items": [],
        "subject": entry["invariant"],
        "disposition": "no_impact",
        "reason": "The route still pairs its commits.",
        "covers": [{"id": entry["id"], "before": anchor, "after": after}],
        "revision": json.loads(files[INVARIANT])["revision"],
    }
    history = {"schema": "ar-history/v1", "leaf": "260928-MIK-L97", "closed": False, "rows": [row]}
    _commit(worktree, {LEAF_HISTORY: encode(history)}, "Work: a row about the route")

    later = _commit(fixture.code_repo, {"src/later.py": b"LATER = 1\n"}, "Later code")
    moved = edit_json(files, INTEGRATE_SIDECAR, _moved)[INTEGRATE_SIDECAR]
    message = render_memory_content_message("Re-anchor", later)
    _commit(fixture.memory_repo, {INTEGRATE_SIDECAR: moved}, message)

    result = fixture.sync(memory_sync_choice="merge-memory")

    if own_row == "consistent":
        assert result.payload["state"] == "synced", result.payload
        assert (worktree / INTEGRATE_SIDECAR).read_bytes() == moved  # the merge moved the entry
    else:
        assert result.payload["state"] == "sync-knowledge-validation-refused", result.payload
        summary = str(result.payload["summary"])
        assert "R09-history-rows" in summary and "R09-history-rows-merged" not in summary


def _cli(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, str]:
    status = cli_main(["knowledge-validate", *args])
    return status, capsys.readouterr().out


def test_the_standalone_command_validates_a_converted_fixture_tree(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], code_repository: tuple[Path, str]
) -> None:
    code_root, code_commit = code_repository
    memory = tmp_path / "memory"
    base = _repository(memory, fixture_tree_files())

    status, out = _cli(capsys, str(memory), "--code", str(code_root), "--base", base)
    assert status == 0, out
    # The one report-only finding is MIK-R27's count of the fixture's exported family.
    assert "passes: 0 violation(s), 1 report-only finding(s)" in out

    (memory / INTEGRATE_CARD).write_bytes(b"# integrate.py\n\nSee [4].\n")
    status, out = _cli(
        capsys, str(memory), "--code", str(code_root), "--code-commit", code_commit, "--json"
    )
    assert status == 1
    document = json.loads(out)
    assert document["ok"] is False
    rules = sorted((one["rule"], one["reportOnly"]) for one in document["violations"])
    assert rules == [("R22.3-markers", False), (LEGACY_COUNT, True)]

    unconverted = tmp_path / "unconverted"
    write_tree(unconverted, {"onboarding/x.md": b"# x [1]\n"})
    status, out = _cli(capsys, str(unconverted), "--code", str(code_root))
    assert status == 0 and "unconverted" in out

    status, out = _cli(capsys, str(memory), "--code", str(code_root), "--base", "no-such-ref")
    assert status == 2 and "cannot read" in out


def test_a_resolved_memory_conflict_that_breaks_validation_is_refused_on_continue(
    tmp_path: Path, worktree_services: None
) -> None:
    """The retained-conflict path: the agent resolves a real conflict into a duplicate ID."""

    fixture = SyncFixture(tmp_path)
    worktree = fixture.contract.memory_worktree
    assert worktree is not None
    work = {
        **fixture_tree_files(),
        LEFT_DUPLICATE: encode(invariant_document("INV-D0P111")),
        "README.md": b"work version\n",
    }
    _commit(worktree, work, "Work: mint an invariant")
    code_tip = fixture.move_official_code()
    source = {
        **fixture_tree_files(),
        RIGHT_DUPLICATE: encode(invariant_document("INV-D0P111")),
        "README.md": b"source version\n",
    }
    _commit(fixture.memory_repo, source, render_memory_content_message("Source mint", code_tip))
    conflicted = fixture.sync(memory_sync_choice="merge-memory")
    assert conflicted.payload["state"] == "sync-resolution-required", conflicted.payload
    (worktree / "README.md").write_text("resolved\n", encoding="utf-8")
    git(worktree, "add", "README.md")

    refused = fixture.sync(resolution_action="continue")

    assert refused.payload["state"] == "sync-knowledge-validation-refused", refused.payload
    summary = str(refused.payload["summary"])
    assert "merge conflict: ID INV-D0P111" in summary
    assert "rerun worktree_sync with resolution_action='continue'" in summary
    git(worktree, "rm", "-q", "-f", RIGHT_DUPLICATE)
    assert fixture.sync(resolution_action="continue").payload["state"] == "synced"
