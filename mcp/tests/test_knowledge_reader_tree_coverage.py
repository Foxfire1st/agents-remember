"""L79's pathname-only tree facts: readable knowledge, actual scope and bounded calls."""

from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path

import pytest
from agents_remember.application.knowledge_reader import tree_coverage as coverage
from agents_remember.application.knowledge_reader.files import FileRead
from agents_remember.application.knowledge_reader.paths import tree_listing
from agents_remember.application.knowledge_reader.selection import ReaderSelection, open_selection
from test_knowledge_reader import REVIEW_INVARIANT, World, _commit, _realization, _sidecar

pytest_plugins = ("test_knowledge_reader",)


def test_presence_and_coverage_count_only_scoped_code_cards(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    opened = open_selection(world.config, "demo", world.second)
    assert isinstance(opened, ReaderSelection)
    calls: list[str] = []
    memory = {
        "onboarding/own/overview.md",
        "onboarding/deep/sub/overview.md",
        "onboarding/card/a.py.md",
        "onboarding/sidecar/a.py.json",
        "onboarding/card/deleted.py.md",
        "onboarding/card/ignored.py.md",
        "onboarding/card/outside.bin.md",
    }
    paths = ["own/a.py", "card/a.py", "card/ignored.py", "card/outside.bin"]
    settings = '{"onboarding":{"storage":{"mode":"memory-repo"},"pathRules":{"include":{"fileTypes":[".py"]},"exclude":{"paths":["**/ignored.py"]}}}}'

    def memory_paths(*args: object) -> coverage.Pathnames:
        calls.append("memory")
        return coverage.Pathnames(
            files=memory, directories={"onboarding/empty", "onboarding/sidecar"}
        )

    def code_paths(*args: object) -> coverage.Pathnames:
        calls.append("code")
        return coverage.Pathnames(
            files=set(paths),
            directories={"own", "deep", "card", "sidecar", "empty", "indexed", "wide"},
            sources=set(paths),
        )

    def metadata(*args: object) -> FileRead:
        calls.append("settings")
        return FileRead("system/settings.json", "present", settings)

    monkeypatch.setattr(coverage, "_memory_paths", memory_paths)
    monkeypatch.setattr(coverage, "_git_paths", code_paths)
    monkeypatch.setattr(coverage, "read_memory_file", metadata)
    with opened:
        for scale in (1, 20):
            paths[:] = ["own/a.py", "card/a.py", "card/ignored.py", "card/outside.bin"] + [
                f"wide/{n}.py" for n in range(scale)
            ]
            monkeypatch.setattr(
                "agents_remember.application.knowledge_reader.paths._entry_counts",
                lambda *args: {"indexed": 1},
            )
            children = tree_listing(opened, ".")["children"]
            by_name = {row["name"]: row for row in children}
            assert by_name["own"]["hasOverview"] is True
            assert by_name["deep"]["hasOverview"] is False and by_name["deep"]["hasKnowledge"]
            assert by_name["card"]["coverage"] == {"state": "counted", "files": 1, "cards": 1}
            assert not by_name["sidecar"]["hasKnowledge"] and not by_name["empty"]["hasKnowledge"]
            assert by_name["indexed"]["hasKnowledge"]
            assert by_name["wide"]["coverage"] == {"state": "counted", "files": scale, "cards": 0}
            assert by_name["empty"]["onboarding"] and by_name["sidecar"]["onboarding"]
    assert calls == ["code", "memory", "settings"] * 2


def test_unreadable_scope_and_memory_do_not_invent_counts_or_false_presence(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    opened = open_selection(world.config, "demo", world.second)
    assert isinstance(opened, ReaderSelection)
    monkeypatch.setattr(
        coverage,
        "_memory_paths",
        lambda *args: coverage.Pathnames(files={"onboarding/card/a.py.md"}),
    )
    monkeypatch.setattr(
        coverage, "read_memory_file", lambda *args: FileRead("system/settings.json", "absent")
    )
    with opened:
        children = coverage.read_tree_listing(opened, ".", {})["children"]
        children = [row for row in children if row["name"] == "card"]
        assert children[0]["hasKnowledge"]
        assert children[0]["coverage"]["state"] == "unavailable"
        assert "settings.json absent" in children[0]["coverage"]["detail"]
        assert "files" not in children[0]["coverage"]
        monkeypatch.setattr(coverage, "_memory_paths", fail_memory)
        unknown = coverage.read_tree_listing(opened, ".", {"card": 1})["children"]
        unknown = [row for row in unknown if row["name"] == "card"]
        assert "hasKnowledge" not in unknown[0] and "hasOverview" not in unknown[0]
        assert unknown[0]["coverage"]["state"] == "unavailable"
        assert "denied" in unknown[0]["coverage"]["detail"]
        monkeypatch.setattr(coverage, "_memory_paths", lambda *args: coverage.Pathnames())
        monkeypatch.setattr(opened.index, "state", replace(opened.index.state, state="partial"))
        partial = coverage.read_tree_listing(opened, ".", {"card": 0})["children"]
        card = next(row for row in partial if row["name"] == "card")
        assert "hasKnowledge" not in card  # Failed index parsing does not prove absence.
        monkeypatch.setattr(opened.index, "state", replace(opened.index.state, state="complete"))
        unavailable = replace(opened, code_tree=None, code_note="missing code pairing")
        monkeypatch.setattr(
            coverage,
            "read_memory_file",
            lambda *args: FileRead(
                "system/settings.json",
                "present",
                '{"onboarding":{"storage":{"mode":"memory-repo"}}}',
            ),
        )
        unknown = coverage.read_tree_listing(unavailable, ".", {"card": 1})["children"]
        assert "missing code pairing" in unknown[0]["coverage"]["detail"]


def fail_memory(*args: object) -> coverage.Pathnames:
    raise PermissionError("denied")


@pytest.mark.parametrize("pinned", [False, True], ids=["working-directory", "pinned-memory"])
def test_real_composed_tree_counts_and_presence_from_selected_settings(
    world: World, pinned: bool
) -> None:
    """Real Git/code paths, memory enumeration, scope settings and indexed descendants."""
    for name in [
        "own/a.py",
        "deep/sub/a.py",
        "card/a.py",
        "card/ignored.py",
        "card/outside.bin",
        "indexed/nested/a.py",
        "empty/a.py",
        "sidecar/a.py",
    ]:
        path = world.code / "probe" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x = 1\n")
    code_commit = _commit(world.code, "coverage code fixture")
    prose = {
        "own/overview.md": "# Own",
        "deep/sub/overview.md": "# Descendant",
        "card/a.py.md": "# Card",
        "card/deleted.py.md": "# Deleted",
        "card/ignored.py.md": "# Excluded",
        "card/outside.bin.md": "# Out of scope",
    }
    for name, text in prose.items():
        path = world.memory / "onboarding/probe" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    for name, document in {
        "sidecar/a.py.json": _sidecar("probe/sidecar/a.py", []),
        "indexed/nested/a.py.json": _sidecar(
            "probe/indexed/nested/a.py", [_realization("RLZ-C0WNT1", REVIEW_INVARIANT, "counted")]
        ),
    }.items():
        path = world.memory / "onboarding/probe" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(document))
    (world.memory / "onboarding/probe/empty").mkdir()
    settings = world.memory / "system/settings.json"
    settings.parent.mkdir(exist_ok=True)
    settings.write_text(
        json.dumps(
            {
                "onboarding": {
                    "storage": {"mode": "memory-repo"},
                    "pathRules": {
                        "include": {"fileTypes": [".py"]},
                        "exclude": {"paths": ["**/ignored.py"]},
                    },
                }
            }
        )
    )
    memory_commit = _commit(world.memory, f"coverage memory fixture\n\nCode-Commit: {code_commit}")
    opened = open_selection(world.config, "demo", memory_commit if pinned else "published")
    assert isinstance(opened, ReaderSelection)
    with opened:
        assert opened.index.state.complete, opened.index.state
        answer = tree_listing(opened, "probe")
    rows = {row["name"]: row for row in answer["children"]}
    assert answer["code"]["state"] == "listed"
    assert rows["own"]["hasOverview"] and rows["own"]["hasKnowledge"]
    assert rows["deep"]["hasOverview"] is False and rows["deep"]["hasKnowledge"]
    assert rows["card"]["coverage"] == {"state": "counted", "files": 1, "cards": 1}
    assert rows["own"]["coverage"] == {"state": "counted", "files": 1, "cards": 0}
    assert rows["empty"]["hasKnowledge"] is False
    assert rows["sidecar"]["hasKnowledge"] is False
    assert rows["indexed"]["hasKnowledge"] and rows["indexed"]["entries"] == 1
    assert rows["indexed"]["hasOverview"] is False
    assert rows["sidecar"]["onboarding"]
    if not pinned:  # Git cannot retain an empty directory; the working mirror can.
        assert rows["empty"]["onboarding"]


def test_unsearchable_mirror_root_is_unavailable_not_zero(world: World) -> None:
    opened = open_selection(world.config, "demo", "published")
    assert isinstance(opened, ReaderSelection)
    parent = world.memory / "onboarding/dashboard"
    before = parent.stat().st_mode
    parent.chmod(0)
    try:
        with opened:
            rows = tree_listing(opened, "dashboard/src")["children"]
        assert os.geteuid() != 0, "This permission witness needs the ordinary non-root test user"
        directories = [row for row in rows if row["kind"] == "dir"]
        assert directories
        for row in directories:
            assert row["coverage"]["state"] == "unavailable"
            assert "PermissionError" in row["coverage"]["detail"]
            assert "cards" not in row["coverage"] and "files" not in row["coverage"]
            assert "hasOverview" not in row and "hasKnowledge" not in row
    finally:
        parent.chmod(before)


def test_requested_mirror_symlink_cannot_enumerate_outside_memory(
    world: World, tmp_path: Path
) -> None:
    outside = tmp_path / "outside-memory"
    outside.mkdir()
    (outside / "secret.py.md").write_text("outside memory")
    (world.memory / "onboarding/escape").symlink_to(outside, target_is_directory=True)
    opened = open_selection(world.config, "demo", "published")
    assert isinstance(opened, ReaderSelection)
    with opened:
        memory, problem = coverage._read_memory(opened, "escape")
    assert memory is None and problem is not None
    assert "escapes the repository root" in problem
