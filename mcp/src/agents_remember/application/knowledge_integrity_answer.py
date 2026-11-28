"""Compact inline answer projection for ``knowledge_integrity_check``.

Keeps outcome, roots, comparison bases, validation counts and bounded top violations,
route summary counts, and worklist summary metadata inline while the full route rows
and worklist item rows remain intact in the validated payload and daily tool log.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from typing import Any

from agents_remember.models.tools.tool_answer import (
    ENVELOPE_STRIPPED_FIELDS,
    TOOL_ANSWER_STATUS_ENTRY_MAX,
)

_WORKLIST_SUMMARY_KEYS = ("path", "digest", "itemCount", "state", "owner", "itemsByKind", "detail")


def summarize_routes(routes: list[Any]) -> dict[str, Any]:
    """Summarize family route rows into bounded counts without per-row entries."""
    by_status: Counter[str] = Counter()
    route_unassigned = 0
    routeless = 0
    differs = 0
    for row in routes:
        if not isinstance(row, Mapping):
            continue
        status = row.get("status")
        by_status[str(status) if status is not None else "unknown"] += 1
        state = row.get("state")
        if isinstance(state, Mapping):
            if state.get("routeUnassigned"):
                route_unassigned += 1
            if state.get("routeless"):
                routeless += 1
        if row.get("differs"):
            differs += 1
    return {
        "familyCount": len(routes),
        "byStatus": dict(sorted(by_status.items())),
        "routeUnassigned": route_unassigned,
        "routeless": routeless,
        "differs": differs,
    }


def summarize_worklist(worklist: Mapping[str, Any]) -> dict[str, Any]:
    """Keep worklist summary metadata inline and leave per-item rows to the file and log."""
    compact = {
        key: worklist[key]
        for key in _WORKLIST_SUMMARY_KEYS
        if key in worklist and worklist[key] is not None
    }
    incomplete = worklist.get("incomplete")
    if isinstance(incomplete, list) and incomplete:
        compact["incomplete"] = incomplete[:TOOL_ANSWER_STATUS_ENTRY_MAX]
    return compact


def _bounded_validation(validation: Mapping[str, Any]) -> dict[str, Any]:
    violations = validation.get("violations")
    if isinstance(violations, list) and len(violations) > TOOL_ANSWER_STATUS_ENTRY_MAX:
        return {
            **validation,
            "violations": {
                "count": len(violations),
                "items": violations[:TOOL_ANSWER_STATUS_ENTRY_MAX],
                "truncated": True,
            },
        }
    if isinstance(violations, Mapping) and isinstance(violations.get("items"), list):
        items = violations["items"]
        count = violations.get("count", len(items))
        if len(items) > TOOL_ANSWER_STATUS_ENTRY_MAX or bool(count > TOOL_ANSWER_STATUS_ENTRY_MAX):
            return {
                **validation,
                "violations": {
                    **violations,
                    "items": items[:TOOL_ANSWER_STATUS_ENTRY_MAX],
                    "truncated": True,
                },
            }
    return dict(validation)


def integrity_answer(full: Mapping[str, Any]) -> dict[str, Any]:
    """Project one integrity-check whole result into its compact inline answer."""
    answer = {
        key: value
        for key, value in full.items()
        if key not in ENVELOPE_STRIPPED_FIELDS and value is not None
    }
    routes = full.get("routes")
    if isinstance(routes, list):
        answer["routes"] = summarize_routes(routes)
    worklist = full.get("worklist")
    if isinstance(worklist, Mapping):
        answer["worklist"] = summarize_worklist(worklist)
    validation = full.get("validation")
    if isinstance(validation, Mapping):
        answer["validation"] = _bounded_validation(validation)
    return answer


__all__ = ["integrity_answer", "summarize_routes", "summarize_worklist"]
