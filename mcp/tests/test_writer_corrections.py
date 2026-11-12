"""MIK-R54 writer corrections, exercised against real paired Git trees."""

from __future__ import annotations

from pathlib import Path

import pytest
from agents_remember.application.knowledge_writer import WriteRequest, write_knowledge
from knowledge_writer_test_support import (
    ADMISSION,
    CODE_FILE,
    SCOPE,
    build_world,
    entry,
    read_json,
    target,
    tree_bytes,
    write,
)
from test_knowledge_writer import _conforming
from writer_corrections_test_support import OWNER, run


@pytest.mark.parametrize("reverse", [False, True])
def test_same_document_attachment_orders_author_first(tmp_path: Path, reverse: bool) -> None:
    world = build_world(tmp_path)
    author = entry("A1", scope=SCOPE, admission=ADMISSION)
    attach = entry(
        "A2", invariant_id="handoff:A1", statement=author["statement"], target=[target("land_pair")]
    )
    report = run(world, [attach, author] if reverse else [author, attach])
    assert report.state == "written", report.render()
    assert len({record.id for record in report.records}) == 1
    sidecar = read_json(world.memory, f"onboarding/{CODE_FILE}.json")
    assert any(one.get("origin", {}).get("handoffEntry") == "A2" for one in sidecar["realizes"])


def test_same_document_attachment_refuses_statement_mismatch(tmp_path: Path) -> None:
    world = build_world(tmp_path)
    before = tree_bytes(world.memory)
    report = run(
        world,
        [
            entry("A1", scope=SCOPE, admission=ADMISSION),
            entry("A2", invariant_id="handoff:A1", statement="A different guarantee."),
        ],
    )
    assert report.refused
    assert "statement must equal" in report.render()
    assert tree_bytes(world.memory) == before


@pytest.mark.parametrize("replacement", [["new note"], []])
def test_same_owner_evidence_can_be_replaced(tmp_path: Path, replacement: list[str]) -> None:
    world = build_world(tmp_path)
    author = entry("A1", scope=SCOPE, admission=ADMISSION, evidence=["old note"])
    assert run(world, [author]).state == "written"
    report = run(world, [{**author, "replace_evidence": True, "evidence": replacement}])
    assert report.state == "written", report.render()
    invariant = read_json(world.memory, report.records[0].path)
    assert invariant["origin"]["handoff"].get("evidence", []) == replacement


def test_leaf_without_worklist_is_told_to_compute_it(tmp_path: Path) -> None:
    world = build_world(tmp_path)
    report = write_knowledge(
        WriteRequest(world.memory, world.code, OWNER, "handoff.json", [], commit=True)
    )
    assert report.refused
    assert "memory_quality_check" in report.render()


@pytest.mark.parametrize("alternative", [False, True])
def test_authored_superseded_uses_derived_rule_text(tmp_path: Path, alternative: bool) -> None:
    world = build_world(tmp_path)
    document = _conforming()
    fields = document["records"][0]["fields"]
    if alternative:
        fields["alternatives"][1]["status"] = "superseded"
    else:
        fields["status"] = "superseded"
    report = run(world, document)
    assert report.refused
    assert "R13.2-superseded-derived" in report.render()
    assert "'superseded' is never stored" in report.render()
    assert "Input should be" not in report.render()


def test_quoted_typescript_case_evidence_and_proof_keep_title(tmp_path: Path) -> None:
    world = build_world(tmp_path)
    path = "tests/review.test.tsx"
    title = "review fixes > cards cover loaded members. (F2)"
    write(world.memory, {f"onboarding/{path}.md": "# Review test\n"})
    write(
        world.code,
        {
            path: "describe('review fixes', () => { it('cards cover loaded members. (F2)', () => { expect(true).toBe(true); }); });\n"
        },
    )
    report = run(
        world,
        [
            entry(
                "A1",
                scope=SCOPE,
                admission=ADMISSION,
                evidence=f'{path}::"{title}"',
                proofs=[{"test": f"{path}::{title}", "facet": "Bounded cards."}],
            )
        ],
    )
    assert report.state == "written", report.render()
    (proof,) = read_json(world.memory, f"onboarding/{path}.json")["proves"]
    assert proof["anchor"]["locator"] == {"kind": "symbol", "name": title}
    assert report.evidence[0].tests[0].state == "proof_written"
    assert report.evidence[0].tests[0].test == f"{path}::{title}"


@pytest.mark.parametrize("title", ["say 😀", "suite 😀 > say 😀"])
def test_utf16_pair_titles_write_bare_and_qualified_proofs(tmp_path: Path, title: str) -> None:
    world = build_world(tmp_path)
    path = "tests/pairs.test.ts"
    write(world.memory, {f"onboarding/{path}.md": "# Pair test\n"})
    write(
        world.code,
        {
            path: r'describe("suite \uD83D\uDE00", () => { it("say \uD83D\uDE00", () => {}); });'
            + "\n"
        },
    )
    author = entry(
        "A1",
        scope=SCOPE,
        admission=ADMISSION,
        evidence=f'{path}::"{title}"',
        proofs=[{"test": f"{path}::{title}", "facet": "Bounded cards."}],
    )
    report = run(world, [author])
    assert report.state == "written", report.render()
    (proof,) = read_json(world.memory, f"onboarding/{path}.json")["proves"]
    assert proof["anchor"]["locator"] == {"kind": "symbol", "name": title}
    assert report.evidence[0].tests[0].state == "proof_written"


def test_equivalent_unicode_titles_refuse_ambiguous_writer_proof(tmp_path: Path) -> None:
    world = build_world(tmp_path)
    path = "tests/pairs.test.ts"
    write(world.memory, {f"onboarding/{path}.md": "# Pair test\n"})
    write(
        world.code,
        {path: r'it("say \uD83D\uDE00", () => {}); test("say \u{1F600}", () => {});' + "\n"},
    )
    before = tree_bytes(world.memory)
    report = run(
        world,
        [
            entry(
                "A1",
                scope=SCOPE,
                admission=ADMISSION,
                proofs=[{"test": f"{path}::say 😀", "facet": "Bounded cards."}],
            )
        ],
    )
    assert report.refused and "names one construct" in report.render()
    assert tree_bytes(world.memory) == before
