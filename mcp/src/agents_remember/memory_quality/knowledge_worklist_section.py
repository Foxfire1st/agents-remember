"""The curator checklist's knowledge-worklist section (MIK-R08 rule 7).

The worklist is computed by the application layer and handed to the checklist as its persisted
``knowledge-worklist/v1`` document. This module only renders it: the state, the pairing it used, and
one row per item with the facts a curator acts on. It does not count toward
``curatorActionableCount`` -- what an open item blocks is the closeout gate's (MIK-R09), which goes
live at the cutover (MIK-R37).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Final

__all__ = ["WORKLIST_SECTION_HEADING", "knowledge_worklist_lines", "worklist_summary"]

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


_FACT_RENDERERS: Final[
    Mapping[str, Callable[[Mapping[str, Any], Mapping[str, Any]], list[str]]]
] = {
    "touched_invariant": lambda _item, facts: _entry_facts(facts),
    "stale_invariant": _stale_facts,
    "reached_family": _family_facts,
    "planned_untouched": _planned_facts,
    "family_route_condition": _route_facts,
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
            "(`knowledge/history/<leaf>.json`, MIK-R07). The closeout gate that refuses an item "
            "without a current row is MIK-R09's; until it is live these items are shown here and "
            "are not counted in `curatorActionableCount`."
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
