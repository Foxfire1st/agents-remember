"""During a real in-progress memory merge the writer and the memory-quality check
validate against both merge parents, ``HEAD`` and ``MERGE_HEAD``.

Every case runs ``git merge --no-ff --no-commit`` in a disposable memory repository, so
``MERGE_HEAD`` really resolves while the writer runs; a completed merge would test nothing here.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from agents_remember.application.knowledge_writer import Owner, WriteReport, WriteRequest
from agents_remember.application.knowledge_writer.base_side import BaseCode, writer_bases
from agents_remember.application.memory_quality.converted_base import context_check_base
from agents_remember.kernel.memory_attribution import render_memory_content_message
from agents_remember.memory.conversion.code_objects import CodeObjects
from agents_remember.memory.conversion.convert import convert_memory
from agents_remember.memory.conversion.inputs import memory_from_directory, write_changed
from agents_remember.memory_quality.converted_check import converted_knowledge_check
from agents_remember.memory_quality.knowledge_validator.trees import (
    knowledge_tree_from_directory,
    knowledge_tree_from_git,
)
from agents_remember.models.knowledge_files.canonical import canonical_text
from knowledge_conversion_test_support import (
    APP_SIDECAR,
    DELETED,
    CodeFixture,
    code_repository,
    git,
    memory_repository,
)
from writer_worklist_test_support import write_knowledge

_ANCHOR = "references.1.targets.0.anchor.path"


def _commit(memory: Path, message: str) -> str:
    git(memory, "add", "-A")
    git(memory, "commit", "-q", "-m", message)
    return git(memory, "rev-parse", "HEAD")


def _converted_main(tmp_path: Path) -> tuple[CodeFixture, Path, str]:
    """A memory repository whose ``main`` is converted; also the unconverted commit before it."""

    code = code_repository(tmp_path / "code")
    memory = tmp_path / "memory"
    legacy = memory_repository(memory, code)
    conversion = convert_memory(
        memory_from_directory(memory), CodeObjects(code.root), paired_commit=code.head
    )
    write_changed(memory, conversion.changed)
    _commit(memory, render_memory_content_message("converted", code.head))
    return code, memory, legacy


def _finish_lines(code: CodeFixture, memory: Path) -> tuple[CodeFixture, Path]:
    """Commit the incoming line, then move ``main`` on, so the merge has two real parents."""

    _commit(memory, render_memory_content_message("incoming", code.head))
    git(memory, "checkout", "-q", "main")
    (memory / "system" / "notes.md").write_text("# notes\n", encoding="utf-8")
    _commit(memory, render_memory_content_message("own", code.head))
    return code, memory


def _lines(tmp_path: Path) -> tuple[CodeFixture, Path]:
    """A converted ``main`` and an ``incoming`` line that moves one reference anchor to a file the
    code no longer holds; ``main`` also moves on, so the merge is a true two-parent merge."""

    code, memory, _legacy = _converted_main(tmp_path)
    git(memory, "checkout", "-q", "-b", "incoming", "main")
    sidecar = json.loads((memory / APP_SIDECAR).read_text(encoding="utf-8"))
    sidecar["references"]["1"]["targets"][0]["anchor"]["path"] = DELETED
    (memory / APP_SIDECAR).write_text(canonical_text(sidecar), encoding="utf-8")
    return _finish_lines(code, memory)


def _unconverted_incoming_lines(tmp_path: Path) -> tuple[CodeFixture, Path]:
    """A converted ``main`` and an ``incoming`` line forked before the conversion that changes
    only a file the conversion leaves alone: its tip is an unconverted parent."""

    code, memory, legacy = _converted_main(tmp_path)
    git(memory, "checkout", "-q", "-b", "incoming", legacy)
    (memory / "system" / "settings.md").write_text("# settings\n\nIncoming.\n", encoding="utf-8")
    return _finish_lines(code, memory)


def _merge(memory: Path) -> str:
    git(memory, "merge", "--no-ff", "--no-commit", "incoming")
    return git(memory, "rev-parse", "--verify", "MERGE_HEAD")  # really in progress


def _files_at(memory: Path, commit: str) -> dict[str, bytes]:
    return dict(knowledge_tree_from_git(memory, commit, label=commit).files)


def _plan(
    memory: Path, code: CodeFixture, coordination: Path, code_base: str | None = None
) -> WriteReport:
    return write_knowledge(
        WriteRequest(
            memory_root=memory,
            code_root=code.root,
            owner=Owner(task="260101-FIX", kind="leaf", id="260101-FIX-L7"),
            handoff_path="handoff.json",
            document={"history": []},
            coordination_root=coordination,
            code_base=code_base,
        )
    )


def test_the_writer_and_the_check_validate_a_real_merge_against_both_parents(
    tmp_path: Path,
) -> None:
    """Before this rule the writer read ``HEAD`` alone: the anchor the incoming side carries was
    new to it and refused (R22.6-anchor-path), although the merge commit's own validation reads
    both parents and carries it. Now the writer and the check see the same two bases."""

    code, memory = _lines(tmp_path)
    coordination = tmp_path / "coordination"
    head = git(memory, "rev-parse", "HEAD")
    incoming = _merge(memory)

    planned = _plan(memory, code, coordination)
    assert planned.state == "planned", planned.render()
    assert not any(one.rule == "R22.6-anchor-path" for one in planned.violations)
    assert any(
        (one.path, one.field, one.rule) == (APP_SIDECAR, _ANCHOR, "R22.6-carried-stale")
        for one in planned.violations
    ), planned.render()  # carried from MERGE_HEAD, reported for the maintenance pass

    candidate = knowledge_tree_from_directory(memory)
    sides = writer_bases(
        memory, candidate, BaseCode(code.root, None, coordination / "runtime" / "bases")
    )
    assert sides.problem is None
    assert sides.base is not None and sides.base.label.endswith("@HEAD")
    assert [one.label for one in sides.validation_bases] == ["memory@HEAD", "memory@MERGE_HEAD"]
    assert sides.base.files == _files_at(memory, head)
    assert sides.validation_bases[1].files == _files_at(memory, incoming)

    port = context_check_base(code.root, SimpleNamespace(coordination_root=coordination))
    assert port is not None
    seen, problem = port(memory, candidate)
    assert problem is None
    assert [one.files for one in seen] == [one.files for one in sides.validation_bases]
    checked = converted_knowledge_check(memory, code.root, base=port, include_landing=False)
    assert not [
        one for one in checked["findings"] if (one["path"], one["field"]) == (APP_SIDECAR, _ANCHOR)
    ], checked["findings"]  # the check never refuses what the writer was allowed to write


def test_outside_a_merge_the_writer_keeps_its_one_head_base(tmp_path: Path) -> None:
    """The outside-merge control: no ``MERGE_HEAD``, so ``HEAD`` is the only base and the same
    anchor is refused as newly written, exactly as before the two-parent read."""

    code, memory = _lines(tmp_path)
    git(memory, "merge", "-q", "--no-ff", "-m", "merged", "incoming")  # completed: no MERGE_HEAD
    git(memory, "reset", "-q", "--soft", "HEAD~1")  # the merged tree, uncommitted, on own HEAD
    candidate = knowledge_tree_from_directory(memory)
    sides = writer_bases(memory, candidate, BaseCode(code.root))
    assert sides.incoming is None and sides.problem is None
    assert [one.label for one in sides.validation_bases] == ["memory@HEAD"]
    refused = _plan(memory, code, tmp_path / "coordination")
    assert refused.state == "refused"
    assert [
        (one.path, one.field) for one in refused.violations if one.rule == "R22.6-anchor-path"
    ] == [(APP_SIDECAR, _ANCHOR)]


def test_an_unreadable_or_unconvertible_merge_head_refuses_by_name(tmp_path: Path) -> None:
    """An incoming parent the writer cannot read or convert is never omitted or replaced by an
    empty tree: the writer refuses with a named reason, as it does for ``HEAD``."""

    code, memory = _lines(tmp_path / "unreadable")
    _merge(memory)
    blob = git(memory, "rev-parse", f"MERGE_HEAD:{APP_SIDECAR}")
    (memory / ".git" / "objects" / blob[:2] / blob[2:]).unlink()
    unreadable = _plan(memory, code, tmp_path / "coordination")
    assert unreadable.state == "refused"
    assert "the memory worktree's MERGE_HEAD, cannot be read" in unreadable.render()

    code, memory = _unconverted_incoming_lines(tmp_path / "unconvertible")
    _merge(memory)
    unconvertible = _plan(memory, code, tmp_path / "coordination", code_base="0" * 40)
    assert unconvertible.state == "refused"
    assert (
        "the converted base of the memory worktree's MERGE_HEAD, the text-format side a "
        "converted tree is compared with, cannot be built" in unconvertible.render()
    )
