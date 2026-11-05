"""R62 rule25: the producer check reads named trees without writing or judging curation."""

from __future__ import annotations

import hashlib
import json
from typing import Any
from unittest.mock import patch

import pytest
from agents_remember.cli.__main__ import main
from knowledge_writer_test_support import (
    BASE_INVARIANT,
    CODE_FILE,
    TEST_FILE,
    build_world,
    commit_all,
    entry,
    git,
    target,
    write,
)


def _invoke(world, document, capsys, *, memory=False, tree="HEAD"):
    listed = world.root / "producer-list.json"
    listed.write_text(json.dumps(document))
    args = ["handoff-check", str(listed), "--code", str(world.code), "--tree", tree]
    if memory:
        args += ["--memory", str(world.memory)]
    try:
        status = main(args)
    except SystemExit as error:
        status = error.code
    captured = capsys.readouterr()
    return status, captured.out + captured.err


def _producer():
    return [entry("P-1", target=[target("land_pair")])]


def test_clean_producer_does_not_need_curator_admission(tmp_path, capsys) -> None:
    world = build_world(tmp_path)
    status, text = _invoke(world, _producer(), capsys)
    assert status == 0, text
    assert "fired link: checked by the writer" in text
    assert "record links: not run" in text


@pytest.mark.parametrize("defect", ["rationale", "symbol", "target-shape", "last-entry"])
def test_each_producer_shape_and_target_problem_is_named(tmp_path, capsys, defect) -> None:
    world = build_world(tmp_path)
    items = _producer()
    if defect == "rationale":
        items[0]["target"][0]["rationale"] = ""
    elif defect == "symbol":
        items[0]["target"][0]["locator"]["value"] = "absent_symbol"
    elif defect == "target-shape":
        items[0]["target"] = ["not a target"]
    else:
        items.extend(entry(f"P-{n}", target=[target("land_pair")]) for n in range(2, 15))
        items[-1]["target"][0]["path"] = "missing.py"
    status, text = _invoke(world, items, capsys)
    assert status == 1, text
    assert ("P-14" if defect == "last-entry" else "P-1") in text


@pytest.mark.parametrize("spelling", ["pytest", "mapping", "bare"])
def test_writer_proof_spellings_and_bare_test_file(tmp_path, capsys, spelling) -> None:
    world = build_world(tmp_path)
    proof = f"{TEST_FILE}::LandingTests::test_pair_lands_together"
    if spelling == "mapping":
        proof = {"path": TEST_FILE, "symbol": "LandingTests.test_pair_lands_together"}
    elif spelling == "bare":
        proof = TEST_FILE
    items = _producer()
    items[0]["proofs"] = [{"test": proof, "facet": "The pair stays together."}]
    status, text = _invoke(world, items, capsys)
    assert status == (1 if spelling == "bare" else 0), text
    if spelling == "bare":
        assert "test reference" in text


def test_range_proof_uses_writer_one_case_resolution(tmp_path, capsys) -> None:
    world = build_world(tmp_path)
    path = "pkg/cases.test.tsx"
    write(
        world.code,
        {
            path: "import {it} from 'vitest';\nit.each([1, 2])('case %s', n => {\n  expect(n).toBeDefined();\n});\n"
        },
    )
    commit_all(world.code, "test case")
    items = _producer()
    items[0]["proofs"] = [
        {
            "test": {"path": path, "locator": {"kind": "line_range", "start": 2, "end": 4}},
            "facet": "Each case runs.",
        }
    ]
    status, text = _invoke(world, items, capsys)
    assert status == 0, text
    items[0]["proofs"][0]["test"]["locator"]["start"] = 3
    status, text = _invoke(world, items, capsys)
    assert status == 1, text
    assert "case" in text


def test_repoint_checks_shape_and_new_target_but_not_fired_link_judgment(tmp_path, capsys) -> None:
    world = build_world(tmp_path)
    row = {
        "subject": "DEC-NOTSTORED",
        "disposition": "still_rejected",
        "reason": "Only the new target is checked here.",
        "repoint": [
            {"link": 0, "path": CODE_FILE, "locator": {"kind": "symbol", "value": "land_pair"}}
        ],
    }
    document = {"entries": _producer(), "history": [row]}
    status, text = _invoke(world, document, capsys)
    assert status == 0, text
    assert "fired link: checked by the writer" in text
    row["repoint"][0]["path"] = "gone.py"
    status, text = _invoke(world, document, capsys)
    assert status == 1, text
    assert "repoint" in text


def test_record_links_need_memory_and_an_unretired_record(tmp_path, capsys) -> None:
    world = build_world(tmp_path)
    record = {
        "key": "doc",
        "kind": "dependent",
        "slug": "landing",
        "fields": {"document": CODE_FILE, "stands_on": [{"kind": "record", "id": BASE_INVARIANT}]},
    }
    document = {"entries": _producer(), "records": [record]}
    status, text = _invoke(world, document, capsys)
    assert status == 1, text
    assert "record links: not run" in text
    status, text = _invoke(world, document, capsys, memory=True)
    assert status == 0, text
    stored = next((world.memory / "knowledge/invariants").glob("*.json"))
    raw = json.loads(stored.read_text())
    raw["status"] = "retired"
    stored.write_text(json.dumps(raw))
    status, text = _invoke(world, document, capsys, memory=True)
    assert status == 1, text
    assert "retired" in text
    record["fields"]["stands_on"][0]["id"] = "INV-MISSING"
    status, text = _invoke(world, document, capsys, memory=True)
    assert status == 1, text
    assert "INV-MISSING" in text


@pytest.mark.parametrize("field", ["weakness", "document", "file-link", "anchor-link", "cover"])
def test_every_candidate_bearing_field_is_checked(tmp_path, capsys, field) -> None:
    world = build_world(tmp_path)
    anchor: dict[str, Any] = {
        "path": CODE_FILE,
        "locator": {"kind": "symbol", "value": "land_pair"},
    }
    changed: dict[str, Any]
    if field == "cover":
        cover = {"id": "RLZ-BASE01", **anchor}
        document = {
            "entries": _producer(),
            "history": [
                {
                    "subject": BASE_INVARIANT,
                    "disposition": "moved",
                    "reason": "The place is named.",
                    "covers": [cover],
                }
            ],
        }
        changed = cover
    else:
        fields: dict[str, Any] = (
            {"anchor": anchor} if field == "weakness" else {"document": CODE_FILE, "stands_on": []}
        )
        if field == "file-link":
            fields["stands_on"] = [{"kind": "file", "path": CODE_FILE}]
            changed = fields["stands_on"][0]
        elif field == "anchor-link":
            fields["stands_on"] = [{"kind": "anchor", "anchor": anchor}]
            changed = anchor
        else:
            changed = anchor if field == "weakness" else fields
        document = {
            "entries": _producer(),
            "records": [
                {
                    "key": "R-1",
                    "kind": "weakness" if field == "weakness" else "dependent",
                    "slug": "place",
                    "fields": fields,
                }
            ],
        }
    status, text = _invoke(world, document, capsys)
    assert status == 0, text
    changed["document" if field == "document" else "path"] = "missing.py"
    status, text = _invoke(world, document, capsys)
    assert status == 1, text
    assert "missing.py" in text


def test_checks_use_the_named_tree_not_the_working_files(tmp_path, capsys) -> None:
    world = build_world(tmp_path)
    old = git(world.code, "rev-parse", "HEAD")
    write(world.code, {CODE_FILE: "def replacement():\n    return True\n"})
    commit_all(world.code, "replace function")
    status, text = _invoke(world, _producer(), capsys, tree=old)
    assert status == 0, text
    status, text = _invoke(world, _producer(), capsys)
    assert status == 1, text


def test_command_writes_no_memory_runtime_or_git_bytes(tmp_path, capsys) -> None:
    world = build_world(tmp_path)
    listed = world.root / "producer-list.json"
    listed.write_text(json.dumps(_producer()))
    runtime = tmp_path / "coordination-runtime"
    runtime.mkdir()
    (runtime / "sentinel").write_bytes(b"unchanged")

    def snapshot():
        return {
            str(p.relative_to(tmp_path)): hashlib.sha256(p.read_bytes()).hexdigest()
            for root in (world.code, world.memory, world.task_root, runtime)
            for p in root.rglob("*")
            if p.is_file()
        }

    before = snapshot()
    with (
        patch(
            "agents_remember.application.knowledge_writer.code_anchors.CodeSnapshot.capture",
            side_effect=AssertionError("capture writes tree objects"),
        ),
        patch(
            "agents_remember.application.knowledge_writer.memory_state.MemoryState.load",
            side_effect=AssertionError("writer base cache forbidden"),
        ),
        patch(
            "agents_remember.application.knowledge_writer.writer.write_knowledge",
            side_effect=AssertionError("writer forbidden"),
        ),
    ):
        status, text = _invoke(world, _producer(), capsys, memory=True)
    assert status == 0, text
    assert snapshot() == before


def test_invocation_error_returns_two(tmp_path, capsys) -> None:
    world = build_world(tmp_path)
    status, text = _invoke(world, _producer(), capsys, tree="no-such-tree")
    assert status == 2, text
