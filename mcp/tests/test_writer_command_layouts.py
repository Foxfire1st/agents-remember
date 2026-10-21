"""MIK-R194 rule 9 on real checkout layouts: a primary checkout refuses, a linked one may write.

The guard's decision is read from a real Git layout built here — a ``.git`` directory is the primary
checkout, a ``.git`` file is a linked worktree — so these cases exercise the guard's own rule and the
command's own cause sorting rather than a stubbed refusal. Every refusal is asserted on the report, and
each asserts that no question was published and no memory byte moved.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from agents_remember.application import knowledge_write_command
from agents_remember.application.knowledge_write_command import CHECKOUT_GUARD_CLAUSE
from agents_remember.application.knowledge_write_route import WriteSubmission, leaf_write_report
from agents_remember.kernel.primitives.checkout_coordination import (
    CheckoutCoordinationError,
    CheckoutLocation,
    require_linked,
    resolve_checkout_location,
)
from agents_remember.models.knowledge_files import canonical_text
from agents_remember.worktrees.worktree_contract import load_contract
from knowledge_writer_test_support import BASE_INVARIANT, World, build_world, git, tree_bytes

DECISION_ID = "DEC-CA5E01"
SUBJECT = f"reconsider:{DECISION_ID}#1"


def _layout(root: Path, kind: str) -> Path:
    """A real checkout: ``.git`` a directory for the primary, a file for a linked worktree."""

    (root / "mcp" / "src" / "agents_remember").mkdir(parents=True)
    (root / "mcp" / "pyproject.toml").write_text("[project]\nname = 'agents-remember'\n")
    package = root / "mcp" / "src" / "agents_remember" / "__init__.py"
    package.write_text("")
    if kind == "primary":
        (root / ".git").mkdir()
    else:
        (root / ".git").write_text("gitdir: /elsewhere/.git/worktrees/leaf\n")
    return package


def test_a_real_primary_layout_is_refused_by_the_guard(tmp_path: Path) -> None:
    package = _layout(tmp_path / "primary", "primary")
    location = resolve_checkout_location(package)
    assert isinstance(location, CheckoutLocation)
    assert location.kind == "primary"
    try:
        require_linked(location)
    except CheckoutCoordinationError as error:
        assert "primary checkout is refused" in str(error)
    else:  # pragma: no cover - the guard must refuse
        raise AssertionError("a primary layout was admitted")


def test_a_real_linked_layout_is_admitted_and_owns_its_coordination_root(tmp_path: Path) -> None:
    package = _layout(tmp_path / "group" / "leaf", "linked")
    location = resolve_checkout_location(package)
    assert isinstance(location, CheckoutLocation)
    assert location.kind == "linked"
    assert require_linked(location) is location
    assert location.coordination_root.parent.name == "provider-runtime"


def test_the_primary_layouts_clause_reaches_the_report_and_writes_nothing(tmp_path: Path) -> None:
    world = build_world(tmp_path / "world")
    (world.memory / "knowledge/decisions").mkdir(parents=True, exist_ok=True)
    (world.task_root / "knowledge-worklist.json").write_text('{"items": []}', encoding="utf-8")
    (world.memory / "knowledge/decisions").mkdir(parents=True, exist_ok=True)
    (world.memory / f"knowledge/decisions/{DECISION_ID}-choice.json").write_text(
        _decision_document(), encoding="utf-8"
    )
    (world.memory / f"knowledge/decisions/{DECISION_ID}-choice.json").write_text(
        canonical_text(
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
                "links": [
                    {"relation": "reconsider_on", "target": BASE_INVARIANT, "alternative": 1}
                ],
                "admission": {
                    "criteria": ["real_alternatives"],
                    "justification": "Weighed and chosen.",
                },
                "origin": {"task": "260928-MIK", "leaf": "260928-MIK-L13"},
            }
        ),
        encoding="utf-8",
    )
    rows = [
        {"subject": SUBJECT, "disposition": "raise", "reason": "The cost may have fallen."},
        {"subject": BASE_INVARIANT, "disposition": "no_impact", "reason": "No effect."},
    ]
    primary = resolve_checkout_location(_layout(tmp_path / "primary", "primary"))

    def guard() -> None:
        require_linked(primary)

    assert (
        knowledge_write_command.discovered_settings(
            tmp_path,
            discover=lambda _cwd: tmp_path / "settings.json",
            guard=guard,
            load=lambda _path: "unused",
            unreadable=(ValueError,),
        )
        == CHECKOUT_GUARD_CLAUSE
    )

    before = tree_bytes(world.memory)
    report = leaf_write_report(
        load_contract(world.contract),
        WriteSubmission(
            document={"history": rows},
            handoff_path="wave6.json",
            authorization_ref="authorized-by-test",
            commit=True,
        ),
        command=knowledge_write_command.CommandRoute(
            settings=lambda: CHECKOUT_GUARD_CLAUSE,
            handoff_path=str(tmp_path / "wave6.json"),
            handoff_name="wave6.json",
            contract_path=str(world.contract),
            authorization_ref="authorized-by-test",
        ),
    )
    assert not isinstance(report, str), report
    text = report.render()
    assert report.refused, text
    assert f"task document: {CHECKOUT_GUARD_CLAUSE}" in text
    assert f"contractPath {world.contract.resolve()}" in text
    assert "Send the same hand-off through the mounted knowledge_write:" in text
    assert "--config" not in text
    assert not report.questions_published
    assert tree_bytes(world.memory) == before


def _decision_document() -> str:
    """The decision the raise names, so the command reaches the checkout guard rather than the subject."""

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


def _settings(root: Path, coordination_root: Path) -> Path:
    """Real discovery's own convention, under a directory the command is run from."""

    settings = root / ".claude" / "mcp" / "agents-remember-settings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text(
        canonical_text(
            {
                "coordinationRoot": str(coordination_root),
                "workspaceRoot": str(root),
                "transcriptRoot": str(root / "logs"),
                "repositories": {},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return settings


def _prepare(world: World) -> None:
    """The leaf state the command needs, written before anything is measured."""

    (world.task_root / "knowledge-worklist.json").write_text('{"items": []}', encoding="utf-8")
    (world.memory / "knowledge/decisions").mkdir(parents=True, exist_ok=True)
    (world.memory / f"knowledge/decisions/{DECISION_ID}-choice.json").write_text(
        _decision_document(), encoding="utf-8"
    )


def _handoff(root: Path) -> Path:
    hand_off = root / "wave.json"
    hand_off.write_text(
        canonical_text(
            {
                "history": [
                    {
                        "subject": SUBJECT,
                        "disposition": "raise",
                        "reason": "The cost may have fallen.",
                    },
                    {"subject": BASE_INVARIANT, "disposition": "no_impact", "reason": "No effect."},
                ]
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return hand_off


def _source_environment(checkout: Path) -> dict[str, str]:
    """Import this candidate from the fixture checkout, without inheriting pytest's mode."""

    source = Path(knowledge_write_command.__file__).resolve().parents[1]
    destination = checkout / "mcp/src/agents_remember"
    shutil.copytree(source, destination, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copyfile(source.parents[1] / "pyproject.toml", checkout / "mcp/pyproject.toml")
    return {**os.environ, "PYTHONPATH": str(destination.parent), "PYTHONDONTWRITEBYTECODE": "1"}


def _probe(checkout: Path, environment: dict[str, str]) -> dict[str, Any]:
    """Prove discovery, settings validity, package location and the real guard before ingest."""

    script = """
import json
from agents_remember.cli.discovery import discover_config
from agents_remember.cli.knowledge_write_route import command_settings
from agents_remember.kernel.primitives import checkout_coordination as checkout
from agents_remember.kernel.primitives.runtime_config import config_from_mapping
path = discover_config()
validated = config_from_mapping(json.loads(path.read_text()), path)
loaded = command_settings()
location = checkout.resolve_checkout_location()
print(json.dumps({
    "package": str(checkout.__file__), "mode": checkout.declared_execution_mode(),
    "checkout": str(location.checkout_root), "kind": location.kind,
    "settings": str(path), "settingsRoot": str(validated.coordination_root),
    "loaded": loaded if isinstance(loaded, str) else {
        "root": str(loaded.coordination_root), "binding": loaded.checkout_binding.as_payload()
    }
}))
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=checkout,
        env=environment,
        text=True,
        capture_output=True,
        check=True,
        timeout=60,
    )
    probe = json.loads(result.stdout)
    assert probe["mode"] is None
    assert probe["checkout"] == str(checkout)
    assert Path(probe["package"]).is_relative_to(checkout)
    return probe


def _run_ingest(
    world: World, checkout: Path, environment: dict[str, str], hand_off: Path
) -> subprocess.CompletedProcess[str]:
    """The source CLI in a new, undeclared interpreter; no guard/settings mocks."""

    return subprocess.run(
        [
            sys.executable,
            "-m",
            "agents_remember.cli",
            "knowledge-ingest",
            "--contract",
            str(world.contract),
            "--list",
            str(hand_off),
            "--authorization-ref",
            "authorized-by-test",
            "--commit",
            "--json",
        ],
        cwd=checkout,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )


def _assert_refused_batch(
    result: subprocess.CompletedProcess[str], world: World, hand_off: Path
) -> None:
    assert result.returncode == 1, result.stderr + result.stdout
    report = json.loads(result.stdout)
    assert report["state"] == "refused", report
    assert report["written"] == [] and report["questionsPublished"] == [], report
    assert report["historyRows"] == [] and report["plannedBeforeRefusal"] == 1, report
    for expected in (
        "Send the same hand-off through the mounted knowledge_write:",
        f"contractPath {world.contract}",
        f"handoffPath {hand_off}",
        "handoffName wave.json",
        "authorizationRef authorized-by-test",
        "preview, then commit: true",
    ):
        assert expected in result.stdout, result.stdout
    assert "--config" not in result.stdout


def test_the_real_command_refuses_from_a_primary_checkout(tmp_path: Path) -> None:
    """Valid discovered settings cannot give an undeclared primary checkout task authority."""

    world = build_world(tmp_path / "coordination")
    _prepare(world)
    settings = _settings(tmp_path, world.root)
    environment = _source_environment(world.code)
    probe = _probe(world.code, environment)
    assert probe["kind"] == "primary" and (world.code / ".git").is_dir()
    assert probe["settings"] == str(settings)
    assert probe["settingsRoot"] == str(world.root)
    assert probe["loaded"] == CHECKOUT_GUARD_CLAUSE
    hand_off = _handoff(tmp_path)
    before = tree_bytes(world.memory)
    task_before = tree_bytes(world.task_root)
    result = _run_ingest(world, world.code, environment, hand_off)
    _assert_refused_batch(result, world, hand_off)
    assert CHECKOUT_GUARD_CLAUSE in result.stdout, result.stdout
    assert tree_bytes(world.memory) == before
    assert tree_bytes(world.task_root) == task_before


def test_the_real_command_writes_from_a_linked_checkout(tmp_path: Path) -> None:
    """A bound linked checkout refuses the raise batch, then writes its ordinary row alone."""

    world = build_world(tmp_path / "coordination")
    linked = tmp_path / "code-leaf"
    git(world.code, "worktree", "add", "-q", "-b", "leaf", str(linked))
    contract = world.contract.read_text(encoding="utf-8")
    world.contract.write_text(contract.replace(str(world.code), str(linked)), encoding="utf-8")
    _prepare(world)
    settings = _settings(tmp_path, world.root)
    environment = _source_environment(linked)
    probe = _probe(linked, environment)
    disposable = tmp_path / "provider-runtime/dev-ar-coordination"
    assert probe["kind"] == "linked" and (linked / ".git").is_file()
    assert probe["settings"] == str(settings)
    assert probe["settingsRoot"] == str(world.root)
    assert probe["loaded"]["root"] == str(disposable)
    assert probe["loaded"]["binding"]["configuredRoot"] == str(world.root)
    hand_off = _handoff(tmp_path)
    before = tree_bytes(world.memory)
    task_before = tree_bytes(world.task_root)
    result = _run_ingest(world, linked, environment, hand_off)
    _assert_refused_batch(result, world, hand_off)
    assert f"but the contract names {world.root}" in result.stdout
    assert str(disposable) in result.stdout
    assert f"loaded from the linked worktree {linked}" in result.stderr
    assert tree_bytes(world.memory) == before
    assert tree_bytes(world.task_root) == task_before

    document = json.loads(hand_off.read_text(encoding="utf-8"))
    document["history"] = document["history"][1:]
    hand_off.write_text(canonical_text(document), encoding="utf-8")
    result = _run_ingest(world, linked, environment, hand_off)
    assert result.returncode == 0, result.stderr + result.stdout
    report = json.loads(result.stdout)
    assert report["state"] == "written", report
    history = world.memory / "knowledge/history/260928-MIK-L99.json"
    rows = json.loads(history.read_text(encoding="utf-8"))["rows"]
    assert any(
        row["subject"] == BASE_INVARIANT and row["disposition"] == "no_impact" for row in rows
    )
    assert tree_bytes(world.memory) != before
    assert tree_bytes(world.task_root) == task_before
