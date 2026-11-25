"""The ``knowledge_write`` answer built from one write report.

The answer holds the writer's outcome, its counts by kind, the problems, violations and notes the
caller acts on -- each cut to the answer's list bound with a flag saying so -- and the location of
the whole report, which is a file. A note is something the write took but wants the caller to know,
such as a revised rule that still has no title. Nothing here decides anything: it
shapes the report's own document for the wire.
"""

from __future__ import annotations

from typing import Any

from agents_remember.models.tools.tool_answer import TOOL_ANSWER_LIST_MAX


def _cut(values: list[Any]) -> tuple[list[Any], bool]:
    return values[:TOOL_ANSWER_LIST_MAX], len(values) > TOOL_ANSWER_LIST_MAX


def write_answer(
    document: dict[str, Any], report_path: str | None, report_error: str | None
) -> dict[str, Any]:
    """The answer for a write report's ``document`` and where the whole report landed."""

    problems, problems_cut = _cut(
        [{"where": one["where"], "message": one["message"]} for one in document["problems"]]
    )
    violations, violations_cut = _cut(list(document["violations"]))
    notes, notes_cut = _cut(list(document["notes"]))
    return {
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


__all__ = ["write_answer"]
