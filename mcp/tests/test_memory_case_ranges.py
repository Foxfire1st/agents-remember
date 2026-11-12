"""MIK-R194 rules 2 and 3: which lines of a JavaScript/TypeScript test file are one case's extent."""

from __future__ import annotations

from agents_remember.memory_quality.style.citations import grammars, test_case_ranges, test_cases
from tree_sitter import Parser

PATH = "pkg/cases.test.tsx"


def case(source: str, start: int, end: int, path: str = PATH) -> object:
    return grammars.test_case_range(path, source.split("\n"), start, end)


def test_a_parameterised_case_is_admitted_by_its_whole_extent() -> None:
    source = """it.each(['failed', 'refused'] as const)('a %s selection', (s) => {
  expect(s).toBe('x');
});
it('after', () => {});"""
    found = case(source, 1, 3)
    assert isinstance(found, test_case_ranges.CaseExtent), found
    assert (found.start, found.end, found.kind, found.test_case) == (1, 3, "test_case", True)
    # The evidence title is the case's own first argument, bare and behind its plain describe.
    assert found.titles == ("a %s selection",)


def test_the_inner_call_of_a_parameterised_case_is_no_case() -> None:
    source = """it.each(['failed', 'refused'] as const)('a %s selection', () => {
  expect(true).toBe(true);
});
it('other', () => {});"""
    # 1-1 is the inner ``it.each([...])``; it holds the parameters, not the title and the body.
    refused = case(source, 1, 1)
    assert isinstance(refused, str), refused
    assert "they are no test case's extent; the cases overlapping them span 1-3" in refused


def test_every_case_that_runs_and_that_no_name_reaches_is_admitted() -> None:
    source = """it.only('only', () => {});
it.concurrent('concurrent', () => {});
it.sequential('sequential', () => {});
it.for([1])('for table', () => {});
it(`computed ${value}`, () => {});
describe.each([1])('suite', () => {
  it('under a plain parameterized suite', () => {});
});
it('twice', () => {});
it('twice', () => {});"""
    for start, end in ((1, 1), (2, 2), (3, 3), (4, 4), (5, 5), (7, 7), (9, 9), (10, 10)):
        found = case(source, start, end)
        assert isinstance(found, test_case_ranges.CaseExtent), (start, found)
    # A title bound twice resolves to two extents, so neither is a definition: the lines are the proof.
    assert case(source, 9, 9).titles == ("twice",)  # type: ignore[union-attr]


def test_a_case_that_does_not_run_is_refused_with_its_real_modifier() -> None:
    source = """it.skip('skipped', () => {});
it.todo('todo');
it.skipIf(flag)('conditional', () => {});
it.runIf(flag)('run if', () => {});
it.fails('fails', () => {});
describe.skip('suite', () => {
  it('under skip', () => {});
});"""
    expected = {
        1: "the case uses .skip, which does not run",
        2: "the case uses .todo, which does not run",
        3: "the case uses .skipIf, which runs only when its condition holds",
        4: "the case uses .runIf, which runs only when its condition holds",
        5: "the case uses .fails, which passes only when its body fails",
        7: "the case uses .skip, which does not run",
    }
    for line, why in expected.items():
        refused = case(source, line, line)
        assert isinstance(refused, str), (line, refused)
        assert refused.startswith(
            f"lines {line}-{line} of {PATH} are not a case a line range proves: "
        )
        assert why in refused, refused


def test_a_uniquely_named_case_is_a_definition_and_the_refusal_names_it() -> None:
    source = """describe('suite', () => {
  it('plain', () => {});
});"""
    refused = case(source, 2, 2)
    assert isinstance(refused, str), refused
    assert "the case is a definition; name it 'pkg/cases.test.tsx::suite > plain'" in refused, (
        refused
    )


def test_lines_that_are_no_extent_name_the_cases_they_overlap() -> None:
    source = """it('first', () => {});
it.each([1, 2])('second %s', () => {
  expect(1).toBe(1);
});"""
    inside = case(source, 3, 3)
    assert isinstance(inside, str), inside
    assert "the cases overlapping them span 2-4" in inside
    nowhere = case(source, 20, 20)
    assert isinstance(nowhere, str), nowhere
    assert "no test case overlaps them" in nowhere


def test_a_non_test_file_or_non_script_file_is_refused_by_name() -> None:
    source = "it('plain', () => {});"
    for path in ("pkg/module.ts", "pkg/cases.test.py"):
        refused = case(source, 1, 1, path)
        assert isinstance(refused, str), (path, refused)
        assert "the file is not a JavaScript or TypeScript .test. or .spec. file" in refused
        assert f"name the test '{path}::<name>'" in refused


def test_a_member_outside_the_modifiers_is_no_case() -> None:
    source = """test.describe('suite', () => {
  test.step('step', () => {});
  test.setTimeout(1000);
  test('inner', () => {});
});"""
    for line in (1, 2, 3):
        refused = case(source, line, line)
        assert isinstance(refused, str), (line, refused)
        assert "are not a case a line range proves" in refused
    # The inner ``test`` of a ``test.describe`` is a definition with its bare title, so its lines
    # are refused by name; the suite's own call is no case at all.
    refused = case(source, 4, 4)
    assert isinstance(refused, str), refused
    assert "the case is a definition; name it 'pkg/cases.test.tsx::inner'" in refused


def test_the_symbol_refusal_offers_the_exact_lines_and_reads_modifiers_by_name() -> None:
    source = """it.each([1])('parameterized', () => {});
it.skipIf(flag)('conditional', () => {});
it.skip('skipped', () => {});"""
    each = grammars.test_case_refusal(PATH, source.split("\n"), "parameterized")
    assert each is not None
    assert "uses .each, which is not a definition" in each
    assert '"start": 1, "end": 1' in each
    conditional = grammars.test_case_refusal(PATH, source.split("\n"), "conditional")
    assert conditional is not None
    assert ".skipIf, which runs only when its condition holds" in conditional
    assert "line_range" not in conditional
    skipped = grammars.test_case_refusal(PATH, source.split("\n"), "skipped")
    assert skipped is not None
    assert ".skip, which does not run" in skipped
    assert "line_range" not in skipped


def test_an_enclosing_conditional_suite_refuses_the_range() -> None:
    """L186-R1-F01: a modifier on either side of the suite's table call still blocks the case."""

    source = """describe.skipIf(flag).each([1])('suite %s', () => {
  it.each([1])('case %s', () => {});
});"""
    refused = case(source, 2, 2)
    assert isinstance(refused, str), refused
    assert "the case uses .skipIf, which runs only when its condition holds" in refused


def test_a_standalone_parameter_table_is_no_case() -> None:
    """L186-R1-F02: a table builds the case in a second call, so the bare table is no extent."""

    refused = case("it.each([1]);\n", 1, 1)
    assert isinstance(refused, str), refused
    assert "no test case overlaps them" in refused


def test_a_plain_case_under_a_modified_suite_still_resolves_by_name() -> None:
    """L186-R1-F03: a suite's modifier never removes a plain case's name (base behaviour kept)."""

    source = "describe.skip('s', () => {\n  it('plain', () => {});\n});"
    tree = Parser(language=grammars.language(grammars.TSX)).parse(source.encode())
    names = [one for node in grammars._walk(tree.root_node) if (one := test_cases.case_names(node))]
    assert names == [("s > plain", "plain")], names
    # The range route meanwhile refuses: a case that does not run proves nothing.
    refused = case(source, 2, 2)
    assert isinstance(refused, str), refused
    assert "the case uses .skip, which does not run" in refused


def test_a_blocked_case_is_never_offered_a_range() -> None:
    """L186-R1-F04: every blocking modifier is read, and the refusal offers no line_range."""

    concurrent = """it.concurrent.skip('c', () => {});"""
    refused = grammars.test_case_refusal(PATH, concurrent.split("\n"), "c")
    assert refused is not None
    assert refused == "test case 'c' uses .skip, which does not run"
    assert "line_range" not in refused

    nested = """describe.skip('s', () => {
  it.each([1])('e %s', () => {});
});"""
    refused = grammars.test_case_refusal(PATH, nested.split("\n"), "e %s")
    assert refused is not None
    assert refused == "test case 'e %s' uses .skip, which does not run"
    assert "line_range" not in refused
