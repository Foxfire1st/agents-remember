"""The ``knowledge_write`` answer built from one write report.

The answer holds the writer's outcome, its counts by kind, the problems, violations and notes the
caller acts on -- each cut to the answer's list bound with a flag saying so -- and the location of
the whole report, which is a file. A note is something the write took but wants the caller to know,
such as a revised rule that still has no title. Nothing here decides anything: it
shapes the report's own document for the wire.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agents_remember.application.role_capsules.refusal_references import with_refusal_reference
from agents_remember.models.tools.tool_answer import (
    ENVELOPE_STRIPPED_FIELDS,
    TOOL_ANSWER_LIST_MAX,
)

COMPACT_WRITE_LIST_MAX = 5


def _cut(values: list[Any], limit: int = TOOL_ANSWER_LIST_MAX) -> tuple[list[Any], bool]:
    return values[:limit], len(values) > limit


def write_answer(
    document: dict[str, Any], report_path: str | None, report_error: str | None
) -> dict[str, Any]:
    """The answer for a write report's ``document`` and where the whole report landed."""

    problems, problems_cut = _cut(
        [{"where": one["where"], "message": one["message"]} for one in document["problems"]]
    )
    all_violations = list(document["violations"])
    violations, violations_cut = _cut(all_violations)
    notes, notes_cut = _cut(list(document["notes"]))
    if document["state"] == "refused":
        pointer = with_refusal_reference("", "knowledge-write-refused")
        if pointer and pointer not in notes:
            notes = [*notes, pointer]
    result: dict[str, Any] = {
        "ok": True,
        "operation": "knowledge_write",
        "state": document["state"],
        "owner": document["owner"],
        "memoryRoot": document["memoryRoot"],
        "codeTree": document["codeTree"],
        "authorization": document["authorization"],
        "counts": {
            "records": len(document["records"]),
            "entries": len(document["entries"]),
            "historyRows": len(document["historyRows"]),
            "evidence": len(document["evidence"]),
            "carried": len(document["carried"]),
            "requirementEndpoints": len(document["requirementEndpoints"]),
        },
        "problems": problems,
        "problemsTruncated": problems_cut,
        "violations": violations,
        "violationsTruncated": violations_cut,
        "notes": notes,
        "notesTruncated": notes_cut,
        "provisionalIds": document["provisionalIds"],
        "plannedBeforeRefusal": document["plannedBeforeRefusal"],
        "written": document["written"],
        "removed": document["removed"],
        "reportPath": report_path,
        "reportError": report_error,
    }
    if violations_cut:
        result["violationCount"] = len(all_violations)
    return result


def compact_write_answer(full: Mapping[str, Any]) -> dict[str, Any]:
    """Project one write payload into its compact inline answer without mutating ``full``."""

    answer = {
        key: value
        for key, value in full.items()
        if key not in ENVELOPE_STRIPPED_FIELDS and value is not None
    }
    violations = answer.get("violations")
    if isinstance(violations, list):
        total_violations = (
            full["violationCount"]
            if isinstance(full.get("violationCount"), int)
            else len(violations)
        )
        if len(violations) > COMPACT_WRITE_LIST_MAX or full.get("violationsTruncated") is True:
            answer["violations"] = violations[:COMPACT_WRITE_LIST_MAX]
            answer["violationsTruncated"] = True
            answer["violationCount"] = total_violations
    for field, count_key, truncated_key in (
        ("written", "writtenCount", "writtenTruncated"),
        ("removed", "removedCount", "removedTruncated"),
    ):
        values = answer.get(field)
        if isinstance(values, list) and len(values) > COMPACT_WRITE_LIST_MAX:
            answer[field] = values[:COMPACT_WRITE_LIST_MAX]
            answer[count_key] = len(values)
            answer[truncated_key] = True
    return answer


__all__ = ["COMPACT_WRITE_LIST_MAX", "compact_write_answer", "write_answer"]
