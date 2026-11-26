"""Focused tests for bounded continued-sync commit checks (L29-R1, L29-R4)."""

from __future__ import annotations

import json
import os
import re
import signal
import time
from collections.abc import Iterator
from contextlib import suppress
from pathlib import Path

import pytest
from agents_remember.application.worktree_services import build_default_worktree_services
from agents_remember.worktrees.integration.integration_ref_transaction import (
    _require_landing_commit_check,
)
from agents_remember.worktrees.modules.commit_check_process import (
    BoundedCommandSpec,
    CommitCheckProcessOutcome,
    CommitCheckRunResult,
)
from agents_remember.worktrees.modules.landing_commit_check import (
    LandingCommitCheckRefusal,
    commit_check_fact,
    commit_check_trailer,
)
from agents_remember.worktrees.services import bind_worktree_services, reset_worktree_services
from agents_remember.worktrees.sync_transaction_git import (
    _code_merge_commit as sync_code_merge_commit,
)
from agents_remember.worktrees.sync_transaction_state import (
    SyncOperationRecord,
    SyncOperationStore,
)
from test_worktree_sync import SyncFixture, commit_file, git


@pytest.fixture(autouse=True)
def _bind_services() -> Iterator[None]:
    bind_worktree_services(build_default_worktree_services())
    try:
        yield
    finally:
        reset_worktree_services()


def _pid_alive(pid: int) -> bool:
    """Whether ``pid`` is still live (a reaped or zombie process counts as dead)."""

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except OSError:
        return False
    return stat.rsplit(")", 1)[1].split()[0] != "Z"


def _write_hook(repo: Path, body: str) -> Path:
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.write_text(body, encoding="utf-8")
    hook.chmod(0o755)
    return hook


def _write_timeout_setting(repo: Path, seconds: int) -> Path:
    system = repo / "system"
    system.mkdir(exist_ok=True)
    path = system / "settings.json"
    path.write_text(
        json.dumps({"orchestration": {"landing": {"commitCheckTimeoutSeconds": seconds}}}),
        encoding="utf-8",
    )
    return path


def _retained_code_conflict(
    tmp_path: Path, *, resolution_text: str = "resolved version\n"
) -> tuple[SyncFixture, str, str, str]:
    """Start a real retained code conflict, resolve and stage it, returning pinned SHAs."""

    fixture = SyncFixture(tmp_path)
    worktree = fixture.contract.code_worktree
    commit_file(worktree, "README.md", "work-branch version")
    commit_file(fixture.code_repo, "README.md", "official version")
    code_tip = git(fixture.code_repo, "rev-parse", "main")
    fixture.map_official_memory(code_tip)
    pre_sync = git(worktree, "rev-parse", "HEAD")

    started = fixture.sync()
    assert started.payload["state"] == "sync-resolution-required", started.payload
    assert git(worktree, "rev-parse", "MERGE_HEAD") == code_tip

    (worktree / "README.md").write_text(resolution_text, encoding="utf-8")
    git(worktree, "add", "README.md")
    resolved_tree = git(worktree, "write-tree")
    return fixture, pre_sync, code_tip, resolved_tree


def _extract_log_path(summary: str) -> Path:
    match = re.search(r"hook log: ([^\s;]+)", summary)
    assert match is not None, summary
    return Path(match.group(1))


def _wait_dead(pid: int, timeout_s: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if not _pid_alive(pid):
            return True
        time.sleep(0.05)
    return not _pid_alive(pid)


def test_rule4_silent_hanging_hook_times_out_kills_group_and_resumes_cleanly(
    tmp_path: Path,
) -> None:
    """L29-R4: timeout kills hook + child, preserves retained merge, and lets continue finish."""

    fixture, pre_sync, code_tip, resolved_tree = _retained_code_conflict(tmp_path)
    worktree = fixture.contract.code_worktree
    _write_timeout_setting(fixture.code_repo, 2)

    hook_pid_file = tmp_path / "hook.pid"
    child_pid_file = tmp_path / "child.pid"
    _write_hook(
        fixture.code_repo,
        "#!/bin/sh\n"
        f"echo $$ > {hook_pid_file.as_posix()}\n"
        f"sleep 60 &\n"
        f"echo $! > {child_pid_file.as_posix()}\n"
        "wait\n",
    )

    strays: list[int] = []
    try:
        stopped = fixture.sync(resolution_action="continue")
        assert stopped.returncode == 2
        assert stopped.payload["state"] == "sync-commit-check-failed", stopped.payload
        summary = str(stopped.payload["summary"])
        assert "time limit" in summary
        assert "2s" in summary
        log_path = _extract_log_path(summary)
        assert log_path.is_file(), log_path
        log_text = log_path.read_text(encoding="utf-8")
        assert "timeout" in log_text
        assert re.search(r"elapsed: \d+\.\d+s", log_text) is not None

        hook_pid = int(hook_pid_file.read_text(encoding="utf-8").strip())
        child_pid = int(child_pid_file.read_text(encoding="utf-8").strip())
        strays.extend([hook_pid, child_pid])
        assert _wait_dead(hook_pid), f"hook pid {hook_pid} still alive"
        assert _wait_dead(child_pid), f"child pid {child_pid} still alive"

        record = SyncOperationStore(fixture.contract.worktree_group).read()
        assert isinstance(record, SyncOperationRecord)
        assert record.phase == "code-resolution-required"
        assert git(worktree, "rev-parse", "HEAD") == pre_sync
        assert git(worktree, "rev-parse", "MERGE_HEAD") == code_tip
        assert git(worktree, "write-tree") == resolved_tree

        counter_file = tmp_path / "hook-runs.log"
        _write_hook(
            fixture.code_repo,
            f"#!/bin/sh\necho ran >> {counter_file.as_posix()}\nexit 0\n",
        )
        continued = fixture.sync(resolution_action="continue")
        assert continued.returncode == 0, continued.payload
        assert continued.payload["state"] == "synced", continued.payload
        assert counter_file.read_text(encoding="utf-8").splitlines() == ["ran"]
        parents = git(worktree, "rev-list", "--parents", "-n", "1", "HEAD").split()[1:]
        assert parents == [pre_sync, code_tip]
        assert git(worktree, "rev-parse", "HEAD^{tree}") == resolved_tree
    finally:
        for pid in strays:
            with suppress(OSError):
                os.kill(pid, signal.SIGKILL)


@pytest.mark.parametrize("stage_mutation", [False, True], ids=["unstaged", "staged"])
def test_hook_mutating_tracked_file_and_exiting_zero_is_refused(
    tmp_path: Path, stage_mutation: bool
) -> None:
    """A hook that changes a tracked file during continued sync's commit is refused."""

    fixture, pre_sync, code_tip, resolved_tree = _retained_code_conflict(tmp_path)
    worktree = fixture.contract.code_worktree
    stage_line = "git add README.md\n" if stage_mutation else ""
    _write_hook(
        fixture.code_repo,
        f"#!/bin/sh\nprintf 'mutated by hook\\n' > README.md\n{stage_line}exit 0\n",
    )

    stopped = fixture.sync(resolution_action="continue")

    assert stopped.returncode == 2
    assert stopped.payload["state"] == "sync-commit-check-failed", stopped.payload
    summary = str(stopped.payload["summary"])
    assert "README.md" in summary
    assert "modified tracked file" in summary
    assert _extract_log_path(summary).is_file()
    record = SyncOperationStore(fixture.contract.worktree_group).read()
    assert isinstance(record, SyncOperationRecord)
    assert record.phase == "code-resolution-required"
    assert git(worktree, "rev-parse", "HEAD") == pre_sync
    assert git(worktree, "rev-parse", "MERGE_HEAD") == code_tip
    assert git(worktree, "write-tree") == resolved_tree

    _write_hook(fixture.code_repo, "#!/bin/sh\nexit 0\n")
    (worktree / "README.md").write_text("resolved version\n", encoding="utf-8")
    git(worktree, "add", "README.md")
    continued = fixture.sync(resolution_action="continue")
    assert continued.payload["state"] == "synced", continued.payload
    assert git(worktree, "rev-parse", "HEAD^{tree}") == resolved_tree


def test_hook_mutating_tracked_file_and_exiting_nonzero_is_refused(tmp_path: Path) -> None:
    """A failing hook that also edits a tracked file still reports sync-commit-check-failed."""

    fixture, pre_sync, code_tip, _resolved_tree = _retained_code_conflict(tmp_path)
    worktree = fixture.contract.code_worktree
    _write_hook(
        fixture.code_repo,
        "#!/bin/sh\nprintf 'mutated\\n' > README.md\necho 'hook refused tree' >&2\nexit 1\n",
    )

    stopped = fixture.sync(resolution_action="continue")

    assert stopped.returncode == 2
    assert stopped.payload["state"] == "sync-commit-check-failed", stopped.payload
    summary = str(stopped.payload["summary"])
    assert "hook refused tree" in summary
    assert "README.md" in summary
    assert git(worktree, "rev-parse", "HEAD") == pre_sync
    assert git(worktree, "rev-parse", "MERGE_HEAD") == code_tip


def test_empty_staged_diff_still_reports_commit_check_failure(tmp_path: Path) -> None:
    """Resolving identically to HEAD (empty staged diff) still emits sync-commit-check-failed."""

    fixture, pre_sync, code_tip, _resolved_tree = _retained_code_conflict(
        tmp_path, resolution_text="work-branch version\n"
    )
    worktree = fixture.contract.code_worktree
    assert git(worktree, "diff", "--cached", "--name-only") == ""
    _write_hook(fixture.code_repo, "#!/bin/sh\necho 'empty-diff hook failed' >&2\nexit 1\n")

    stopped = fixture.sync(resolution_action="continue")

    assert stopped.returncode == 2
    assert stopped.payload["state"] == "sync-commit-check-failed", stopped.payload
    summary = str(stopped.payload["summary"])
    assert "empty-diff hook failed" in summary
    assert "no file differences staged against HEAD" in summary
    assert git(worktree, "rev-parse", "HEAD") == pre_sync
    assert git(worktree, "rev-parse", "MERGE_HEAD") == code_tip


def test_stale_index_lock_cleared_after_timeout_kill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stale index.lock left by a killed Git commit is cleared before proving retained merge."""

    fixture, pre_sync, code_tip, resolved_tree = _retained_code_conflict(tmp_path)
    worktree = fixture.contract.code_worktree
    git_dir = Path(git(worktree, "rev-parse", "--absolute-git-dir"))
    lock_path = git_dir / "index.lock"
    log_path = tmp_path / "timeout-lock.log"
    log_path.write_text("sync commit check: side=code bound=3s\nelapsed: 3.001s timeout\n")

    def fake_timeout_with_lock(spec: BoundedCommandSpec) -> CommitCheckRunResult:
        lock_path.write_bytes(b"stale lock")
        return CommitCheckRunResult(
            outcome=CommitCheckProcessOutcome(returncode=-9, timed_out=True),
            log=log_path,
            timeout_seconds=3,
            elapsed_seconds=3.001,
        )

    monkeypatch.setattr(sync_code_merge_commit, "run_bounded_command", fake_timeout_with_lock)
    stopped = fixture.sync(resolution_action="continue")

    assert stopped.payload["state"] == "sync-commit-check-failed", stopped.payload
    assert not lock_path.exists(), "stale index.lock must be cleared after timeout kill"
    assert git(worktree, "rev-parse", "HEAD") == pre_sync
    assert git(worktree, "rev-parse", "MERGE_HEAD") == code_tip
    assert git(worktree, "write-tree") == resolved_tree


def test_published_head_proven_when_commit_landed_before_escaped_holder_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the pinned merge commit already landed before timeout cleanup, prove HEAD and complete."""

    fixture, pre_sync, code_tip, resolved_tree = _retained_code_conflict(tmp_path)
    worktree = fixture.contract.code_worktree
    log_path = tmp_path / "escaped-after-commit.log"
    log_path.write_text("sync commit check: side=code bound=4s\nelapsed: 4.010s timeout\n")

    def fake_commit_then_escaped_timeout(spec: BoundedCommandSpec) -> CommitCheckRunResult:
        git(worktree, "commit", "--no-edit")
        return CommitCheckRunResult(
            outcome=CommitCheckProcessOutcome(
                returncode=0, timed_out=True, escaped_survivors=(51515,)
            ),
            log=log_path,
            timeout_seconds=4,
            elapsed_seconds=4.010,
        )

    monkeypatch.setattr(
        sync_code_merge_commit, "run_bounded_command", fake_commit_then_escaped_timeout
    )
    continued = fixture.sync(resolution_action="continue")

    assert continued.returncode == 0, continued.payload
    assert continued.payload["state"] == "synced", continued.payload
    parents = git(worktree, "rev-list", "--parents", "-n", "1", "HEAD").split()[1:]
    assert parents == [pre_sync, code_tip]
    assert git(worktree, "rev-parse", "HEAD^{tree}") == resolved_tree


def test_timeout_with_missing_merge_head_and_unproven_head_refuses_without_staged_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Never emit sync-commit-check-failed staged-recovery when MERGE_HEAD disappeared unproved."""

    fixture, _pre_sync, _code_tip, _resolved_tree = _retained_code_conflict(tmp_path)
    worktree = fixture.contract.code_worktree
    git_dir = Path(git(worktree, "rev-parse", "--absolute-git-dir"))
    log_path = tmp_path / "missing-merge-head.log"
    log_path.write_text("sync commit check: side=code bound=4s\nelapsed: 4.010s timeout\n")

    def fake_disappeared_merge(spec: BoundedCommandSpec) -> CommitCheckRunResult:
        (git_dir / "MERGE_HEAD").unlink()
        return CommitCheckRunResult(
            outcome=CommitCheckProcessOutcome(returncode=-9, timed_out=True),
            log=log_path,
            timeout_seconds=4,
            elapsed_seconds=4.010,
        )

    monkeypatch.setattr(sync_code_merge_commit, "run_bounded_command", fake_disappeared_merge)
    refused = fixture.sync(resolution_action="continue")

    assert refused.returncode == 2
    assert refused.payload["state"] == "sync-resolution-incomplete", refused.payload
    assert "merge state disappeared" in str(refused.payload["summary"])


def test_escaped_holder_pids_named_in_sync_commit_check_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verified live escaped holder PIDs are named in sync-commit-check-failed as in closeout."""

    fixture, pre_sync, code_tip, _resolved_tree = _retained_code_conflict(tmp_path)
    worktree = fixture.contract.code_worktree
    log_path = tmp_path / "escaped-survivors.log"
    log_path.write_text("sync commit check: side=code bound=5s\nelapsed: 5.020s timeout\n")

    def fake_escaped_survivors(spec: BoundedCommandSpec) -> CommitCheckRunResult:
        return CommitCheckRunResult(
            outcome=CommitCheckProcessOutcome(
                returncode=-9,
                timed_out=True,
                escaped_survivors=(42424, 42425),
            ),
            log=log_path,
            timeout_seconds=5,
            elapsed_seconds=5.020,
        )

    monkeypatch.setattr(sync_code_merge_commit, "run_bounded_command", fake_escaped_survivors)
    stopped = fixture.sync(resolution_action="continue")

    assert stopped.returncode == 2
    assert stopped.payload["state"] == "sync-commit-check-failed", stopped.payload
    summary = str(stopped.payload["summary"])
    assert "5s" in summary
    assert "pid=42424" in summary
    assert "pid=42425" in summary
    assert "escaped descendant(s) are still alive" in summary
    assert log_path.as_posix() in summary
    assert git(worktree, "rev-parse", "HEAD") == pre_sync
    assert git(worktree, "rev-parse", "MERGE_HEAD") == code_tip


def test_hook_staging_tracked_mutation_then_exiting_nonzero_restores_pinned_index(
    tmp_path: Path,
) -> None:
    """A failing hook that stages a tracked mutation rolls the index back to the pinned tree."""

    fixture, pre_sync, code_tip, resolved_tree = _retained_code_conflict(tmp_path)
    worktree = fixture.contract.code_worktree
    _write_hook(
        fixture.code_repo,
        "#!/bin/sh\n"
        "printf 'mutated and staged\\n' > README.md\n"
        "git add README.md\n"
        "echo 'hook failed after staging' >&2\n"
        "exit 42\n",
    )

    stopped = fixture.sync(resolution_action="continue")

    assert stopped.returncode == 2
    assert stopped.payload["state"] == "sync-commit-check-failed", stopped.payload
    summary = str(stopped.payload["summary"])
    assert "hook failed after staging" in summary
    assert "README.md" in summary
    assert git(worktree, "rev-parse", "HEAD") == pre_sync
    assert git(worktree, "rev-parse", "MERGE_HEAD") == code_tip
    assert git(worktree, "write-tree") == resolved_tree

    _write_hook(fixture.code_repo, "#!/bin/sh\nexit 0\n")
    uncleaned = fixture.sync(resolution_action="continue")
    assert uncleaned.returncode == 2
    assert uncleaned.payload["state"] == "sync-resolution-incomplete", uncleaned.payload
    assert git(worktree, "rev-parse", "HEAD") == pre_sync

    git(worktree, "checkout", "--", "README.md")
    continued = fixture.sync(resolution_action="continue")
    assert continued.returncode == 0, continued.payload
    assert continued.payload["state"] == "synced", continued.payload
    assert git(worktree, "rev-parse", "HEAD^{tree}") == resolved_tree


def test_hook_staging_tracked_mutation_then_timing_out_restores_pinned_index(
    tmp_path: Path,
) -> None:
    """A timed-out hook that staged a tracked mutation rolls the index back to the pinned tree."""

    fixture, pre_sync, code_tip, resolved_tree = _retained_code_conflict(tmp_path)
    worktree = fixture.contract.code_worktree
    _write_timeout_setting(fixture.code_repo, 2)
    _write_hook(
        fixture.code_repo,
        "#!/bin/sh\nprintf 'mutated before timeout\\n' > README.md\ngit add README.md\nsleep 60\n",
    )

    stopped = fixture.sync(resolution_action="continue")

    assert stopped.returncode == 2
    assert stopped.payload["state"] == "sync-commit-check-failed", stopped.payload
    summary = str(stopped.payload["summary"])
    assert "2s" in summary
    assert "README.md" in summary
    assert git(worktree, "rev-parse", "HEAD") == pre_sync
    assert git(worktree, "rev-parse", "MERGE_HEAD") == code_tip
    assert git(worktree, "write-tree") == resolved_tree

    _write_hook(fixture.code_repo, "#!/bin/sh\nexit 0\n")
    uncleaned = fixture.sync(resolution_action="continue")
    assert uncleaned.returncode == 2
    assert uncleaned.payload["state"] == "sync-resolution-incomplete", uncleaned.payload
    assert git(worktree, "rev-parse", "HEAD") == pre_sync

    git(worktree, "checkout", "--", "README.md")
    continued = fixture.sync(resolution_action="continue")
    assert continued.returncode == 0, continued.payload
    assert continued.payload["state"] == "synced", continued.payload
    assert git(worktree, "rev-parse", "HEAD^{tree}") == resolved_tree


@pytest.mark.parametrize(
    "mode",
    ["nonzero_unstaged", "nonzero_staged", "timeout_staged"],
)
def test_hook_mutating_nonstaged_tracked_file_is_named_in_refusal_summary(
    tmp_path: Path, mode: str
) -> None:
    """Tracked files changed by a hook outside the staged resolution are named in the summary."""

    fixture = SyncFixture(tmp_path)
    worktree = fixture.contract.code_worktree
    commit_file(worktree, "other.txt", "original other\n")
    commit_file(worktree, "README.md", "work-branch version")
    commit_file(fixture.code_repo, "README.md", "official version")
    code_tip = git(fixture.code_repo, "rev-parse", "main")
    fixture.map_official_memory(code_tip)
    pre_sync = git(worktree, "rev-parse", "HEAD")

    started = fixture.sync()
    assert started.payload["state"] == "sync-resolution-required", started.payload
    (worktree / "README.md").write_text("resolved version\n", encoding="utf-8")
    git(worktree, "add", "README.md")
    assert git(worktree, "diff", "--cached", "--name-only") == "README.md"
    resolved_tree = git(worktree, "write-tree")

    if mode == "nonzero_unstaged":
        _write_hook(
            fixture.code_repo,
            "#!/bin/sh\nprintf 'mutated\\n' > other.txt\necho 'hook failed' >&2\nexit 1\n",
        )
    elif mode == "nonzero_staged":
        _write_hook(
            fixture.code_repo,
            "#!/bin/sh\n"
            "printf 'mutated\\n' > other.txt\n"
            "git add other.txt\n"
            "echo 'hook failed' >&2\n"
            "exit 1\n",
        )
    else:
        _write_timeout_setting(fixture.code_repo, 2)
        _write_hook(
            fixture.code_repo,
            "#!/bin/sh\nprintf 'mutated\\n' > other.txt\ngit add other.txt\nsleep 60\n",
        )

    stopped = fixture.sync(resolution_action="continue")

    assert stopped.returncode == 2
    assert stopped.payload["state"] == "sync-commit-check-failed", stopped.payload
    summary = str(stopped.payload["summary"])
    assert "other.txt" in summary, summary
    assert git(worktree, "rev-parse", "HEAD") == pre_sync
    assert git(worktree, "rev-parse", "MERGE_HEAD") == code_tip
    assert git(worktree, "write-tree") == resolved_tree


def test_r8_synced_leaf_integrates_without_second_hook_run(tmp_path: Path) -> None:
    """L29-R8 (i): stamped continued code sync & hooked memory sync skip a second hook run."""

    fixture = SyncFixture(tmp_path)
    code_worktree = fixture.contract.code_worktree
    mem_worktree = fixture.contract.memory_worktree
    assert mem_worktree is not None

    commit_file(code_worktree, "README.md", "work-branch version")
    commit_file(mem_worktree, "task-note.md", "leaf memory note\n")
    commit_file(fixture.code_repo, "README.md", "official version")
    code_tip = git(fixture.code_repo, "rev-parse", "main")
    fixture.map_official_memory(code_tip)

    started = fixture.sync(memory_sync_choice="merge-memory")
    assert started.payload["state"] == "sync-resolution-required", started.payload
    (code_worktree / "README.md").write_text("resolved version\n", encoding="utf-8")
    git(code_worktree, "add", "README.md")
    resolved_tree = git(code_worktree, "write-tree")

    code_runs = tmp_path / "code-hook-runs.log"
    _write_hook(
        fixture.code_repo,
        f"#!/bin/sh\necho ran >> {code_runs.as_posix()}\nexit 0\n",
    )
    mem_runs = tmp_path / "mem-hook-runs.log"
    _write_hook(
        fixture.memory_repo,
        f"#!/bin/sh\necho ran >> {mem_runs.as_posix()}\nexit 0\n",
    )

    continued = fixture.sync(resolution_action="continue")
    assert continued.returncode == 0, continued.payload
    assert continued.payload["state"] == "synced", continued.payload
    assert code_runs.read_text(encoding="utf-8").splitlines() == ["ran"]
    assert mem_runs.read_text(encoding="utf-8").splitlines() == ["ran"]

    code_commit = git(code_worktree, "rev-parse", "HEAD")
    code_body = git(code_worktree, "log", "-1", "--format=%B", code_commit)
    code_fact = commit_check_fact(code_body)
    assert code_fact is not None, code_body
    assert code_fact[0] == resolved_tree
    assert code_fact[1].endswith("hooks/pre-commit"), code_fact

    _require_landing_commit_check(fixture.code_repo, code_commit, side="code")
    assert code_runs.read_text(encoding="utf-8").splitlines() == ["ran"]

    mem_commit = git(mem_worktree, "rev-parse", "HEAD")
    mem_tree = git(mem_worktree, "rev-parse", f"{mem_commit}^{{tree}}")
    mem_body = git(mem_worktree, "log", "-1", "--format=%B", mem_commit)
    mem_fact = commit_check_fact(mem_body)
    assert mem_fact is not None, mem_body
    assert mem_fact[0] == mem_tree
    assert mem_fact[1].endswith("hooks/pre-commit"), mem_fact
    _require_landing_commit_check(fixture.memory_repo, mem_commit, side="memory")
    assert mem_runs.read_text(encoding="utf-8").splitlines() == ["ran"]


def test_r8_stamped_tree_mismatch_is_still_checked_at_integration(tmp_path: Path) -> None:
    """L29-R8 (ii): a Commit-Check-Pass trailer for a different tree does not bypass integration."""

    fixture, _pre_sync, _code_tip, resolved_tree = _retained_code_conflict(tmp_path)
    worktree = fixture.contract.code_worktree
    runs = tmp_path / "hook-runs.log"
    _write_hook(fixture.code_repo, f"#!/bin/sh\necho ran >> {runs.as_posix()}\nexit 0\n")

    continued = fixture.sync(resolution_action="continue")
    assert continued.payload["state"] == "synced", continued.payload
    assert runs.read_text(encoding="utf-8").splitlines() == ["ran"]

    (worktree / "extra.txt").write_text("diverged after stamp\n", encoding="utf-8")
    git(worktree, "add", "extra.txt")
    other_tree = git(worktree, "write-tree")
    assert other_tree != resolved_tree
    parent = git(worktree, "rev-parse", "HEAD")
    mismatched = git(
        worktree,
        "commit-tree",
        other_tree,
        "-p",
        parent,
        "-m",
        f"stale stamp\n\n{commit_check_trailer(resolved_tree, '.git/hooks/pre-commit')}",
    )

    _require_landing_commit_check(fixture.code_repo, mismatched, side="code")
    assert runs.read_text(encoding="utf-8").splitlines() == ["ran", "ran"]

    _write_hook(fixture.code_repo, "#!/bin/sh\necho 'refused mismatch' >&2\nexit 1\n")
    with pytest.raises(LandingCommitCheckRefusal) as refused:
        _require_landing_commit_check(fixture.code_repo, mismatched, side="code")
    assert refused.value.reason == "failed"


@pytest.mark.parametrize("hook_mode", ["absent", "disabled"])
def test_r8_sync_commit_without_runnable_hook_carries_no_stamp(
    tmp_path: Path, hook_mode: str
) -> None:
    """L29-R8 (v): sync commits carry no Commit-Check-Pass when no runnable pre-commit exists."""

    fixture = SyncFixture(tmp_path)
    code_worktree = fixture.contract.code_worktree
    mem_worktree = fixture.contract.memory_worktree
    assert mem_worktree is not None

    commit_file(code_worktree, "README.md", "work-branch version")
    commit_file(mem_worktree, "task-note.md", "leaf memory note\n")
    commit_file(fixture.code_repo, "README.md", "official version")
    code_tip = git(fixture.code_repo, "rev-parse", "main")
    fixture.map_official_memory(code_tip)

    started = fixture.sync(memory_sync_choice="merge-memory")
    assert started.payload["state"] == "sync-resolution-required", started.payload
    (code_worktree / "README.md").write_text("resolved version\n", encoding="utf-8")
    git(code_worktree, "add", "README.md")

    hook_path = fixture.code_repo / ".git" / "hooks" / "pre-commit"
    if hook_mode == "disabled":
        hook_path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        hook_path.chmod(0o644)
    elif hook_path.exists():
        hook_path.unlink()

    continued = fixture.sync(resolution_action="continue")
    assert continued.returncode == 0, continued.payload
    assert continued.payload["state"] == "synced", continued.payload

    code_body = git(code_worktree, "log", "-1", "--format=%B")
    assert commit_check_fact(code_body) is None, code_body
    assert "Commit-Check-Pass" not in code_body

    mem_parents = git(mem_worktree, "rev-list", "--parents", "-n", "1", "HEAD").split()[1:]
    assert len(mem_parents) == 2
    mem_body = git(mem_worktree, "log", "-1", "--format=%B")
    assert commit_check_fact(mem_body) is None, mem_body
    assert "Commit-Check-Pass" not in mem_body
