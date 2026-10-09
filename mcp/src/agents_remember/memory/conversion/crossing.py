"""The structural three-way merge of a crossing sync (MIK-R24 rule 8 steps 3 and 4).

Once every unconverted side is converted (step 2), the three converted trees merge file by file:

* a file only one side changed is taken from that side (a deletion included);
* **Markdown** merges three-way by line (``git merge-file``);
* **sidecars** merge by key: ``references`` by number, ``realizes``/``proves`` by entry ``id``, every
  other field by name; **records** and other JSON documents merge field by field.

**Items.** A reference, an entry or a record field is one item. Its *mechanical* fields are an
anchor's ``blob``, ``content`` and line numbers; everything else is authored. For an item both sides
changed:

* only one side changed it at all: that side's item, whole;
* one side changed authored fields and the other only mechanical ones: the authored side's, whole;
* both changed only mechanical fields: the incoming side's;
* deleted on one side and changed only mechanically on the other: deleted;
* authored fields changed differently on both sides (or a reference number both sides added with
  different targets): a **conflict** for the curator. A record conflict is resolved through the
  writer, whose ``revision`` becomes one more than the higher side's.

No anchor is re-resolved; staleness is reported later by MIK-R03 wherever the knowledge is read.

**Reference alignment (MIK-R66).** Before the merge, every card both lines changed has its
reference numbers aligned: a reference the incoming line kept stays on the incoming number, and
the syncing line's references take those numbers where they match, or fresh numbers above every
side's highest otherwise; the prose ``[n]`` markers move with the same map. The merge itself
renumbers where needed, so a curator never renumbers a reference by hand to clear a conflict.
"""

from __future__ import annotations

import json
import re
import tempfile
from bisect import bisect_left, bisect_right
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from itertools import pairwise
from pathlib import Path
from typing import Any, Final

from agents_remember.kernel.git_command import merge_file_bytes, run_git
from agents_remember.memory_quality.knowledge_validator.markers import (
    find_marker_positions,
    find_markers,
    remap_markers,
)
from agents_remember.models.knowledge_files.canonical import canonical_text, parse_json

_MECHANICAL_ANCHOR: Final = frozenset({"blob", "content"})
_ENTRY_LISTS: Final = ("realizes", "proves")
_ABSENT: Final = object()


CONFLICT_MARKER: Final = "crossing-conflict"
"""The key of the explicit conflict marker a conflicted JSON item or file is written as.

No knowledge-file model admits it, so the validator refuses the file (MIK-R22 rule 1) until the
curator replaces the marker with the resolved item: a conflicted item can never be committed as a
silent "ours".
"""


@dataclass(frozen=True)
class Conflict:
    """One unresolved item: the file, the item's key, why, and the three sides' values."""

    path: str
    item: str
    reason: str
    sides: tuple[Any, Any, Any] = (None, None, None)

    def to_document(self) -> dict[str, Any]:
        document: dict[str, Any] = {"path": self.path, "item": self.item, "reason": self.reason}
        for name, value in zip(("base", "own", "incoming"), self.sides, strict=True):
            if value is not None and value is not _ABSENT:
                document[name] = value
        return document


def _conflicted(
    conflicts: list[Conflict], path: str, item: str, reason: str, sides: tuple[Any, Any, Any]
) -> dict[str, Any]:
    """Record one conflict and return the marker written in its place."""

    conflict = Conflict(path, item, reason, sides)
    conflicts.append(conflict)
    marker = conflict.to_document()
    del marker["path"]
    return {CONFLICT_MARKER: marker}


@dataclass
class AlignmentReport:
    """Rule 8 counts of one structural merge's reference alignment (MIK-R66).

    ``aligned_cards`` counts the cards whose numbers the alignment changed;
    ``renumbered_references`` the own and base reference entries that moved;
    ``evidence_lines_kept`` the evidence lines kept from both sides of an
    empty-base conflict region; ``skipped_cards`` the cards merged without
    alignment, each with its rule 7 reason.
    """

    aligned_cards: int = 0
    renumbered_references: int = 0
    evidence_lines_kept: int = 0
    skipped_cards: list[dict[str, str]] = field(default_factory=list)


@dataclass
class MergeResult:
    """The merged files (``None`` = deleted), the conflicts, and per-file provenance counts."""

    files: dict[str, bytes | None] = field(default_factory=dict)
    conflicts: list[Conflict] = field(default_factory=list)
    taken: Counter[str] = field(default_factory=Counter)
    alignment: AlignmentReport = field(default_factory=AlignmentReport)

    @property
    def conflicted_paths(self) -> tuple[str, ...]:
        return tuple(sorted({conflict.path for conflict in self.conflicts}))


def authored(value: Any) -> Any:
    """``value`` with every mechanical anchor field removed (blob, content, line numbers)."""

    if isinstance(value, dict):
        stripped: dict[str, Any] = {}
        is_anchor = "locator" in value and "blob" in value
        for key, inner in value.items():
            if is_anchor and key in _MECHANICAL_ANCHOR:
                continue
            if is_anchor and key == "locator" and inner.get("kind") == "line_range":
                stripped[key] = {"kind": "line_range"}
                continue
            stripped[key] = authored(inner)
        return stripped
    if isinstance(value, list):
        return [authored(inner) for inner in value]
    return value


def merge_item(base: Any, ours: Any, theirs: Any) -> tuple[Any, str | None]:
    """Merge one item (``_ABSENT`` for a missing one); return (result, conflict reason or None)."""

    if ours == theirs:
        return ours, None
    if base == ours:
        return theirs, None
    if base == theirs:
        return ours, None
    if _ABSENT in (base, ours, theirs):
        return _absent_on_a_side(base, ours, theirs)
    return _both_changed(base, ours, theirs)


def _absent_on_a_side(base: Any, ours: Any, theirs: Any) -> tuple[Any, str | None]:
    """Added on both sides, or deleted on one: equal authored content merges, anything else conflicts."""

    if base is _ABSENT:
        if authored(ours) == authored(theirs):
            return theirs, None
        return ours, "added on both sides with different content"
    kept = theirs if ours is _ABSENT else ours
    if authored(kept) == authored(base):
        return _ABSENT, None
    return kept, "deleted on one side and changed on the other"


def _both_changed(base: Any, ours: Any, theirs: Any) -> tuple[Any, str | None]:
    """An item both sides changed: authored beats mechanical; only authored against authored conflicts."""

    ours_authored = authored(ours) != authored(base)
    theirs_authored = authored(theirs) != authored(base)
    if ours_authored != theirs_authored:
        return (ours if ours_authored else theirs), None
    if not ours_authored or authored(ours) == authored(theirs):
        return theirs, None
    return ours, "authored fields changed differently on both sides"


def _keyed(document: Any, key: str) -> dict[str, Any]:
    if not isinstance(document, dict):
        return {}
    value = document.get(key)
    if key in _ENTRY_LISTS:
        return {str(item["id"]): item for item in value or []}
    return dict(value or {})


def _merge_json(
    path: str, base: Any, ours: Any, theirs: Any, conflicts: list[Conflict]
) -> dict[str, Any]:
    """Merge three JSON documents by key: keyed collections by item, other fields by name."""

    merged: dict[str, Any] = {}
    documents = [one for one in (base, ours, theirs) if isinstance(one, dict)]
    keys = sorted({key for document in documents for key in document})
    for key in keys:
        if key == "references" or key in _ENTRY_LISTS:
            items = _merge_collection(path, key, (base, ours, theirs), conflicts)
            if key in _ENTRY_LISTS:
                if items or any(isinstance(one, dict) and key in one for one in (ours, theirs)):
                    merged[key] = [items[identifier] for identifier in sorted(items)]
            else:
                merged[key] = {number: items[number] for number in sorted(items, key=int)}
            continue
        sides = (_field(base, key), _field(ours, key), _field(theirs, key))
        value, reason = merge_item(*sides)
        if reason is not None:
            value = _conflicted(conflicts, path, key, reason, sides)
        if value is not _ABSENT:
            merged[key] = value
    return merged


def _field(document: Any, key: str) -> Any:
    return document.get(key, _ABSENT) if isinstance(document, dict) else _ABSENT


def _merge_collection(
    path: str, key: str, documents: tuple[Any, Any, Any], conflicts: list[Conflict]
) -> dict[str, Any]:
    sides = [_keyed(document, key) for document in documents]
    merged: dict[str, Any] = {}
    for identifier in sorted({name for side in sides for name in side}):
        values = (
            sides[0].get(identifier, _ABSENT),
            sides[1].get(identifier, _ABSENT),
            sides[2].get(identifier, _ABSENT),
        )
        value, reason = merge_item(*values)
        if reason is not None:
            label = f"{key}.{identifier}"
            if key == "references" and identifier not in sides[0]:
                reason = (
                    f"reference [{identifier}] added on both sides with different targets; "
                    "renumber one side"
                )
            value = _conflicted(conflicts, path, label, reason, values)
        if value is not _ABSENT:
            merged[identifier] = value
    return merged


def _authored_signature(reference: Any) -> str:
    """The authored identity of one reference: note and targets, no anchor mechanics."""

    return canonical_text(authored(reference))


def _numeric(number: str) -> bool:
    try:
        int(number)
    except ValueError:
        return False
    return True


class _LiveUnpaired:
    """Unpaired numbers of one sorted side with logarithmic range count/locate/remove.

    A Fenwick tree over the side's number order: each gap interval counts and
    locates its live numbers without scanning already-paired populations, so
    overlapping intervals stay sorting-dominated. Removals mirror gap pairs.
    """

    def __init__(self, numbers: list[str], paired: set[str]) -> None:
        self.numbers = numbers
        self.values = [int(number) for number in numbers]
        self.positions = {number: index for index, number in enumerate(numbers)}
        self.tree = [0] * (len(numbers) + 1)
        for number in numbers:
            if number not in paired:
                self._add(self.positions[number] + 1, 1)

    def _add(self, index: int, delta: int) -> None:
        while index < len(self.tree):
            self.tree[index] += delta
            index += index & -index

    def _prefix(self, end: int) -> int:
        total = 0
        while end > 0:
            total += self.tree[end]
            end -= end & -end
        return total

    def span(self, low: str | None, high: str | None) -> tuple[int, int]:
        """Index bounds of the numbers strictly between the neighbours."""
        start = bisect_right(self.values, int(low)) if low is not None else 0
        end = bisect_left(self.values, int(high)) if high is not None else len(self.values)
        return start, end

    def count(self, start: int, end: int) -> int:
        """Live numbers in the index span."""
        return self._prefix(end) - self._prefix(start)

    def lone(self, start: int) -> str:
        """The single live number at or after the index; exactly one must be live in range."""
        rank = self._prefix(start) + 1
        index = 0
        step = 1 << (len(self.tree).bit_length() - 1)
        while step:
            following = index + step
            if following < len(self.tree) and self.tree[following] < rank:
                index = following
                rank -= self.tree[following]
            step >>= 1
        return self.numbers[index]

    def remove(self, number: str) -> None:
        """Mark one number paired."""
        self._add(self.positions[number] + 1, -1)


def _pair_with_base(
    base_references: Mapping[str, Any], side_references: Mapping[str, Any]
) -> dict[str, str]:
    """Pair base numbers to side numbers: unique content (a), same number (b), gap (c).

    Test a pairs a reference occurring exactly once on each side; test b pairs an
    unpaired same number; test c pairs the only unpaired reference of each side
    between the same two paired neighbours, in number order (the start and the end
    of the list count as neighbours). Each reference pairs at most once.
    """
    pairs: dict[str, str] = {}
    base_signatures: dict[str, list[str]] = {}
    for number, reference in base_references.items():
        base_signatures.setdefault(_authored_signature(reference), []).append(number)
    side_signatures: dict[str, list[str]] = {}
    for number, reference in side_references.items():
        side_signatures.setdefault(_authored_signature(reference), []).append(number)
    for signature, bases in base_signatures.items():
        sides = side_signatures.get(signature, [])
        if len(bases) == 1 and len(sides) == 1:
            pairs[bases[0]] = sides[0]
    paired_sides = set(pairs.values())
    for number in base_references:
        if number in side_references and number not in pairs and number not in paired_sides:
            pairs[number] = number
            paired_sides.add(number)
    ordered = sorted(pairs.items(), key=lambda pair: int(pair[0]))
    edges: list[tuple[str | None, str | None]] = [(None, None), *ordered, (None, None)]
    live_bases = _LiveUnpaired(sorted(base_references, key=int), set(pairs))
    live_sides = _LiveUnpaired(sorted(side_references, key=int), set(pairs.values()))
    for (low_base, low_side), (high_base, high_side) in pairwise(edges):
        base_span = live_bases.span(low_base, high_base)
        if live_bases.count(*base_span) != 1:
            continue
        side_span = live_sides.span(low_side, high_side)
        if live_sides.count(*side_span) != 1:
            continue
        lone_base = live_bases.lone(base_span[0])
        lone_side = live_sides.lone(side_span[0])
        pairs[lone_base] = lone_side
        paired_sides.add(lone_side)
        live_bases.remove(lone_base)
        live_sides.remove(lone_side)
    return pairs


def _pair_direct(
    own_references: Mapping[str, Any], incoming_references: Mapping[str, Any]
) -> dict[str, str]:
    """Pair partnerless own and incoming references by unique content (test a only)."""

    return _pair_signatures(own_references, incoming_references)


def _pair_signatures(first: Mapping[str, Any], second: Mapping[str, Any]) -> dict[str, str]:
    pairs: dict[str, str] = {}
    first_signatures: dict[str, list[str]] = {}
    for number, reference in first.items():
        first_signatures.setdefault(_authored_signature(reference), []).append(number)
    second_signatures: dict[str, list[str]] = {}
    for number, reference in second.items():
        second_signatures.setdefault(_authored_signature(reference), []).append(number)
    for signature, owned in first_signatures.items():
        fellows = second_signatures.get(signature, [])
        if len(owned) == 1 and len(fellows) == 1:
            pairs[owned[0]] = fellows[0]
    return pairs


@dataclass(frozen=True)
class ReferencePairing:
    """One card's reference pairs: base numbers to own and incoming numbers (MIK-R66 rule 2)."""

    base_to_own: Mapping[str, str]
    base_to_incoming: Mapping[str, str]
    direct: Mapping[str, str]


def _partner_numbers(pairing: ReferencePairing) -> tuple[dict[str, str], dict[str, str]]:
    """Numbers taken from an incoming partner; the rest is numbered fresh later."""

    base_map: dict[str, str] = {}
    own_map: dict[str, str] = dict(pairing.direct)
    for base, incoming in pairing.base_to_incoming.items():
        base_map[base] = incoming
        own = pairing.base_to_own.get(base)
        if own is not None:
            own_map[own] = incoming
    return base_map, own_map


def _fresh_groups(
    base_references: Mapping[str, Any],
    own_references: Mapping[str, Any],
    base_map: Mapping[str, str],
    own_map: Mapping[str, str],
    base_to_own: Mapping[str, str],
) -> list[tuple[str | None, str | None]]:
    """Groups still needing one fresh number: own's groups first, then base-only ones."""

    groups: list[tuple[str | None, str | None]] = []
    for base in base_references:
        if base in base_map:
            continue
        own = base_to_own.get(base)
        if own in own_map:
            own = None  # its direct partner already numbered it; the base goes alone
        groups.append((base, own))
    for own in own_references:
        if own not in own_map and own not in base_to_own.values():
            groups.append((None, own))
    own_ordered = sorted(
        (group for group in groups if group[1] is not None), key=lambda group: int(group[1] or 0)
    )
    base_ordered = sorted(
        (group for group in groups if group[1] is None), key=lambda group: int(group[0] or 0)
    )
    return [*own_ordered, *base_ordered]


def _assign_numbers(
    base_references: Mapping[str, Any],
    own_references: Mapping[str, Any],
    incoming_references: Mapping[str, Any],
    pairing: ReferencePairing,
) -> tuple[dict[str, str], dict[str, str]] | None:
    """Old-to-new numbers for the base and own sides; None when a number would repeat.

    Incoming keeps every number it has. A base or own reference with an incoming
    partner takes the partner's number. Every other group takes one fresh number:
    first each group represented on own, in own's number order (one number per
    group, MIK-R66 rule 2), then the remaining base-only groups in base's number
    order. Fresh numbers start above the highest number of all three sides.
    """

    base_map, own_map = _partner_numbers(pairing)
    highest = max(
        [
            int(number)
            for references in (base_references, own_references, incoming_references)
            for number in references
        ],
        default=0,
    )
    fresh = highest + 1
    for base, own in _fresh_groups(
        base_references, own_references, base_map, own_map, pairing.base_to_own
    ):
        number = str(fresh)
        fresh += 1
        if base is not None:
            base_map[base] = number
        if own is not None:
            own_map[own] = number
    for side in (base_map, own_map):
        if len(set(side.values())) != len(side):
            return None
    return base_map, own_map


def _card_references(document: Any) -> dict[str, Any]:
    if not isinstance(document, dict):
        raise ValueError("a card sidecar is not a JSON document")
    references = document.get("references", {})
    if not isinstance(references, dict):
        raise ValueError("a card sidecar holds 'references' that is not a map")
    for number in references:
        if not isinstance(number, str) or not _numeric(number):
            raise ValueError(f"a card sidecar holds reference number {number!r}")
    return references


def _marker_numbers(markdown: bytes | None) -> set[str] | None:
    """The marker numbers of Markdown, or None when it is not decodable text."""

    if markdown is None:
        return set()
    try:
        return {marker.number for marker in find_markers(markdown.decode("utf-8"))}
    except UnicodeDecodeError:
        return None


def _mismatch_reason(side: str, markers: set[str], references: Mapping[str, Any]) -> str | None:
    without_reference = sorted(markers - set(references), key=int)
    without_marker = sorted(set(references) - markers, key=int)
    if not without_reference and not without_marker:
        return None
    parts = []
    if without_reference:
        parts.append(
            f"a marker without a reference ({', '.join(f'[{n}]' for n in without_reference)})"
        )
    if without_marker:
        parts.append(
            f"a reference without a marker ({', '.join(f'[{n}]' for n in without_marker)})"
        )
    return f"the {side} side would be renumbered but has {' and '.join(parts)}"


def _align_card(
    documents: tuple[Any, Any, Any],
    markdowns: tuple[bytes | None, bytes | None, bytes | None],
) -> tuple[dict[str, Any], dict[str, Any], bytes | None, bytes | None, int] | str:
    """Align one card's reference numbers (MIK-R66 rules 1-4, 7).

    Returns the rewritten base and own sidecars, their rewritten Markdown (None
    where that side holds none), and how many reference entries moved; or the
    rule 7 reason to merge the card without alignment, exactly as today.
    """

    base_refs = _card_references(documents[0])
    own_refs = _card_references(documents[1])
    incoming_refs = _card_references(documents[2])
    base_to_own = _pair_with_base(base_refs, own_refs)
    base_to_incoming = _pair_with_base(base_refs, incoming_refs)
    partnered_own = {own for base, own in base_to_own.items() if base in base_to_incoming}
    partnered_incoming = {
        incoming for base, incoming in base_to_incoming.items() if base in base_to_own
    }
    direct = _pair_direct(
        {n: r for n, r in own_refs.items() if n not in partnered_own},
        {n: r for n, r in incoming_refs.items() if n not in partnered_incoming},
    )
    assigned = _assign_numbers(
        base_refs,
        own_refs,
        incoming_refs,
        ReferencePairing(base_to_own, base_to_incoming, direct),
    )
    if assigned is None:
        return "the pairing would give one number to two references of the card"
    base_map, own_map = assigned
    for side, number_map, references, markdown in (
        ("own", own_map, own_refs, markdowns[1]),
        ("base", base_map, base_refs, markdowns[0]),
    ):
        if all(old == new for old, new in number_map.items()):
            continue
        markers = _marker_numbers(markdown)
        if markers is None:
            return f"the {side} side would be renumbered but its Markdown is not decodable text"
        reason = _mismatch_reason(side, markers, references)
        if reason is not None:
            return reason
    base_document = {**documents[0], "references": {base_map[n]: r for n, r in base_refs.items()}}
    own_document = {**documents[1], "references": {own_map[n]: r for n, r in own_refs.items()}}
    renumbered = sum(1 for old, new in own_map.items() if old != new)
    renumbered += sum(1 for old, new in base_map.items() if old != new)
    base_markdown = (
        remap_markers(markdowns[0].decode("utf-8"), base_map).encode("utf-8")
        if markdowns[0] is not None
        else None
    )
    own_markdown = (
        remap_markers(markdowns[1].decode("utf-8"), own_map).encode("utf-8")
        if markdowns[1] is not None
        else None
    )
    return base_document, own_document, base_markdown, own_markdown, renumbered


def _card_inputs(
    base: Mapping[str, bytes],
    ours: Mapping[str, bytes],
    theirs: Mapping[str, bytes],
    path: str,
    markdown: str,
) -> tuple[tuple[Any, Any, Any], tuple[bytes | None, bytes | None, bytes | None]] | None:
    """A both-changed reference card's sidecars and Markdown, or None when it is not one.

    A card both sides added (absent base, both sides present and different) aligns
    against an empty base reference population (MIK-R66 rule 1): incoming keeps its
    numbers, own takes partner numbers or fresh ones, with the same rule 7 checks.
    """

    before, mine, incoming = (tree.get(path) for tree in (base, ours, theirs))
    if before is None:
        if mine is None or incoming is None or mine == incoming:
            return None
        empty_base: tuple[Any, Any, Any] = (
            {"references": {}},
            parse_json(mine.decode("utf-8")),
            parse_json(incoming.decode("utf-8")),
        )
        if not all(
            isinstance(document, dict) and isinstance(document.get("references"), dict)
            for document in empty_base[1:]
        ):
            return None
        return empty_base, (
            None,
            ours.get(markdown),
            theirs.get(markdown),
        )
    if mine is None or incoming is None or before in (mine, incoming):
        return None
    documents = [parse_json(one.decode("utf-8")) for one in (before, mine, incoming)]
    if not any(
        isinstance(document, dict) and isinstance(document.get("references"), dict)
        for document in documents
    ):
        return None
    return (documents[0], documents[1], documents[2]), (
        base.get(markdown),
        ours.get(markdown),
        theirs.get(markdown),
    )


def _align_cards(
    base: Mapping[str, bytes],
    ours: Mapping[str, bytes],
    theirs: Mapping[str, bytes],
    result: MergeResult,
) -> tuple[dict[str, bytes], dict[str, bytes], set[str]]:
    """Renumber both-changed cards before the merge (MIK-R66 rule 1).

    Returns the adjusted base and own trees with every aligned card's sidecar
    and Markdown rewritten, and the Markdown paths merged without alignment
    under rule 7, exactly as today.
    """

    adjusted_base = dict(base)
    adjusted_ours = dict(ours)
    skipped_markdown: set[str] = set()
    for path in sorted(set(base) | set(ours) | set(theirs)):
        if not path.endswith(".json"):
            continue
        markdown = f"{path[: -len('.json')]}.md"
        if not any(markdown in tree for tree in (base, ours, theirs)):
            continue
        inputs = _card_inputs(base, ours, theirs, path, markdown)
        if inputs is None:
            continue
        outcome = _align_card(*inputs)
        if isinstance(outcome, str):
            result.alignment.skipped_cards.append({"path": path, "reason": outcome})
            skipped_markdown.add(markdown)
            continue
        base_document, own_document, base_markdown, own_markdown, renumbered = outcome
        if not renumbered:
            continue
        if path in base:
            adjusted_base[path] = _dump(base_document)
        adjusted_ours[path] = _dump(own_document)
        if base_markdown is not None:
            adjusted_base[markdown] = base_markdown
        if own_markdown is not None:
            adjusted_ours[markdown] = own_markdown
        result.alignment.aligned_cards += 1
        result.alignment.renumbered_references += renumbered
    return adjusted_base, adjusted_ours, skipped_markdown


_EVIDENCE_LINE: Final = re.compile(r"[ \t]*[-*+][ \t]+.*\[[0-9]+\][ \t]*$")
_TRAILING_MARKER: Final = re.compile(r"\[([0-9]+)\][ \t]*$")


def _is_evidence_line(line: str) -> bool:
    """A list line whose trailing ``[n]`` is a real marker (MIK-R66 rule 6).

    Fragment-local: the trailing bracket at its exact position must itself be a
    real marker, so an escaped or code-span trailing bracket does not qualify
    even when a real marker sits earlier on the line. Fenced-block context comes
    from the document table (:func:`_doc_evidence_table`), not from this check.
    """

    if _EVIDENCE_LINE.match(line) is None:
        return False
    match = _TRAILING_MARKER.search(line)
    if match is None:
        return False
    head, number = line[: match.start()], match.group(1)
    before = [(marker.number) for marker in find_markers(line)]
    after = [(marker.number) for marker in find_markers(head)]
    return len(before) == len(after) + 1 and before.count(number) == after.count(number) + 1


def _doc_evidence_table(text: str) -> dict[str, bool]:
    """Each document line text: whether every occurrence ends in a real marker.

    The full-document marker grammar supplies fences, multiline code spans and
    escapes, so a marker-shaped line inside a fenced block never qualifies. A
    line text shared by an evidence line and a non-evidence line maps to False:
    automatic resolution must not broaden past the evidence-only boundary.
    """

    lines = text.splitlines()
    real = {(line, offset, number) for line, offset, number in find_marker_positions(text)}
    standing: dict[str, bool] = {}
    for index, line in enumerate(lines, start=1):
        if line not in standing:
            standing[line] = True
        if not standing[line]:
            continue
        match = _TRAILING_MARKER.search(line)
        standing[line] = (
            _EVIDENCE_LINE.match(line) is not None
            and match is not None
            and (index, match.start(), match.group(1)) in real
        )
    return standing


def _marker_line(lines: list[str], start: int, marker: str) -> int:
    """First line at or after ``start`` opening a conflict section, or -1."""

    for at in range(start, len(lines)):
        if lines[at].startswith(marker) and (marker != "=======" or lines[at].strip() == marker):
            return at
    return -1


def _split_regions(
    lines: list[str], *, diff3: bool
) -> list[tuple[int, int, list[str], list[str], list[str]]] | None:
    """Each conflict region as (start, end, ours lines, base lines, theirs lines).

    Plain merges carry no base section; ``--diff3`` merges always do, empty or
    not. None when a region is malformed.
    """

    regions: list[tuple[int, int, list[str], list[str], list[str]]] = []
    index = 0
    while index < len(lines):
        if not lines[index].startswith("<<<<<<< "):
            index += 1
            continue
        if diff3:
            middle = _marker_line(lines, index + 1, "||||||| ")
            divider = _marker_line(lines, middle + 1, "=======") if middle >= 0 else -1
        else:
            middle = -1
            divider = _marker_line(lines, index + 1, "=======")
        end = _marker_line(lines, divider + 1, ">>>>>>> ") if divider >= 0 else -1
        if divider < 0 or end < 0:
            return None
        if middle >= 0:
            regions.append(
                (
                    index,
                    end,
                    lines[index + 1 : middle],
                    lines[middle + 1 : divider],
                    lines[divider + 1 : end],
                )
            )
        else:
            regions.append((index, end, lines[index + 1 : divider], [], lines[divider + 1 : end]))
        index = end + 1
    return regions


def _differing_lines_ok(
    theirs_block: list[str],
    ours_block: list[str],
    theirs_table: Mapping[str, bool],
    ours_table: Mapping[str, bool],
) -> bool:
    """Every non-blank differing line ends in a real marker of its own side's document."""

    for line in (*theirs_block, *ours_block):
        if not line.strip():
            continue
        key = line.rstrip("\n")
        if line in theirs_block and not theirs_table.get(key, False):
            return False
        if line in ours_block and not ours_table.get(key, False):
            return False
    return True


def _emit_lines(resolved: list[str], seen: set[str], block: list[str], evidence: bool) -> int:
    """Append unseen block lines once, counting the non-blank evidence ones."""

    kept = 0
    for line in block:
        if line in seen:
            continue
        seen.add(line)
        resolved.append(line)
        if evidence and line.strip():
            kept += 1
    return kept


def _evidence_resolution(
    ours_part: list[str],
    base_part: list[str],
    theirs_part: list[str],
    ours_table: Mapping[str, bool],
    theirs_table: Mapping[str, bool],
) -> tuple[list[str], int] | None:
    """Incoming lines then own lines with duplicates dropped, and lines kept.

    None when the region stays a conflict: a non-blank base line, or a differing
    own or incoming line that is no evidence line. Lines identical on both sides
    are shared context and stay once in place; only differing runs must be
    evidence lines, each judged in its own side's document grammar. A line text
    unknown to its side's table never qualifies.
    """

    if not _is_empty(base_part):
        return None
    matcher = SequenceMatcher(None, ours_part, theirs_part, autojunk=False)
    resolved: list[str] = []
    kept = 0
    seen: set[str] = set()
    for tag, ours_start, ours_end, theirs_start, theirs_end in matcher.get_opcodes():
        theirs_block = theirs_part[theirs_start:theirs_end]
        ours_block = ours_part[ours_start:ours_end]
        if tag == "equal":
            kept += _emit_lines(resolved, seen, ours_block, False)
            continue
        if not _differing_lines_ok(theirs_block, ours_block, theirs_table, ours_table):
            return None
        kept += _emit_lines(resolved, seen, theirs_block, True)
        kept += _emit_lines(resolved, seen, ours_block, True)
    return resolved, kept


def _resolve_evidence_regions(
    merged: bytes,
    diff3: str | None,
    ours_table: Mapping[str, bool],
    theirs_table: Mapping[str, bool],
) -> tuple[bytes, bool, int]:
    """Keep both sides' evidence lines where the base part is empty (rule 6).

    The plain merge carries no base section, so the base span of every region
    comes from the same merge in ``--diff3`` style. The tables judge each
    differing line in its own side's document grammar (fences, code spans,
    escapes); lines identical on both sides stay once as shared context.
    Returns the bytes, whether no conflict region remains, and how many
    evidence lines were kept from both sides. A region whose base part holds
    anything but blank lines, or whose differing own or incoming lines hold
    anything but evidence lines, stays a conflict, exactly as today -- as does
    everything when the two presentations disagree.
    """

    try:
        text = merged.decode("utf-8")
    except UnicodeDecodeError:
        return merged, False, 0
    if diff3 is None:
        return merged, False, 0
    lines = text.splitlines(keepends=True)
    normal = _split_regions(lines, diff3=False)
    shaped = _split_regions(diff3.splitlines(keepends=True), diff3=True)
    if normal is None or shaped is None or len(normal) != len(shaped):
        return merged, False, 0
    out: list[str] = []
    kept = 0
    cursor = 0
    resolved_all = True
    for region, shaped_region in zip(normal, shaped, strict=True):
        start, end, ours_part, _, theirs_part = region
        _, _, shaped_ours, base_part, shaped_theirs = shaped_region
        if not _is_subsequence(
            _non_blank(ours_part), _non_blank(shaped_ours)
        ) or not _is_subsequence(_non_blank(theirs_part), _non_blank(shaped_theirs)):
            return merged, False, 0
        out.extend(lines[cursor:start])
        cursor = end + 1
        resolved = _evidence_resolution(ours_part, base_part, theirs_part, ours_table, theirs_table)
        if resolved is None:
            resolved_all = False
            out.extend(lines[start : end + 1])
        else:
            lines_kept, count = resolved
            out.extend(lines_kept)
            kept += count
    out.extend(lines[cursor:])
    try:
        return "".join(out).encode("utf-8"), resolved_all, kept
    except UnicodeEncodeError:
        return merged, False, 0


def _is_empty(part: list[str]) -> bool:
    return all(not line.strip() for line in part)


def _is_subsequence(needles: list[str], haystack: list[str]) -> bool:
    """Every needle appears in the haystack in order (not necessarily adjacent)."""

    cursor = 0
    for needle in needles:
        while cursor < len(haystack) and haystack[cursor] != needle:
            cursor += 1
        if cursor == len(haystack):
            return False
        cursor += 1
    return True


def _non_blank(part: list[str]) -> list[str]:
    return [line for line in part if line.strip()]


def _merge_markdown_diff3(
    repository: Path, base: bytes | None, ours: bytes, theirs: bytes
) -> str | None:
    """The same three-way merge in ``--diff3`` style, for rule 6 base analysis only."""

    try:
        with tempfile.TemporaryDirectory(prefix="ar-crossing-") as scratch:
            root = Path(scratch)
            paths = []
            for name, data in (("ours", ours), ("base", base or b""), ("theirs", theirs)):
                (root / name).write_bytes(data)
                paths.append(root / name)
            result = run_git(
                repository,
                [
                    "merge-file",
                    "--diff3",
                    "-p",
                    "-L",
                    "ours",
                    "-L",
                    "base",
                    "-L",
                    "theirs",
                    paths[0].as_posix(),
                    paths[1].as_posix(),
                    paths[2].as_posix(),
                ],
            )
    except (OSError, ValueError):
        return None
    return result.stdout


def _merge_markdown(
    repository: Path, base: bytes | None, ours: bytes, theirs: bytes
) -> tuple[bytes, bool]:
    """Three-way line merge through ``git merge-file``; returns (merged bytes, conflicted)."""

    with tempfile.TemporaryDirectory(prefix="ar-crossing-") as scratch:
        root = Path(scratch)
        paths = []
        for name, data in (("ours", ours), ("base", base or b""), ("theirs", theirs)):
            (root / name).write_bytes(data)
            paths.append(root / name)
        merged, conflicts = merge_file_bytes(
            repository, paths[0], paths[1], paths[2], ("ours", "base", "theirs")
        )
    return merged, conflicts > 0


def _merge_json_file(
    path: str, versions: tuple[bytes | None, bytes | None, bytes | None], result: MergeResult
) -> None:
    """A JSON document both sides changed: merged by key, or kept whole with a conflict."""

    before, mine, incoming = (
        parse_json(one.decode("utf-8")) if one else _ABSENT for one in versions
    )
    conflicts: list[Conflict] = []
    if mine is _ABSENT or incoming is _ABSENT:
        value, reason = merge_item(before, mine, incoming)
        if reason is not None:
            value = _conflicted(conflicts, path, "(file)", reason, (before, mine, incoming))
        result.files[path] = None if value is _ABSENT else _dump(value)
    else:
        result.files[path] = _dump(_merge_json(path, before, mine, incoming, conflicts))
    result.conflicts.extend(conflicts)


def _merge_text_file(
    path: str,
    versions: tuple[bytes | None, bytes | None, bytes | None],
    result: MergeResult,
    repository: Path,
    evidence_regions: bool = True,
) -> None:
    """A Markdown (or other text) file both sides changed: a three-way line merge."""

    before, mine, incoming = versions
    if mine is None or incoming is None:
        result.files[path] = mine if incoming is None else incoming
        result.conflicts.append(
            Conflict(path, "(file)", "deleted on one side and changed on the other")
        )
        return
    merged_text, conflicted = _merge_markdown(repository, before, mine, incoming)
    if conflicted and evidence_regions:
        analysis = _merge_markdown_diff3(repository, before, mine, incoming)
        try:
            ours_table = _doc_evidence_table(mine.decode("utf-8"))
            theirs_table = _doc_evidence_table(incoming.decode("utf-8"))
        except UnicodeDecodeError:
            ours_table = {}
            theirs_table = {}
        merged_text, resolved, kept = _resolve_evidence_regions(
            merged_text, analysis, ours_table, theirs_table
        )
        if kept:
            result.alignment.evidence_lines_kept += kept
        conflicted = not resolved
    result.files[path] = merged_text
    if conflicted:
        result.conflicts.append(Conflict(path, "(lines)", "lines changed on both sides"))


def merge_trees(
    base: Mapping[str, bytes],
    ours: Mapping[str, bytes],
    theirs: Mapping[str, bytes],
    *,
    repository: Path,
) -> MergeResult:
    """Merge three converted knowledge/onboarding trees structurally (rule 8 steps 3 and 4)."""

    result = MergeResult()
    base, ours, skipped = _align_cards(base, ours, theirs, result)
    for path in sorted(set(base) | set(ours) | set(theirs)):
        versions = (base.get(path), ours.get(path), theirs.get(path))
        before, mine, incoming = versions
        if incoming in (mine, before):
            result.files[path] = mine
            result.taken["same" if mine == incoming else "own"] += 1
            continue
        if before == mine:
            result.files[path] = incoming
            result.taken["incoming"] += 1
            continue
        result.taken["merged"] += 1
        if path.endswith(".json") and all(one is None or _is_json(one) for one in versions):
            _merge_json_file(path, versions, result)
        else:
            _merge_text_file(path, versions, result, repository, path not in skipped)
    return result


def _is_json(data: bytes) -> bool:
    try:
        json.loads(data)
    except ValueError:
        return False
    return True


def _dump(document: Any) -> bytes:
    return canonical_text(document).encode("utf-8")
