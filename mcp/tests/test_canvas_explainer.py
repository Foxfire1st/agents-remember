"""One leaf's explainer environment: build, Update, Retire and their confinement (MIK-R186).

Each case protects one promise the environment makes: the snapshot equals the bound tree, the
binding is written last, an unchanged Update answers ``current`` and writes nothing, a moved Update
links what it can and removes the snapshot it replaced, Retire ends the seat's processes by both
counts and leaves a receipt, and a symlink planted in the folder is held rather than followed.
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
    retired_marker,
)
from agents_remember.canvas.explainer_state import (
    ProcessRecord,
    ProcessTree,
    RetireReceipt,
    agent_id,
    end_processes,
    generation,
    mark_retired,
    process_tree,
    raise_generation,
    read_binding,
    read_receipt,
    receipt_path,
    write_receipt,
)
from agents_remember.canvas.explainer_workspace import (
    MAX_SNAPSHOT_FILE_BYTES,
    EnvironmentInputs,
    EnvironmentRef,
    EnvironmentRun,
    RetireRequest,
    SnapshotTooLarge,
    build_environment,
    count_tree,
    next_snapshot_number,
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


def test_a_build_writes_the_bound_trees_and_binds_last(tmp_path: Path) -> None:
    code = _repository(tmp_path, "code", {"src/app.py": "x = 1\n", "README.md": "hi\n"})
    memory = _repository(tmp_path, "memory", {"system/settings.md": "s\n"})
    runtime = config(tmp_path)
    build = build_environment(
        EnvironmentRun(ref=_ref(runtime), inputs=_inputs(code, memory), now=NOW)
    )
    workspace = leaf_workspace(runtime, REPOSITORY, TASK, LEAF)
    snapshot = workspace / build.binding.snapshot
    assert (snapshot / "code/src/app.py").read_text() == "x = 1\n"
    assert (snapshot / "memory/system/settings.md").read_text() == "s\n"
    # Every blob of the tree is there, and the count equals git's own.
    assert build.code is not None
    assert count_tree(code, _tree(code)) == build.code.files == 2
    # The binding is the last write, and it names the snapshot that exists.
    stored = read_binding(workspace)
    assert stored is not None and stored.snapshot == build.binding.snapshot
    assert (workspace / "instructions" / "evidence.json").is_file()
    assert (workspace / "instructions" / "assignment.txt").read_text() == "You are the explainer.\n"
    assert (workspace / "scratch").is_dir() and (workspace / "drafts").is_dir()
    assert build.steps[-1] == "bound"


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
    assert update.snapshot is None
    assert binding_path(build.workspace).read_bytes() == before


def test_a_moved_update_links_unchanged_files_and_removes_the_old_snapshot(tmp_path: Path) -> None:
    code = _repository(tmp_path, "code", {"a.py": "1\n", "b.py": "2\n"})
    memory = _repository(tmp_path, "memory", {"m.md": "m\n"})
    runtime = config(tmp_path)
    build = build_environment(
        EnvironmentRun(ref=_ref(runtime), inputs=_inputs(code, memory, 7), now=NOW)
    )
    _write(code, {"b.py": "2 changed\n", "c.py": "3\n"})
    _git(code, "add", "-A")
    _git(code, "commit", "-q", "-m", "two")
    update = update_environment(
        EnvironmentRun(
            ref=_ref(runtime),
            inputs=_inputs(code, memory, 8),
            now=NOW,
            previous=build.binding,
        )
    )
    assert not update.current
    assert update.binding.snapshot != build.binding.snapshot
    assert update.code is not None
    assert update.code.files == 3
    assert update.code.linked == 1  # a.py was unchanged and linked from the old snapshot
    assert update.code.written == 2
    assert not (build.workspace / build.binding.snapshot).exists()
    stored = read_binding(build.workspace)
    assert stored is not None and stored.binding.comparison_number == 8


def test_retire_ends_the_seats_processes_and_leaves_a_receipt(tmp_path: Path) -> None:
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
        env={**os.environ, "AR_EXPLAINER_WORKSPACE": str(workspace), "PASEO_AGENT_ID": seat},
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
    assert retired_marker(workspace).is_file()
    assert not (workspace / "instructions").exists()
    assert not (workspace / build.binding.snapshot).exists()
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
        with pytest.raises(PathRefused):
            mark_retired(workspace, bad, at=NOW)
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


def test_update_stages_instructions_reclaims_orphans_and_keeps_spanning_turn_snapshot(
    tmp_path: Path,
) -> None:
    """MIK-R186 rule 6: Update stages instructions, reclaims orphans, and checks in_turn before removal."""

    code = _repository(tmp_path, "code", {"a.py": "1\n"})
    memory = _repository(tmp_path, "memory", {"m.md": "m\n"})
    runtime = config(tmp_path)
    build = build_environment(
        EnvironmentRun(ref=_ref(runtime), inputs=_inputs(code, memory, 7), now=NOW)
    )
    old_evidence = (build.workspace / "instructions" / "evidence.json").read_bytes()
    old_grants = (build.workspace / "instructions" / "grants.json").read_bytes()

    # Plant an interrupted orphan snapshot-2; next_snapshot_number reclaims it.
    orphan = build.workspace / "snapshot-2"
    orphan.mkdir()
    (orphan / "stale.txt").write_text("orphan\n")
    assert next_snapshot_number(build.workspace) == 2
    assert not orphan.exists()

    # Fault write_binding during Update: old instructions/evidence.json and grants.json stay intact.
    _write(code, {"a.py": "2\n"})
    _git(code, "add", "-A")
    _git(code, "commit", "-q", "-m", "two")
    with (
        mock.patch.object(explainer_workspace, "write_binding", side_effect=OSError("disk full")),
        pytest.raises(OSError),
    ):
        update_environment(
            EnvironmentRun(
                ref=_ref(runtime),
                inputs=_inputs(code, memory, 8),
                now=NOW,
                previous=build.binding,
            )
        )
    assert read_binding(build.workspace) == build.binding
    assert (build.workspace / "instructions" / "evidence.json").read_bytes() == old_evidence
    assert (build.workspace / "instructions" / "grants.json").read_bytes() == old_grants
    assert not (build.workspace / "snapshot-2").exists()

    # Spanning turn keeps old snapshot-1 readable; subsequent idle Update removes it.
    spanning = update_environment(
        EnvironmentRun(
            ref=_ref(runtime),
            inputs=_inputs(code, memory, 8),
            now=NOW,
            previous=build.binding,
            in_turn=lambda: True,
        )
    )
    assert (build.workspace / "snapshot-1").is_dir()
    assert (build.workspace / "snapshot-2").is_dir()
    _write(code, {"a.py": "3\n"})
    _git(code, "add", "-A")
    _git(code, "commit", "-q", "-m", "three")
    settled = update_environment(
        EnvironmentRun(
            ref=_ref(runtime),
            inputs=_inputs(code, memory, 9),
            now=NOW,
            previous=spanning.binding,
            in_turn=lambda: False,
        )
    )
    assert settled.binding.snapshot == "snapshot-3"
    assert not (build.workspace / "snapshot-1").exists()
    assert not (build.workspace / "snapshot-2").exists()


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
        env={**os.environ, "AR_EXPLAINER_WORKSPACE": str(workspace), "PASEO_AGENT_ID": seat},
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


def test_snapshot_preserves_nul_paths_enforces_pre_read_size_bounds_and_sets_readonly_modes(
    tmp_path: Path,
) -> None:
    """MIK-R186 rule 2: NUL-delimited ls-tree paths, pre-read size ceiling, and readonly files."""

    unusual = 'dir/with\ttab "quote" and café.py'
    code = _repository(tmp_path, "code", {unusual: "val = 1\n", "keep.py": "k = 1\n"})
    memory = _repository(tmp_path, "memory", {"m.md": "m\n"})
    runtime = config(tmp_path)
    build = build_environment(
        EnvironmentRun(ref=_ref(runtime), inputs=_inputs(code, memory, 7), now=NOW)
    )
    snap1_file = build.workspace / build.binding.snapshot / "code" / unusual
    snap1_keep = build.workspace / build.binding.snapshot / "code" / "keep.py"
    assert snap1_file.read_text(encoding="utf-8") == "val = 1\n"
    assert (snap1_file.stat().st_mode & 0o222) == 0
    assert (snap1_keep.stat().st_mode & 0o222) == 0

    # Make snap1_keep writable before Update; linking must restore read-only mode on the inode.
    snap1_keep.chmod(0o644)
    _write(code, {unusual: "val = 2\n"})
    _git(code, "add", "-A")
    _git(code, "commit", "-q", "-m", "two")
    updated = update_environment(
        EnvironmentRun(
            ref=_ref(runtime),
            inputs=_inputs(code, memory, 8),
            now=NOW,
            previous=build.binding,
        )
    )
    snap2_keep = build.workspace / updated.binding.snapshot / "code" / "keep.py"
    assert (snap2_keep.stat().st_mode & 0o222) == 0

    # Oversized blob in tree metadata refuses before reading blobs via cat-file.
    with (
        mock.patch.object(
            explainer_workspace,
            "_tree_entries",
            return_value=[("huge.bin", "a" * 40, MAX_SNAPSHOT_FILE_BYTES + 1, "100644")],
        ),
        mock.patch.object(
            explainer_workspace,
            "read_git_blobs_bytes",
            side_effect=AssertionError("cat-file should not be called"),
        ),
        pytest.raises(SnapshotTooLarge),
    ):
        build_environment(
            EnvironmentRun(ref=_ref(runtime), inputs=_inputs(code, memory, 9), now=NOW)
        )
