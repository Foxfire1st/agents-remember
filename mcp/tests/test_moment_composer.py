"""The start moment's file and message, composed over a real Git change.

Which files a change touches is read from Git as Git stores the names, so a path with characters
outside ASCII keeps its rules and its moved anchors. A message at a tight limit keeps its counts,
its number of moved rules and a whole pointer to the moment's file.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from agents_remember.application.file_briefs.moments import (
    MOMENTS,
    MomentMessage,
    MomentReport,
    StartRequest,
    changed_files,
    compose_start_message,
    moment_message,
)
from agents_remember.kernel.memory_ignore import BRIEFS_IGNORE_TEXT
from agents_remember.models.knowledge_files.documents import (
    LAYOUT_MARKER_PATH,
    file_sidecar_path,
    record_path,
)
from knowledge_index_test_support import (
    anchor,
    commit_all,
    git,
    init_repository,
    write_document,
)

CAFE = "src/café.py"
RESUME = "src/résumé.py"
RULE = "INV-CAFE01"


def _memory(root: Path) -> None:
    write_document(
        root, LAYOUT_MARKER_PATH, {"schema": "ar-memory-layout/v2", "conversion": "fixture"}
    )
    write_document(
        root,
        record_path("invariant", RULE, "brewing"),
        {
            "schema": "ar-invariant/v1",
            "id": RULE,
            "origin": {"task": "261112-KNO"},
            "revision": 1,
            "status": "accepted",
            "statement": "Brewing always returns a full cup.",
            "applicability": "Every brew.",
            "conditions": [],
            "exclusions": [],
            "supersedes": [],
            "admission": "legacy-unassessed",
            "title": "A brew returns a full cup",
        },
    )
    write_document(
        root,
        file_sidecar_path(CAFE),
        {
            "schema": "ar-onboarding-file/v1",
            "path": CAFE,
            "references": {},
            "realizes": [
                {
                    "id": "RLZ-CAFE01",
                    "invariant": RULE,
                    "anchor": anchor("brew"),
                    "role": "enforcement",
                    "rationale": "brew enforces the full cup.",
                }
            ],
        },
    )


def build_change(root: Path) -> tuple[Path, Path, str, Path]:
    """A code checkout with a tracked and an untracked non-ASCII path, and its converted memory.

    Returns the code checkout, the memory, the base commit and an empty coordination root.
    """

    code = root / "code"
    init_repository(code)
    git(code, "config", "core.quotePath", "true")
    (code / "src").mkdir()
    (code / CAFE).write_text("def brew():\n    return 1\n", encoding="utf-8")
    base = commit_all(code, "base")
    (code / CAFE).write_text("def brew():\n    return 2\n", encoding="utf-8")
    (code / RESUME).write_text("def resume():\n    return 3\n", encoding="utf-8")
    memory = root / "memory"
    _memory(memory)
    init_repository(memory)
    (memory / ".gitignore").write_text(BRIEFS_IGNORE_TEXT, encoding="utf-8")
    commit_all(memory, "memory")
    coordination = root / "coordination"
    coordination.mkdir(exist_ok=True)
    return code, memory, base, coordination


@pytest.fixture
def change(tmp_path: Path) -> tuple[Path, Path, str, Path]:
    return build_change(tmp_path)


def test_changed_files_keep_names_git_would_quote(change: tuple[Path, Path, str, Path]) -> None:
    code, _memory_root, base, _coordination = change
    quoted = git(code, "diff", "--name-only", base, "--")
    assert quoted.startswith('"'), quoted
    assert changed_files(code, base) == [CAFE, RESUME]


@pytest.mark.parametrize("moment", MOMENTS)
def test_both_moments_count_the_rules_of_non_ascii_paths(
    change: tuple[Path, Path, str, Path], moment: Any
) -> None:
    code, memory, base, coordination = change
    composed = compose_start_message(
        StartRequest(moment, "261112-KNO-L4", memory, code, base, coordination, 1000)
    )
    assert isinstance(composed, MomentMessage), composed
    report = composed.report
    assert report.changed == (CAFE, RESUME)
    assert [one.head.id for one in report.rules] == [RULE]
    assert [one.head.id for one in report.moved] == [RULE]
    assert "The change touches 2 files with 1 rule" in composed.text
    assert f"Anchors moved at: {RULE} A brew returns a full cup." in composed.text
    written = composed.file.read_text(encoding="utf-8")
    assert CAFE in written and "Brewing always returns a full cup." in written


def _report(moment: Any, moved: int) -> MomentReport:
    """A report shaped as a sandbox review start: 11 files, 25 rules, 5 of them moved."""

    rules = tuple(
        SimpleNamespace(
            head=SimpleNamespace(
                id=f"INV-{number:06d}",
                title=SimpleNamespace(text=f"A rule title of some length, number {number} …"),
            )
        )
        for number in range(25)
    )
    return MomentReport(
        moment=moment,
        leaf="260101-DEMO-L1",
        changed=tuple(f"src/file_{number}.py" for number in range(11)),
        briefs=(),
        rules=cast(Any, rules),
        moved=cast(Any, rules[:moved]),
        families=3,
        without_proof=11,
        documents=5,
    )


LONG_LOCATION = (
    "/var/tmp/agents-remember/ar-e56fdce8a871/sbx-cand/coordination/worktrees/sandbox-app/"
    "01_the-task-8ff09ad102-ar/memory-01_the-task-8ff09ad102/briefs/_task/{moment}.md"
)


@pytest.mark.parametrize("moment", MOMENTS)
@pytest.mark.parametrize("moved", [0, 5])
def test_a_tight_limit_keeps_the_counts_and_a_whole_pointer(moment: Any, moved: int) -> None:
    text = moment_message(
        _report(moment, moved),
        LONG_LOCATION.format(moment=moment),
        320,
        relative_location=f"briefs/_task/{moment}.md",
    )
    assert len(text) <= 320
    assert "The change touches 11 files with 25 rules in 3 families" in text
    assert f"{moved} rules with a moved anchor." in text
    if moved:
        assert "All 5 are named in the file." in text
    assert text.endswith(f" briefs/_task/{moment}.md"), text
    assert not text.endswith("…")


def test_a_tight_limit_names_the_task_scratch_file() -> None:
    location = LONG_LOCATION.format(moment="review-start").replace(
        "memory-01_the-task-8ff09ad102/briefs/_task", "scratch/reviewer/long-request-id"
    )
    text = moment_message(
        _report("review-start", 5),
        location,
        320,
        relative_location="review-start.md in your task scratch folder",
    )
    assert len(text) <= 320
    assert "All 5 are named in the file." in text
    assert text.endswith("review-start.md in your task scratch folder")


@pytest.mark.parametrize("moment", MOMENTS)
def test_room_for_the_absolute_location_keeps_it(moment: Any) -> None:
    location = LONG_LOCATION.format(moment=moment)
    text = moment_message(_report(moment, 5), location, 1000)
    assert text.endswith(location) and len(text) <= 1000
    assert text.count("A rule title of some length") == 5
