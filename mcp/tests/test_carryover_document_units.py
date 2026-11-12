"""Focused MIK-R56 document/record units; real Git trees and real derived indexes."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from contextlib import ExitStack
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from agents_remember.application.knowledge_currentness.observe import CodeTree, open_code_tree
from agents_remember.application.knowledge_worklist.code import CodeTrees
from agents_remember.memory.carryover_documents import (
    compare_document_pair,
    fresh_document_payloads,
    load_document_unit,
    observe_document_anchors,
    require_matching_layout,
    safe_memory_path,
)
from agents_remember.memory.carryover_records import (
    RecordContext,
    named_record_ids,
    plan_record_dependencies,
    record_payloads,
)
from agents_remember.memory.knowledge_index import KnowledgeIndex, build_index, directory_snapshot
from agents_remember.models.knowledge_files.canonical import canonical_text
from agents_remember_test_support.testing.waits import HANG_GUARD_SECONDS

MD = "onboarding/src.txt.md"
SIDECAR = "onboarding/src.txt.json"
INV = "INV-AAAAAA"
OLD = "INV-BBBBBB"
FAM = "FAM-CCCCCC"
DEC = "DEC-DDDDDD"


def _write(root: Path, path: str, value: Any) -> None:
    file = root / path
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(value if isinstance(value, str) else canonical_text(value), encoding="utf-8")


def _memory(root: Path) -> Path:
    _write(root, "knowledge/layout.json", {"schema": "ar-memory-layout/v2", "conversion": "1"})
    _git(root, "init", "-q")
    return root


def _git(root: Path, *args: str, data: str | None = None) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        input=data,
        text=True,
        capture_output=True,
        check=True,
        env={**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"},
    ).stdout.strip()


def _tree(root: Path, text: str) -> str:
    root.mkdir(exist_ok=True)
    _git(root, "init", "-q")
    blob = _git(root, "hash-object", "-w", "--stdin", data=text)
    return _git(root, "mktree", data=f"100644 blob {blob}\tsrc.txt\n")


def _document(code: Path, tree: str) -> dict[str, Any]:
    trees = CodeTrees.open(code, tree, tree)
    blob = trees.base()["src.txt"]
    locator = {"kind": "line_range", "start": 2, "end": 2}
    resolved = trees.resolve("src.txt", locator, blob, blob)
    assert resolved is not None
    anchor = {"locator": locator, "blob": blob, "content": resolved.content}
    value = json.loads("""{
        "schema": "ar-onboarding-file/v1", "path": "src.txt",
        "realizes": [{"id": "RLZ-AAAAAA", "invariant": "INV-AAAAAA",
                      "role": "enforcement", "rationale": "Keeps the rule."}],
        "proves": [{"id": "PRF-BBBBBB", "invariant": "INV-AAAAAA", "facet": "The rule holds."}],
        "references": {"1": {"targets": [{"kind": "code"}, {"kind": "test"}]}}
    }""")
    for field in ("realizes", "proves"):
        value[field][0]["anchor"] = deepcopy(anchor)
    for target in value["references"]["1"]["targets"]:
        target["anchor"] = deepcopy(anchor)
    return value


def _index(root: Path, destination: Path, stack: ExitStack) -> KnowledgeIndex:
    destination.parent.mkdir(parents=True, exist_ok=True)
    snapshot = directory_snapshot(root)
    build_index(snapshot, destination)
    return stack.enter_context(KnowledgeIndex(destination, expected_key=snapshot.key))


def _context(source: Path, target: Path, tmp: Path, stack: ExitStack) -> RecordContext:
    return RecordContext(
        source,
        target,
        _index(source, tmp / "source.sqlite", stack),
        _index(target, tmp / "target.sqlite", stack),
    )


def _records(root: Path) -> dict[str, Any]:
    common = dict(
        revision=1, status="accepted", origin={"task": "old"}, admission="legacy-unassessed"
    )
    for identity, supersedes in ((INV, [OLD]), (OLD, [])):
        value = dict(
            schema="ar-invariant/v1",
            id=identity,
            statement="Rule holds.",
            applicability="Always.",
            conditions=[],
            exclusions=[],
            supersedes=supersedes,
        )
        _write(root, f"knowledge/invariants/{identity}-rule.json", common | value)
        _write(root, f"knowledge/invariants/{identity}-rule.md", "# Rule\n")
    family = dict(
        schema="ar-family/v1",
        id=FAM,
        title="Together",
        guarantee="Together they hold.",
        members=[INV],
        routes=["."],
    )
    _write(root, f"knowledge/families/{FAM}-group.json", common | family)
    decision = json.loads("""{
        "schema": "ar-decision/v1", "id": "DEC-DDDDDD", "status": "active", "context": "Choose.",
        "alternatives": [{"option": "Do it", "status": "chosen", "reason": "Works."}],
        "consequences": [], "decider": "curator", "supersedes": [],
        "links": [{"target": "FAM-CCCCCC", "relation": "explains"},
                  {"target": "route:pkg", "relation": "constrains"}]
    }""")
    _write(root, f"knowledge/decisions/{DEC}-choice.json", common | decision)
    history = json.loads("""{
        "schema": "ar-history/v1", "leaf": "old", "closed": true,
        "rows": [{"id": "ROW-AAAAAA", "subject": "INV-AAAAAA", "revision": 1,
                  "disposition": "no_impact", "reason": "Already judged.", "items": [], "covers": []}]
    }""")
    _write(root, "knowledge/history/old.json", history)
    targets = [
        dict(kind="family", id=FAM),
        dict(kind="decision", id=DEC),
        dict(kind="external", document={"document": "manual"}),
        dict(kind="unresolved", text="old citation"),
    ]
    return dict(schema="ar-onboarding-route/v1", path=".", references={"1": {"targets": targets}})


def test_all_document_kinds_compare_paired_bytes(tmp_path: Path) -> None:
    source, target = _memory(tmp_path / "source"), _memory(tmp_path / "target")
    file = {"schema": "ar-onboarding-file/v1", "path": "src.txt", "references": {}, "realizes": []}
    route = {"schema": "ar-onboarding-route/v1", "path": ".", "references": {}}
    for markdown, document in (
        (MD, file),
        ("onboarding/overview.md", route),
        ("onboarding/notes.md", {**file, "path": "notes"}),
        ("entities.md", route),
    ):
        sidecar = markdown[:-3] + ".json"
        for root in (source, target):
            _write(root, markdown, "# Same\n")
            _write(root, sidecar, document)
        assert compare_document_pair(source, target, markdown).identical
        assert load_document_unit(source, markdown).paths == (markdown, sidecar)
        (target / sidecar).write_text(canonical_text(document) + "\n", encoding="utf-8")
        assert compare_document_pair(source, target, markdown).differing_paths == (sidecar,)
        (source / sidecar).unlink()
        assert compare_document_pair(source, target, markdown).source_missing == (sidecar,)
        with pytest.raises(FileNotFoundError):
            load_document_unit(source, markdown)


def test_moved_anchors_all_reanchor_without_writing(tmp_path: Path) -> None:
    code, memory = tmp_path / "code", _memory(tmp_path / "memory")
    base = _tree(code, "first\nkept\nlast\n")
    document = _document(code, base)
    _write(memory, MD, "# Evidence [1]\n")
    _write(memory, SIDECAR, document)
    before = (memory / SIDECAR).read_bytes()
    official = open_code_tree(CodeTree(code, _tree(code, "inserted\nfirst\nkept\nlast\n")))
    observation = observe_document_anchors(document, SIDECAR, official)
    assert observation["all_current"] and observation["anchor_count"] == 4
    assert all(item["needsReanchor"] for item in observation["observations"])
    payloads = fresh_document_payloads(memory, MD, official)
    assert set(payloads) == {MD, SIDECAR}
    fresh = json.loads(payloads[SIDECAR])
    anchors = [fresh[key][0]["anchor"] for key in ("realizes", "proves")]
    anchors += [target["anchor"] for target in fresh["references"]["1"]["targets"]]
    assert all(anchor["locator"]["start"] == 3 for anchor in anchors)
    assert (memory / SIDECAR).read_bytes() == before and document == json.loads(before)
    assert observe_document_anchors(fresh, SIDECAR, official)["all_current"]


def test_stale_unreadable_and_anchorless_need_review(tmp_path: Path) -> None:
    code, memory = tmp_path / "code", _memory(tmp_path / "memory")
    document = _document(code, _tree(code, "first\nkept\nlast\n"))
    _write(memory, MD, "# Evidence\n")
    _write(memory, SIDECAR, document)
    official = open_code_tree(CodeTree(code, _tree(code, "first\nchanged\nlast\n")))
    observation = observe_document_anchors(document, SIDECAR, official)
    assert not observation["all_current"] and len(observation["problems"]) == 4
    assert any("RLZ-AAAAAA" in item for item in observation["problems"])
    assert any("reference[1].target[1]" in item for item in observation["problems"])
    with pytest.raises(ValueError, match="stale"):
        fresh_document_payloads(memory, MD, official)
    reviewed = fresh_document_payloads(memory, MD, official, reviewed=True)
    assert reviewed[SIDECAR] == (memory / SIDECAR).read_bytes()
    unreadable = open_code_tree(CodeTree(code, "0" * 40))
    observed = observe_document_anchors(document, SIDECAR, unreadable)
    assert all(
        item["readFailed"] and item["state"] == "unverifiable" for item in observed["observations"]
    )
    empty = {**document, "realizes": [], "proves": [], "references": {}}
    assert not observe_document_anchors(empty, SIDECAR, official)["all_current"]


def test_layout_and_paths_refuse_crossing_and_symlink_escape(tmp_path: Path) -> None:
    source, target = _memory(tmp_path / "source"), _memory(tmp_path / "target")
    assert require_matching_layout(source, target) == "1"
    _write(target, "knowledge/layout.json", {"schema": "ar-memory-layout/v2", "conversion": "2"})
    with pytest.raises(ValueError, match=r"knowledge-convert.*crossing sync"):
        require_matching_layout(source, target)
    (target / "knowledge/layout.json").unlink()
    with pytest.raises(ValueError, match="knowledge-convert"):
        require_matching_layout(source, target)
    for path in ("../outside", "/absolute", "a/../b", "a//b"):
        with pytest.raises(ValueError):
            safe_memory_path(source, path)
    (source / "onboarding").symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        compare_document_pair(source, target, MD)


@pytest.mark.parametrize("value", [None, [], "text", 1, True])
@pytest.mark.parametrize("side", ["source", "target"])
def test_nonobject_layout_roots_refuse_conversion_before_further_reads(tmp_path, value, side):
    source, target = _memory(tmp_path / "source"), _memory(tmp_path / "target")
    bad = source if side == "source" else target
    (bad / "knowledge/layout.json").write_text(json.dumps(value))
    with pytest.raises(ValueError, match=r"knowledge-convert.*crossing sync"):
        require_matching_layout(source, target)


@pytest.mark.parametrize("side", ["source", "target"])
def test_fifo_layout_refuses_without_waiting_for_a_writer(tmp_path, side):
    source, target = _memory(tmp_path / "source"), _memory(tmp_path / "target")
    bad = (source if side == "source" else target) / "knowledge/layout.json"
    bad.unlink()
    os.mkfifo(bad)
    script = (
        "import sys; from pathlib import Path; "
        "from agents_remember.memory.carryover_documents import require_matching_layout; "
        "\ntry: require_matching_layout(Path(sys.argv[1]), Path(sys.argv[2]))"
        "\nexcept ValueError as e: assert 'knowledge-convert' in str(e); print(str(e))"
        "\nelse: raise AssertionError('FIFO marker accepted')"
    )
    outcome = subprocess.run(
        [sys.executable, "-c", script, str(source), str(target)],
        capture_output=True,
        text=True,
        timeout=HANG_GUARD_SECONDS,
        check=False,
    )
    assert outcome.returncode == 0, outcome.stderr
    assert "regular" in outcome.stdout


@pytest.mark.parametrize(
    "member", [MD, SIDECAR, f"knowledge/invariants/{INV}.json", f"knowledge/invariants/{INV}.md"]
)
def test_selected_fifo_member_is_refused_by_path_admission(tmp_path, member):
    root = _memory(tmp_path / "memory")
    path = root / member
    path.parent.mkdir(parents=True, exist_ok=True)
    os.mkfifo(path)
    with pytest.raises(ValueError, match="regular"):
        safe_memory_path(root, member)


def test_transitive_records_and_read_only_history(tmp_path: Path) -> None:
    source, target = _memory(tmp_path / "source"), _memory(tmp_path / "target")
    document = _records(source)
    with ExitStack() as stack:
        context = _context(source, target, tmp_path, stack)
        plan = plan_record_dependencies(context, document)
        assert not plan.conflicts and {item.id for item in plan.needed} == {INV, OLD, FAM, DEC}
        assert len(plan.history) == 1 and plan.history[0]["reading_aid_only"]
        assert plan.history[0]["subject"] == INV
        json.dumps(plan.to_document())
        payloads = record_payloads(context, plan)
        assert len(payloads) == 6 and all("history" not in path for path in payloads)
        assert not list(target.glob("knowledge/invariants/*"))
        for path, value in payloads.items():
            _write(target, path, value.decode())
        assert record_payloads(context, plan) == {}
        path = next(iter(payloads))
        (target / path).write_text("edited", encoding="utf-8")
        with pytest.raises(ValueError, match="never overwrite"):
            record_payloads(context, plan)


def test_record_identity_and_markdown_conflicts(tmp_path: Path) -> None:
    source, target = _memory(tmp_path / "source"), _memory(tmp_path / "target")
    document = _records(source)
    _records(target)
    old_path = f"knowledge/invariants/{INV}-rule.json"
    moved_path = f"knowledge/invariants/{INV}-renamed.json"
    (target / old_path).rename(target / moved_path)
    (target / old_path.replace(".json", ".md")).rename(target / moved_path.replace(".json", ".md"))
    _write(target, f"knowledge/invariants/{OLD}-rule.md", "# Different\n")
    with ExitStack() as stack:
        context = _context(source, target, tmp_path, stack)
        plan = plan_record_dependencies(context, document)
        assert [item.id for item in plan.conflicts] == [OLD]
        assert plan.conflicts[0].state == "record-differs"
        assert not plan.needed
        with pytest.raises(ValueError, match="record-differs"):
            record_payloads(context, plan)
        assert (target / moved_path).exists()


def test_missing_partial_and_changed_records_need_review(tmp_path: Path) -> None:
    source, target = _memory(tmp_path / "source"), _memory(tmp_path / "target")
    document = _records(source)
    (source / f"knowledge/invariants/{OLD}-rule.json").unlink()
    with ExitStack() as stack:
        context = _context(source, target, tmp_path, stack)
        plan = plan_record_dependencies(context, document)
        assert any(item.state == "source-record-missing" for item in plan.conflicts)
        _write(source, f"knowledge/invariants/{INV}-rule.json", "{}")
        changed = plan_record_dependencies(context, document)
        assert any(item.state == "record-unreadable" for item in changed.conflicts)
    with ExitStack() as stack:
        partial = _context(source, target, tmp_path / "partial", stack)
        assert plan_record_dependencies(partial, document).conflicts[0].state == "index-incomplete"
    assert set(named_record_ids(document)) == {FAM, DEC}
