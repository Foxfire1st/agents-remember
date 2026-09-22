"""What a source observation *is*: the two bound sides, the paths they differ at, and the seam.

This vocabulary is shared by everything that reads a code-tree pair -- the config review's inventory,
the comparison's expansion and the attribution partition -- so it lives on its own rather than inside
any one of its readers. Nothing here measures anything: a probe produces these values, and a reader of
them states what the measurement could and could not carry.

``TreePaths`` is the honesty boundary of the whole source half of a review. ``available`` is separate
from ``paths`` because a probe that could not run has not observed *no changes*; ``partial`` and
``unrepresentable`` exist because a Git pathname is bytes and this surface carries text, so a name that
is not valid UTF-8 is measured, preserved and stated rather than silently dropped.

:mod:`agents_remember.memory.knowledge.diff_display` re-exports every name here, so an importer that
has always read them from the display seam keeps working.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

__all__ = [
    "TreeChange",
    "TreeDifferenceProbe",
    "TreePaths",
    "TreeSide",
    "no_tree_difference_probe",
]


@dataclass(frozen=True)
class TreeSide:
    """One side's source binding: the exact code tree, and the repository root it lives in.

    Both are carried because they answer different questions: the tree id is the *identity* the
    comparison resolved against, and the root is *where* a caller runs the command the expansion
    publishes. ``tree_id`` is ``None`` when the side requested no source resolution, which is a
    supported state and not a failure -- the record half of the comparison is complete without it.
    """

    tree_id: str | None
    root: str | None


@dataclass(frozen=True)
class TreeChange:
    """One path two code trees differ at, with Git's own status and what can be rendered.

    ``path`` is the raw filename exactly as Git recorded it -- a tab or a newline inside a name is
    part of the address and not a separator, which is why the observation that produces these values
    reads a NUL-delimited Git interface rather than lines. ``status`` and ``content`` are the two
    separate facts a listing needs: what happened to the path, and whether its content could be
    rendered at all. A mode change is a ``modified`` entry that also says so, because "the bytes are
    the same and the mode moved" is a different change from "the bytes moved".

    ``detail`` states why ``content`` is ``unknown``; it is empty for a measured classification, so a
    reader never has to guess whether an unknown was measured and failed or simply not reported.
    """

    path: str
    status: str
    content: str
    mode_change: bool = False
    detail: str = ""


@dataclass(frozen=True)
class TreePaths:
    """The paths two code trees differ at, as the probe observed them.

    ``available`` is separate from ``paths`` on purpose: a probe that could not run (an absent root,
    a tree this repository does not hold) has not observed *no changes*, and reporting its silence
    as "nothing changed between the trees" would be a fabricated fact. An unavailable probe
    therefore contributes no expansion at all and says so through its ``detail``.

    ``entries`` is the same measurement at full resolution -- one :class:`TreeChange` per *carriable*
    path, in the same order ``paths`` reports them -- and the two are held in agreement by
    construction: a value that named paths its entries do not carry would make one measurement into
    two. ``partial`` says the path set was measured while part of it could not be reported whole:
    either one field of the entries could not be classified, or some changed paths had to travel in
    ``unrepresentable`` instead. ``detail`` states whichever of those happened, so a partial
    observation is never a quiet one.

    ``unrepresentable`` is the second half of that honesty rule, and it is not an empty list a caller
    may ignore. A Git pathname is *bytes* and this surface carries *text*; a name that is not valid
    UTF-8 decodes to lone surrogates, which is a value the surface's own text fields refuse. Dropping
    such a path would make a partial change set read as a whole one, so it is measured, kept here in
    full, and rendered by its byte form by whoever can state that fact to a reader.
    """

    available: bool
    paths: tuple[str, ...] = ()
    detail: str = ""
    entries: tuple[TreeChange, ...] = ()
    partial: bool = False
    unrepresentable: tuple[TreeChange, ...] = ()

    def __post_init__(self) -> None:
        if self.entries and self.paths != tuple(entry.path for entry in self.entries):
            raise ValueError(
                "a tree observation's paths and its entries are two renderings of one measurement, "
                "so a value whose paths are not exactly the entry paths describes no observation"
            )
        if self.unrepresentable and not self.partial:
            raise ValueError(
                "an observation that could not carry some of its changed paths as text is a partial "
                "one; a complete observation beside an unrepresentable path describes no measurement"
            )


# The Git seam, narrowed to one question. It is a callable rather than a class so the application
# layer can pass the same kind of seam the anchor resolver is, and so a case can substitute an
# observation without a repository.
TreeDifferenceProbe = Callable[[TreeSide, TreeSide], TreePaths]


def no_tree_difference_probe(before: TreeSide, after: TreeSide) -> TreePaths:
    """Return the probe that observes nothing, for a comparison whose sides named no code tree.

    It is the honest answer for a comparison with no source half: there is no pair of trees to
    compare, so nothing was observed, and the expansion says exactly that instead of reporting an
    empty change set as though it had been measured.
    """

    if before.tree_id is None or after.tree_id is None:
        return TreePaths(
            available=False,
            detail=(
                "at least one side named no exact code tree, so no source expansion was observed; "
                "the record half of this comparison is complete and the source half was not "
                "requested for every side"
            ),
        )
    return TreePaths(available=True)
