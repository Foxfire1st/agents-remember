"""Plan exact-name tier-1 citation repairs.

Generate one extent per anchor and merge only overlapping/adjacent extents in the same
file; never widen discontiguous anchors into one enclosing range. Search cited files first,
then the wider code tree only when no cited file exists any more. Repoint only a unique
exact-name match.

A tree-wide retarget additionally has to PROVE continuity: the anchor must have existed in a
cited file at the document's ``lastVerifiedCommitHash`` and must have carried the same extent
KIND there as the extent found now. An exact name matching somewhere is not the claim's
evidence having moved -- a mention inside a tuple and the function that declares the same name
are different facts -- and nothing here infers an origin from similarity. Missing provenance
is grounds to refuse, never a licence to guess.

Renames, deletions, typos, and ambiguity are declined because syntax cannot distinguish
them safely. Tree-wide mode plans only failing citations. Document-scoped normalization
also regenerates passing ranges through ``migration._scoped`` so verified mention spans
are preserved.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from agents_remember.memory_quality.integrity.onboarding_drift_check.discovery import (
    parse_table_metadata,
)
from agents_remember.memory_quality.style.citations import (
    claim_change_router,
    extents,
    model,
    provenance,
    symbol_index,
)
from agents_remember.memory_quality.style.citations.range_resolution import Sources
from agents_remember.memory_quality.style.citations.resolution import Trees

ANCHOR_ABSENT = "anchor_absent"
ANCHOR_AMBIGUOUS = "anchor_ambiguous"
ANCHOR_LEFT_LIVE_FILE = "anchor_left_live_file"
ANCHOR_CONTINUITY_UNPROVEN = "anchor_continuity_unproven"
ANCHOR_KIND_CHANGED = "anchor_kind_changed"

DECLINE_REMEDIATION = {
    ANCHOR_ABSENT: (
        "--fix repairs pure moves only, and this is not one: the anchor keeps its name "
        "nowhere in the tree. Read the CLAIM rather than the pointer -- a symbol that "
        "exists nowhere usually means the behaviour changed, not that a number went stale."
    ),
    ANCHOR_AMBIGUOUS: (
        "--fix rewrites a path only when the anchor resolves UNIQUELY, and it never picks "
        "between candidates by similarity: an agent that READS two functions can tell a "
        "mapper from a validator, a string distance cannot. Read the candidates and cite "
        "the one the claim is about."
    ),
    ANCHOR_LEFT_LIVE_FILE: (
        "--fix retargets across the tree only when every cited file is GONE, because a cited "
        "file that survives is still the claim's own evidence. Here the anchor left a file "
        "that is still there, so the pointer is stale: read the CLAIM and cite where its fact "
        "now lives. Every candidate the tree holds is named in the message above."
    ),
    ANCHOR_CONTINUITY_UNPROVEN: (
        "--fix retargets across the tree only when CONTINUITY IS PROVED: the anchor has to "
        "have existed in a cited file at the document's lastVerifiedCommitHash and to have "
        "carried the same extent kind there. Nothing here guesses from a name. Establish the "
        "claim's original evidence first -- stamp the card at the commit it was verified "
        "against -- and then re-cite where its fact now lives: a declaration elsewhere does "
        "not evidence a claim about a mention here."
    ),
    ANCHOR_KIND_CHANGED: (
        "--fix will not follow a name into its DECLARATION. The anchor was a mention where "
        "the claim was verified and the only tree-wide match is the construct that declares "
        "the same name -- a different fact, and one that does not evidence a claim about a "
        "mention here. Read the CLAIM and cite where its fact now lives; if the claim really "
        "is about the declaration, re-word it and cite the declaration deliberately."
    ),
}


@dataclass(frozen=True)
class ResolvedLocation:
    """One anchor's chosen extent, exactly as the shared oracle resolved it.

    Carried on Repair so the deterministic projection binds the extent the
    repair actually followed -- the in-file tiebreaker when the cited range picks one
    candidate, the tree-wide Sightings.unique definition otherwise -- without a
    second lookup or a second authority for the answer.
    """

    anchor: model.Anchor
    path: str
    extent: extents.Extent


@dataclass(frozen=True)
class Repair:
    """The source list ``--fix`` would write for one claim."""

    sources: tuple[str, ...]
    locations: tuple[ResolvedLocation, ...] = ()


@dataclass(frozen=True)
class Decline:
    """Why one claim stays for the curator, and the facts it needs to work it down."""

    code: str
    anchor: model.Anchor | None
    detail: str

    @property
    def message(self) -> str:
        return f"{self.detail} {DECLINE_REMEDIATION[self.code]}"


@dataclass(frozen=True)
class Cited:
    """One of a claim's sources and the file it named, when that file still exists."""

    citation: model.Citation
    file: Path | None


def targets(claim: model.Claim, trees: Trees) -> tuple[Cited, ...]:
    return tuple(
        Cited(citation=citation, file=trees.resolve(citation.path)) for citation in claim.citations
    )


def chosen(found: tuple[extents.Extent, ...], citation: model.Citation) -> extents.Extent | None:
    """The one extent this citation means, or ``None`` when the file offers a choice.

    The claim's own range is the tiebreaker inside a file, for the same reason the cited
    PATH is the tiebreaker across files: it is the author's statement about where they
    were pointing, and it is the only signal available that is not a similarity score.
    """
    if len(found) == 1:
        return found[0]
    overlapping = [one for one in found if one.holds(citation.start, citation.end)]
    return overlapping[0] if len(overlapping) == 1 else None


@dataclass(frozen=True)
class Origin:
    """What the claim's anchor was, in the sources it cited, at its verification stamp.

    ``kind`` is ``None`` exactly when the origin could not be established, and ``detail``
    then says why. A proven origin also carries the cited path and commit it was read from,
    so a kind mismatch can name both sides of the difference it refuses.
    """

    kind: str | None
    detail: str
    commit: str | None = None
    source: str = ""


@dataclass
class Continuity:
    """The verification provenance one memory document declares, read on demand.

    ``stamp`` is the document's ``lastVerifiedCommitHash``: the tree the claim was read in,
    and therefore the only authority on what its evidence was. The current tree can answer
    only where the NAME is now, which is not the same question. Nothing here is guessed --
    every unreadable or non-unique origin comes back as an unproven :class:`Origin`, and an
    unproven origin refuses the relocation.
    """

    trees: Trees
    histories: provenance.Histories
    stamp: str = ""

    def commit(self) -> provenance.Read:
        """The stamp resolved to one reachable commit, or why it does not name one."""
        if not self.stamp:
            return provenance.Read(
                None,
                "the document carries no lastVerifiedCommitHash, so the extent this claim was "
                "verified against cannot be established",
            )
        return self.histories.code.commit(self.stamp)

    def origin(self, citations: tuple[model.Citation, ...], anchor: model.Anchor) -> Origin:
        """The one extent this claim was verified against, or why there is not one."""
        resolved = self.commit()
        if resolved.text is None:
            return Origin(None, resolved.error or "the verification stamp names no commit")
        commit = resolved.text
        choices: list[tuple[model.Citation, extents.Extent]] = []
        failures: list[str] = []
        for citation in citations:
            holds, failure = self._holds(citation, anchor, commit)
            if failure:
                failures.append(failure)
                continue
            one = chosen(holds, citation)
            if one is None:
                failures.append(
                    f"{anchor.written} occurs {len(holds)} times in {citation.path} at "
                    f"{commit} and the cited range {citation.text} picks out none of them"
                )
                continue
            choices.append((citation, one))
        if len(choices) == 1:
            citation, one = choices[0]
            return Origin(one.kind, "", commit=commit, source=citation.path)
        if not choices:
            return Origin(None, "; ".join(failures) or f"no cited source could be read at {commit}")
        return Origin(
            None,
            f"{anchor.written} was in {len(choices)} of the cited files at {commit} "
            f"({', '.join(citation.path for citation, _ in choices)}), so the extent this "
            f"claim was verified against is not unique",
        )

    def _holds(
        self, citation: model.Citation, anchor: model.Anchor, commit: str
    ) -> tuple[tuple[extents.Extent, ...], str]:
        """The anchor's extents in one cited path at the stamp, or why they cannot be read."""
        source, error = claim_change_router.classify_citation(self.trees, citation)
        if error is not None:
            return (), error
        if source is None:
            return (), f"{citation.path} names no local source to read at {commit}"
        read = self._file(source, commit)
        if read.text is None:
            return (), read.error or f"{citation.path} could not be read at {commit}"
        holds = extents.FileView(path=citation.path, lines=read.text.splitlines()).extents(anchor)
        if not holds:
            return (), f"{citation.path} at {commit} holds no {anchor.written}"
        return holds, ""

    def _file(self, source: claim_change_router.LocalCitation, commit: str) -> provenance.Read:
        if source.repository == "code":
            return self.histories.code.file(commit, source.citation.path)
        mapped = self.histories.memory_commit(commit)
        if mapped.text is None:
            return mapped
        return self.histories.memory.file(mapped.text, source.citation.path)


def continuity_for(document: Path, trees: Trees, histories: provenance.Histories) -> Continuity:
    """The verification provenance one memory document declares, or an empty one.

    A document with no metadata table, no stamp, or an unreadable one yields the empty
    ``Continuity``, whose ``origin`` is unproven -- the refusal, never a guess.
    """
    try:
        stamp = parse_table_metadata(document).get("lastVerifiedCommitHash", "").strip()
    except (OSError, UnicodeDecodeError):  # pragma: no cover - the walk already read it
        stamp = ""
    return Continuity(trees=trees, histories=histories, stamp=stamp)


@dataclass
class _Plan:
    """One claim's repair as it accumulates: the spans found, and the first refusal."""

    spans: dict[str, list[tuple[int, int]]] = field(default_factory=dict)
    reached: set[str] = field(default_factory=set)
    relocated: bool = False
    declined: Decline | None = None
    locations: list[ResolvedLocation] = field(default_factory=list)

    def add(self, anchor: model.Anchor, path: str, extent: extents.Extent) -> None:
        self.spans.setdefault(path, []).append((extent.start, extent.end))
        self.reached.add(path)
        self.locations.append(ResolvedLocation(anchor=anchor, path=path, extent=extent))

    def refuse(self, decline: Decline) -> None:
        self.declined = self.declined or decline


@dataclass(frozen=True)
class _Placement:
    """Every authority one anchor's placement consults, held for the whole claim."""

    cited: tuple[Cited, ...]
    sources: Sources
    sightings: dict[model.Anchor, symbol_index.Sightings]
    continuity: Continuity | None


def plan(
    claim: model.Claim,
    trees: Trees,
    sources: Sources,
    sightings: dict[model.Anchor, symbol_index.Sightings],
    continuity: Continuity | None,
) -> Repair | Decline:
    """The tiebreaker, applied to one claim.

    ``continuity`` is the document's verification provenance, and it is what a tree-wide
    retarget has to prove itself against. ``None`` means the caller has no continuity
    authority for this claim -- the scoped pass, whose empty sightings never relocate -- and
    a relocation is then refused rather than guessed.
    """
    cited = targets(claim, trees)
    found = _Plan()
    placement = _Placement(cited=cited, sources=sources, sightings=sightings, continuity=continuity)
    for anchor in claim.anchors:
        _place(anchor, found, placement)
    if found.declined is not None:
        return found.declined
    return Repair(
        sources=tuple(_written(found, cited, trees)),
        locations=tuple(found.locations),
    )


def _carried(cited: tuple[Cited, ...], found: _Plan, trees: Trees) -> list[str]:
    """The sources that survive unchanged because nothing here could regenerate them.

    A source into somebody else's tree is ALWAYS kept: it resolves in neither root by
    construction, it is what the quoted-anchor form exists for, and deleting it would
    delete the only evidence a third-party claim has.

    A live source that holds none of the claim's anchors is kept only while nothing MOVED.
    Once an anchor has been resolved from the wider tree, an unanchored cited file is the
    location it was resolved out of, and keeping it would leave the stale pointer sitting
    beside its own replacement -- which is the exact damage this whole check exists to end.
    A source whose file is gone is never kept: it points at nothing.
    """
    return [
        one.citation.text
        for one in cited
        if one.citation.path not in found.reached
        and (
            (one.file is None and not trees.ours(one.citation.path))
            or (one.file is not None and not found.relocated)
        )
    ]


def _place(anchor: model.Anchor, found: _Plan, placement: _Placement) -> None:
    """Where this anchor's range comes from: a cited file first, the wider tree second."""
    placed = False
    for one in placement.cited:
        if one.file is None:
            continue
        holds = placement.sources.view(one.file, one.citation.path).extents(anchor)
        if not holds:
            continue
        extent = chosen(holds, one.citation)
        if extent is None:
            found.refuse(_ambiguous_in_file(anchor, one, holds))
            return
        found.add(anchor, one.citation.path, extent)
        placed = True
    if placed:
        return
    _retarget(anchor, found, placement)


def _retarget(anchor: model.Anchor, found: _Plan, placement: _Placement) -> None:
    """The wider-tree answer, admitted only after three refusals, cheapest first.

    A cited file that still exists is the claim's own evidence, and the anchor leaving it is
    a stale range rather than a move. An anchor with no single tree-wide destination has no
    destination. And a relocation needs CONTINUITY: the extent the claim was verified against
    must exist at its stamp and carry the same KIND as the extent found now. An exact name
    matching somewhere is not the claim's evidence having moved.
    """
    cited = placement.cited
    seen = placement.sightings[anchor]
    live = sorted({one.citation.path for one in cited if one.file is not None})
    if live:
        found.refuse(_left_a_live_file(anchor, live, seen))
        return
    unique = seen.unique
    if unique is None:
        found.refuse(_no_single_sighting(anchor, seen))
        return
    origin = (
        placement.continuity.origin(tuple(one.citation for one in cited), anchor)
        if placement.continuity is not None
        else Origin(None, "this pass carries no verification provenance for the claim")
    )
    if origin.kind is None:
        found.refuse(_continuity_unproven(anchor, seen, origin))
        return
    if origin.kind != unique.extent.kind:
        found.refuse(_kind_changed(anchor, unique, origin))
        return
    found.add(anchor, unique.path, unique.extent)
    found.relocated = True


def _left_a_live_file(
    anchor: model.Anchor, live: list[str], seen: symbol_index.Sightings
) -> Decline:
    return Decline(
        code=ANCHOR_LEFT_LIVE_FILE,
        anchor=anchor,
        detail=(
            f"{anchor.written} is in none of the cited files, and "
            f"{', '.join(live)} still exists in the tree, so a tree-wide exact-name "
            f"match is a different fact rather than this claim's location: "
            f"{symbol_index.described(anchor, seen)}."
        ),
    )


def _no_single_sighting(anchor: model.Anchor, seen: symbol_index.Sightings) -> Decline:
    return Decline(
        code=ANCHOR_ABSENT if not seen.files else ANCHOR_AMBIGUOUS,
        anchor=anchor,
        detail=f"{anchor.written} is in none of the cited files and "
        f"{symbol_index.described(anchor, seen)}.",
    )


def _continuity_unproven(
    anchor: model.Anchor, seen: symbol_index.Sightings, origin: Origin
) -> Decline:
    return Decline(
        code=ANCHOR_CONTINUITY_UNPROVEN,
        anchor=anchor,
        detail=(
            f"{anchor.written} is in none of the cited files, the extent it was verified "
            f"against cannot be established -- {origin.detail} -- so the tree-wide exact-name "
            f"match {symbol_index.described(anchor, seen)} is not this claim's evidence moved."
        ),
    )


def _kind_changed(anchor: model.Anchor, unique: symbol_index.Location, origin: Origin) -> Decline:
    return Decline(
        code=ANCHOR_KIND_CHANGED,
        anchor=anchor,
        detail=(
            f"{anchor.written} was {_named_kind(origin.kind)} in {origin.source} at "
            f"{origin.commit}, and the only tree-wide exact-name match is "
            f"{_named_kind(unique.extent.kind)} at {unique.written}: a different fact rather "
            f"than this claim's evidence moved."
        ),
    )


def _named_kind(kind: str | None) -> str:
    """One extent kind with the article a sentence needs, or the fact that it is unproven."""
    if kind is None:
        return "an extent whose kind could not be established"
    return f"{'an' if kind[:1].lower() in 'aeiou' else 'a'} {kind}"


def _ambiguous_in_file(
    anchor: model.Anchor, one: Cited, holds: tuple[extents.Extent, ...]
) -> Decline:
    written = ", ".join(f"{one.citation.path}:{e.start}-{e.end}" for e in holds)
    return Decline(
        code=ANCHOR_AMBIGUOUS,
        anchor=anchor,
        detail=f"{anchor.written} occurs {len(holds)} times in {one.citation.path} and the "
        f"cited range {one.citation.text} picks out none of them: {written}.",
    )


def _written(found: _Plan, cited: tuple[Cited, ...], trees: Trees) -> list[str]:
    """The generated source list: one range per anchor, merged per file, then what survives.

    Merged only where two extents overlap or abut, so two anchors in different parts of one
    file stay two ranges. Ordered by path and line, which is what makes a second run of
    ``--fix`` a byte-for-byte no-op.
    """
    generated = [
        f"{path}:{start}-{end}"
        for path in sorted(found.spans)
        for start, end in extents.merged(found.spans[path])
    ]
    return generated + _carried(cited, found, trees)
