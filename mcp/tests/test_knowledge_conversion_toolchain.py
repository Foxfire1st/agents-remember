"""MIK-R24 rules 5 and 9: the master line's toolchain reads the text format, and unconverted memory
is read as legacy-format and refused elsewhere once its official line is converted.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from agents_remember.application.memory_tools import memory_init_tool
from agents_remember.application.read_files import read_ar_files_tool
from agents_remember.kernel.memory_init import LAYOUT_MARKER_TEXT
from agents_remember.kernel.primitives.runtime_config import load_config
from agents_remember.memory.conversion.code_objects import CodeObjects
from agents_remember.memory.conversion.convert import convert_memory
from agents_remember.memory.conversion.inputs import memory_from_directory, write_changed
from agents_remember.memory_quality.check import DriftCheckContext, run_memory_quality_check
from agents_remember.memory_quality.reference_state import check_references, fix_references
from agents_remember.models.knowledge_files.canonical import canonical_text
from agents_remember.models.knowledge_files.ids import derived_record_id
from agents_remember.observer import reset_ambient
from agents_remember.worktrees.knowledge_crossing import unconverted_line_refusal
from knowledge_conversion_test_support import (
    APP,
    APP_SOURCE,
    CodeFixture,
    code_repository,
    git,
    memory_repository,
)
from test_read_ar_files import REPO, _build_context, _make_config, _write_route_index


def _convert_in_place(memory: Path, code: CodeFixture) -> None:
    outcome = convert_memory(
        memory_from_directory(memory), CodeObjects(code.root), paired_commit=code.head
    )
    write_changed(memory, outcome.changed)


def test_read_ar_files_returns_legacy_format_or_resolved_references(tmp_path: Path) -> None:
    code = code_repository(tmp_path / "workspace" / REPO)
    memory = tmp_path / "memory"
    memory_repository(memory, code)
    onboarding = memory / "onboarding"
    _write_route_index(onboarding, "", covered=[APP])
    config = _make_config(tmp_path, code.root)
    context = _build_context(
        code.root, onboarding, coordination_root=config.coordination_root, storage_mode="external"
    )
    reset_ambient()

    def read() -> dict:
        return read_ar_files_tool(config, repo_id=REPO, files=[{"path": APP}], _context=context)

    legacy = read()
    assert legacy["files"][0]["format"] == "legacy-format"
    assert "references" not in legacy["files"][0] and "sidecar" not in legacy["files"][0]
    assert legacy["published_intent"]["state"] == "legacy-format"

    _convert_in_place(memory, code)
    sidecar_path = onboarding / f"{APP}.json"
    sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
    invariant = derived_record_id("invariant", "inv-one")
    sidecar["references"]["9"] = {"targets": [{"kind": "invariant", "id": invariant}]}
    sidecar_path.write_text(canonical_text(sidecar), encoding="utf-8")

    payload = read()
    # The converted tree's knowledge section is the derived index's (MIK-R23 rule 6), read through
    # this same route; test_knowledge_index_reuse measures that block's content.
    block = payload["published_intent"]
    assert block["state"] == "recorded", block
    assert block["memoryTree"]["indexState"] == "complete"
    statements = {row.get("statement") for row in block["seeds"][0]["rows"]}  # leaf (MIK-R01)
    assert "Alpha adds one." in statements  # the exported invariant realized in this file
    converted = payload["files"][0]
    assert converted["format"] == "text/v2"
    assert "- alpha and beta compute the total. [2]" in converted["onboarding"]
    assert converted["sidecar"]["path"] == APP
    by_number = {reference["number"]: reference for reference in converted["references"]}
    assert by_number[2]["note"] == "alpha and beta compute the total."
    assert by_number[2]["targets"][0]["anchor"]["path"] == APP  # filled in for the own file
    assert by_number[9]["targets"][0]["record"]["statement"] == "Alpha adds one."


def test_references_are_checked_and_only_mechanical_moves_are_fixed(tmp_path: Path) -> None:
    code = code_repository(tmp_path / "code")
    memory = tmp_path / "memory"
    memory_repository(memory, code)
    _convert_in_place(memory, code)
    assert check_references(memory, code.root)["reportOnlyFindings"] == []

    shifted = '"""App."""\n# one\n# two\n' + APP_SOURCE.split('"""App."""\n', 1)[1]
    (code.root / APP).write_text(shifted.replace("total * 2", "total * 3"), encoding="utf-8")
    before = check_references(memory, code.root)
    assert before["ok"] is True and before["findingCount"] == 0  # stale is never a finding
    stale = {(item["field"], item["state"]) for item in before["reportOnlyFindings"]}
    assert {state for _, state in stale} == {"stale"}

    fixed = fix_references(memory, code.root)

    references = json.loads((memory / f"onboarding/{APP}.json").read_text())["references"]
    assert references["4"]["targets"][0]["anchor"]["locator"] == {
        "kind": "line_range",
        "start": 7,
        "end": 7,
    }  # the literal moved two lines down and was re-found exactly once
    assert fixed["refreshedAnchors"] >= 3
    after = {(item["path"], item["field"]) for item in fixed["stale"]}
    # beta's body changed: that is the curator's to refresh, in the card and in the route overview.
    assert after == {
        (f"onboarding/{APP}.json", "references.2.targets.1"),
        ("onboarding/src/overview.json", "references.1.targets.0"),
    }


def test_memory_quality_reads_the_converted_format(tmp_path: Path) -> None:
    code = code_repository(tmp_path / "code")
    memory = tmp_path / "memory"
    memory_repository(memory, code)
    _convert_in_place(memory, code)
    git(memory, "add", "-A")
    git(memory, "commit", "-q", "-m", "converted")
    result = run_memory_quality_check(
        memory / "onboarding",
        drift_context=DriftCheckContext(code_repository_root=code.root, context=None),
    )

    assert result["ok"] is True, result["findings"]
    assert result["checks"]["knowledge.converted"]["status"] == "converted"
    assert result["checks"]["style.update_history.history_order"]["status"] == (
        "not-applicable-converted"
    )
    assert "integrity.onboarding_drift_check.summary" not in result["checks"]


def test_an_unconverted_leaf_is_refused_only_once_its_official_line_is_converted(
    tmp_path: Path,
) -> None:
    code = code_repository(tmp_path / "code")
    memory = tmp_path / "memory"
    memory_repository(memory, code)
    leaf = tmp_path / "leaf"
    git(memory, "worktree", "add", "-q", "-b", "leaf", str(leaf))

    def refusal() -> str | None:
        return unconverted_line_refusal(
            memory_worktree=leaf,
            memory_repository=memory,
            official_branch="main",
            operation="knowledge-ingest",
        )

    assert refusal() is None  # before the cutover every line is unconverted: nothing changes
    _convert_in_place(memory, code)
    git(memory, "add", "-A")
    git(memory, "commit", "-q", "-m", "convert the official line")

    refused = refusal()
    assert refused is not None and "crossing sync" in refused and "worktree_sync" in refused
    assert (
        unconverted_line_refusal(
            memory_worktree=memory, memory_repository=memory, official_branch="main", operation="x"
        )
        is None
    )


def test_memory_init_creates_new_memory_in_the_text_format_only(tmp_path: Path) -> None:
    root = tmp_path / "world"
    git_root = root / "repo"
    git_root.mkdir(parents=True)
    git(git_root, "init", "-q", "-b", "main")
    (root / "coordination").mkdir()
    settings = root / "settings.json"
    settings.write_text(
        json.dumps(
            {
                "version": 1,
                "coordinationRoot": (root / "coordination").as_posix(),
                "workspaceRoot": root.as_posix(),
                "repositories": {"repo": {}},
            }
        ),
        encoding="utf-8",
    )
    config = load_config(settings)
    assert (
        canonical_text({"schema": "ar-memory-layout/v2", "conversion": "1"}) == LAYOUT_MARKER_TEXT
    )

    created = memory_init_tool(config, repo_id="repo", initial_branch="main")
    memory = Path(str(created["memoryRoot"]))
    assert created["layoutMarker"] == "created"
    assert (memory / "knowledge/layout.json").read_text(encoding="utf-8") == LAYOUT_MARKER_TEXT

    (memory / "knowledge/layout.json").unlink()
    (memory / "onboarding/legacy.py.md").write_text("# legacy\n", encoding="utf-8")
    repaired = memory_init_tool(config, repo_id="repo", initial_branch="main")
    assert repaired["layoutMarker"] == "unconverted-existing-memory"
    assert not (memory / "knowledge/layout.json").exists()


@pytest.fixture(autouse=True)
def _ambient() -> None:
    reset_ambient()
