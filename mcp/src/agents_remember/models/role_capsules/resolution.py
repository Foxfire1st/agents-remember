"""Resolving each declared instruction identity to exactly one source block.

Three outcomes, and the difference between them is the whole point of this module:

* **One source.** The ordinary case; the block is composed once.
* **Duplicate identity that collapses.** Two admitted sources carry the same
  identity with byte-identical content. Composing both would duplicate the same
  obligation, so exactly one block survives and the collapse is recorded.
* **Explicit supersession.** The admitted binding names an override, and the
  superseded identity is replaced wholesale with its provenance preserved.

Anything else is a **contradiction at equal authority**, and it stops compilation.
Two different contents claiming one identity, with no declared winner, cannot be
resolved by the compiler: picking by filename, by path order or by "last one wins"
would make the delivered instructions depend on an accident nobody wrote down.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from agents_remember.errors import CapsuleCompilationError
from agents_remember.models.role_capsules.sources import (
    CapsuleDeclaredInstruction,
    CapsuleSource,
)
from agents_remember.models.role_capsules.statuses import (
    STATUS_EQUAL_AUTHORITY_CONTRADICTION,
    STATUS_SUPERSESSION_CONFLICT,
    STATUS_UNKNOWN_SUPERSESSION,
)
from agents_remember.models.role_capsules.types import (
    CAPSULE_COMPOSITION_ORDER,
    CONFLICT_EQUAL_AUTHORITY,
    SUPERSEDE_EXPLICIT,
    CapsuleBlockIdentity,
    CapsuleCompositionRoot,
    CapsuleInstructionBlock,
    CapsuleOverride,
    CapsuleSelectionReference,
)

# Composition-root tiers, in contract order. A supersession may replace a block in
# its own tier or a later one, but never pull an EARLIER tier forward to override a
# later decision: shared core and role authority compose first precisely so a more
# specific tier can refine them, not so a less specific one can silently outrank a
# role, an operation or an admitted repository decision.
_TIER: Mapping[CapsuleCompositionRoot, int] = {
    root: index for index, root in enumerate(CAPSULE_COMPOSITION_ORDER)
}


@dataclass(frozen=True, slots=True)
class Candidate:
    """One admitted source competing for one declared instruction identity."""

    composition_root: CapsuleCompositionRoot
    source: CapsuleSource
    authorities: tuple[CapsuleSelectionReference, ...]


@dataclass(frozen=True, slots=True)
class ResolvedInstruction:
    """One identity resolved to exactly one source, with its selection provenance."""

    identity: CapsuleBlockIdentity
    composition_root: CapsuleCompositionRoot
    source: CapsuleSource
    authorities: tuple[CapsuleSelectionReference, ...]
    superseded_by: CapsuleBlockIdentity | None = None
    superseded_kind: str | None = None
    collapsed: tuple[CapsuleSource, ...] = ()

    @property
    def tier(self) -> int:
        return _TIER[self.composition_root]

    def block(self) -> CapsuleInstructionBlock:
        """The composed instruction block this resolution produces."""

        return CapsuleInstructionBlock(
            identity=self.identity,
            composition_root=self.composition_root,
            authorities=self.authorities,
            source_path=self.source.path,
            revision=self.source.revision,
            content_digest=self.source.revision,
            content=self.source.text(),
        )


def gather_candidates(
    declared: tuple[CapsuleDeclaredInstruction, ...],
    by_path: Mapping[str, CapsuleSource],
    identities: Mapping[str, CapsuleBlockIdentity],
) -> Mapping[CapsuleBlockIdentity, tuple[Candidate, ...]]:
    """Every declared identity that an admitted source carries, with its reasons."""

    gathered: dict[CapsuleBlockIdentity, list[Candidate]] = {}
    for item in declared:
        matches = [
            source for path, source in by_path.items() if identities.get(path) == item.identity
        ]
        for source in matches:
            gathered.setdefault(item.identity, []).append(
                Candidate(
                    composition_root=item.composition_root,
                    source=source,
                    authorities=item.authorities,
                )
            )
    return {
        identity: tuple(
            Candidate(
                composition_root=candidate.composition_root,
                source=candidate.source,
                authorities=tuple(
                    sorted(
                        {
                            reference
                            for entry in candidates
                            if entry.source.path == candidate.source.path
                            for reference in entry.authorities
                        }
                    )
                ),
            )
            for candidate in sorted(candidates, key=lambda item: item.source.path)
        )
        for identity, candidates in gathered.items()
    }


def resolve_instructions(
    candidates: Mapping[CapsuleBlockIdentity, tuple[Candidate, ...]],
    overrides: tuple[CapsuleOverride, ...],
    by_path: Mapping[str, CapsuleSource],
    identities: Mapping[str, CapsuleBlockIdentity],
    preferred_paths: Mapping[CapsuleBlockIdentity, str] | None = None,
) -> tuple[ResolvedInstruction, ...]:
    """Reduce candidates to one instruction per identity, in composition order.

    ``preferred_paths`` names, per identity, the path the admission itself selected.
    It only ever breaks a byte-identical tie between two files claiming one identity;
    it can never make a contradiction resolvable, because a contradiction has no
    admitted winner to prefer.
    """

    preferred = preferred_paths or {}
    superseding = _supersessions(overrides, candidates, by_path, identities)
    replaced = {
        identity: override
        for identity, override in superseding.items()
        if override.superseding_identity != identity
    }
    resolved: list[ResolvedInstruction] = []
    for identity in sorted(
        (name for name in candidates if name not in replaced),
        key=lambda name: (_TIER[candidates[name][0].composition_root], name),
    ):
        override = superseding.get(identity)
        admits_winner = override is not None and override.superseding_identity == identity
        entries = _ordered(identity, candidates[identity], preferred)
        _require_one_identity(identity, entries, adjudicated=admits_winner)
        winner = entries[0]
        chosen = (
            next(
                (entry for entry in entries if entry.source.path == preferred.get(identity)),
                winner,
            )
            if admits_winner
            else winner
        )
        collapsed = tuple(
            entry.source for entry in entries if entry.source.path != chosen.source.path
        )
        resolved.append(
            ResolvedInstruction(
                identity=identity,
                composition_root=chosen.composition_root,
                source=chosen.source,
                authorities=chosen.authorities,
                superseded_by=None if override is None else override.superseding_identity,
                superseded_kind=None if override is None else SUPERSEDE_EXPLICIT,
                collapsed=collapsed,
            )
        )
    return tuple(resolved)


def _ordered(
    identity: CapsuleBlockIdentity,
    entries: tuple[Candidate, ...],
    preferred: Mapping[CapsuleBlockIdentity, str],
) -> tuple[Candidate, ...]:
    """Put the admission-selected path first, then keep a stable order for the rest."""

    wanted = preferred.get(identity)
    return tuple(
        sorted(entries, key=lambda entry: (entry.source.path != wanted, entry.source.path))
    )


def override_winners(overrides: tuple[CapsuleOverride, ...]) -> Mapping[CapsuleBlockIdentity, str]:
    """The path an explicit override names as the admitted carrier of an identity.

    A self-supersession (``superseded_identity == superseding_identity``) is how an
    admitted caller says "this identity is carried by the file I name, not by the
    other one that also claims it". It resolves a duplicate; it never resolves a
    contradiction the caller has not actually decided.
    """

    winners: dict[CapsuleBlockIdentity, str] = {}
    for override in overrides:
        if override.superseded_identity == override.superseding_identity and override.admitted_path:
            winners[override.superseded_identity] = override.admitted_path
    return winners


def _require_one_identity(
    identity: CapsuleBlockIdentity,
    entries: tuple[Candidate, ...],
    *,
    adjudicated: bool = False,
) -> None:
    """Refuse an identity that several sources claim, unless an override decides it.

    ``adjudicated`` is set only when the admitted binding names the winning path, so
    the exemption is an admitted decision rather than the compiler preferring a file.
    """

    if len(entries) == 1 or adjudicated:
        return
    if len({entry.source.revision for entry in entries}) == 1:
        # Byte-identical duplicates collapse: composing both would state the same
        # obligation twice, which is the duplication this resolution exists to end.
        return
    paths = sorted(entry.source.path for entry in entries)
    authorities = sorted({reference for entry in entries for reference in entry.authorities})
    raise CapsuleCompilationError(
        status=STATUS_EQUAL_AUTHORITY_CONTRADICTION,
        detail=(
            f"instruction identity {identity!r} is carried by {len(entries)} sources with different "
            f"contents at equal authority, so there is no declared winner: {_quoted(paths)}"
        ),
        next_action=(
            "declare an explicit override naming which identity supersedes which; the compiler "
            "never picks a winner by filename"
        ),
        conflicts=(
            {
                "identity": identity,
                "kind": CONFLICT_EQUAL_AUTHORITY,
                "authorities": authorities,
                "contenders": paths,
            },
        ),
    )


def _supersessions(
    overrides: tuple[CapsuleOverride, ...],
    candidates: Mapping[CapsuleBlockIdentity, tuple[Candidate, ...]],
    by_path: Mapping[str, CapsuleSource],
    identities: Mapping[str, CapsuleBlockIdentity],
) -> Mapping[CapsuleBlockIdentity, CapsuleOverride]:
    by_identity: dict[CapsuleBlockIdentity, CapsuleOverride] = {}
    for override in overrides:
        if override.superseded_identity in by_identity:
            raise CapsuleCompilationError(
                status=STATUS_SUPERSESSION_CONFLICT,
                detail=(
                    f"identity {override.superseded_identity!r} is superseded by more than one "
                    "declared override, so the compiler cannot order them"
                ),
                next_action="declare exactly one supersession per superseded identity",
            )
        by_identity[override.superseded_identity] = override
    for override in by_identity.values():
        _require_override_target(override, candidates, by_path, identities)
    return by_identity


def _require_override_target(
    override: CapsuleOverride,
    candidates: Mapping[CapsuleBlockIdentity, tuple[Candidate, ...]],
    by_path: Mapping[str, CapsuleSource],
    identities: Mapping[str, CapsuleBlockIdentity],
) -> None:
    superseded = candidates.get(override.superseded_identity)
    if superseded is None:
        raise CapsuleCompilationError(
            status=STATUS_UNKNOWN_SUPERSESSION,
            detail=(
                f"override claims to supersede {override.superseded_identity!r}, but that identity "
                "is not selected for this capsule"
            ),
            next_action=(
                "drop the override or admit the identity it supersedes; an override that matches "
                "nothing is a stale binding"
            ),
        )
    present = candidates.get(override.superseding_identity) or tuple(
        Candidate(composition_root=source.composition_root, source=source, authorities=())
        for path, source in by_path.items()
        if identities.get(path) == override.superseding_identity
    )
    if not present:
        raise CapsuleCompilationError(
            status=STATUS_UNKNOWN_SUPERSESSION,
            detail=(
                f"override names {override.superseding_identity!r} as the replacement, but no "
                "admitted source carries that identity"
            ),
            next_action="admit the superseding source, or drop the override",
        )
    superseded_root = superseded[0].composition_root
    superseding_root = present[0].composition_root
    if _TIER[superseding_root] < _TIER[superseded_root]:
        raise CapsuleCompilationError(
            status=STATUS_SUPERSESSION_CONFLICT,
            detail=(
                f"override replaces the {superseded_root!r} block "
                f"{override.superseded_identity!r} with an earlier {superseding_root!r} block, "
                "which would let shared instruction override a role, operation or repository "
                "decision"
            ),
            next_action=(
                "an override may replace a block in its own tier or a later one; shared core and "
                "role authority are composed first and never rewritten from above it"
            ),
        )


def _quoted(names: list[str]) -> str:
    return ", ".join(repr(name) for name in names) if names else "<none>"


__all__ = [
    "Candidate",
    "ResolvedInstruction",
    "gather_candidates",
    "resolve_instructions",
]
