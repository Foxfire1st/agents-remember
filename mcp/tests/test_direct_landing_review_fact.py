"""Direct landing writes the memory review's fact in the write that closes the leaf's history.

A history file is frozen once closed: a later landing leaves it byte-identical and adds no fact.
Shared fixtures are imported from ``test_direct_landing``, not copied.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from agents_remember.application.worktree_services import build_default_worktree_services
from agents_remember.models.knowledge_files import canonical_text
from agents_remember.worktrees import direct_landing as direct_landing_owner
from agents_remember.worktrees import knowledge_gate
from agents_remember.worktrees.modules.git import head_commit
from agents_remember.worktrees.review_fact_source import NO_PASS_SENTENCE, owner_review_fact
from agents_remember.worktrees.services import bind_worktree_services, reset_worktree_services
from test_direct_landing import _converted_request, _converted_series_fixture, direct_landing_tool
from test_knowledge_closeout_gate import LEAF, A
from test_worktree_support import git

HISTORY = f"knowledge/history/{LEAF}.json"
VERDICT_REF = "notes/reports/memory.md"


@pytest.fixture
def services() -> Iterator[None]:
    yield
    reset_worktree_services()


def _checkout_tree(memory: Path) -> str:
    """The memory checkout's working tree as a commit of every change would record it."""

    with tempfile.TemporaryDirectory() as scratch:
        env = {**os.environ, "GIT_INDEX_FILE": f"{scratch}/index"}
        for args in (["read-tree", "HEAD"], ["add", "-A"]):
            subprocess.run(["git", *args], cwd=memory, env=env, check=True)
        return subprocess.run(
            ["git", "write-tree"], cwd=memory, env=env, check=True, capture_output=True, text=True
        ).stdout.strip()


def _record_pass(world: Any) -> str:
    """Record a passed memory round naming the checkout's current tree, the one the Reviewer read."""

    tree = _checkout_tree(world.memory)
    leaf_json = world.task_root / "leaf.json"
    leaf = json.loads(leaf_json.read_text(encoding="utf-8"))
    passed = {"round": 1, "kind": "memory", "findingsRemain": False, "verdict": "pass"}
    leaf["memoryReviewState"] = {
        "round": 1,
        "recordedRounds": [{**passed, "verdictRef": VERDICT_REF, "memoryTree": tree}],
    }
    leaf_json.write_text(json.dumps(leaf), encoding="utf-8")
    return tree


def _land(fixture: dict, message: str) -> dict[str, Any]:
    world = fixture["world"]
    git(world.memory, "add", "-A")
    git(
        world.memory,
        "commit",
        "--allow-empty",
        "-m",
        f"{message}\n\nCode-Commit: {world.code_base}",
    )
    git(world.code, "commit", "--allow-empty", "-m", "leaf without code changes")
    bind_worktree_services(build_default_worktree_services())
    return direct_landing_tool(
        fixture["config"], _converted_request(fixture, head_commit(world.code))
    )


def _landed_history(world: Any) -> dict[str, Any]:
    return json.loads(git(world.memory, "show", f"HEAD:{HISTORY}"))


def _assert_fact(closed: dict[str, Any], tree: str) -> None:
    assert closed["closed"] is True
    review = closed["review"]
    assert (review["round"], review["verdictRef"], review["tree"]) == (1, VERDICT_REF, tree)
    assert review["handedOverBy"]["leaf"] == LEAF


def test_converted_direct_landing_without_a_pass_closes_and_says_so(
    tmp_path: Path, services: None
) -> None:
    fixture = _converted_series_fixture(tmp_path / "no-pass")
    fixture["world"].rows()
    landed = _land(fixture, "open leaf history")
    assert landed["ok"], landed.get("detail", landed)
    world = fixture["world"]
    assert _landed_history(world)["closed"] is True
    assert str(landed["summary"]).endswith(NO_PASS_SENTENCE), landed["summary"]
    assert "/briefs/" in git(world.memory, "show", "HEAD:.gitignore").splitlines()
    memory_tree = git(world.memory, "rev-parse", "HEAD^{tree}")
    manifest = world.memory / "briefs" / "TREE"
    assert manifest.read_text().strip() == memory_tree
    brief = (world.memory / "briefs" / f"{A}.md").read_text()
    assert f"<!-- memory tree {memory_tree} -->" in brief
    assert f"code commit `{str(landed['codeCommit'])[:12]}`" in brief

    manifest.unlink()
    replay = direct_landing_tool(
        fixture["config"], _converted_request(fixture, str(landed["codeCommit"]))
    )
    assert not replay["ok"] and replay["status"] == "direct-landing-input-conflict", replay
    assert head_commit(world.memory) == landed["memoryContentCommit"]
    assert not manifest.exists()


def test_converted_direct_landing_writes_the_review_fact_into_the_closed_history(
    tmp_path: Path, services: None
) -> None:
    fixture = _converted_series_fixture(tmp_path / "review-fact")
    fixture["world"].rows()
    tree = _record_pass(fixture["world"])
    landed = _land(fixture, "open leaf history")
    assert landed["ok"], landed.get("detail", landed)
    _assert_fact(_landed_history(fixture["world"]), tree)
    assert NO_PASS_SENTENCE not in str(landed["summary"])


def test_a_pass_of_another_memory_tree_gives_no_fact_and_says_so(
    tmp_path: Path, services: None
) -> None:
    """The leaf wrote its history after the passed review: the closing writes no fact."""

    fixture = _converted_series_fixture(tmp_path / "other-tree")
    world = fixture["world"]
    tree = _record_pass(world)
    world.rows()
    assert _checkout_tree(world.memory) != tree
    landed = _land(fixture, "open leaf history changed after its review")
    assert landed["ok"], landed.get("detail", landed)
    closed = _landed_history(world)
    assert closed["closed"] is True and "review" not in closed
    assert str(landed["summary"]).endswith(NO_PASS_SENTENCE), landed["summary"]


def test_a_leaf_without_history_lands_with_its_created_closed_history_and_fact(
    tmp_path: Path, services: None
) -> None:
    """No history file before the landing: the closing creates it, and that write holds the fact."""

    fixture = _converted_series_fixture(tmp_path / "created")
    world = fixture["world"]
    tree = _record_pass(world)
    assert not (world.memory / HISTORY).exists()
    landed = _land(fixture, "leaf wrote no history")
    assert landed["ok"], landed.get("detail", landed)
    created = _landed_history(world)
    assert created["rows"] == []
    _assert_fact(created, tree)
    assert NO_PASS_SENTENCE not in str(landed["summary"])


def _frozen(fixture: dict) -> bytes:
    """A closed history without a fact, published before this leaf's review passed."""

    world = fixture["world"]
    world.rows(closed=True)
    git(world.memory, "add", "-A")
    git(world.memory, "commit", "-m", f"closed leaf history\n\nCode-Commit: {world.code_base}")
    _record_pass(world)
    return (world.memory / HISTORY).read_bytes()


def test_a_frozen_history_stays_byte_identical_through_a_landing(
    tmp_path: Path, services: None
) -> None:
    fixture = _converted_series_fixture(tmp_path / "frozen")
    frozen = _frozen(fixture)
    landed = _land(fixture, "frozen leaf history")
    assert landed["ok"], landed.get("detail", landed)
    assert git(fixture["world"].memory, "show", f"HEAD:{HISTORY}").encode("utf-8") == frozen.strip()
    assert "review" not in _landed_history(fixture["world"])


def test_the_gate_refuses_a_fact_written_into_a_frozen_history(
    tmp_path: Path, services: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _converted_series_fixture(tmp_path / "frozen-written")
    frozen = _frozen(fixture)

    def reviewed_anyway(contract: Any, memory_root: Path, owner: str) -> Any:
        review = owner_review_fact(contract, owner, memory_root)
        assert review is not None
        path = memory_root / HISTORY

        def write_it() -> knowledge_gate.HistoryClosing:
            document = json.loads(path.read_text(encoding="utf-8"))
            path.write_text(canonical_text({**document, "review": review.to_document()}))
            return knowledge_gate.HistoryClosing(path, frozen, path.read_bytes())

        return write_it

    monkeypatch.setattr(direct_landing_owner, "reviewed_closing", reviewed_anyway)
    refused = _land(fixture, "frozen leaf history")
    assert not refused["ok"], refused
    assert "only its closing flag and review fact may change" in str(refused["detail"])
    assert (fixture["world"].memory / HISTORY).read_bytes() == frozen
    assert not (fixture["world"].memory / "briefs").exists()
