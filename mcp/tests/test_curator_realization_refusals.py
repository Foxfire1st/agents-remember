"""The five realization refusals, driven through the taskless ``knowledge-bootstrap`` hand-off.

``curator_realization_authoring`` states five refusal codes for a hand-off entry whose realization
cannot be stored as authored. Its own module test went away with the database writer, but the
refusals are still how the curator file writer reads a hand-off list. Each case below sends one
defective entry beside one sound entry through the shipped command line on a converted scratch
memory root, and asserts that the planning run names the code and the offending entry, that the sound
entry is unaffected, and that the committing run writes nothing for the refused entry.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from agents_remember.application.curator_realization_authoring import (
    CODE_NOT_TEXT,
    CODE_RATIONALE_ABSENT,
    CODE_RATIONALE_TOO_LONG,
    CODE_ROLE_UNKNOWN,
    CODE_ROUTE_ABSENT_LITERAL,
)
from agents_remember.cli.__main__ import main
from agents_remember.models.knowledge.base import PROSE_MAX_LENGTH
from test_knowledge_bootstrap import argv, entry, hand_off, tree, world

pytestmark = pytest.mark.evidence_unit


def _target(entry_dict: dict[str, Any]) -> dict[str, Any]:
    return entry_dict["target"][0]


def _drop_rationale(entry_dict: dict[str, Any]) -> None:
    _target(entry_dict).pop("rationale")


def _array_rationale(entry_dict: dict[str, Any]) -> None:
    _target(entry_dict)["rationale"] = ["not", "text"]


def _unknown_role(entry_dict: dict[str, Any]) -> None:
    _target(entry_dict)["role"] = "absent"


def _long_rationale(entry_dict: dict[str, Any]) -> None:
    _target(entry_dict)["rationale"] = "x" * (PROSE_MAX_LENGTH + 1)


def _absent_route(entry_dict: dict[str, Any]) -> None:
    _target(entry_dict)["governing_route"] = " Absent "


@pytest.mark.parametrize(
    ("code", "defect"),
    [
        (CODE_RATIONALE_ABSENT, _drop_rationale),
        (CODE_NOT_TEXT, _array_rationale),
        (CODE_ROLE_UNKNOWN, _unknown_role),
        (CODE_RATIONALE_TOO_LONG, _long_rationale),
        (CODE_ROUTE_ABSENT_LITERAL, _absent_route),
    ],
)
def test_each_realization_refusal_surfaces_through_the_bootstrap_route_and_writes_nothing(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    code: str,
    defect: Callable[[dict[str, Any]], None],
) -> None:
    one = world(tmp_path / "world", converted=True)
    broken = entry("B-broken")
    defect(broken)
    listed = hand_off(one.root, "first", {"entries": [broken]})
    before = tree(one.memory_root)

    planned_code = main(argv(one, listed, "--wave", "wave-1", "--json"))
    planned = capsys.readouterr().out
    assert planned_code != 0, planned
    assert f"[{code}]" in planned and "B-broken" in planned, planned

    committed_code = main(argv(one, listed, "--wave", "wave-1", "--commit", "--json"))
    capsys.readouterr()
    assert committed_code != 0
    assert tree(one.memory_root) == before

    # The same list with the defect corrected is written, so the refusal was the defect's own.
    fixed = hand_off(one.root, "second", {"entries": [entry("B-broken")]})
    assert main(argv(one, fixed, "--wave", "wave-1", "--commit", "--json")) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "written"


@pytest.mark.parametrize(
    ("anchor", "extra_file", "reason"),
    [
        (
            {"path": "pkg/module.py", "locator": {"kind": "symbol", "value": "no_such_function"}},
            None,
            "does not define",
        ),
        (
            {"path": "pkg/module.py", "locator": {"kind": "symbol", "value": "twice"}},
            ("pkg/module.py", "\n\ndef twice():\n    return 1\n\n\ndef twice():\n    return 2\n"),
            "bound more than once",
        ),
        (
            {"path": "pkg/notes.txt", "locator": {"kind": "symbol", "value": "anything"}},
            ("pkg/notes.txt", "anything is only mentioned here\n"),
            "no grammar",
        ),
    ],
)
def test_a_symbol_the_code_does_not_bind_to_one_construct_is_refused_and_writes_nothing(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    anchor: dict[str, Any],
    extra_file: tuple[str, str] | None,
    reason: str,
) -> None:
    one = world(tmp_path / "world", converted=True)
    if extra_file is not None:
        relative, text = extra_file
        existing = one.code_root / relative
        previous = existing.read_text(encoding="utf-8") if existing.exists() else ""
        existing.write_text(previous + text, encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=one.code_root, check=True)
        subprocess.run(
            ["git", "-c", "user.email=a@b.c", "-c", "user.name=x", "commit", "-q", "-m", "more"],
            cwd=one.code_root,
            check=True,
        )
    broken = entry("B-anchor")
    _target(broken).update(anchor)
    listed = hand_off(one.root, "first", {"entries": [broken]})
    before = tree(one.memory_root)

    code = main(argv(one, listed, "--wave", "wave-1", "--commit", "--json"))
    printed = capsys.readouterr().out

    assert code != 0, printed
    assert reason in printed and "B-anchor" in printed, printed
    assert tree(one.memory_root) == before


@pytest.mark.parametrize(
    "scope", [None, {"applicability": "  ", "conditions": [], "exclusions": []}]
)
def test_an_unfilled_scope_is_a_named_refusal_and_writes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], scope: dict[str, Any] | None
) -> None:
    one = world(tmp_path / "world", converted=True)
    broken = entry("B-scope")
    broken["scope"] = scope
    listed = hand_off(one.root, "first", {"entries": [broken]})
    before = tree(one.memory_root)

    code = main(argv(one, listed, "--wave", "wave-1", "--commit", "--json"))
    printed = capsys.readouterr().out

    assert code != 0, printed
    assert "[unfilled_curation_scope]" in printed and "B-scope" in printed, printed
    assert tree(one.memory_root) == before


def _written_realizations(root: Path) -> list[dict[str, Any]]:
    sidecar = json.loads((root / "onboarding/pkg/module.py.json").read_text(encoding="utf-8"))
    return sidecar["realizes"]


def test_a_target_states_its_own_role_and_rationale_and_otherwise_inherits_the_entrys(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    one = world(tmp_path / "world", converted=True)
    written = entry("B-inherit")
    written["realization_role"] = "enforcement"
    written["realization_rationale"] = "  The entry states this once for every target.  "
    inheriting = _target(written)
    inheriting.pop("rationale")
    stating = {
        "path": inheriting["path"],
        "locator": {"kind": "file"},
        "role": "support",
        "rationale": "This target states its own words.",
    }
    written["target"] = [inheriting, stating]
    listed = hand_off(one.root, "first", {"entries": [written]})

    assert main(argv(one, listed, "--wave", "wave-1", "--commit", "--json")) == 0
    capsys.readouterr()
    by_locator = {
        item["anchor"]["locator"]["kind"]: item for item in _written_realizations(one.memory_root)
    }
    assert by_locator["symbol"]["role"] == "enforcement"
    assert by_locator["symbol"]["rationale"] == "The entry states this once for every target."
    assert by_locator["file"]["role"] == "support"
    assert by_locator["file"]["rationale"] == "This target states its own words."


def test_a_targets_path_resolves_in_the_captured_candidate_tree_including_uncommitted_files(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """C is the code worktree captured as a tree: an untracked file is in it, a deleted one is not."""

    one = world(tmp_path / "world", converted=True)
    (one.code_root / "pkg" / "live_only.py").write_text("def live():\n    return 1\n", "utf-8")
    uncommitted = entry("B-live")
    _target(uncommitted).update(
        {"path": "pkg/live_only.py", "locator": {"kind": "symbol", "value": "live"}}
    )
    listed = hand_off(one.root, "first", {"entries": [uncommitted]})
    assert main(argv(one, listed, "--wave", "wave-1", "--commit", "--json")) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "written"
    sidecar = json.loads((one.memory_root / "onboarding/pkg/live_only.py.json").read_text("utf-8"))
    assert sidecar["realizes"][0]["anchor"]["locator"] == {"kind": "symbol", "name": "live"}

    # A tracked file removed from the live directory is no longer in C, so it does not resolve.
    (one.code_root / "pkg" / "module.py").unlink()
    gone = hand_off(one.root, "second", {"entries": [entry("B-gone")]})
    before = tree(one.memory_root)
    assert main(argv(one, gone, "--wave", "wave-1", "--commit", "--json")) != 0
    refused = capsys.readouterr().out
    assert "B-gone" in refused and "pkg/module.py" in refused, refused
    assert tree(one.memory_root) == before
