"""The curator checklist's knowledge-worklist section (MIK-R08 rule 7).

The worklist is computed by the application layer and handed to the checklist as its persisted
``knowledge-worklist/v1`` document. This module only renders it: the state, the pairing it used, and
one row per item with the facts a curator acts on. The section itself counts nothing: the mandatory
gate (MIK-R09) turns every item without a current satisfying row into one repair finding (check
``knowledge-gate``), which the checklist counts toward ``curatorActionableCount``.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Final

__all__ = ["WORKLIST_SECTION_HEADING", "item_facts", "knowledge_worklist_lines", "worklist_summary"]

WORKLIST_SECTION_HEADING: Final = "## Knowledge worklist (MIK-R08)"
_SHORT: Final = 12


def _short(value: object) -> str:
    text = str(value or "")
    return text if len(text) <= _SHORT + 7 else f"{text[: _SHORT + 7]}..."


def worklist_summary(document: Mapping[str, Any], path: str | None) -> dict[str, Any]:
    """The compact wire summary of one worklist: state, counts by kind, digest and where it is."""

    items = document.get("items") or ()
    return {
        "state": document.get("state"),
        "path": path,
        "digest": document.get("digest"),
        "itemCount": len(items),
        "itemsByKind": dict(sorted(Counter(str(item.get("kind")) for item in items).items())),
        "incomplete": list(document.get("incomplete") or ()),
    }


def _entry_facts(facts: Mapping[str, Any]) -> list[str]:
    parts = [f"{entry['id']} {entry['class']}" for entry in facts.get("entries") or ()]
    parts += [f"added {fact['id']}" for fact in facts.get("added") or ()]
    parts += [f"retired {fact['id']}" for fact in facts.get("retired") or ()]
    parts += [f"re-anchored {fact['id']}" for fact in facts.get("reanchored") or ()]
    record = facts.get("record") or {}
    if record.get("changed"):
        parts.append(
            f"record changed (revision {record.get('baseRevision')} -> "
            f"{record.get('candidateRevision')})"
        )
    return parts


def _stale_facts(_item: Mapping[str, Any], facts: Mapping[str, Any]) -> list[str]:
    return [f"{entry['id']} stale at base" for entry in facts.get("entries") or ()]


def _family_facts(_item: Mapping[str, Any], facts: Mapping[str, Any]) -> list[str]:
    members = facts.get("members") or ()
    return [f"{len(members)} member(s) to examine", *(facts.get("reachedBy") or ())]


def item_facts(item: Mapping[str, Any]) -> str:
    """The facts a curator acts on for one item, on one line (the section's Facts cell)."""

    return _item_facts(item)


def _item_facts(item: Mapping[str, Any]) -> str:
    facts = item.get("facts") or {}
    render = _FACT_RENDERERS.get(str(item.get("kind")))
    parts = render(item, facts) if render is not None else [f"{key}" for key in sorted(facts)]
    return "; ".join(parts) or "-"


def _planned_facts(item: Mapping[str, Any], facts: Mapping[str, Any]) -> list[str]:
    declared = facts.get("declared") or {}
    parts = [f"declared by {declared.get('requirementRef')}", str(facts.get("unmatched"))]
    parts += [
        f"{row.get('id')} {row.get('disposition')} {row.get('effect') or ''}".rstrip()
        + " (does not deliver it)"
        for row in facts.get("rows") or ()
    ]
    answered = item.get("satisfiedBy")
    parts.append(f"answered by {answered}" if answered else "needs a planned row")
    return parts


def _unexplained_facts(item: Mapping[str, Any], facts: Mapping[str, Any]) -> list[str]:
    """MIK-R10: where the change is, its path's coverage, and what answers it."""

    coverage = facts.get("coverage") or {}
    hunks = facts.get("hunks") or ()
    where = (
        ", ".join(
            f"-{one['base'][0]},{one['base'][1]} +{one['candidate'][0]},{one['candidate'][1]}"
            for one in hunks
        )
        if hunks
        else f"{facts.get('content')} {facts.get('status')}"
    )
    parts = [
        f"{facts.get('path')} {where}",
        f"{coverage.get('state')} ({coverage.get('realizationEntries')} entries; route "
        f"{coverage.get('route') or '-'} {coverage.get('routeStatus')})",
    ]
    if facts.get("deleteOnly"):
        parts.append("delete-only: only no_invariant")
    answered = item.get("satisfiedBy")
    if answered:
        parts.append(f"answered by {answered}")
    elif coverage.get("state") == "covered":
        parts.append(f"needs attach/author or a no_invariant row `{facts.get('row')}`")
    else:
        trace = facts.get("onboardingTrace") or {}
        parts.append(f"needs the onboarding trace `{trace.get('subject')}`")
    return parts


def _planned_lines(document: Mapping[str, Any]) -> list[str]:
    """Rule 7: the declaration's reconciliation, one line per declared effect."""

    planned = document.get("plannedEffects") or {}
    if not planned.get("declared"):
        return [
            "Planned effects (MIK-R11): none declared in the task document, so every item is "
            "`unplanned`.",
            "",
        ]
    answered = {
        item.get("id"): item.get("satisfiedBy")
        for item in document.get("items") or ()
        if item.get("kind") == "planned_untouched"
    }
    lines = ["Planned effects (MIK-R11), declared in the task document:", ""]
    for entry in planned.get("entries") or ():
        row = answered.get(entry.get("item"))
        state = (
            f"matched by `{entry.get('matchedBy')}`"
            if entry.get("matched")
            else f"**unmatched** ({entry.get('unmatched')}): `planned_untouched`, "
            + (f"answered by `{row}`" if row else "needs a planned row")
        )
        lines.append(f"- `{_cell(str(entry.get('key')))}` ({entry.get('requirementRef')}): {state}")
    lines.append("")
    return lines


def _route_facts(item: Mapping[str, Any], facts: Mapping[str, Any]) -> list[str]:
    """MIK-R06: what is affected, where it went, the suggestion and what answers the item."""

    parts = [f"{facts.get('condition')}: {', '.join(_route_affected(facts)) or 'no routes'}"]
    if facts.get("renameCandidates"):
        parts.append(f"renamed to {', '.join(facts['renameCandidates'])}")
    parts.append(_route_suggestion(facts.get("suggestion")))
    if facts.get("unmappedLocations"):
        parts.append(f"absent without a rename: {', '.join(facts['unmappedLocations'])}")
    satisfied = item.get("satisfiedBy")
    parts.append(
        f"answered by {satisfied}"
        if satisfied
        else "needs a family row (rerouted, assigned, changed or retired; never no_impact) "
        "and routes that satisfy MIK-R04"
    )
    return parts


def _route_affected(facts: Mapping[str, Any]) -> list[str]:
    """The affected routes or entry paths, from the base view when it shows the condition."""

    affected = facts.get("affected") or {}
    named = affected.get("base") if affected.get("base") is not None else affected.get("candidate")
    return [one["path"] if isinstance(one, Mapping) else str(one) for one in named or ()]


def _route_suggestion(suggestion: Any) -> str:
    if isinstance(suggestion, Mapping):
        return f"mechanical suggestion {', '.join(suggestion['routes']) or '-'}"
    return "no suggestion (ambiguous rename target, or a file absent at C without a rename)"


def _reconsideration_facts(item: Mapping[str, Any], facts: Mapping[str, Any]) -> list[str]:
    """MIK-R14: the alternative, each changed target with its trigger, and what answers it."""

    parts = [
        f"{facts.get('alternativeStatus')} alternative {facts.get('alternative')} "
        f"{facts.get('option')!r} of {facts.get('decision')}"
    ]
    parts += [f"{one.get('target')} ({one.get('trigger')})" for one in facts.get("changed") or ()]
    satisfied = item.get("satisfiedBy")
    parts.append(
        f"answered by {satisfied}"
        if satisfied
        else "needs a reconsideration row (still_rejected with a reason, or raise)"
    )
    return parts


def _trace_facts(item: Mapping[str, Any], facts: Mapping[str, Any]) -> list[str]:
    """MIK-R30: the changed sources a card or route overview traces, and what answers it."""

    parts = [f"sources {', '.join(facts.get('sources') or ()) or '-'}"]
    if facts.get("sidecarUnreadable"):
        parts.append("sidecar unreadable: repair it through the writer")
    answered = item.get("satisfiedBy")
    parts.append(
        f"answered by {answered}"
        if answered
        else "needs a counted change of its Markdown or sidecar, or a no_impact row"
    )
    return parts


_FACT_RENDERERS: Final[
    Mapping[str, Callable[[Mapping[str, Any], Mapping[str, Any]], list[str]]]
] = {
    "touched_invariant": lambda _item, facts: _entry_facts(facts),
    "stale_invariant": _stale_facts,
    "reached_family": _family_facts,
    "planned_untouched": _planned_facts,
    "family_route_condition": _route_facts,
    "unexplained_hunk": _unexplained_facts,
    "unexplained_file": _unexplained_facts,
    "reconsideration_candidate": _reconsideration_facts,
    "onboarding_trace": _trace_facts,
}


def _cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


def knowledge_worklist_lines(document: Mapping[str, Any], path: str | None) -> list[str]:
    """The section's Markdown lines for one worklist document."""

    summary = worklist_summary(document, path)
    pairing = document.get("pairing") or {}
    base = pairing.get("base") or {}
    memory_base = pairing.get("memoryBase") or {}
    lines = [
        WORKLIST_SECTION_HEADING,
        "",
        f"- State: **{summary['state']}**",
        f"- Worklist: `{_cell(str(path or '-'))}`",
        f"- Digest: `{summary['digest']}`",
        (
            f"- Pairing: B `{_short(base.get('commit'))}`, K_B `{_short(memory_base.get('commit'))}`"
            f"{' (converted base)' if memory_base.get('convertedBase') else ''}, "
            f"C tree `{_short((pairing.get('candidate') or {}).get('tree'))}`, "
            f"K_C tree `{_short((pairing.get('memoryCandidate') or {}).get('tree'))}`"
        ),
        "",
    ]
    incomplete: Sequence[Mapping[str, Any]] = summary["incomplete"]
    if incomplete:
        lines += ["The run is incomplete; no item list exists until these inputs can be read:", ""]
        lines += [f"- `{one.get('input')}`: {_cell(str(one.get('detail')))}" for one in incomplete]
        lines.append("")
        return lines
    items = document.get("items") or ()
    lines += [
        (
            "Each item needs a row about its subject in the leaf's history file "
            "(`knowledge/history/<leaf>.json`, MIK-R07). The mandatory gate (MIK-R09) counts each "
            "item without a current satisfying row as one repairable finding (check "
            "`knowledge-gate`) toward `curatorActionableCount`, and refuses closeout while any is "
            "open."
        ),
        "",
    ]
    lines += _planned_lines(document)
    if not items:
        lines += ["No item: the change reaches no recorded knowledge.", ""]
        return lines
    lines += ["| Kind | Subject | Plan | Item | Facts |", "| --- | --- | --- | --- | --- |"]
    lines += [
        f"| {item.get('kind')} | {_cell(str(item.get('subject')))} | {item.get('planning') or '-'} "
        f"| `{_short(item.get('id'))}` | {_cell(_item_facts(item))} |"
        for item in items
    ]
    lines.append("")
    return lines
