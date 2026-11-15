"""Public weakness closure authoring and historical code attribution."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import anyio
import pytest
from agents_remember.application.knowledge_reader.marks import open_weakness_counts_in
from agents_remember.application.knowledge_worklist.knowledge import KnowledgeSide
from agents_remember.application.knowledge_worklist.marks import mark_items
from agents_remember.kernel.git_command import run_git_bytes
from agents_remember.memory.knowledge_index import KnowledgeIndexCache, directory_snapshot
from agents_remember.memory_quality.knowledge_validator.parsed import parse_tree
from agents_remember.memory_quality.knowledge_validator.trees import KnowledgeTree
from knowledge_writer_test_support import World, canonical, commit_all, git, tree_bytes
from mcp.server.fastmcp import FastMCP
from test_mik_r118_r124_marks import (
    WRITER_LEAF,
    _registered_call,
    _registered_server,
    _weakness_record,
    _writer_world,
)

_REASON = "The repeated receipt scan is removed."
_PENDING = "The code commit comes with this task's closeout."
_UNAVAILABLE = "The closing code commit is unavailable: "


@pytest.fixture
def case(tmp_path: Path) -> tuple[World, FastMCP]:
    world = _writer_world(tmp_path)
    (world.task_root / "knowledge-worklist.json").write_text(
        json.dumps({"items": []}), encoding="utf-8"
    )
    server = _registered_server(world)
    assert _submit(world, server, [_weakness_record()])["state"] == "written"
    return world, server


def _call(server: FastMCP, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    result = anyio.run(_registered_call, server, name, arguments)
    assert not result.isError, result.content
    assert result.structuredContent is not None, result.content
    return result.structuredContent


def _submit(
    world: World, server: FastMCP, records: list[dict[str, Any]], *, apply: bool = True
) -> dict[str, Any]:
    return _call(
        server,
        "knowledge_write",
        {
            "contractPath": world.contract.as_posix(),
            "handoffName": "weakness-closure.json",
            "authorizationRef": "Exercise the public weakness closure writer.",
            "handoff": {"records": records, "history": []},
            "commit": apply,
        },
    )


def _stored(world: World) -> dict[str, Any]:
    return json.loads(next(world.memory.glob("knowledge/weaknesses/*.json")).read_text())


def _store(world: World, document: dict[str, Any]) -> None:
    path = next(world.memory.glob("knowledge/weaknesses/*.json"))
    path.write_text(canonical(document), encoding="utf-8")


def _update(world: World, **fields: Any) -> dict[str, Any]:
    return {"key": "receipts", "kind": "weakness", "id": _stored(world)["id"], "fields": fields}


def _closure(**extra: Any) -> dict[str, Any]:
    return {"task": WRITER_LEAF, "reason": _REASON, **extra}


def _snapshot(world: World) -> tuple[Any, ...]:
    return tuple(
        (tree_bytes(root), git(root, "rev-parse", "HEAD"), (root / ".git/index").read_bytes())
        for root in (world.code, world.memory)
    )


def _read_closures(world: World, server: FastMCP) -> list[dict[str, Any]]:
    before = _snapshot(world)
    arguments = {
        "memoryRoot": world.memory.as_posix(),
        "repositoryRoot": world.code.as_posix(),
        "codeTreeId": git(world.code, "rev-parse", "HEAD^{tree}"),
        "includeClosed": True,
    }
    weaknesses = _call(server, "knowledge_read", {**arguments, "view": "weakness"})
    record = _call(
        server,
        "knowledge_read",
        {**arguments, "view": "record", "recordId": _stored(world)["id"]},
    )
    assert weaknesses["state"] == record["state"] == "view", (weaknesses, record)
    assert len(weaknesses["rows"]) == 1, weaknesses
    assert "document" not in record["record"]
    assert _snapshot(world) == before
    return [weaknesses["rows"][0]["closed"], record["facet"]["closed"]]


def _assert_closed(world: World, server: FastMCP, expected: dict[str, Any]) -> None:
    assert _read_closures(world, server) == [expected, expected]


def _defer(world: World) -> None:
    document = _stored(world)
    document.update(state="closed", closed=_closure(), revision=document["revision"] + 1)
    _store(world, document)


def _paired_commit(world: World, message: str, code: str | None = None) -> str:
    return commit_all(
        world.memory, f"{message}\n\nCode-Commit: {code or git(world.code, 'rev-parse', 'HEAD')}"
    )


def _code_commit(world: World, message: str) -> str:
    (world.code / "change.txt").write_text(message, encoding="utf-8")
    return commit_all(world.code, message)


def _git_trailers(world: World, memory_commit: str) -> tuple[bytes, Path]:
    evidence = world.root / "trailer-evidence"
    evidence.mkdir()
    commit = run_git_bytes(world.memory, ["cat-file", "commit", memory_commit])
    (evidence / "commit.stdout").write_bytes(commit.stdout)
    (evidence / "commit.stderr").write_bytes(commit.stderr)
    assert commit.returncode == 0, commit.stderr
    message = evidence / "memory-commit-message.txt"
    message.write_bytes(commit.stdout.split(b"\n\n", 1)[1])
    parsed = run_git_bytes(world.memory, ["interpret-trailers", "--parse", message.as_posix()])
    commands = []
    for name, result in (("commit", commit), ("trailers", parsed)):
        (evidence / f"{name}.stdout").write_bytes(result.stdout)
        (evidence / f"{name}.stderr").write_bytes(result.stderr)
        commands.append({"command": result.args, "returncode": result.returncode, "output": name})
    (evidence / "commands.json").write_text(canonical({"commands": commands}), encoding="utf-8")
    assert parsed.returncode == 0, parsed.stderr
    return parsed.stdout, evidence


@pytest.mark.parametrize("attribution", ["body-only", "real-trailer"])
def test_public_reads_use_only_the_memory_commits_final_trailer_block(case, attribution):
    world, server = case
    _paired_commit(world, "record the open weakness")
    body_commit = git(world.code, "rev-parse", "HEAD")
    closing = _code_commit(world, "remove the repeated scan")
    written = _submit(world, server, [_update(world, state="closed", closed=_closure())])
    assert written["state"] == "written", written
    message = (
        "Publish the closed weakness\n\n"
        f"Code-Commit: {body_commit}\n\n"
        "The earlier line is explanatory prose, not closing attribution."
    )
    if attribution == "real-trailer":
        message += f"\n\nCode-Commit: {closing}"
    memory_commit = commit_all(world.memory, message)
    parsed, evidence = _git_trailers(world, memory_commit)
    closures = _read_closures(world, server)
    (evidence / "public-read-comparison.json").write_text(
        canonical({"memoryCommit": memory_commit, "closures": closures}), encoding="utf-8"
    )
    assert _stored(world)["closed"] == _closure()
    if attribution == "body-only":
        assert parsed == b""
        for closed in closures:
            assert "commit" not in closed, closed
            assert closed["commitNote"].startswith(_UNAVAILABLE), closed
            assert closed["commitNote"] != _PENDING
        assert closures[0] == closures[1]
    else:
        assert parsed == f"Code-Commit: {closing}\n".encode()
        assert closures == [_closure(commit=closing), _closure(commit=closing)]


def test_own_task_closure_plans_then_writes_without_guessing_a_commit(case):
    world, server = case
    before = _snapshot(world)
    original = _stored(world)
    records = [_update(world, state="closed", closed=_closure())]
    planned = _submit(world, server, records, apply=False)
    assert planned["state"] == "planned", planned
    assert _snapshot(world) == before
    written = _submit(world, server, records)
    assert written["state"] == "written", written
    document = _stored(world)
    assert document["closed"] == _closure()
    assert document["anchor"] == original["anchor"]
    assert document["seen"] == original["seen"]
    _assert_closed(world, server, _closure(commitNote=_PENDING))
    closing = _code_commit(world, "remove the repeated scan")
    _paired_commit(world, "publish the closure", closing)
    _assert_closed(world, server, _closure(commit=closing))
    assert _stored(world)["closed"] == _closure()


def test_another_tasks_new_closure_needs_its_commit_and_refuses_atomically(case):
    world, server = case
    before = _snapshot(world)
    answer = _submit(
        world,
        server,
        [_update(world, state="closed", closed=_closure(task="260928-MIK-L100"))],
    )
    assert answer["state"] == "refused", answer
    assert any("closed" in problem["where"] for problem in answer["problems"]), answer
    assert _snapshot(world) == before


@pytest.mark.parametrize("explicit", [False, True], ids=["deferred", "explicit"])
def test_stored_closure_forms_parse_and_leave_no_open_maintenance_item(case, explicit):
    world, server = case
    cache = KnowledgeIndexCache(world.root / "index-cache")
    with cache.for_directory(world.memory) as index:
        assert open_weakness_counts_in(index) == {"pkg/landing.py": 1}
    opened = KnowledgeSide.from_snapshot("open weakness", directory_snapshot(world.memory))
    assert [item["subject"] for item in mark_items(opened, {"pkg/landing.py"})] == [
        _stored(world)["id"]
    ]
    closed = _closure(**({"commit": git(world.code, "rev-parse", "HEAD")} if explicit else {}))
    answer = _submit(world, server, [_update(world, state="closed", closed=closed)])
    assert answer["state"] == "written", answer
    before = _snapshot(world)
    snapshot = directory_snapshot(world.memory)
    parsed = parse_tree(KnowledgeTree(label="writer-authored closure", files=snapshot.files))
    assert not parsed.problems, parsed.problems
    weakness = next(row.record for row in parsed.records if row.record.id == _stored(world)["id"])
    assert weakness.to_document()["closed"] == closed
    side = KnowledgeSide.from_snapshot("closed weakness", snapshot)
    assert mark_items(side, {"pkg/landing.py"}) == []
    with cache.for_directory(world.memory) as index:
        record = index.record(_stored(world)["id"]).value
        assert record is not None
        assert record.document["closed"] == closed
        assert open_weakness_counts_in(index) == {}
    assert _snapshot(world) == before


def test_another_tasks_real_explicit_commit_stays_exact_across_later_pairs(case):
    world, server = case
    explicit = _code_commit(world, "the earlier task removed the cause")
    closed = _closure(task="260928-MIK-L100", commit=explicit)
    answer = _submit(world, server, [_update(world, state="closed", closed=closed)])
    assert answer["state"] == "written", answer
    _paired_commit(world, "publish an explicitly attributed closure")
    later = _code_commit(world, "unrelated later code")
    (world.memory / "unrelated.md").write_text("Later memory.\n", encoding="utf-8")
    _paired_commit(world, "an unrelated later pair", later)
    assert _stored(world)["closed"] == closed
    _assert_closed(world, server, closed)


@pytest.mark.parametrize("kind", ["tree", "blob", "missing", "annotated-tag", "foreign"])
def test_an_explicit_closure_requires_a_code_commit_object_without_partial_writes(case, kind):
    world, server = case
    git(world.code, "tag", "-a", "closure-label", "-m", "A label is not a commit object.")
    values = {
        "tree": git(world.code, "rev-parse", "HEAD^{tree}"),
        "blob": git(world.code, "rev-parse", "HEAD:pkg/landing.py"),
        "missing": "f" * 40,
        "annotated-tag": git(world.code, "rev-parse", "closure-label"),
        "foreign": git(world.memory, "rev-parse", "HEAD"),
    }
    before = _snapshot(world)
    answer = _submit(
        world,
        server,
        [_update(world, state="closed", closed=_closure(commit=values[kind]))],
    )
    assert answer["state"] == "refused", answer
    assert any("closed.commit" in problem["where"] for problem in answer["problems"]), answer
    assert _snapshot(world) == before


def test_a_later_task_can_clarify_metadata_without_reauthoring_the_deferred_closure(case):
    world, server = case
    answer = _submit(world, server, [_update(world, state="closed", closed=_closure())])
    assert answer["state"] == "written", answer
    closing = _code_commit(world, "remove the scan")
    _paired_commit(world, "the original closure", closing)
    world.contract.write_text(
        world.contract.read_text().replace(WRITER_LEAF, "260928-MIK-L100"), encoding="utf-8"
    )
    answer = _submit(
        world,
        server,
        [_update(world, statement="The historical receipt scan used to read every file.")],
    )
    assert answer["state"] == "written", answer
    assert _stored(world)["closed"] == _closure()
    _paired_commit(world, "clarify the observation", _code_commit(world, "later code"))
    _assert_closed(world, server, _closure(commit=closing))


def test_slug_reanchor_and_revision_changes_preserve_the_original_closing_pair(case):
    world, server = case
    _paired_commit(world, "record the open weakness")
    _defer(world)
    closing = _code_commit(world, "remove the original cause")
    origin = _paired_commit(world, "the closure origin", closing)
    _assert_closed(world, server, _closure(commit=closing))
    update = _update(
        world,
        statement="The historical scan was attached to the landing helper.",
        anchor={"path": "pkg/landing.py", "locator": {"kind": "symbol", "value": "Helper.check"}},
    )
    update["slug"] = "historical-receipts"
    answer = _submit(world, server, [update])
    assert answer["state"] == "written", answer
    assert next(world.memory.glob("knowledge/weaknesses/*.json")).name.endswith(
        "-historical-receipts.json"
    )
    tip = _paired_commit(world, "rename and reanchor", _code_commit(world, "unrelated code"))
    _assert_closed(world, server, _closure(commit=closing))
    git(world.memory, "checkout", "--detach", origin)
    _assert_closed(world, server, _closure(commit=closing))
    git(world.memory, "checkout", "--detach", tip)
    _assert_closed(world, server, _closure(commit=closing))


def test_reopening_and_reclosing_the_same_block_selects_the_new_episode(case):
    world, server = case
    _paired_commit(world, "record the open weakness")
    _defer(world)
    first = _code_commit(world, "remove the first occurrence")
    _paired_commit(world, "the first closure", first)
    _assert_closed(world, server, _closure(commit=first))
    document = _stored(world)
    document.update(state="open", revision=document["revision"] + 1)
    document.pop("closed")
    _store(world, document)
    _paired_commit(world, "the cause recurred")
    _defer(world)
    _assert_closed(world, server, _closure(commitNote=_PENDING))
    second = _code_commit(world, "remove the recurrence")
    _paired_commit(world, "the second closure", second)
    assert first != second
    _assert_closed(world, server, _closure(commit=second))


def test_a_merge_follows_the_closure_imported_from_its_second_parent(case):
    world, server = case
    base = _paired_commit(world, "record the open weakness")
    git(world.memory, "checkout", "-b", "closing-side")
    _defer(world)
    closing = _code_commit(world, "remove the cause on the side branch")
    origin = _paired_commit(world, "the side closure", closing)
    git(world.memory, "checkout", "main")
    (world.memory / "unrelated.md").write_text("Mainline work.\n", encoding="utf-8")
    _paired_commit(world, "mainline memory", _code_commit(world, "later mainline code"))
    git(world.memory, "merge", "--no-ff", "closing-side", "-m", "Import the closed weakness")
    assert git(world.memory, "rev-parse", "HEAD^2") == origin
    assert git(world.memory, "merge-base", "HEAD^1", "HEAD^2") == base
    _assert_closed(world, server, _closure(commit=closing))


def test_independent_merge_closures_are_unavailable_instead_of_choosing_a_parent(case):
    world, server = case
    base = _paired_commit(world, "record the open weakness")
    git(world.memory, "branch", "other-closure", base)
    _defer(world)
    _paired_commit(world, "close on main", _code_commit(world, "main repair"))
    git(world.memory, "checkout", "other-closure")
    _defer(world)
    _paired_commit(world, "close independently", _code_commit(world, "other repair"))
    git(world.memory, "checkout", "main")
    git(world.memory, "merge", "--no-ff", "other-closure", "-m", "Combine independent closures")
    closures = _read_closures(world, server)
    for closed in closures:
        assert "commit" not in closed
        assert closed["commitNote"].startswith(_UNAVAILABLE)
        assert "no unique closure origin" in closed["commitNote"]
        assert closed["commitNote"] != _PENDING
    assert closures[0] == closures[1]


@pytest.mark.parametrize("trailer", ["absent", "tree", "foreign"])
def test_a_committed_closure_with_no_usable_pair_is_unavailable_not_pending(case, trailer):
    world, server = case
    _paired_commit(world, "record the open weakness")
    _defer(world)
    messages = {
        "absent": "A committed closure without a pair",
        "tree": f"A tree is not a commit\n\nCode-Commit: {git(world.code, 'rev-parse', 'HEAD^{tree}')}",
        "foreign": f"A foreign pair\n\nCode-Commit: {git(world.memory, 'rev-parse', 'HEAD')}",
    }
    commit_all(world.memory, messages[trailer])
    closures = _read_closures(world, server)
    for closed in closures:
        assert "commit" not in closed
        assert closed["commitNote"].startswith(_UNAVAILABLE)
        assert closed["commitNote"] != _PENDING
    assert closures[0] == closures[1]
