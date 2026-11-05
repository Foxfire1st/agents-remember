"""Separate native file-reader limits from a failed rubric response."""

from __future__ import annotations

from typing import Any


def finalize_judge_context(audit: dict[str, Any], verdict: dict, rubric: tuple[str, ...]) -> None:
    """Record reader refusals without inferring why a verdict did not arrive."""
    # Cases imports JudgeRequest from judges; reuse its schema validator only
    # after both modules have initialized, rather than defining another schema.
    from ..graders.cases import _verdict_schema_valid  # noqa: PLC0415

    valid = _verdict_schema_valid(verdict) and [
        row["criterion"] for row in verdict["criteria"]
    ] == list(rubric)
    refusals = audit["readerLimitRefusals"]
    audit["readerLimitDisposition"] = {
        "validVerdictReturned": valid,
        "readerLimitRefusals": len(refusals),
        "contextFailureCauses": sum(
            value.startswith("judge-context-over-limit:") for value in audit["unknowns"]
        ),
        "basis": "existing verdict schema and exact supplied criterion order; earlier reader refusals do not establish a terminal context failure; independent causes remain unchanged",
    }
