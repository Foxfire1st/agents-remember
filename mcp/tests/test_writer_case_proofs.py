"""MIK-R194 rules 1 to 3: a proof that names a test case by its lines, against real Git trees."""

from __future__ import annotations

import hashlib
from pathlib import Path

from knowledge_writer_test_support import (
    ADMISSION,
    SCOPE,
    build_world,
    entry,
    read_json,
    tree_bytes,
    write,
)
from writer_corrections_test_support import run

CASES = "pkg/cases.test.tsx"
CASES_SOURCE = """import { expect, it } from 'vitest';

describe('review surface', () => {
  it.each(['failed', 'refused'] as const)('a %s selection marks the slot unavailable', (s) => {
    expect(s).toBeDefined();
  });
  it(`a ${s} computed case`, () => {});
  it.skip('a skipped case', () => {});
});
"""
RANGE = {"kind": "line_range", "start": 4, "end": 6}


def _world_with_cases(tmp_path: Path):
    world = build_world(tmp_path)
    write(world.code, {CASES: CASES_SOURCE})
    write(world.memory, {f"onboarding/{CASES}.md": "# Review cases\n"})
    return world


def _entry(proofs, evidence: str | None = None):
    return entry(
        "A1",
        scope=SCOPE,
        admission=ADMISSION,
        evidence=evidence,
        proofs=proofs,
    )


def test_a_proof_by_lines_writes_a_line_range_anchor_and_pairs_the_evidence(
    tmp_path: Path,
) -> None:
    world = _world_with_cases(tmp_path)
    title = "a %s selection marks the slot unavailable"
    report = run(
        world,
        [
            _entry(
                [{"test": {"path": CASES, "locator": RANGE}, "facet": "The slot is unavailable."}],
                evidence=f'{CASES}::"{title}"',
            )
        ],
    )
    assert report.state == "written", report.render()
    (proof,) = read_json(world.memory, f"onboarding/{CASES}.json")["proves"]
    assert proof["anchor"]["locator"] == RANGE
    body = "".join(CASES_SOURCE.splitlines(keepends=True)[3:6]).encode("utf-8")
    assert proof["anchor"]["content"] == f"sha256:{hashlib.sha256(body).hexdigest()}"
    # The evidence names the case the proof proves, so it is reported written, not unresolvable.
    assert [one.state for one in report.evidence[0].tests] == ["proof_written"]
    assert report.evidence[0].tests[0].test == f"{CASES}::{title}"


def test_a_range_that_is_no_case_extent_refuses_the_run_and_names_the_case(
    tmp_path: Path,
) -> None:
    world = _world_with_cases(tmp_path)
    before = tree_bytes(world.memory)
    report = run(
        world,
        [
            _entry(
                [
                    {
                        "test": {
                            "path": CASES,
                            "locator": {"kind": "line_range", "start": 5, "end": 5},
                        },
                        "facet": "The slot is unavailable.",
                    }
                ]
            )
        ],
    )
    text = report.render()
    assert report.refused, text
    assert "lines 5-5 of pkg/cases.test.tsx are not a case a line range proves" in text
    assert "the cases overlapping them span 4-6" in text
    assert tree_bytes(world.memory) == before


def test_a_case_that_does_not_run_cannot_be_proved_by_its_lines(tmp_path: Path) -> None:
    world = _world_with_cases(tmp_path)
    before = tree_bytes(world.memory)
    report = run(
        world,
        [
            _entry(
                [
                    {
                        "test": {
                            "path": CASES,
                            "locator": {"kind": "line_range", "start": 8, "end": 8},
                        },
                        "facet": "The skipped case proves it.",
                    }
                ]
            )
        ],
    )
    assert report.refused, report.render()
    assert "the case uses .skip, which does not run" in report.render()
    assert tree_bytes(world.memory) == before


def test_a_computed_title_evidence_stays_unresolvable_and_the_proof_is_written(
    tmp_path: Path,
) -> None:
    world = _world_with_cases(tmp_path)
    expanded = "a failed computed case"
    report = run(
        world,
        [
            _entry(
                [
                    {
                        "test": {
                            "path": CASES,
                            "locator": {"kind": "line_range", "start": 7, "end": 7},
                        },
                        "facet": "A computed title still proves it.",
                    }
                ],
                evidence=f'{CASES}::"{expanded}"',
            )
        ],
    )
    assert report.state == "written", report.render()
    (proof,) = read_json(world.memory, f"onboarding/{CASES}.json")["proves"]
    assert proof["anchor"]["locator"] == {"kind": "line_range", "start": 7, "end": 7}
    # An expanded title does not pair: the evidence stays unresolvable, and the proof is listed.
    assert [one.state for one in report.evidence[0].tests] == ["unresolvable"]
    assert [one.kind for one in report.entries] == ["proof"]


def test_a_proof_by_lines_in_a_non_test_file_is_refused(tmp_path: Path) -> None:
    world = _world_with_cases(tmp_path)
    write(world.code, {"pkg/module.ts": "export const value = 1;\n"})
    report = run(
        world,
        [
            _entry(
                [
                    {
                        "test": {
                            "path": "pkg/module.ts",
                            "locator": {"kind": "line_range", "start": 1, "end": 1},
                        },
                        "facet": "No case here.",
                    }
                ]
            )
        ],
    )
    assert report.refused, report.render()
    assert "the file is not a JavaScript or TypeScript .test. or .spec. file" in report.render()


def test_a_name_taken_by_an_ordinary_declaration_leaves_the_range_open(tmp_path: Path) -> None:
    """L186-R1-F05: the range is admitted when no real path::name reaches this exact case."""

    source = "const same = 0;\nit('same', () => {});\n"
    world = build_world(tmp_path)
    write(world.code, {CASES: source})
    write(world.memory, {f"onboarding/{CASES}.md": "# cases\n"})
    report = run(
        world,
        [
            _entry(
                [
                    {
                        "test": {
                            "path": CASES,
                            "locator": {"kind": "line_range", "start": 2, "end": 2},
                        },
                        "facet": "The declaration and the case share a name, so only the lines reach it.",
                    }
                ],
                evidence=f'{CASES}::"same"',
            )
        ],
    )
    assert report.state == "written", report.render()
    (proof,) = read_json(world.memory, f"onboarding/{CASES}.json")["proves"]
    assert proof["anchor"]["locator"] == {"kind": "line_range", "start": 2, "end": 2}
    assert [one.state for one in report.evidence[0].tests] == ["proof_written"]


def test_a_literal_case_under_a_computed_suite_pairs_its_own_title(tmp_path: Path) -> None:
    """L186-R1-F06: eligibility stays closed, and the case's own literal title still pairs."""

    source = "describe(suiteName, () => {\n  it('literal', () => {});\n});\n"
    world = build_world(tmp_path)
    write(world.code, {CASES: source})
    write(world.memory, {f"onboarding/{CASES}.md": "# cases\n"})
    report = run(
        world,
        [
            _entry(
                [
                    {
                        "test": {
                            "path": CASES,
                            "locator": {"kind": "line_range", "start": 2, "end": 2},
                        },
                        "facet": "The suite title is computed, so the case pairs by its own title.",
                    }
                ],
                evidence=f'{CASES}::"literal"',
            )
        ],
    )
    assert report.state == "written", report.render()
    assert [one.state for one in report.evidence[0].tests] == ["proof_written"]


def test_a_name_that_resolves_to_a_declaration_elsewhere_leaves_the_range_open(
    tmp_path: Path,
) -> None:
    """L186-R1-F05 (round 2): resolving is not reaching — only the case's own bytes close the range."""

    source = "function table_case() {\n  return 1;\n}\nit.each([1])('table_case', () => {});\n"
    world = build_world(tmp_path)
    write(world.code, {CASES: source})
    write(world.memory, {f"onboarding/{CASES}.md": "# cases\n"})
    report = run(
        world,
        [
            _entry(
                [
                    {
                        "test": {
                            "path": CASES,
                            "locator": {"kind": "line_range", "start": 4, "end": 4},
                        },
                        "facet": "The name resolves to the declaration, so only the lines reach the case.",
                    }
                ],
                evidence=f'{CASES}::"table_case"',
            )
        ],
    )
    assert report.state == "written", report.render()
    (proof,) = read_json(world.memory, f"onboarding/{CASES}.json")["proves"]
    assert proof["anchor"]["locator"] == {"kind": "line_range", "start": 4, "end": 4}


def test_the_witness_const_same_beside_a_parameterised_case(tmp_path: Path) -> None:
    """L186-R1-F05 (round 2 witness, verbatim): ``const same`` line 1, ``it.each([1])('same')`` line 2."""

    source = "const same = 0;\nit.each([1])('same', () => {});\n"
    world = build_world(tmp_path)
    write(world.code, {CASES: source})
    write(world.memory, {f"onboarding/{CASES}.md": "# cases\n"})
    report = run(
        world,
        [
            _entry(
                [
                    {
                        "test": {
                            "path": CASES,
                            "locator": {"kind": "line_range", "start": 2, "end": 2},
                        },
                        "facet": "path::same reaches the declaration on line 1, so the case is proved by line 2.",
                    }
                ],
                evidence=f'{CASES}::"same"',
            )
        ],
    )
    assert report.state == "written", report.render()
    (proof,) = read_json(world.memory, f"onboarding/{CASES}.json")["proves"]
    assert proof["anchor"]["locator"] == {"kind": "line_range", "start": 2, "end": 2}
