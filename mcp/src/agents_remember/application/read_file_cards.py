"""Card record states for ``read_ar_files`` (MIK-R109 rule 1).

A read names where a thing is and does not carry it: for each requested
source path the tool answers the governing card's location, the count of the
card's records by state, and — only for records whose state asks the caller
to act — the text ID, kind, state and card heading of each.

**States are computed as today** (packet Preservation): the one state
function (:mod:`agents_remember.application.knowledge_currentness.state`)
over the family-complete leaf selection of the path
(:func:`agents_remember.application.knowledge_leaf.pages.prepare_leaf`) at
the code tree the caller sees. What this module adds is the projection to
names: counts by state, the non-current records, and the card heading that
covers each one's differing anchor.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agents_remember.application.knowledge_currentness.observe import CodeTree
from agents_remember.application.knowledge_currentness.state import (
    INVARIANT_STATES,
    InvariantState,
)
from agents_remember.application.knowledge_leaf.pages import (
    LeafRequest,
    PreparedLeaf,
    prepare_leaf,
)

# Packet rule 1: up to this many repository-relative paths per call.
READ_AR_FILES_MAX_PATHS = 20

# How the states were measured, while MIK-R73 rule 7 still measures at the
# checkout's HEAD tree. L73 keeps the field and moves the measurement to the
# working tree; the value then becomes "working-tree".
CODE_STATE_HEAD = "head"

# A record asks the caller to act when its computed state is not current
# (packet rule 1: stale, anchor not found, refused — today's vocabulary for
# those is stale, unverifiable and unrealized).
ACTION_STATES: tuple[InvariantState, ...] = tuple(
    state for state in INVARIANT_STATES if state != "current"
)

_GIT_TIMEOUT_SECONDS = 20
_HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.*?)\s*$")
# A citation's code target: `], <path>` or `], <path>:<lines>` at the end of
# a cit marker's target list, e.g. `cit:([`x`], mcp/src/a.py:464-479)`.
_CIT_TARGET_PATTERN = re.compile(r"\],\s*([A-Za-z0-9_][\w\-./]*\.\w[\w\-.]*)")


@dataclass(frozen=True)
class CardSelection:
    """What one card's records are selected from: the index and the code tree."""

    index_path: Path
    memory_tree_id: str
    index_state: str
    code_tree: CodeTree | None
    index_problems: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class NamedRecord:
    """One record whose state asks the caller to act (packet rule 1)."""

    id: str
    kind: str
    state: InvariantState
    heading: str
    title_fallback: bool = False


@dataclass(frozen=True)
class CardRecords:
    """A card's records by state, and the ones asking for action."""

    counts: dict[str, int]
    action_needed: tuple[NamedRecord, ...]
    problem: str | None = None
    index_complete: bool = True


_MAX_NAMED_INDEX_PROBLEMS = 5


def _index_problem_note(index_state: str, problems: tuple[tuple[str, str], ...]) -> str | None:
    """The partial-index qualifier with repair evidence, or ``None`` when complete.

    A partial index stays usable, but the answer must not present its
    surviving counts as complete: the qualifier names the skipped files
    with their reasons (path, reason) from the index owner, capped so the
    row holds its bound, and points at the integrity-check repair report
    beside the counts.
    """

    if index_state == "complete":
        return None
    named = "; ".join(f"{path} ({reason})" for path, reason in problems[:_MAX_NAMED_INDEX_PROBLEMS])
    if len(problems) > _MAX_NAMED_INDEX_PROBLEMS:
        named = f"{named}; and {len(problems) - _MAX_NAMED_INDEX_PROBLEMS} more"
    if not named:
        named = "no file details recorded"
    return (
        f"the knowledge index is {index_state or 'not complete'}: some records "
        f"could not be indexed ({named}), so counts may omit them; run "
        "knowledge_integrity_check for the skipped files, then reread"
    )


def head_tree_of(code_root: Path) -> str | None:
    """The ``HEAD^{tree}`` of the code checkout the caller sees, or ``None``."""

    try:
        completed = subprocess.run(
            ["git", "-C", str(code_root), "rev-parse", "HEAD^{tree}"],
            capture_output=True,
            text=True,
            check=False,
            timeout=_GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    tree = completed.stdout.strip()
    return tree if completed.returncode == 0 and tree else None


@dataclass(frozen=True)
class SectionAnchors:
    """What one card-heading resolution scores against.

    ``references`` is the card sidecar's ``references{}`` mapping for
    converted ``[n]`` heading resolution; ``own_path`` names the source a
    pathless own-file anchor cites; ``wanted`` carries recorded anchor
    identity (entry id, path, blob, content) when the caller holds it,
    otherwise the paths alone score path matches.
    """

    references: Mapping[str, Any] | None = None
    own_path: str | None = None
    wanted: list[tuple[Any, Any, Any, Any]] | None = None


def card_records_for_path(
    selection: CardSelection,
    path: str,
    card_text: str | None,
    card_title_fallback: str,
    *,
    anchors: SectionAnchors | None = None,
) -> CardRecords:
    """The record states of one governing card, measured as today.

    ``card_text`` is the card's Markdown when a card governs ``path`` (else
    ``None``, and headings fall back to the card title). ``anchors`` carries
    the sidecar's ``references{}`` mapping and the source a pathless
    own-file anchor names. A selection the tree does not hold is not an
    error: the card simply names no records.
    """

    references = anchors.references if anchors is not None else None
    own_path = anchors.own_path if anchors is not None else None
    prepared = prepare_leaf(
        LeafRequest(
            index_path=selection.index_path,
            memory_tree_id=selection.memory_tree_id,
            index_state=selection.index_state,
            path=path,
            code_tree=selection.code_tree,
        )
    )
    partial_note = _index_problem_note(selection.index_state, selection.index_problems)
    if not isinstance(prepared, PreparedLeaf):
        problem = f"the card's records could not be selected ({prepared.code}): {prepared.detail}"
        if partial_note is not None:
            problem = f"{problem}; {partial_note}"
        return CardRecords(
            counts={state: 0 for state in INVARIANT_STATES},
            action_needed=(),
            problem=problem,
            index_complete=partial_note is None,
        )
    currentness = prepared.currentness
    if currentness.problem is not None:
        problem = currentness.problem
        if partial_note is not None:
            problem = f"{problem}; {partial_note}"
        return CardRecords(
            counts={state: 0 for state in INVARIANT_STATES},
            action_needed=(),
            problem=problem,
            index_complete=partial_note is None,
        )
    counts = currentness.counts()
    action = tuple(
        NamedRecord(
            id=one.id,
            kind="invariant",
            state=one.state,
            heading=heading,
            title_fallback=fell_back,
        )
        for one in currentness.invariants
        if one.state in ACTION_STATES
        for heading, fell_back in (
            _heading_for(card_text, one, card_title_fallback, references, own_path),
        )
    )
    return CardRecords(
        counts=counts,
        action_needed=action,
        problem=partial_note,
        index_complete=partial_note is None,
    )


def card_section_for(
    card_text: str | None,
    code_paths: set[str],
    fallback: str,
    anchors: SectionAnchors | None = None,
) -> str:
    """The card heading covering one of ``code_paths``, or ``fallback``.

    The shared rule behind :func:`_heading_for`, for callers that already
    hold anchor paths instead of a record: the first section whose converted
    ``[n]`` references (resolved through the card's sidecar) or legacy
    ``cit`` markers cite one of the paths; the card's title when none does;
    the fallback without card text. ``anchors`` carries the sidecar's
    ``references{}`` mapping, the source path a pathless own-file anchor
    names, and recorded anchor identity (entry id, path, blob, content)
    when the caller holds it; otherwise the paths alone score path
    matches. See :func:`_select_section` for the title-fallback limitation.
    """

    heading, _fell_back = _select_section(card_text, code_paths, fallback, anchors)
    return heading


def _select_section(
    card_text: str | None,
    code_paths: set[str],
    fallback: str,
    anchors: SectionAnchors | None = None,
) -> tuple[str, bool]:
    """One card heading and whether it is a title fallback despite sections.

    The boolean reports the limitation: the card carries sections, but none
    scored, so the heading is the title rather than a resolved section.
    Callers that present the heading as a section resolution report it.
    """

    references = anchors.references if anchors is not None else None
    own_path = anchors.own_path if anchors is not None else None
    wanted = anchors.wanted if anchors is not None else None
    if card_text is None:
        return fallback, False
    sections = _split_sections(card_text)
    title = _title_of(sections)
    if title is None:
        return fallback, False
    if not code_paths:
        return title, bool(sections)
    if wanted is None:
        wanted = [("", path, None, None) for path in code_paths]
    best, sections_present = _best_section(sections, wanted, references, own_path)
    if best is not None:
        return best, False
    return title, sections_present


def _heading_for(
    card_text: str | None,
    record: Any,
    fallback: str,
    references: Mapping[str, Any] | None = None,
    own_path: str | None = None,
) -> tuple[str, bool]:
    """One record's covering heading and the title-fallback limitation.

    A record is discussed where its anchor is cited: the section whose
    converted ``[n]`` reference resolves (through the sidecar) to the
    record's own anchor wins over a section that merely cites the same
    file, which wins over the card's title. Without card text the fallback
    is returned; with text but no match, the card's title (its first
    non-empty heading) is -- never an invented label. ``references`` is
    the sidecar's ``references{}`` mapping; ``own_path`` is the source path
    a pathless own-file anchor names. The record's differing anchors carry
    its entry identity (entry id, path, blob, content) into scoring, so two
    records in one file under distinct sections resolve to their own
    anchors rather than the first path-matching section.
    """

    if card_text is None:
        return fallback, False
    sections = _split_sections(card_text)
    title = _title_of(sections)
    if title is None:
        return fallback, False
    differing = getattr(record, "differing", ()) or ()
    wanted = [
        (
            getattr(entry, "entry_id", None),
            getattr(entry, "path", None),
            getattr(entry, "recorded_blob", None),
            getattr(entry, "recorded_content", None),
        )
        for entry in differing
    ]
    wanted = [entry for entry in wanted if entry[1]]
    if not wanted:
        return title, bool(sections)
    best, sections_present = _best_section(sections, wanted, references, own_path)
    if best is not None:
        return best, False
    return title, sections_present


def _title_of(sections: list[tuple[str, str]]) -> str | None:
    """The card's title: its first non-empty heading, if any.

    The text before the first heading is not a title: falling back to it
    (or to the caller's path-shaped fallback) would name no heading.
    """

    for heading, _body in sections:
        if heading:
            return heading
    return None


_NUMBERED_REF_PATTERN = re.compile(r"^\s*-\s+.*\[(\d+)\]\s*$", re.MULTILINE)


def _reference_numbers(section_body: str) -> list[str]:
    """The converted ``[n]`` reference numbers one card section cites."""

    return [
        match.group(1) for match in _NUMBERED_REF_PATTERN.finditer(section_body) if match.group(1)
    ]


def _anchor_targets(
    references: Mapping[str, Any] | None, own_path: str | None
) -> dict[str, dict[str, Any]]:
    """The sidecar's reference anchors by number, with own-file paths filled in.

    Converted references name ``{ targets: [...] }`` (rule 6): the target is
    the first code/test anchor's own anchor mapping, so anchor-exact scoring
    sees the recorded blob/content instead of a bare path match. A mapping
    without a targets list keeps the previous lenient reading (itself).
    """

    targets: dict[str, dict[str, Any]] = {}
    if not isinstance(references, Mapping):
        return targets
    for number, anchor in references.items():
        if not isinstance(anchor, Mapping):
            continue
        listed = anchor.get("targets")
        target: dict[str, Any] | None = None
        if isinstance(listed, list):
            for candidate in listed:
                if (
                    isinstance(candidate, Mapping)
                    and candidate.get("kind") in ("code", "test")
                    and isinstance(candidate.get("anchor"), Mapping)
                ):
                    target = dict(candidate["anchor"])
                    break
        if target is None:
            target = dict(anchor)
        if target.get("path") is None and own_path is not None:
            target["path"] = own_path
        targets[str(number)] = target
    return targets


def _section_score(
    numbers: list[str],
    legacy: set[str],
    targets: dict[str, dict[str, Any]],
    wanted: list[tuple[Any, Any, Any, Any]],
) -> tuple[int, int]:
    """One section's score and earliest best-match anchor position.

    The score is 3 for content-exact (the reference's recorded content is
    the wanted anchor's own content), 2 for blob-exact (the same file
    version, recorded content differing), 1 for a path match; the position
    is the wanted anchor order of the earliest best match, so ties between
    sections break toward the record's own earliest differing anchor
    (entry identity order) rather than section order. Content is the
    per-anchor distinctive signal: two sections citing one recorded file
    version under distinct headings resolve to the anchor whose content
    they cite. A legacy ``cit`` path match scores 1 without an anchor
    position.
    """

    score = 0
    first_best = len(wanted)
    for position, (_entry_id, path, blob, content) in enumerate(wanted):
        anchor_best = 0
        for number in numbers:
            target = targets.get(number)
            if target is None:
                continue
            if target.get("path") != path:
                continue
            if content is not None and target.get("content") == content:
                anchor_best = 3
            elif blob is not None and target.get("blob") == blob:
                anchor_best = max(anchor_best, 2)
            else:
                anchor_best = max(anchor_best, 1)
        if path in legacy:
            anchor_best = max(anchor_best, 1)
        if anchor_best > score:
            score, first_best = anchor_best, position
    return score, first_best


def _best_section(
    sections: list[tuple[str, str]],
    wanted: list[tuple[Any, Any, Any, Any]],
    references: Mapping[str, Any] | None,
    own_path: str | None,
) -> tuple[str | None, bool]:
    """The best-matching heading (or ``None``) and whether sections exist.

    A content-exact match (the reference's recorded content is the
    entry's own) beats a blob-exact match (the same file version), which
    beats a path match (the reference names the entry's file), which beats
    a legacy ``cit`` path match. Score ties break toward the record's
    earliest differing anchor, so two records in one file under distinct
    sections resolve to their own anchors, not to the first
    path-matching section.
    """

    targets = _anchor_targets(references, own_path)
    best_heading: str | None = None
    best_score = 0
    best_first = len(wanted)
    for heading, body in sections:
        if not heading:
            continue
        score, first_exact = _section_score(
            _reference_numbers(body), _cited_paths(body), targets, wanted
        )
        if (score, -first_exact) > (best_score, -best_first):
            best_score = score
            best_first = first_exact
            best_heading = heading
    return (best_heading if best_score else None), bool(sections)


def _split_sections(card_text: str) -> list[tuple[str, str]]:
    """The card as ``(heading, body)`` pairs; the text before the first heading first."""

    sections: list[tuple[str, str]] = []
    heading = ""
    body: list[str] = []
    for line in card_text.splitlines():
        match = _HEADING_PATTERN.match(line)
        if match is not None:
            sections.append((heading, "\n".join(body)))
            heading = match.group(2).strip()
            body = []
        else:
            body.append(line)
    sections.append((heading, "\n".join(body)))
    return sections


def _cited_paths(section_body: str) -> set[str]:
    """The code paths one card section cites."""

    return set(_CIT_TARGET_PATTERN.findall(section_body))


def aggregate_state(counts: dict[str, int]) -> InvariantState | None:
    """The worst present state, or ``None`` when the card names no records."""

    for state in ACTION_STATES:
        if counts.get(state, 0):
            return state
    if counts.get("current", 0):
        return "current"
    return None


__all__ = [
    "ACTION_STATES",
    "CODE_STATE_HEAD",
    "READ_AR_FILES_MAX_PATHS",
    "CardRecords",
    "CardSelection",
    "NamedRecord",
    "aggregate_state",
    "card_records_for_path",
    "card_section_for",
    "head_tree_of",
]
