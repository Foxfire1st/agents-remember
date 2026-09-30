"""MIK-R13's decision content rules in the validator's registry (MIK-R22 rule 9).

The rules are the pure functions of :mod:`agents_remember.models.knowledge_files.decisions`; this
module runs them over every decision record of the candidate tree. They apply to every decision,
new or carried, because a decision that breaks them cannot be read as a decision at all:

* ``R13.1-alternatives`` (refusing): at least two alternatives, exactly one ``chosen``;
* ``R13.1-reconsider-when`` (refusing): every ``rejected`` or ``deferred`` alternative states its
  ``reconsider_when``. The prose is never evaluated;
* ``R13.2-superseded-derived`` (refusing): ``superseded`` is never stored, as a decision's status or
  an alternative's. The shape rule refuses the same file, because the spelling does not exist; this
  rule says why and names the field, reading the raw document;
* ``R13.3-reconsider-on`` (refusing): a ``reconsider_on`` link's ``alternative`` indexes an existing
  ``rejected`` or ``deferred`` alternative, so MIK-R14's subject ``reconsider:<DEC-ID>#<index>`` is
  always a real one;
* ``R13.3-governs`` (report-only): a decision with no ``explains``, ``constrains`` or
  ``motivated_change_to`` link governs nothing, so no record, file or route it would be read from
  shows it. Reported, never refused: whether a lifted decision still governs code is the curator's
  judgment.

``origin`` naming the task is MIK-R21's shape (``origin.task`` is required), and ``admission`` is
MIK-R27's (``rules_admission``). An unresolved requirement endpoint is never a violation: the
validator has no task plane, and the writer reports each endpoint's resolution instead.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Final

from agents_remember.memory_quality.knowledge_validator.registry import (
    Finding,
    ValidationContext,
    ValidationRule,
    register_rule,
)
from agents_remember.models.knowledge_files.decisions import (
    GOVERNS_RELATIONS,
    SUPERSEDED,
    ContentProblem,
    alternative_problems,
    governs_links,
    reconsider_link_problems,
    reconsider_when_problems,
    stored_superseded_fields,
)
from agents_remember.models.knowledge_files.documents import KNOWLEDGE_ROOT, RECORD_DIRECTORIES
from agents_remember.models.knowledge_files.records import DecisionRecord

_DECISION_DIRECTORY: Final = f"{KNOWLEDGE_ROOT}/{RECORD_DIRECTORIES['decision']}/"


def _decisions(context: ValidationContext) -> Iterator[tuple[str, DecisionRecord]]:
    for record_file in context.parsed.records:
        if isinstance(record_file.record, DecisionRecord):
            yield record_file.path, record_file.record


def _findings(path: str, problems: list[ContentProblem]) -> Iterator[Finding]:
    for problem in problems:
        yield Finding(path, problem.field, problem.message)


def check_alternatives(context: ValidationContext) -> Iterator[Finding]:
    for path, record in _decisions(context):
        yield from _findings(path, alternative_problems(record))


def check_reconsider_when(context: ValidationContext) -> Iterator[Finding]:
    for path, record in _decisions(context):
        yield from _findings(path, reconsider_when_problems(record))


def check_reconsider_on(context: ValidationContext) -> Iterator[Finding]:
    for path, record in _decisions(context):
        yield from _findings(path, reconsider_link_problems(record))


def check_superseded_not_stored(context: ValidationContext) -> Iterator[Finding]:
    for path in sorted(context.candidate.files):
        if not (path.startswith(_DECISION_DIRECTORY) and path.endswith(".json")):
            continue
        try:
            document = json.loads(context.candidate.files[path].decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            continue  # the shape rule reports a file that is not JSON
        if not isinstance(document, dict):
            continue
        for field in stored_superseded_fields(document):
            yield Finding(
                path,
                field,
                f"{SUPERSEDED!r} is never stored: a decision is superseded when another "
                "decision's 'supersedes' names it. Keep this one 'active' and name it in the "
                "superseding decision's 'supersedes'",
            )


def check_governs(context: ValidationContext) -> Iterator[Finding]:
    for path, record in _decisions(context):
        if not governs_links(record):
            yield Finding(
                path,
                "links",
                f"decision {record.id} has no {'/'.join(sorted(GOVERNS_RELATIONS))} link, so it "
                "is read from no record, file or route; link what it governs, or keep it in the "
                "task if it governs nothing",
            )


DECISION_RULES = (
    ValidationRule(
        "R13.1-alternatives",
        "MIK-R13 rule 1",
        "a decision has at least two alternatives and exactly one chosen",
        check_alternatives,
    ),
    ValidationRule(
        "R13.1-reconsider-when",
        "MIK-R13 rule 1",
        "every rejected or deferred alternative states reconsider_when",
        check_reconsider_when,
    ),
    ValidationRule(
        "R13.2-superseded-derived",
        "MIK-R13 rule 2",
        "superseded is derived from supersedes and never stored",
        check_superseded_not_stored,
    ),
    ValidationRule(
        "R13.3-reconsider-on",
        "MIK-R13 rule 3",
        "a reconsider_on link names an existing rejected or deferred alternative",
        check_reconsider_on,
    ),
    ValidationRule(
        "R13.3-governs",
        "MIK-R13 rule 3",
        "a decision links what it governs",
        check_governs,
        report_only=True,
    ),
)

for _rule in DECISION_RULES:
    register_rule(_rule)
