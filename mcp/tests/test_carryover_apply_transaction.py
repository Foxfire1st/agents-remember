"""Converted carryover transaction boundaries on isolated real Git trees."""

from __future__ import annotations

import json
import stat
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from agents_remember.application.memory_tools import (
    memory_carryover_apply_tool,
    memory_carryover_plan_tool,
)
from agents_remember.kernel.coordination_context.models import StorageSettings
from agents_remember.memory import carryover
from agents_remember.memory.carryover_apply import apply_converted
from agents_remember.memory.knowledge_index import (
    KnowledgeIndexCache,
    build_index,
    default_cache_directory,
)
from agents_remember.models.knowledge_files.canonical import canonical_text
from knowledge_writer_test_support import (
    CODE_FILE,
    TEST_FILE,
    World,
    build_world,
    git,
    tree_bytes,
    write,
)
from test_converted_carryover import _converted_world, _source_record_pair


def _world(tmp_path: Path) -> World:
    world = build_world(tmp_path)
    # The leaf base resolver must use the official memory attribution, not a
    # fabricated contract memory_base_commit or the operation's HEAD shortcut.
    code = git(world.code, "rev-parse", "HEAD")
    (world.memory / f"onboarding/{CODE_FILE}.md").chmod(0o755)
    git(world.memory, "add", "-A")
    git(world.memory, "commit", "--amend", "-q", "-m", f"Converted memory\n\nCode-Commit: {code}")
    return world


def _apply(world: World, payloads: dict[str, bytes]) -> dict[str, Any]:
    request = SimpleNamespace(
        target_memory=world.memory,
        target_contract_path=world.contract,
        code_repository_root=world.code,
        code_repository_name="agents-remember",
    )
    selected = [
        {
            "source_path": CODE_FILE,
            "target_onboarding": str(world.memory / f"onboarding/{CODE_FILE}.md"),
        }
    ]
    return apply_converted(
        request,
        {"official_code_head": git(world.code, "rev-parse", "HEAD")},
        selected,
        payloads,
        StorageSettings(),
    )


def _pair(world: World) -> dict[str, bytes]:
    return {
        f"onboarding/{CODE_FILE}.md": b"# pkg/landing.py\n\nCarried explanation.\n",
        f"onboarding/{CODE_FILE}.json": (
            world.memory / f"onboarding/{CODE_FILE}.json"
        ).read_bytes(),
    }


def _state(root: Path) -> tuple[dict[str, bytes], dict[str, int], list[str], str, bytes]:
    files = tree_bytes(root)
    modes = {
        path.relative_to(root).as_posix(): stat.S_IMODE(path.stat().st_mode)
        for path in root.rglob("*")
        if ".git" not in path.relative_to(root).parts
    }
    dirs = sorted(
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_dir() and ".git" not in path.relative_to(root).parts
    )
    index = Path(git(root, "rev-parse", "--path-format=absolute", "--git-path", "index"))
    return files, modes, dirs, git(root, "rev-parse", "HEAD"), index.read_bytes()


def test_validator_refusal_restores_modes_edits_indexes_and_created_directories(
    tmp_path: Path,
) -> None:
    world = _world(tmp_path)
    write(
        world.memory,
        {
            f"onboarding/{TEST_FILE}.md": "# tests/test_landing.py\n\nExisting curator edit.\n",
            "onboarding/overview.index.json": b"original derived cache\n",
            "onboarding/.retained/local.bin": b"\x00\xffpreexisting\r\n",
        },
    )
    (world.memory / "onboarding/overview.index.json").chmod(0o640)
    git(world.memory, "add", f"onboarding/{TEST_FILE}.md")
    before = _state(world.memory)
    payloads = {
        **_pair(world),
        "onboarding/new/deep/absent.py.md": b"# absent.py\n",
        "onboarding/new/deep/absent.py.json": b"{}\n",
    }

    result = _apply(world, payloads)

    assert result["state"] == "refused", result
    assert result["reason"] == "knowledge-validator-refused", result
    assert result["violations"]
    assert result["written_paths"] == []
    assert result["route_index_refresh"]["state"] == "refreshed"
    assert _state(world.memory) == before
    assert not (world.memory / "onboarding/new").exists()


@pytest.mark.parametrize("edit", ["document", "sidecar", "deleted"])
def test_target_edited_member_refuses_entire_candidate_without_writing(
    tmp_path: Path, edit: str
) -> None:
    world = _world(tmp_path)
    payloads = _pair(world)
    document = world.memory / f"onboarding/{CODE_FILE}.md"
    sidecar = world.memory / f"onboarding/{CODE_FILE}.json"
    if edit == "document":
        document.write_bytes(b"# Uncommitted curator edit\n")
    elif edit == "sidecar":
        sidecar.write_bytes(b'{"uncommitted": true}\n')
    else:
        document.unlink()
    before = _state(world.memory)

    result = _apply(world, payloads)

    assert result["state"] == "refused"
    assert result["reason"] == "target-edited"
    assert result["target_edited"][0]["decision"] == "target-edited"
    assert result["route_index_refresh"]["state"] == "not-run"
    assert result["carried_paths_differ_from_head"]
    assert _state(world.memory) == before


def test_repeat_skips_equal_payloads_but_validates_unrelated_tree_and_restores_retry(
    tmp_path: Path,
) -> None:
    world = _world(tmp_path)
    payloads = _pair(world)
    head = git(world.memory, "rev-parse", "HEAD")
    index = (world.memory / ".git/index").read_bytes()

    first = _apply(world, payloads)
    assert first["state"] == "carried-into-worktree", first
    assert first["payload_written_paths"] == [f"onboarding/{CODE_FILE}.md"]
    assert f"onboarding/{CODE_FILE}.md" in first["written_paths"]
    assert first["worklist"] is not None
    assert git(world.memory, "rev-parse", "HEAD") == head
    assert (world.memory / ".git/index").read_bytes() == index
    assert not (world.contract.parent / "knowledge-worklist.json").exists()

    second = _apply(world, payloads)
    assert second["state"] == "nothing-to-carryover", second
    assert second["written_paths"] == []
    assert (world.memory / ".git/index").read_bytes() == index

    # An interrupted operation can leave already-equal payloads and a separate
    # invalid file. The next apply must still validate the entire tree.
    write(world.memory, {"knowledge/invariants/broken.json": b"{}\n"})
    before = _state(world.memory)
    retry = _apply(world, payloads)
    assert retry["state"] == "refused", retry
    assert retry["reason"] == "knowledge-validator-refused", retry
    assert retry["violations"]
    assert retry["carried_paths_differ_from_head"] == [f"onboarding/{CODE_FILE}.md"]
    assert _state(world.memory) == before


def test_plan_reuses_exact_cache_and_removes_scratch_after_builder_refusal(tmp_path, monkeypatch):
    fixture, contract, selection = _converted_world(tmp_path)
    source, target = Path(selection.source_memory), contract.memory_worktree
    assert target is not None
    cache = KnowledgeIndexCache(default_cache_directory(fixture.cfg.coordination_root))
    for root in (source, target):
        with cache.for_directory(root):
            pass
    files = {
        path: (path.read_bytes(), path.stat().st_mtime_ns) for path in cache.directory.iterdir()
    }
    monkeypatch.setattr(carryover, "build_index", lambda *args: pytest.fail("rebuilt a warm tree"))
    plan = memory_carryover_plan_tool(fixture.cfg, selection)
    assert not plan["candidates"][0]["records"]["conflicts"]
    assert {
        path: (path.read_bytes(), path.stat().st_mtime_ns) for path in cache.directory.iterdir()
    } == files
    # A distinct source tree needs a scratch build; a refusal after that write still cleans it.
    document = source / "onboarding/feature.py.md"
    document.write_bytes(document.read_bytes() + b"\nAdded explanation.\n")
    destinations = []

    def refused(snapshot, destination):
        destinations.append(destination)
        assert not destination.is_relative_to(fixture.cfg.coordination_root)
        assert not destination.is_relative_to(source) and not destination.is_relative_to(target)
        build_index(snapshot, destination)
        assert destination.is_file()
        raise RuntimeError("injected refusal after the real scratch build")

    monkeypatch.setattr(carryover, "build_index", refused)
    plan = memory_carryover_plan_tool(fixture.cfg, selection)
    assert destinations and all(not path.parent.exists() for path in destinations)
    assert plan["candidates"][0]["records"]["conflicts"][0]["state"] == "index-incomplete"
    assert "injected refusal" in plan["candidates"][0]["reason"]


def _record_retry(tmp_path: Path, cut: str):
    fixture, contract, selection = _converted_world(tmp_path)
    source, target = Path(selection.source_memory), contract.memory_worktree
    assert target is not None
    identity, facts, _record = _source_record_pair(source)
    prose = str(Path(facts).with_suffix(".md"))
    if cut == "optional-prose":
        (source / prose).unlink()
    pair = {
        path: (source / path).read_bytes() for path in (facts, prose) if (source / path).exists()
    }
    present = (
        list(pair)
        if cut == "complete"
        else []
        if cut in {"missing", "optional-prose"}
        else [facts if cut == "json" else prose]
    )
    for path in present:
        write(target, {path: pair[path]})
        (target / path).chmod(0o640)
        git(target, "add", path)
    assert all((target / path).read_bytes() == pair[path] for path in present)
    assert all(not (target / path).exists() for path in pair.keys() - set(present))
    return fixture, contract, selection, identity, pair, present


@pytest.mark.parametrize("cut", ["json", "markdown", "missing", "complete", "optional-prose"])
def test_public_record_member_cut_resumes_exact_bytes_without_overwrite(tmp_path, cut):
    fixture, contract, selection, identity, pair, present = _record_retry(tmp_path, cut)
    target = contract.memory_worktree
    assert target is not None
    before = _state(target)
    plan = memory_carryover_plan_tool(fixture.cfg, selection)
    candidate = next(item for item in plan["candidates"] if item["source_path"] == "feature.py")
    assert not candidate["records"]["conflicts"], candidate
    assert [item["id"] for item in candidate["records"]["needed"]] == (
        [] if cut == "complete" else [identity]
    )
    result = memory_carryover_apply_tool(fixture.cfg, selection, intent_note="resume record cut")
    assert result["ok"] and result["state"] == "carried-into-worktree", result
    assert result["route_index_refresh"]["state"] == "refreshed"
    assert set(pair) - set(present) <= set(result["payload_written_paths"])
    assert not set(present).intersection(result["payload_written_paths"])
    assert all((target / path).read_bytes() == data for path, data in pair.items())
    assert all(stat.S_IMODE((target / path).stat().st_mode) == 0o640 for path in present)
    after = _state(target)
    assert after[3:] == before[3:]  # HEAD and staged blobs/index bytes are untouched.
    if cut == "optional-prose":
        assert not (target / Path(next(iter(pair))).with_suffix(".md")).exists()
    repeated = memory_carryover_apply_tool(
        fixture.cfg, selection, intent_note="repeat record retry"
    )
    assert repeated["ok"] and repeated["state"] == "nothing-to-carryover", repeated
    assert _state(target)[3:] == before[3:]


@pytest.mark.parametrize("dirty", ["json", "markdown", "wrong-id"])
def test_public_record_missing_companion_never_overwrites_differing_member(tmp_path, dirty):
    cut = "markdown" if dirty == "markdown" else "json"
    fixture, contract, selection, _identity, pair, present = _record_retry(tmp_path, cut)
    target = contract.memory_worktree
    assert target is not None
    path = present[0]
    if dirty == "markdown":
        edited = b"# Curator prose must survive\n"
    else:
        document = json.loads(pair[path])
        document["statement" if dirty == "json" else "id"] = (
            "Curator statement must survive." if dirty == "json" else "INV-QQQQQQ"
        )
        edited = canonical_text(document).encode()
    (target / path).write_bytes(edited)
    assert edited != pair[path]
    assert all(not (target / other).exists() for other in pair if other != path)
    before = _state(target)
    plan = memory_carryover_plan_tool(fixture.cfg, selection)
    candidate = next(item for item in plan["candidates"] if item["source_path"] == "feature.py")
    assert candidate["records"]["conflicts"], candidate
    result = memory_carryover_apply_tool(
        fixture.cfg,
        selection,
        intent_note="refuse edited record",
        include_review_required=["feature.py"],
    )
    assert not result["ok"] and result["state"] == "refused", result
    assert result["written_paths"] == []
    assert _state(target) == before


@pytest.mark.parametrize("cut", ["json", "markdown"])
def test_public_record_partial_retry_validates_whole_tree_and_restores_with_diagnostics(
    tmp_path, cut
):
    fixture, contract, selection, _identity, pair, present = _record_retry(tmp_path, cut)
    target = contract.memory_worktree
    assert target is not None
    unrelated = target / "onboarding/notes.md"
    write(
        target,
        {
            "onboarding/notes.md": b"# Notes\n\nUnresolved reference. [999]\n",
            "onboarding/notes.json": (
                b'{"schema":"ar-onboarding-file/v1","path":"notes","realizes":[],"references":{}}\n'
            ),
        },
    )
    before = _state(target)
    plan = memory_carryover_plan_tool(fixture.cfg, selection)
    candidate = next(item for item in plan["candidates"] if item["source_path"] == "feature.py")
    assert not candidate["records"]["conflicts"], candidate
    result = memory_carryover_apply_tool(
        fixture.cfg, selection, intent_note="validate partial retry"
    )
    assert not result["ok"] and result["state"] == "refused", result
    assert result["reason"] == "knowledge-validator-refused", result
    assert result["violations"]
    assert any(unrelated.relative_to(target).as_posix() in str(one) for one in result["violations"])
    assert result["route_index_refresh"]["state"] == "refreshed"
    assert result["route_index_refresh"]["rolled_back"]
    assert result["written_paths"] == []
    assert set(present) <= set(result["carried_paths_differ_from_head"])
    assert not (set(pair) - set(present)).intersection(result["carried_paths_differ_from_head"])
    assert _state(target) == before
