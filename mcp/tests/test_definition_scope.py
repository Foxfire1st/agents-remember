"""Definitions are parsed once per call without changing ownership or diagnostics."""

from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context

import pytest
from agents_remember.memory_quality.style.citations import extents, grammars, test_case_ranges
from tree_sitter import Language, Tree


def count_parses(monkeypatch: pytest.MonkeyPatch) -> list[bytes]:
    calls: list[bytes] = []
    parser = grammars.Parser

    class CountedParser:
        def __init__(self, language: Language) -> None:
            self.parser = parser(language)

        def parse(self, source: bytes) -> Tree:
            calls.append(source)
            return self.parser.parse(source)

    monkeypatch.setattr(grammars, "Parser", CountedParser)
    return calls


def test_distinct_path_content_and_grammar_each_have_one_parse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = count_parses(monkeypatch)
    lines = ["VALUE = 1"]
    with extents.definition_scope():
        for _ in range(3):
            assert "VALUE" in extents.definitions("one.py", lines)
            assert "VALUE" in extents.definitions("two.py", lines)
            assert "VALUE" in extents.definitions("one.py", ["VALUE = 2"])
        with monkeypatch.context() as patch:
            patch.setattr(grammars, "grammar_of", lambda _path: grammars.JAVASCRIPT)
            assert extents.definitions("one.py", lines) == {}
    assert len(calls) == 4


def test_nested_scope_owns_containers_and_error_exit_discards_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = count_parses(monkeypatch)
    lines = ["def answer():", "    return 42"]
    with pytest.raises(ValueError, match="end call"), extents.definition_scope():
        first = extents.definitions("answer.py", lines)
        first["answer"].clear()
        first["invented"] = []
        with extents.definition_scope():
            second = extents.definitions("answer.py", tuple(lines))
            assert second["answer"] and "invented" not in second
        assert len(calls) == 1
        raise ValueError("end call")
    with extents.definition_scope():
        assert extents.definitions("answer.py", lines)["answer"]
    assert len(calls) == 2
    extents.definitions("answer.py", lines)
    extents.definitions("answer.py", lines)
    assert len(calls) == 4


@pytest.mark.parametrize("suffix", ["js", "ts", "tsx"])
def test_case_diagnostics_share_one_parse_and_ordinary_trees_are_not_retained(
    monkeypatch: pytest.MonkeyPatch, suffix: str
) -> None:
    calls = count_parses(monkeypatch)
    path = f"widget.test.{suffix}"
    lines = ["test.each([1])('parameterized', () => { expect(1).toBe(1); });"]
    with extents.definition_scope():
        bound = extents.definitions(path, lines)
        refusal = grammars.test_case_refusal(path, lines, "parameterized")
        located = grammars.test_case_range(path, lines, 1, 1)
        assert refusal is not None and ".each" in refusal and "line_range" in refusal
        assert isinstance(located, test_case_ranges.CaseExtent)
        assert (located.start, located.end, located.titles) == (1, 1, ("parameterized",))
        assert len(calls) == 1
        parsed = grammars._parsed_in_call.get()
        assert parsed is not None
        assert len(parsed.values) == 1
        assert "ordinary" in extents.definitions("ordinary.py", ["ordinary = 1"])
        assert len(parsed.values) == 1
    assert extents.definitions(path, lines) == bound
    assert grammars.test_case_refusal(path, lines, "parameterized") == refusal
    assert grammars.test_case_range(path, lines, 1, 1) == located
    assert grammars._parsed_in_call.get() is None
    assert extents._definitions_in_call.get() is None


def test_copied_context_workers_share_one_parse_without_sharing_returned_containers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = count_parses(monkeypatch)
    lines = ["def answer():", "    return 42"]
    with extents.definition_scope(), ThreadPoolExecutor(max_workers=4) as workers:
        pending = [
            workers.submit(copy_context().run, extents.definitions, "answer.py", lines)
            for _ in range(12)
        ]
        results = [future.result() for future in pending]
        assert len(calls) == 1
        results[0]["answer"].clear()
        assert all(result["answer"] for result in results[1:])
