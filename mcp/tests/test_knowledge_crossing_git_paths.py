"""A structural sync keeps Git's exact index entries for unmodeled paths."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from agents_remember.kernel.git_command import run_git_bytes
from agents_remember.memory.conversion.convert import CONVERSION_FORMAT_VERSION
from agents_remember.models.knowledge_files.canonical import canonical_text
from agents_remember.models.knowledge_files.documents import (
    LAYOUT_MARKER_PATH,
    LAYOUT_MARKER_SCHEMA,
)
from agents_remember.worktrees.sync_transaction_git import start_side_merge
from agents_remember.worktrees.sync_transaction_state import SyncSideRecord
from knowledge_conversion_test_support import commit_files, git, init_repository

UNCHANGED = "onboarding/.hidden/unchanged.index.json"
ONE_SIDE = "knowledge/one-side.index.json"
DELETE_EDIT = "knowledge/delete-edit.index.json"
LINK = "onboarding/becomes-link"
GITLINK = "onboarding/module"
MODE_EDIT = "onboarding/mode-edit"
FILE_TO_DIRECTORY = "onboarding/becomes-directory"
CHILD = f"{FILE_TO_DIRECTORY}/child"


def _sides(repo: Path) -> tuple[str, str, str]:
    init_repository(repo)
    first = commit_files(
        repo,
        {
            LAYOUT_MARKER_PATH: canonical_text(
                {"schema": LAYOUT_MARKER_SCHEMA, "conversion": CONVERSION_FORMAT_VERSION}
            ),
            UNCHANGED: "keep this content\n",
            ONE_SIDE: "before\n",
            DELETE_EDIT: "before\n",
            LINK: "regular file\n",
            MODE_EDIT: "before\n",
            FILE_TO_DIRECTORY: "regular file\n",
        },
        "converted base",
    )
    git(repo, "update-index", "--add", "--cacheinfo", f"160000,{first},{GITLINK}")
    git(repo, "commit", "-q", "-m", "add tracked gitlink")
    base = git(repo, "rev-parse", "HEAD")
    git(repo, "branch", "incoming")
    git(repo, "rm", "--", DELETE_EDIT)
    (repo / MODE_EDIT).write_text("own edit\n", encoding="utf-8")
    git(repo, "add", "--", MODE_EDIT)
    git(repo, "commit", "-q", "-m", "own changes")
    own = git(repo, "rev-parse", "HEAD")

    git(repo, "switch", "incoming")
    (repo / ONE_SIDE).write_text("incoming edit\n", encoding="utf-8")
    (repo / DELETE_EDIT).write_text("incoming edit\n", encoding="utf-8")
    executable = repo / MODE_EDIT
    executable.write_text("incoming edit\n", encoding="utf-8")
    executable.chmod(0o755)
    (repo / LINK).unlink()
    (repo / LINK).symlink_to("mode-edit")
    git(repo, "rm", "--", FILE_TO_DIRECTORY)
    (repo / FILE_TO_DIRECTORY).mkdir()
    (repo / CHILD).write_text("child in incoming tree\n", encoding="utf-8")
    git(repo, "add", "--", ONE_SIDE, DELETE_EDIT, LINK, MODE_EDIT, CHILD)
    git(repo, "update-index", "--chmod=+x", "--", MODE_EDIT)
    git(repo, "commit", "-q", "-m", "incoming changes")
    incoming = git(repo, "rev-parse", "HEAD")
    git(repo, "switch", "main")
    (repo / GITLINK).mkdir(exist_ok=True)
    return base, own, incoming


def _index_entries(root: Path) -> dict[str, list[tuple[str, str, int]]]:
    listed = run_git_bytes(
        root,
        [
            "ls-files",
            "--stage",
            "-z",
            "--",
            UNCHANGED,
            ONE_SIDE,
            DELETE_EDIT,
            LINK,
            GITLINK,
            MODE_EDIT,
            FILE_TO_DIRECTORY,
        ],
    )
    assert listed.returncode == 0, listed.stderr
    entries: dict[str, list[tuple[str, str, int]]] = {}
    for row in listed.stdout.rstrip(b"\0").split(b"\0"):
        fields, path = row.split(b"\t", 1)
        mode, object_id, stage = fields.decode("ascii").split()
        entries.setdefault(os.fsdecode(path), []).append((mode, object_id, int(stage)))
    return entries


@pytest.mark.usefixtures("worktree_services")
def test_a_structural_sync_keeps_gits_unmodeled_entries(tmp_path: Path) -> None:
    repo = tmp_path / "memory"
    base, own, incoming = _sides(repo)
    comparison = tmp_path / "git-merge"
    git(repo, "worktree", "add", "--detach", comparison.as_posix(), own)
    compared = run_git_bytes(comparison, ["merge", "--no-commit", "--no-edit", incoming])
    assert compared.returncode == 1, compared.stdout + compared.stderr

    side = SyncSideRecord(
        side="memory",
        repository=repo.as_posix(),
        worktree=repo.as_posix(),
        sourceBranch="incoming",
        workBranch="main",
        sourceCommit=incoming,
        preSyncHead=own,
        baseCommit=base,
        backupRef="refs/agents-remember/sync/test/memory/own",
        sourceBackupRef="refs/agents-remember/sync/test/memory/incoming",
        baseBackupRef="refs/agents-remember/sync/test/memory/base",
        plan="merge",
    )
    outcome = start_side_merge(side, crossing_owner=("leaf", "260101-FIX-L1"))

    assert outcome.state == "resolution-required", outcome
    assert set(outcome.conflicts) == {DELETE_EDIT, MODE_EDIT}
    unchanged = ("100644", git(repo, "rev-parse", f"{base}:{UNCHANGED}"), 0)
    changed = ("100644", git(repo, "rev-parse", f"{incoming}:{ONE_SIDE}"), 0)
    entries = _index_entries(repo)
    assert entries == _index_entries(comparison)
    assert entries[UNCHANGED] == [unchanged]
    assert entries[ONE_SIDE] == [changed]
    assert [stage for _, _, stage in entries[DELETE_EDIT]] == [1, 3]
    assert [mode for mode, _, _ in entries[MODE_EDIT]] == ["100644", "100644", "100755"]
    assert entries[LINK][0][0] == "120000"
    assert entries[GITLINK][0][0] == "160000"
    assert FILE_TO_DIRECTORY not in entries and entries[CHILD][0][2] == 0
    assert (repo / LINK).is_symlink()
    assert (repo / GITLINK).is_dir()
