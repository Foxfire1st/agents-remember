"""Render a file brief as Markdown, beginning with the file's own context.

The first line names the memory tree the brief was made from: a reader compares it with
``briefs/TREE`` or the memory's own tree to see the brief's age without a tool. A part the memory
holds nothing for says "none" in one line; retired records are counted, never shown.
"""

from __future__ import annotations

from collections.abc import Iterable

from agents_remember.application.file_briefs.model import (
    DecisionLine,
    FamilyBlock,
    FileBrief,
    Place,
    RuleBlock,
    RuleHead,
    WeaknessLine,
)

NONE_LINE = "None."


def brief_markdown(brief: FileBrief) -> str:
    """The whole brief of one file."""

    lines = [*_head(brief), "", *_purpose(brief), "", *_rules(brief)]
    lines += ["", *_loose_weaknesses(brief.loose_weaknesses)]
    lines += ["", *_loose_decisions(brief.loose_decisions)]
    lines += ["", *_documents(brief), "", *_families(brief.families)]
    return "\n".join(lines).rstrip() + "\n"


def tree_line(memory_tree: str) -> str:
    """The brief's first line: the one place its age is read from."""

    return f"<!-- memory tree {memory_tree} -->"


def head_line(head: RuleHead) -> str:
    """A rule's state, writer, proof and anchors in one line."""

    proof = "none" if not head.proofs else f"{head.proofs} test" + ("s" if head.proofs != 1 else "")
    anchors = (
        f"{head.anchors_current} of {head.anchors_total} current"
        if head.anchors_measured
        else f"{head.anchors_total}, not measured"
    )
    moved = " · **moved**" if head.moved else ""
    return (
        f"State {head.standing.state} · writer: {head.standing.writer} · "
        f"proof: {proof} · anchors here: {anchors}{moved}"
    )


def counted(number: int, one: str, many: str | None = None) -> str:
    """``1 rule``, ``2 rules``: a count with its unit."""

    return f"{number} {one if number == 1 else many or one + 's'}"


def _head(brief: FileBrief) -> list[str]:
    head = brief.head
    counts = head.counts
    code = (
        "no code tree could be read; anchors are not measured"
        if head.code_commit is None
        else f"code commit `{head.code_commit[:12]}`"
        + (" with the working tree's edits" if head.code_state == "working-tree" else "")
    )
    card = f"`{head.card_path}`" if head.card_path else "none"
    retired = f" ({counted(counts.retired, 'retired rule')} left out)" if counts.retired else ""
    return [
        tree_line(head.memory_tree),
        f"# Brief: `{head.path}`",
        "",
        f"- Made from memory tree `{head.memory_tree[:12]}`; anchors checked against {code}.",
        "- Derived file, written by the product. Never edit it; it is not in Git and no check "
        "reads it. If the tree in its first line differs from `briefs/TREE` or from this memory's "
        "tree, it is older than the memory.",
        f"- Card: {card}.",
        f"- In this file: {counted(counts.rules, 'rule')}{retired}, "
        f"{counted(counts.weaknesses, 'open weakness', 'open weaknesses')}, "
        f"{counted(counts.documents, 'document')} that stand on it, "
        f"{counted(counts.families, 'family', 'families')}.",
    ]


def _purpose(brief: FileBrief) -> list[str]:
    return ["## What the file is for", "", brief.purpose or NONE_LINE]


def _rules(brief: FileBrief) -> list[str]:
    lines = [f"## The file's own rules ({len(brief.rules)})"]
    if not brief.rules:
        return [*lines, "", NONE_LINE]
    for number, rule in enumerate(brief.rules, 1):
        lines += ["", *rule_markdown(number, rule)]
    return lines


def rule_markdown(number: int, rule: RuleBlock, *, level: str = "###") -> list[str]:
    """One rule's block: name, standing, text, places, proof, decisions and what burdens it."""

    lines = [
        f"{level} {number}. {rule.head.title.text} · `{rule.head.id}`",
        "",
        head_line(rule.head),
        "",
        f"**Rule.** {rule.statement}",
    ]
    if rule.applicability:
        lines.append(f"- **Applies to:** {rule.applicability}")
    lines += [f"- **Condition:** {one}" for one in rule.conditions]
    lines += [f"- **Not covered:** {one}" for one in rule.exclusions]
    lines += ["", "**Where it is realized in this file**", *_places(rule.here, measured=True)]
    lines += ["", "**Where else**", *_places(rule.elsewhere, measured=False)]
    lines += ["", "**Proof**"]
    lines += [
        f"- `{one.path}` · `{one.symbol}`" + (f": {one.facet}" if one.facet else "")
        for one in rule.proofs
    ] or ["- No test is recorded as proving this rule."]
    lines += ["", "**Decisions that formed it**", *_decisions(rule.decisions)]
    lines += ["", "**Weaknesses, limits, failure modes, assumptions**"]
    lines += _burdens(rule)
    families = ", ".join(f"`{one}`" for one in rule.families) or "none"
    lines += ["", f"Families: {families}. Record: `{rule.record_path}`."]
    return lines


def _places(places: Iterable[Place], *, measured: bool) -> list[str]:
    lines = []
    for place in places:
        state = _place_state(place) if measured else ""
        where = f"`{place.symbol}`" if measured else f"`{place.path}` · `{place.symbol}`"
        role = f" ({place.role}{state})" if place.role or state else ""
        rationale = f": {place.rationale}" if place.rationale and measured else ""
        lines.append(f"- {where}{role}{rationale}")
    return lines or [f"- {NONE_LINE}"]


def _place_state(place: Place) -> str:
    if place.current is None:
        return ", not measured"
    return ", current" if place.current else ", **moved**"


def _decisions(decisions: Iterable[DecisionLine]) -> list[str]:
    lines = []
    for decision in decisions:
        chosen = " / ".join(decision.chosen) or "no chosen option recorded"
        lines.append(f"- `{decision.id}` ({decision.relation}). Chosen: {chosen}")
        lines += [f"  - Rejected: {option} — {reason}" for option, reason in decision.rejected]
    return lines or [f"- {NONE_LINE}"]


def _burdens(rule: RuleBlock) -> list[str]:
    lines = [_weakness(one) for one in rule.weaknesses]
    lines += [
        f"- `{one}`: a weakness at the same symbol, shown with an earlier rule of this brief."
        for one in rule.weaknesses_named
    ]
    lines += [f"- `{one.id}` ({one.kind}, {one.relation}): {one.text}" for one in rule.facets]
    return lines or [f"- {NONE_LINE}"]


def _weakness(weakness: WeaknessLine) -> str:
    return (
        f"- `{weakness.id}` (weakness, {weakness.kind}, `{weakness.symbol}`): {weakness.statement}"
    )


def _loose_weaknesses(weaknesses: tuple[WeaknessLine, ...]) -> list[str]:
    lines = [f"## Weaknesses at this file that belong to no rule ({len(weaknesses)})", ""]
    return lines + ([_weakness(one) for one in weaknesses] or [NONE_LINE])


def _loose_decisions(decisions: tuple[DecisionLine, ...]) -> list[str]:
    lines = [f"## Decisions at this file that belong to no rule ({len(decisions)})", ""]
    return lines + (_decisions(decisions) if decisions else [NONE_LINE])


def _documents(brief: FileBrief) -> list[str]:
    lines = [f"## Documents that stand on this file ({len(brief.documents)})", ""]
    return lines + (
        [
            f"- `{one.document}` (`{one.id}`): a change here owes this document a confirmation."
            for one in brief.documents
        ]
        or [NONE_LINE]
    )


def _families(families: tuple[FamilyBlock, ...]) -> list[str]:
    lines = [f"## Families ({len(families)})"]
    if not families:
        return [*lines, "", NONE_LINE]
    for family in families:
        here = ", ".join(f"`{one}`" for one in family.here)
        lines += [
            "",
            f"### {family.title} · `{family.id}`",
            "",
            f"**What the family guarantees.** {family.guarantee}",
            "",
            f"Its rules in this file: {here}.",
            "",
            f"**Its other rules ({len(family.others)})**",
        ]
        for other in family.others:
            paths = ", ".join(f"`{one}`" for one in other.paths) or "no realization recorded"
            name = f"{other.title.text}: " if other.title.given else ""
            lines += [f"- `{other.id}` {name}{other.statement}", f"  - Lives in: {paths}"]
        if not family.others:
            lines.append(f"- {NONE_LINE}")
    return lines


__all__ = [
    "NONE_LINE",
    "brief_markdown",
    "counted",
    "head_line",
    "rule_markdown",
    "tree_line",
]
