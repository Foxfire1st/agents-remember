"""The tests a hand-off names in its text: test IDs, pytest selections and bare test files.

A hand-off entry's evidence names the tests that prove its rule as text. This module is the one
reading of those spellings: a test ID ``path::name`` (``path::Class::method`` is ``Class.method``),
a JavaScript or TypeScript case ``path::"title"``, the pytest selection ``path -k name``, and a test
file named with no test in it. Reading resolves nothing: whether a named test exists at C is the
writer's to establish.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass

__all__ = [
    "CASE_PROOF",
    "TEST_ID",
    "TestFileMention",
    "TestReference",
    "tests_named_in",
]

_CASE_PATH = r"[\w./@+-]+\.(?:test|spec)\.(?:[cm]?[jt]s|[jt]sx)"
CASE_PROOF = re.compile(rf"(?P<path>{_CASE_PATH})::(?P<name>[^\r\n]+)")
_CASE_EVIDENCE = re.compile(rf'(?P<path>{_CASE_PATH})::(?P<title>"(?:\\.|[^"\\])*?")')
TEST_ID = re.compile(r"(?P<path>[\w./@+-]+\.py)::(?P<name>[A-Za-z_][\w]*(?:::[A-Za-z_][\w]*)*)")
# MIK-R28 rule 2's "path plus symbol": the pytest selection ``<path> -k <name>`` where the ``-k``
# expression is one bare identifier (optionally quoted). An expression (``a and not b``) names no one
# test, so it is not read as one.
_SELECTED_TEST = re.compile(
    r"(?P<path>[\w./@+-]+\.py)\s+-k\s+(?P<quote>['\"]?)(?P<name>[A-Za-z_]\w*)(?P=quote)"
    r"(?=$|[\s,;:.)\]`'\"])(?!\s+(?:and|or|not)\b)"
)
# A test module named without a test in it: a ``test_*.py`` or ``*_test.py`` file (pytest's module
# pattern), not followed by ``::``. It names no resolvable test, so it is reported rather than
# silently dropped (MIK-R28 rule 2). Helper modules (``conftest.py``, ``*_test_support.py``, any
# other module under ``tests/``) hold no tests and are never reported.
_TEST_FILE = re.compile(
    r"(?<![\w./@+-])(?P<path>(?:[\w.@+-]+/)*(?:test_[\w.@+-]*|[\w.@+-]*_test)\.py)"
    r"(?![\w./@+-]|::)"
)


@dataclass(frozen=True)
class TestReference:
    """A test named as ``path::name`` (``path::Class::method`` is the symbol ``Class.method``)."""

    path: str
    name: str

    @property
    def spelling(self) -> str:
        name = self.name.replace(".", "::") if self.path.endswith(".py") else self.name
        return f"{self.path}::{name}"


@dataclass(frozen=True)
class TestFileMention:
    """A test file the evidence names without naming a test in it: no proof can be prepared."""

    path: str

    @property
    def spelling(self) -> str:
        return self.path


def tests_named_in(evidence: Sequence[str]) -> tuple[TestReference | TestFileMention, ...]:
    """Every test ``evidence`` names, in order of appearance, once each (MIK-R28 rule 2).

    A test is named as a test ID ``path::name`` (``path::Class::method`` is ``Class.method``) or as a
    path plus symbol, the pytest selection ``path -k name``. A test file named with neither is a
    :class:`TestFileMention`, so the report can say the evidence names no resolvable test. Reading
    resolves nothing: whether a named test exists at C is the writer's to establish.
    """

    found: list[tuple[int, int, TestReference | TestFileMention]] = []
    for position, text in enumerate(evidence):
        for match in _CASE_EVIDENCE.finditer(text):
            try:
                title = json.loads(match["title"])
            except ValueError:
                continue
            found.append((position, match.start(), TestReference(match["path"], title)))
        for pattern in (TEST_ID, _SELECTED_TEST):
            for match in pattern.finditer(text):
                reference = TestReference(match["path"], match["name"].replace("::", "."))
                found.append((position, match.start(), reference))
    # A file some evidence string names a test in is named, wherever else it is mentioned bare.
    named = {item.path for _, _, item in found}
    for position, text in enumerate(evidence):
        for match in _TEST_FILE.finditer(text):
            if match["path"] not in named:
                found.append((position, match.start(), TestFileMention(match["path"])))
    ordered = [item for _, _, item in sorted(found, key=lambda one: (one[0], one[1]))]
    return tuple(dict.fromkeys(ordered))
