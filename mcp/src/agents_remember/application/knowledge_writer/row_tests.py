"""The tests a rule or family row names, and what a consolidation task's rows must say about them.

A hand-off row may carry ``testsRun`` (each test with ``result`` ``passed`` or ``failed``),
``testsNotRun`` (each named test the reader could not run, with its ``reason``) and
``noProofRecorded: true``. They belong to a rule's or a family's row only
(:class:`~agents_remember.models.knowledge_files.history_consolidation.ConsolidationEvidence`).

In a consolidation task every row about a rule says which tests were run with their result, which
named tests were not run and why, or that the rule has no recorded proof: the row is the pass's
record of how the rule was checked, and a row that says none of it cannot be told apart from a rule
nobody checked. The task is known by its persisted worklist, which holds the items of the pass's
list; the writer then also writes the records that list reached into the history file
(``consolidationSubjects``), so the state reader knows which kept rows the pass may consolidate.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, MutableMapping
from typing import Any, Final

from pydantic import ValidationError

from agents_remember.application.knowledge_worklist.consolidation import CONSOLIDATION_KIND
from agents_remember.models.knowledge_files.history import row_kind_for_subject
from agents_remember.models.knowledge_files.history_consolidation import ConsolidationEvidence

__all__ = [
    "TEST_KEYS",
    "consolidation_subjects_of",
    "misplaced_tests_refusal",
    "missing_rule_tests_refusal",
    "read_row_tests",
    "with_consolidation_subjects",
]

TEST_KEYS: Final = ("testsRun", "testsNotRun", "noProofRecorded")
_TEST_ROW_KINDS: Final = frozenset({"invariant", "family"})


def read_row_tests(raw: Mapping[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """The row's test fields as written, checked by the row model's own shape; the refusals."""

    named = {key: raw[key] for key in TEST_KEYS if key in raw}
    if not named:
        return {}, []
    try:
        ConsolidationEvidence.model_validate(named)
    except ValidationError as error:
        return {}, [
            f"{'.'.join(str(part) for part in one['loc']) or 'tests'}: {one['msg']}"
            for one in error.errors()
        ]
    return named, []


def misplaced_tests_refusal(row: Mapping[str, Any], tests: Mapping[str, Any]) -> str | None:
    """Why a row of another kind cannot carry test fields, or ``None``."""

    if not tests or row_kind_for_subject(str(row["subject"])).name in _TEST_ROW_KINDS:
        return None
    return (
        f"{', '.join(sorted(tests))} belong to a rule's or a family's row; remove them from the "
        f"row about {row['subject']}"
    )


def missing_rule_tests_refusal(row: Mapping[str, Any], consolidating: bool) -> str | None:
    """Why a rule's row in a consolidation task is refused for naming no test fact, or ``None``."""

    if not consolidating or any(row.get(key) for key in TEST_KEYS):
        return None
    return (
        f"{row['subject']}: in a consolidation task a rule's row says how the rule was checked. "
        "Add to the row in the hand-off's 'history' the tests that were run with their result "
        '(\'testsRun\': [{"test": "<path>::<name>", "result": "passed"}]), the named tests '
        'that could not be run with the reason (\'testsNotRun\': [{"test": ..., "reason": ...}]),'
        " or 'noProofRecorded': true when the rule has no recorded proof"
    )


def consolidation_subjects_of(items: Iterable[tuple[str, str, str]]) -> tuple[str, ...]:
    """The sorted subjects of the persisted worklist's list items: ``(kind, subject, id)``."""

    return tuple(sorted({subject for kind, subject, _ in items if kind == CONSOLIDATION_KIND}))


def with_consolidation_subjects(
    document: MutableMapping[str, Any], subjects: tuple[str, ...]
) -> None:
    """Write the pass's reached records into a consolidation task's history file document."""

    if subjects:
        document["consolidationSubjects"] = list(subjects)
