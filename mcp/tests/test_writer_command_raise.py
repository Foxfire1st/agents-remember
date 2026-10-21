"""MIK-R194 rule 9: the command's raise refusal names the cause and the route that works."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pytest
from agents_remember.application import knowledge_write_route as application_route
from agents_remember.application.knowledge_write_route import WriteSubmission, leaf_write_report
from agents_remember.cli import knowledge_write_route
from agents_remember.cli.__main__ import build_parser
from agents_remember.kernel.primitives.checkout_coordination import CheckoutCoordinationError
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.knowledge_files import canonical_text
from agents_remember.worktrees.worktree_contract import load_contract
from knowledge_writer_test_support import BASE_INVARIANT, build_world, tree_bytes
from reconsideration_test_support import _config

NO_SETTINGS = "no trusted settings file is found at or above"
GUARD = "the checkout guard refuses settings to an undeclared run from the primary checkout"
UNREADABLE = "cannot be loaded"
OTHER_ROOT = "name coordination root"

DECISION_ID = "DEC-CA5E01"
SUBJECT = f"reconsider:{DECISION_ID}#1"
SETTINGS = Path(".claude") / "mcp" / "agents-remember-settings.json"


def _decision() -> str:
    return canonical_text(
        {
            "schema": "ar-decision/v1",
            "id": DECISION_ID,
            "revision": 1,
            "status": "active",
            "context": "A choice the writer must reach a task document about.",
            "alternatives": [
                {"option": "Chosen", "status": "chosen", "reason": "Weighed."},
                {
                    "option": "Rejected",
                    "status": "rejected",
                    "reason": "It cost more.",
                    "reconsider_when": "The cost falls.",
                },
            ],
            "consequences": ["Later work follows it."],
            "decider": "developer",
            "supersedes": [],
            "links": [{"relation": "reconsider_on", "target": BASE_INVARIANT, "alternative": 1}],
            "admission": {
                "criteria": ["real_alternatives"],
                "justification": "Weighed and chosen.",
            },
            "origin": {"task": "260928-MIK", "leaf": "260928-MIK-L13"},
        }
    )


def _prepare(world: Any, tmp_path: Path) -> tuple[Path, list[dict[str, Any]]]:
    """A leaf list with one raise row and one other valid row, and its path."""

    (world.memory / "knowledge/decisions").mkdir(parents=True, exist_ok=True)
    (world.memory / f"knowledge/decisions/{DECISION_ID}-choice.json").write_text(
        _decision(), encoding="utf-8"
    )
    (world.task_root / "knowledge-worklist.json").write_text('{"items": []}', encoding="utf-8")
    rows = [
        {"subject": SUBJECT, "disposition": "raise", "reason": "The cost may have fallen."},
        {"subject": BASE_INVARIANT, "disposition": "no_impact", "reason": "No effect."},
    ]
    list_path = tmp_path / "wave6.json"
    list_path.write_text(json.dumps({"history": rows}), encoding="utf-8")
    return list_path, rows


def _run(world: Any, list_path: Path, capsys: Any) -> tuple[int, str]:
    args = argparse.Namespace(
        hand_off_list=str(list_path),
        authorization_ref="authorized-by-test",
        commit=True,
        as_json=False,
    )
    code = knowledge_write_route.run_leaf_write(args, load_contract(world.contract))
    return code, capsys.readouterr().out


def _assert_names_the_mounted_route(world: Any, list_path: Path, text: str, clause: str) -> None:
    assert (
        "the raise is refused, so the item stays open: this command cannot reach the leaf's" in text
    )
    assert f"task document: {clause}" in text, text
    assert "Send the same hand-off through the mounted knowledge_write:" in text
    assert f"contractPath {world.contract.resolve()}" in text
    assert f"handoffPath {list_path.resolve()}" in text
    assert "handoffName wave6.json" in text
    assert "authorizationRef authorized-by-test" in text
    assert "preview, then commit: true." in text
    assert "--config" not in text
    assert "nothing was written" in text


def test_no_trusted_settings_at_or_above_the_working_directory(
    tmp_path: Path, capsys: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = build_world(tmp_path / "world")
    list_path, _rows = _prepare(world, tmp_path)
    monkeypatch.chdir(tmp_path)
    before = tree_bytes(world.memory)
    code, text = _run(world, list_path, capsys)
    assert code == 1
    _assert_names_the_mounted_route(world, list_path, text, f"{NO_SETTINGS} {tmp_path}")
    assert tree_bytes(world.memory) == before


def test_the_checkout_guard_refuses_settings_to_an_undeclared_primary_checkout(
    tmp_path: Path, capsys: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = build_world(tmp_path / "world")
    list_path, _rows = _prepare(world, tmp_path)
    settings = tmp_path / SETTINGS
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps({"coordinationRoot": str(tmp_path)}), encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    def refuse() -> None:
        raise CheckoutCoordinationError("undeclared run refused")

    monkeypatch.setattr(knowledge_write_route, "checkout_cli_location", refuse)
    before = tree_bytes(world.memory)
    code, text = _run(world, list_path, capsys)
    assert code == 1
    _assert_names_the_mounted_route(world, list_path, text, GUARD)
    assert tree_bytes(world.memory) == before


def test_a_settings_file_that_cannot_be_loaded(
    tmp_path: Path, capsys: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = build_world(tmp_path / "world")
    list_path, _rows = _prepare(world, tmp_path)
    settings = tmp_path / SETTINGS
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps({"coordinationRoot": str(tmp_path)}), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    before = tree_bytes(world.memory)
    code, text = _run(world, list_path, capsys)
    assert code == 1
    _assert_names_the_mounted_route(world, list_path, text, f"the settings {settings} {UNREADABLE}")
    assert tree_bytes(world.memory) == before


def test_settings_for_another_coordination_root_keep_both_roots_and_the_file(
    tmp_path: Path, capsys: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = build_world(tmp_path / "world")
    list_path, _rows = _prepare(world, tmp_path)
    other = _config(tmp_path / "other")
    settings = tmp_path / SETTINGS
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps({"coordinationRoot": str(tmp_path)}), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(knowledge_write_route, "load_config", lambda _path: other)
    before = tree_bytes(world.memory)
    code, text = _run(world, list_path, capsys)
    assert code == 1
    clause = (
        f"the settings {other.config_path} {OTHER_ROOT} {other.coordination_root}, "
        f"but the contract names {world.root}"
    )
    _assert_names_the_mounted_route(world, list_path, text, clause)
    assert tree_bytes(world.memory) == before


def test_the_batch_without_the_raise_row_commits(
    tmp_path: Path, capsys: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = build_world(tmp_path / "world")
    list_path, rows = _prepare(world, tmp_path)
    monkeypatch.chdir(tmp_path)
    list_path.write_text(json.dumps({"history": rows[1:]}), encoding="utf-8")
    code, text = _run(world, list_path, capsys)
    assert code == 0, text
    assert "knowledge-write written" in text
    assert "row" in text and BASE_INVARIANT in text


def test_the_mounted_route_keeps_its_own_texts(tmp_path: Path) -> None:
    world = build_world(tmp_path / "world")
    _list_path, rows = _prepare(world, tmp_path)
    other: McpRuntimeConfig = _config(tmp_path / "other")
    report = leaf_write_report(
        load_contract(world.contract),
        WriteSubmission(
            document={"history": rows},
            handoff_path="wave6.json",
            authorization_ref="authorized-by-test",
            commit=True,
        ),
        other,
    )
    assert not isinstance(report, str), report
    text = report.render()
    assert report.refused, text
    assert "the mounted knowledge_write" not in text
    assert f"settings {other.config_path} name coordination root" in text
    assert "load settings for the contract's root" in text


def test_the_mounted_route_publishes_the_raise_the_command_refuses(
    tmp_path: Path, capsys: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same rows through the mounted entry publish the question the command cannot raise."""

    world = build_world(tmp_path / "world")
    list_path, rows = _prepare(world, tmp_path)
    published: list[tuple[str, str]] = []

    class Recorder:
        destination = str(world.task_root)

        def check(self, key: str, question: str) -> None:
            del key, question

        def append(self, key: str, question: str) -> None:
            published.append((key, question))

        def append_many(self, raised: list[tuple[str, str]]) -> None:
            published.extend(raised)

    monkeypatch.setattr(application_route, "TaskDocOpenQuestions", lambda **_kwargs: Recorder())
    report = leaf_write_report(
        load_contract(world.contract),
        WriteSubmission(
            document={"history": rows},
            handoff_path="wave6.json",
            authorization_ref="authorized-by-test",
            commit=True,
        ),
        _config(world.root),
    )
    assert not isinstance(report, str), report
    assert report.questions_published, report.render()
    assert len(published) == 1, published
    key, question = published[0]
    assert SUBJECT in key and DECISION_ID in question, published

    published.clear()
    monkeypatch.chdir(tmp_path)
    before = tree_bytes(world.memory)
    code, text = _run(world, list_path, capsys)
    assert code == 1
    _assert_names_the_mounted_route(world, list_path, text, f"{NO_SETTINGS} {tmp_path}")
    assert published == [], published
    assert tree_bytes(world.memory) == before


def test_the_parser_refuses_the_removed_config_option() -> None:
    parser = build_parser()
    arguments = [
        "knowledge-ingest",
        "--contract",
        "leaf-contract",
        "--list",
        "handoff.json",
        "--authorization-ref",
        "wording-proof",
    ]
    with pytest.raises(SystemExit) as caught:
        parser.parse_args([*arguments, "--config", "settings.json"])
    assert caught.value.code == 2
