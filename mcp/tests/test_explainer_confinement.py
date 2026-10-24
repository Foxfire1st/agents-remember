"""Physical confinement of every explainer metadata write (MIK-R186 rule 2; S07).

Every writer of the environment's durable facts -- the binding, the generation counter, the
retired marker, the request ledger and the seat lock -- must check the *physical* confinement of
its target before writing: every component below the task's explainer folder must be a real
directory, with no symlink on the way, and the target must resolve inside the folder. A planted
ancestor link (a symlinked task folder, workspace, counter, ledger or lock) is held and reported,
and nothing behind the link sees a single byte change.

Each case plants the link, calls the real writer in-process, and asserts the refusal plus zero
outside effects.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest import mock

import pytest
from agents_remember.canvas import explainer_service, explainer_workspace
from agents_remember.canvas.explainer_paths import (
    generation_path,
    leaf_workspace,
    lock_path,
    remove_workspace_contents,
)
from agents_remember.canvas.explainer_service import LeafScope
from agents_remember.canvas.explainer_state import (
    ExplainerBinding,
    clear_retired,
    ledger_path,
    mark_retired,
    raise_generation,
    read_binding,
    record_request,
    restore_generation,
    write_binding,
    write_inflight_create,
)
from agents_remember.canvas.explainer_workspace import (
    EnvironmentInputs,
    EnvironmentRef,
    EnvironmentRun,
    build_environment,
    update_environment,
)
from agents_remember.canvas.paths import PathRefused
from agents_remember.models.knowledge.review_frame import FrameBinding
from canvas_host_test_support import LEAF, REPO, TASK, config, packet, record

NOW = "2026-10-22T08:00:00+00:00"


def _binding() -> ExplainerBinding:
    return ExplainerBinding(
        binding=FrameBinding.from_packet(packet(record(7)).comparison),
        snapshot="snapshot-1",
        prepared_at=NOW,
        packet_digest="sha256:" + "0" * 64,
    )


def _workspace(tmp_path: Path):
    runtime = config(tmp_path)
    workspace = leaf_workspace(runtime, REPO, TASK, LEAF)
    workspace.mkdir(parents=True)
    return runtime, workspace


def _outside(tmp_path: Path) -> Path:
    outside = tmp_path / "outside"
    outside.mkdir()
    return outside


def _assert_untouched(path: Path, content: bytes) -> None:
    assert path.read_bytes() == content


def test_every_metadata_writer_refuses_a_planted_task_folder_ancestor_link(tmp_path: Path) -> None:
    """The task's explainer folder as a symlink refuses every writer, with zero outside effects."""

    runtime, workspace = _workspace(tmp_path)
    outside = _outside(tmp_path)
    (outside / "counter").write_text("3\n")
    (outside / "ledger.json").write_text('{"schema": "x", "requests": {}}')
    (outside / "binding.json").write_text("outside\n")
    (outside / "retired.json").write_text("outside\n")
    counter_outside = (outside / "counter").read_bytes()
    ledger_outside = (outside / "ledger.json").read_bytes()
    binding_outside = (outside / "binding.json").read_bytes()
    retired_outside = (outside / "retired.json").read_bytes()

    gen_file = generation_path(runtime, REPO, TASK, LEAF)
    ledger = ledger_path(runtime, REPO, TASK, LEAF)
    task_folder = workspace.parent
    workspace.rmdir()
    task_folder.rename(tmp_path / "explainer-task-moved")
    task_folder.symlink_to(outside, target_is_directory=True)

    writers = [
        lambda: write_binding(workspace, _binding()),
        lambda: mark_retired(workspace, "receipt-1", at=NOW),
        lambda: clear_retired(workspace),
        lambda: raise_generation(gen_file),
        lambda: restore_generation(gen_file, 2),
        lambda: record_request(ledger, LEAF, "r1", {"generation": 1}),
        lambda: write_inflight_create(ledger, {"generation": 1}),
    ]
    for writer in writers:
        with pytest.raises(PathRefused):
            writer()
    _assert_untouched(outside / "counter", counter_outside)
    _assert_untouched(outside / "ledger.json", ledger_outside)
    _assert_untouched(outside / "binding.json", binding_outside)
    _assert_untouched(outside / "retired.json", retired_outside)
    assert (outside / "retired.json").read_bytes() == retired_outside
    # Nothing else appeared behind the link: the writers wrote no file anywhere outside.
    assert {path.name for path in outside.iterdir()} == {
        "counter",
        "ledger.json",
        "binding.json",
        "retired.json",
    }


def test_binding_and_marker_writers_refuse_a_planted_workspace_link(tmp_path: Path) -> None:
    """A symlinked workspace refuses the binding and marker writers before any byte is written."""

    _runtime, workspace = _workspace(tmp_path)
    outside = _outside(tmp_path)
    (outside / "binding.json").write_text("outside\n")
    (outside / "retired.json").write_text("outside\n")
    workspace.rmdir()
    workspace.symlink_to(outside, target_is_directory=True)

    with pytest.raises(PathRefused):
        write_binding(workspace, _binding())
    with pytest.raises(PathRefused):
        mark_retired(workspace, "receipt-1", at=NOW)
    with pytest.raises(PathRefused):
        clear_retired(workspace)
    assert (outside / "binding.json").read_text() == "outside\n"
    assert (outside / "retired.json").read_text() == "outside\n"


def test_the_generation_writers_refuse_a_planted_counter_link(tmp_path: Path) -> None:
    """A symlinked counter refuses raise and restore, and the outside file keeps its bytes."""

    runtime, _ws = _workspace(tmp_path)
    outside = _outside(tmp_path)
    target = outside / "counter"
    target.write_text("3\n")
    gen_file = generation_path(runtime, REPO, TASK, LEAF)
    gen_file.parent.mkdir(parents=True, exist_ok=True)
    gen_file.symlink_to(target)

    with pytest.raises(PathRefused):
        raise_generation(gen_file)
    with pytest.raises(PathRefused):
        restore_generation(gen_file, 1)
    assert target.read_text() == "3\n"


def test_the_ledger_writers_refuse_a_planted_ledger_link(tmp_path: Path) -> None:
    """A symlinked request ledger refuses every ledger writer; the outside file keeps its bytes."""

    runtime, _ws = _workspace(tmp_path)
    outside = _outside(tmp_path)
    target = outside / "ledger.json"
    target.write_text('{"schema": "x", "requests": {}}')
    ledger = ledger_path(runtime, REPO, TASK, LEAF)
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.symlink_to(target)

    with pytest.raises(PathRefused):
        record_request(ledger, LEAF, "r1", {"generation": 1})
    with pytest.raises(PathRefused):
        write_inflight_create(ledger, {"generation": 1})
    assert target.read_text() == '{"schema": "x", "requests": {}}'

    # The same for the leaf-less shared ledger the guard names beside it.
    shared = ledger.parent / "requests.json"
    shared.symlink_to(target)
    with pytest.raises(PathRefused):
        record_request(ledger, LEAF, "r1", {"generation": 1})
    assert target.read_text() == '{"schema": "x", "requests": {}}'


def test_the_seat_lock_refuses_a_planted_lock_link_before_locking(tmp_path: Path) -> None:
    """The service's lock step admits the lock file physically before the kernel opens it."""

    runtime, _ws = _workspace(tmp_path)
    outside = _outside(tmp_path)
    (outside / "lock").write_text("outside\n")
    lock_file = lock_path(runtime, REPO, TASK, LEAF)
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    lock_file.symlink_to(outside / "lock")

    deps: Any = SimpleNamespace(config=runtime, lock_timeout=None)
    scope = LeafScope(repository_id=REPO, task=TASK, leaf_id=LEAF)
    with pytest.raises(PathRefused), explainer_service._lock(deps, scope, wait=True):
        pytest.fail("the planted lock link must be refused before the lock is taken")
    assert (outside / "lock").read_text() == "outside\n"
    assert not (outside / "lock.lock").exists()


def test_workspace_removal_holds_a_nested_link_instead_of_rmtree_following_it(
    tmp_path: Path,
) -> None:
    """WS:867-868: a nested symlink holds the removal; nothing behind it is removed."""

    _runtime, workspace = _workspace(tmp_path)
    outside = _outside(tmp_path)
    (outside / "keep.txt").write_text("keep\n")
    (workspace / "instructions").mkdir()
    (workspace / "scratch" / "sub").mkdir(parents=True)
    nested = workspace / "scratch" / "sub" / "escape"
    nested.symlink_to(outside, target_is_directory=True)
    (workspace / "drafts").mkdir()
    (workspace / "drafts" / "draft.txt").write_text("draft\n")

    held = remove_workspace_contents(workspace, root=workspace.parent)

    assert any(str(nested) in line for line in held)
    assert nested.is_symlink()
    assert (outside / "keep.txt").read_text() == "keep\n"
    # What is not behind a link is still removed; the folder holding the link is held with it.
    assert not (workspace / "drafts").exists()
    assert not (workspace / "instructions").exists()
    assert (workspace / "scratch").is_dir()


# -- S10: an Update faulted in any pre-bind write leaves one coherent generation --------------------


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def _repository(root: Path, name: str, files: dict[str, str]) -> Path:
    repo = root / name
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "test")
    for path, text in files.items():
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "one")
    return repo


def _tree(repo: Path) -> str:
    return _git(repo, "rev-parse", "HEAD^{tree}")


def _inputs(code: Path, memory: Path, number: int) -> EnvironmentInputs:
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


def test_an_update_faulted_in_a_pre_bind_write_leaves_the_old_generation_coherent(
    tmp_path: Path,
) -> None:
    """S10: a snapshot write faulted mid-Update leaves the old binding, snapshot and instructions
    intact, and the partial next snapshot is removed."""

    code = _repository(tmp_path, "code", {"a.py": "1\n"})
    memory = _repository(tmp_path, "memory", {"m.md": "m\n"})
    runtime = config(tmp_path)
    ref = EnvironmentRef(config=runtime, repository_id=REPO, task=TASK, leaf_id=LEAF)
    build = build_environment(EnvironmentRun(ref=ref, inputs=_inputs(code, memory, 7), now=NOW))
    workspace = leaf_workspace(runtime, REPO, TASK, LEAF)
    old_binding = build.binding
    old_evidence = (workspace / "instructions" / "evidence.json").read_bytes()
    (code / "a.py").write_text("2\n")
    _git(code, "add", "-A")
    _git(code, "commit", "-q", "-m", "two")

    with (
        mock.patch.object(
            explainer_workspace,
            "write_snapshot",
            side_effect=OSError("the tree cannot be written"),
        ),
        pytest.raises(OSError),
    ):
        update_environment(
            EnvironmentRun(
                ref=ref,
                inputs=_inputs(code, memory, 8),
                now=NOW,
                previous=old_binding,
            )
        )

    assert read_binding(workspace) == old_binding
    assert (workspace / "instructions" / "evidence.json").read_bytes() == old_evidence
    assert (workspace / old_binding.snapshot).is_dir()
    assert not (workspace / "snapshot-2").exists()
    # The next Update runs the same faulted step again and answers a coherent new generation.
    update = update_environment(
        EnvironmentRun(ref=ref, inputs=_inputs(code, memory, 8), now=NOW, previous=old_binding)
    )
    assert not update.current
    assert update.binding.snapshot == "snapshot-2"
    assert (workspace / "snapshot-2" / "code" / "a.py").read_text() == "2\n"
    assert not (workspace / old_binding.snapshot).exists()
