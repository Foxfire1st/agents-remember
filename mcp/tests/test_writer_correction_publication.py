"""MIK-R54 writer corrections, exercised against real paired Git trees."""

from __future__ import annotations

from pathlib import Path

import pytest
from agents_remember.application.knowledge_writer.file_publication import publish_files
from knowledge_writer_test_support import build_world, tree_bytes
from test_knowledge_writer import _conforming
from writer_corrections_test_support import run


def test_write_failure_after_second_file_rolls_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = build_world(tmp_path)
    before = tree_bytes(world.memory)
    original = Path.replace
    replaced: list[str] = []

    def failing_replace(path: Path, destination: str | Path) -> Path:
        replaced.append(str(destination))
        if len(replaced) == 3:
            raise OSError("injected third-file disk failure")
        return original(path, destination)

    monkeypatch.setattr(Path, "replace", failing_replace)
    report = run(world, _conforming())
    assert report.refused, report.render()
    assert "injected third-file disk failure" in report.render()
    assert replaced[2] in report.render()
    assert tree_bytes(world.memory) == before
    assert report.to_document()["records"] == []


def test_rollback_failure_keeps_original_error_and_names_survivor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = build_world(tmp_path)
    replace, unlink = Path.replace, Path.unlink
    replaced: list[Path] = []
    failed = False

    def fail_third(path: Path, destination: str | Path) -> Path:
        nonlocal failed
        replaced.append(Path(destination))
        if len(replaced) == 3:
            failed = True
            raise OSError("original publication failure")
        return replace(path, destination)

    def fail_one_rollback(path: Path, missing_ok: bool = False) -> None:
        if failed and path == replaced[1]:
            raise OSError("persistent rollback failure")
        unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "replace", fail_third)
    monkeypatch.setattr(Path, "unlink", fail_one_rollback)
    report = run(world, _conforming())
    assert report.refused, report.render()
    rendered = report.render()
    assert "original publication failure" in rendered
    assert "persistent rollback failure" in rendered
    assert "rollback incomplete" in rendered and "was undone" not in rendered
    assert str(replaced[1]) in rendered and replaced[1].exists()
    assert not replaced[0].exists(), "rollback must continue past an independent failure"
    assert report.to_document()["records"] == []


@pytest.mark.parametrize("phase", ["replace", "unlink"])
def test_replaced_created_and_deleted_files_restore_together(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    previous = {name: ("old " + name).encode() for name in ("a", "c", "d", "z")}
    candidate = {name: ("new " + name).encode() for name in ("a", "b", "z")}
    for name, body in previous.items():
        (tmp_path / name).write_bytes(body)
    replace, unlink = Path.replace, Path.unlink
    tripped = False

    def fail_replace(path: Path, destination: str | Path) -> Path:
        nonlocal tripped
        if phase == "replace" and Path(destination).name == "z":
            tripped = True
            raise OSError("third replacement")
        return replace(path, destination)

    def fail_unlink(path: Path, missing_ok: bool = False) -> None:
        nonlocal tripped
        if phase == "unlink" and path.name == "d":
            tripped = True
            raise OSError("second deletion")
        unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "replace", fail_replace)
    monkeypatch.setattr(Path, "unlink", fail_unlink)
    with pytest.raises(OSError, match=r"third replacement|second deletion"):
        publish_files(tmp_path, previous, candidate)
    assert tripped
    assert {path.name: path.read_bytes() for path in tmp_path.iterdir()} == previous
