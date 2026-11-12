"""One leaf's explainer environment: build, Update, Retire and their confinement (MIK-R186).

Each case protects one environment operation: exact detached worktrees and binding last,
unchanged Update, in-place Update and failure recovery, process retirement and Git registration
cleanup, and physical confinement of metadata and ordinary workspace entries.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from unittest import mock

import pytest
from agents_remember.canvas import explainer_workspace
from agents_remember.canvas.explainer_paths import (
    binding_path,
    generation_path,
    leaf_workspace,
    retire_receipts_root,
)
from agents_remember.canvas.explainer_state import (
    ProcessRecord,
    ProcessTree,
    RetireReceipt,
    agent_id,
    end_processes,
    generation,
    process_tree,
    raise_generation,
    read_binding,
    read_receipt,
    receipt_path,
    write_receipt,
)
from agents_remember.canvas.explainer_workspace import (
    EnvironmentInputs,
    EnvironmentRef,
    EnvironmentRun,
    RetireRequest,
    build_environment,
    plan_retire,
    retire_environment,
    update_environment,
)
from agents_remember.canvas.paths import PathRefused
from agents_remember.models.knowledge.review_frame import FrameBinding
from agents_remember_test_support.testing.waits import HANG_GUARD_SECONDS
from canvas_host_test_support import LEAF, TASK, config, packet, record

NOW = "2026-10-22T08:00:00+00:00"
REPOSITORY = "agents-remember"


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def _write(repo: Path, files: dict[str, str]) -> None:
    for path, text in files.items():
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")


def _repository(root: Path, name: str, files: dict[str, str]) -> Path:
    repo = root / name
    repo.mkdir(parents=True)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "test")
    _write(repo, files)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "one")
    return repo


def _tree(repo: Path, commit: str = "HEAD") -> str:
    return _git(repo, "rev-parse", f"{commit}^{{tree}}")


def _inputs(code: Path, memory: Path, number: int = 7) -> EnvironmentInputs:
    comparison = record(number)
    binding = FrameBinding.from_packet(packet(comparison).comparison).model_copy(
        update={
            "code_base_tree": _tree(code),
            "code_candidate_tree": _tree(code),
            "memory_base_tree": _tree(memory),
            "memory_candidate_tree": _tree(memory),
            "compared_memory_base_tree": _tree(memory),
        }
    )
    return EnvironmentInputs(
        binding=binding,
        packet=packet(comparison),
        capsule="You are the explainer.\n",
        code_repository=code,
        memory_repository=memory,
        record_digest="sha256:" + "0" * 64,
    )


def _ref(runtime) -> EnvironmentRef:
    return EnvironmentRef(config=runtime, repository_id=REPOSITORY, task=TASK, leaf_id=LEAF)


def _receipts(runtime) -> Path:
    task_folder = runtime.coordination_root / "tasks" / REPOSITORY / TASK
    task_folder.mkdir(parents=True, exist_ok=True)
    return retire_receipts_root(task_folder, LEAF)


def test_a_build_checks_out_both_bound_trees_and_binds_last(tmp_path: Path) -> None:
    code = _repository(tmp_path, "code", {"src/app.py": "x = 1\n", "README.md": "hi\n"})
    memory = _repository(tmp_path, "memory", {"system/settings.md": "s\n"})
    runtime = config(tmp_path)
    workspace = leaf_workspace(runtime, REPOSITORY, TASK, LEAF)
    seen = []

    def observe(step, _facts):
        if step != "bound":
            assert read_binding(workspace) is None
        seen.append(step)

    build = build_environment(
        EnvironmentRun(
            ref=_ref(runtime),
            inputs=_inputs(code, memory),
            now=NOW,
            progress=observe,
        )
    )
    assert build.binding.snapshot is None
    for side, repository in (("code", code), ("memory", memory)):
        checkout = workspace / "trees" / side
        assert _tree(checkout) == _tree(repository)
        assert str(checkout) in _git(repository, "worktree", "list", "--porcelain")
        assert _git(checkout, "branch", "--show-current") == ""
    assert (workspace / "trees/code/src/app.py").read_text() == "x = 1\n"
    assert build.code is not None and build.code.files == 2
    assert read_binding(workspace) == build.binding
    assert (workspace / "instructions/evidence.json").is_file()
    assert (workspace / "instructions/assignment.txt").read_text() == "You are the explainer.\n"
    assert (workspace / "scratch").is_dir() and (workspace / "drafts").is_dir()
    assert seen[-1] == build.steps[-1] == "bound"


def test_an_unchanged_update_answers_current_and_writes_nothing(tmp_path: Path) -> None:
    code = _repository(tmp_path, "code", {"a.py": "1\n"})
    memory = _repository(tmp_path, "memory", {"m.md": "m\n"})
    runtime = config(tmp_path)
    run = EnvironmentRun(ref=_ref(runtime), inputs=_inputs(code, memory), now=NOW)
    build = build_environment(run)
    before = binding_path(build.workspace).read_bytes()
    update = update_environment(
        EnvironmentRun(ref=run.ref, inputs=run.inputs, now=NOW, previous=build.binding)
    )
    assert update.current
    assert update.code is None and update.memory is None
    assert binding_path(build.workspace).read_bytes() == before


def test_a_moved_update_reuses_both_registered_worktrees(tmp_path: Path) -> None:
    code = _repository(tmp_path, "code", {"a.py": "1\n", "b.py": "2\n"})
    memory = _repository(tmp_path, "memory", {"m.md": "m\n"})
    runtime = config(tmp_path)
    build = build_environment(
        EnvironmentRun(ref=_ref(runtime), inputs=_inputs(code, memory, 7), now=NOW)
    )
    original_metadata = {
        side: (build.workspace / "trees" / side / ".git").read_bytes()
        for side in ("code", "memory")
    }
    _write(code, {"b.py": "changed\n", "c.py": "3\n"})
    _git(code, "add", "-A")
    _git(code, "commit", "-qm", "two")
    _write(memory, {"m.md": "changed memory\n"})
    _git(memory, "add", "-A")
    _git(memory, "commit", "-qm", "two")

    def observe(step, _facts):
        if step != "bound":
            assert read_binding(build.workspace) is None

    update = update_environment(
        EnvironmentRun(
            ref=_ref(runtime),
            inputs=_inputs(code, memory, 8),
            now=NOW,
            previous=build.binding,
            progress=observe,
        )
    )
    assert not update.current
    assert update.binding.snapshot == build.binding.snapshot is None
    for side, repository in (("code", code), ("memory", memory)):
        checkout = build.workspace / "trees" / side
        assert (checkout / ".git").read_bytes() == original_metadata[side]
        assert _tree(checkout) == _tree(repository)
        assert _git(repository, "worktree", "list", "--porcelain").count("worktree ") == 2
    assert (build.workspace / "trees/code/b.py").read_text() == "changed\n"
    assert (build.workspace / "trees/memory/m.md").read_text() == "changed memory\n"
    assert not list(build.workspace.glob("snapshot-*"))
    assert read_binding(build.workspace) == update.binding


def test_retire_ends_the_seats_processes_and_leaves_a_receipt(tmp_path: Path) -> None:
    # The seat counter lies with the task (MIK-R211 v5 rule 12), so the task folder exists.
    (tmp_path / "tasks" / REPOSITORY / TASK).mkdir(parents=True)
    code = _repository(tmp_path, "code", {"a.py": "1\n"})
    memory = _repository(tmp_path, "memory", {"m.md": "m\n"})
    runtime = config(tmp_path)
    build = build_environment(
        EnvironmentRun(ref=_ref(runtime), inputs=_inputs(code, memory), now=NOW)
    )
    workspace = build.workspace
    seat_number = raise_generation(generation_path(runtime, REPOSITORY, TASK, LEAF))
    seat = agent_id(workspace, seat_number)
    child = subprocess.Popen(
        ["sleep", "300"],
        env={
            **os.environ,
            "AR_EXPLAINER_WORKSPACE": str(workspace),
            "PASEO_AGENT_ID": seat,
        },
    )
    try:
        tree = process_tree(workspace, seat)
        assert [item.pid for item in tree.processes] == [child.pid]
        receipts = _receipts(runtime)
        outcome = retire_environment(
            RetireRequest(
                ref=_ref(runtime),
                receipts_root=receipts,
                receipt_id="receipt-1",
                generation=seat_number,
                now=NOW,
            )
        )
        child.wait(timeout=HANG_GUARD_SECONDS)
    finally:
        if child.poll() is None:
            child.kill()
    assert outcome.receipt.state == "finished"
    assert outcome.receipt.ended_by_marker >= 1
    assert outcome.receipt.ended_by_tree >= 1
    assert outcome.receipt.remaining == ()
    # MIK-R211 v5 rule 12: the folder is gone completely, and no Git worktree entry is left.
    assert not workspace.exists()
    assert list(workspace.parent.iterdir()) == []
    for repository in (code, memory):
        assert _git(repository, "worktree", "list", "--porcelain").count("worktree ") == 1
    assert (code / "a.py").read_text() == "1\n"
    assert (memory / "m.md").read_text() == "m\n"
    stored_receipt = read_receipt(receipts, "receipt-1")
    assert stored_receipt is not None and stored_receipt.state == "finished"


def test_a_retire_preview_touches_nothing(tmp_path: Path) -> None:
    runtime = config(tmp_path)
    leaf = leaf_workspace(runtime, REPOSITORY, TASK, LEAF)
    leaf.mkdir(parents=True)
    preview = plan_retire(_ref(runtime), generation=2)
    assert preview["generation"] == 2
    assert preview["exists"] is True
    assert preview["agentId"] == agent_id(leaf, 2)
    assert list(leaf.iterdir()) == []


def test_a_symlink_in_the_workspace_is_held_and_reported(tmp_path: Path) -> None:
    code = _repository(tmp_path, "code", {"a.py": "1\n"})
    memory = _repository(tmp_path, "memory", {"m.md": "m\n"})
    runtime = config(tmp_path)
    build = build_environment(
        EnvironmentRun(ref=_ref(runtime), inputs=_inputs(code, memory), now=NOW)
    )
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("keep\n")
    (build.workspace / "escape").symlink_to(outside)
    seat_number = raise_generation(generation_path(runtime, REPOSITORY, TASK, LEAF))
    outcome = retire_environment(
        RetireRequest(
            ref=_ref(runtime),
            receipts_root=_receipts(runtime),
            receipt_id="receipt-2",
            generation=seat_number,
            now=NOW,
        )
    )
    assert outcome.held
    assert not outcome.workspace_removed
    assert outcome.receipt.state == "pending"
    assert (outside / "keep.txt").read_text() == "keep\n"
    assert (build.workspace / "escape").is_symlink()


def test_the_generation_only_grows_and_each_seat_has_its_own_id(tmp_path: Path) -> None:
    runtime = config(tmp_path)
    workspace = leaf_workspace(runtime, REPOSITORY, TASK, LEAF)
    workspace.mkdir(parents=True)
    counter = generation_path(runtime, REPOSITORY, TASK, LEAF)
    assert generation(counter) == 0
    first = raise_generation(counter)
    second = raise_generation(counter)
    assert (first, second) == (1, 2)
    assert generation(counter) == 2
    assert agent_id(workspace, first) != agent_id(workspace, second)


def test_receipt_id_validation_rejects_traversal_and_separators_in_internal_writers(
    tmp_path: Path,
) -> None:
    """MIK-R186 rule 8: internal receipt paths and writers reject malformed receipt ids."""

    runtime = config(tmp_path)
    receipts = _receipts(runtime)
    workspace = leaf_workspace(runtime, REPOSITORY, TASK, LEAF)
    workspace.mkdir(parents=True)
    sentinel = tmp_path / "outside.json"
    sentinel.write_text("keep\n")
    sample = RetireReceipt(
        receipt_id="valid-id",
        leaf_id=LEAF,
        agent_id="agent-1",
        generation=1,
        tree=ProcessTree(agent_id="agent-1", workspace=str(workspace)),
        recorded_at=NOW,
    )
    for bad in ("../outside", "/tmp/outside", "a/b", "a\\b", ".hidden", ""):
        with pytest.raises(PathRefused):
            receipt_path(receipts, bad)
        with pytest.raises(PathRefused):
            read_receipt(receipts, bad)
        if bad:
            with pytest.raises(PathRefused):
                write_receipt(receipts, sample.model_copy(update={"receipt_id": bad}))
    assert sentinel.read_text() == "keep\n"


def test_every_metadata_writer_guards_ancestors_and_retire_holds_nested_symlinks(
    tmp_path: Path,
) -> None:
    """MIK-R186 rule 2: instructions/metadata writers guard ancestors; retire holds nested symlinks."""

    code = _repository(tmp_path, "code", {"a.py": "1\n"})
    memory = _repository(tmp_path, "memory", {"m.md": "m\n"})
    runtime = config(tmp_path)
    build = build_environment(
        EnvironmentRun(ref=_ref(runtime), inputs=_inputs(code, memory, 7), now=NOW)
    )
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("keep\n")

    # Replace instructions/ with a symlink to outside: Update and capsule writer must refuse.
    shutil.rmtree(build.workspace / "instructions")
    (build.workspace / "instructions").symlink_to(outside)
    with pytest.raises(PathRefused):
        update_environment(
            EnvironmentRun(
                ref=_ref(runtime),
                inputs=_inputs(code, memory, 8),
                now=NOW,
                previous=build.binding,
            )
        )
    assert not (outside / "evidence.json").exists()
    assert not (outside / "assignment.txt").exists()
    (build.workspace / "instructions").unlink()
    (build.workspace / "instructions").mkdir()

    # Nested symlink inside scratch/sub/link is held and not removed via rmtree during retire.
    nested_dir = build.workspace / "scratch" / "sub"
    nested_dir.mkdir(parents=True)
    nested_link = nested_dir / "escape"
    nested_link.symlink_to(outside)
    outcome = retire_environment(
        RetireRequest(
            ref=_ref(runtime),
            receipts_root=_receipts(runtime),
            receipt_id="retire-nested",
            generation=1,
            now=NOW,
        )
    )
    assert not outcome.workspace_removed
    assert outcome.receipt.state == "pending"
    assert any(str(nested_link) in item for item in outcome.held)
    assert nested_link.is_symlink()
    assert (outside / "keep.txt").read_text() == "keep\n"


def test_a_failed_binding_write_leaves_no_claim_and_retry_binds_the_same_worktrees(
    tmp_path: Path,
) -> None:
    code = _repository(tmp_path, "code", {"a.py": "1\n"})
    memory = _repository(tmp_path, "memory", {"m.md": "m\n"})
    runtime = config(tmp_path)
    ref = _ref(runtime)
    build = build_environment(EnvironmentRun(ref=ref, inputs=_inputs(code, memory, 7), now=NOW))
    _write(code, {"a.py": "2\n"})
    _git(code, "add", "-A")
    _git(code, "commit", "-qm", "two")
    with (
        mock.patch.object(explainer_workspace, "write_binding", side_effect=OSError("disk full")),
        pytest.raises(OSError),
    ):
        update_environment(
            EnvironmentRun(
                ref=ref,
                inputs=_inputs(code, memory, 8),
                now=NOW,
                previous=build.binding,
            )
        )
    assert read_binding(build.workspace) is None
    assert (build.workspace / "trees/code/a.py").read_text() == "2\n"
    recovered = update_environment(
        EnvironmentRun(ref=ref, inputs=_inputs(code, memory, 8), now=NOW)
    )
    assert recovered.previous_number is None
    assert read_binding(build.workspace) == recovered.binding
    assert recovered.binding.binding.comparison_number == 8
    assert _git(code, "worktree", "list", "--porcelain").count("worktree ") == 2
    assert _git(memory, "worktree", "list", "--porcelain").count("worktree ") == 2


def test_process_matching_and_signalling_use_exact_markers_and_start_time_identity(
    tmp_path: Path,
) -> None:
    """MIK-R186 rule 8: exact env marker equality and per-signal (pid, start_time) verification."""

    runtime = config(tmp_path)
    workspace = leaf_workspace(runtime, REPOSITORY, TASK, LEAF)
    workspace.mkdir(parents=True)
    seat = agent_id(workspace, 1)
    neighbor = subprocess.Popen(
        ["sleep", "300"],
        env={
            **os.environ,
            "AR_EXPLAINER_WORKSPACE": f"{workspace}-neighbor",
            "PASEO_AGENT_ID": f"{seat}-neighbor",
        },
    )
    actual = subprocess.Popen(
        ["sleep", "300"],
        env={
            **os.environ,
            "AR_EXPLAINER_WORKSPACE": str(workspace),
            "PASEO_AGENT_ID": seat,
        },
    )
    try:
        tree = process_tree(workspace, seat)
        assert [p.pid for p in tree.processes] == [actual.pid]
        assert tree.measured_memory_bytes > 0
        # Include a synthetic recycled PID record pointing to our own PID with a fake start_time.
        with_recycled = tree.model_copy(
            update={
                "processes": (
                    *tree.processes,
                    ProcessRecord(pid=os.getpid(), start_time="999999999999", marker="agent"),
                )
            }
        )
        by_m, by_t, remaining = end_processes(
            with_recycled,
            workspace=workspace,
            agent=seat,
            timeout=2.0,  # load-independent: end_processes' own contract under test, not a race.
        )
        actual.wait(timeout=HANG_GUARD_SECONDS)
        assert by_m == 1 and by_t == 1 and remaining == ()
        assert neighbor.poll() is None
    finally:
        for proc in (neighbor, actual):
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=HANG_GUARD_SECONDS)
