"""The labelled Git rename inference over two bound code trees (ICR-R08@v1).

A source rename may be displayed, and it may never be displayed as proof that an invariant moved.
This module owns that distinction as a value: Git's own similarity detection is asked once, over the
two *bound tree objects* -- never a branch, a working tree or ``HEAD`` -- and its answer travels as
:class:`~agents_remember.models.knowledge.review_relationships.ReviewRenameInference`, whose ``basis``
can only be Git's detection, whose ``state`` separates a measured pairing from a measured non-pairing
and from a measurement that was never made, and whose ``statement`` says in its own words that this is
a similarity inference about the source.

Nothing here creates, moves or attributes anything. The pairs are asked about *after* a movement has
been established by the recorded anchors and the author's own edges, and the answer is attached beside
that movement: the inference can say which recorded old address looks like the file that moved, and it
can say nothing about which invariant moved, because the traversal never reads it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from agents_remember.kernel.git_command import PARSED_DIFF_OPTIONS, run_git
from agents_remember.memory.knowledge.diff_display import TreeSide
from agents_remember.models.knowledge.review_relationships import (
    ReviewRelationshipMovement,
    ReviewRenameInference,
)

__all__ = [
    "RENAME_INFERENCE_COMMAND",
    "RenameInferenceProbe",
    "RenameInferenceSources",
    "RenameObservations",
    "RenamePair",
    "git_rename_inference",
    "moved_pairs",
    "rename_command",
    "with_rename_inferences",
]

# The Git arguments the rename inference is measured with, in front of the two tree ids.
# ``--find-renames`` is the one place this surface asks Git for a similarity inference, and its answer
# is labelled as an inference everywhere it is displayed.
_RENAME_ARGS = ("diff", *PARSED_DIFF_OPTIONS, "--raw", "-z", "--find-renames")

# The same interface as the published text, in full so a reader reproduces the inference without this
# module: the two bound tree objects are substituted and never a branch, a working tree or ``HEAD``.
# It is built from the tuple the measurement passes to Git, so the text and the executed arguments
# have one source and cannot differ.
RENAME_INFERENCE_COMMAND = " ".join(("git", *_RENAME_ARGS, "{before_tree}", "{after_tree}"))

# The Git status letters that carry *two* paths in the ``-z`` raw form: a rename and a copy print the
# old name and then the new one, while every other status prints one path.
_TWO_PATH_STATUSES = ("R", "C")

_UNPARSED_RENAME_DETAIL = (
    "the Git rename interface answered with output that is not the declared NUL-delimited record "
    "format, so no pairing was read from it and none is reported"
)
_UNROOTED_RENAME_DETAIL = (
    "a side named an exact code tree without the repository root it lives in, so no rename "
    "inference was measured over the pair"
)
_MISSING_TREE_RENAME_DETAIL = (
    "one side named no exact code tree, so no rename inference was measured and neither a pairing "
    "nor its absence is reported"
)


@dataclass(frozen=True)
class RenamePair:
    """One rename Git's own similarity detection reported between the two bound trees."""

    before_path: str
    after_path: str
    similarity: str | None


@dataclass(frozen=True)
class RenameObservations:
    """One measurement of Git's rename inference over a pair of trees, or the reason there is none.

    ``available`` is the honesty boundary, exactly as it is for the source observation: a pair of
    trees that share no rename produces ``available=True`` with no pairs, and a measurement that was
    not made produces ``available=False`` with its reason and no pairs either. The two are different
    facts and neither is rendered as the other.
    """

    available: bool
    pairs: tuple[RenamePair, ...] = ()
    detail: str = ""


# The seam the inference is read through, so a case can measure one review with a substituted
# observation rather than running Git twice.
RenameInferenceProbe = Callable[[TreeSide, TreeSide], RenameObservations]


def git_rename_inference(before: TreeSide, after: TreeSide) -> RenameObservations:
    """Git's own rename detection over two exact tree objects, as one labelled measurement.

    The two trees are addressed by object id and never by a branch, a working tree or ``HEAD``, so
    the inference cannot silently become a statement about whatever is checked out now. A failed or
    malformed answer is the ``available=False`` state with its reason: it is never an empty pairing,
    because "Git found no rename" and "Git was not asked" are different facts.
    """

    if before.tree_id is None or after.tree_id is None:
        return RenameObservations(available=False, detail=_MISSING_TREE_RENAME_DETAIL)
    if before.root is None or after.root is None:
        return RenameObservations(available=False, detail=_UNROOTED_RENAME_DETAIL)
    result = run_git(Path(after.root), [*_RENAME_ARGS, before.tree_id, after.tree_id])
    if result.returncode != 0:
        return RenameObservations(
            available=False,
            detail=(
                f"the two bound code trees {before.tree_id} and {after.tree_id} could not be read "
                f"for rename detection in {after.root}, so no pairing is reported and no working "
                "tree or HEAD was substituted for either tree"
            ),
        )
    pairs = _rename_pairs(result.stdout)
    if pairs is None:
        return RenameObservations(available=False, detail=_UNPARSED_RENAME_DETAIL)
    return RenameObservations(available=True, pairs=pairs)


@dataclass(frozen=True)
class RenameInferenceSources:
    """The two bound code trees and the seam the inference is measured through, as one value.

    The three travel together because they are one measurement: an inference measured over another
    pair of trees, or through another seam, would be a different answer wearing this one's identity.
    """

    before_code: TreeSide | None = None
    after_code: TreeSide | None = None
    probe: RenameInferenceProbe = git_rename_inference

    def observe(self) -> RenameObservations:
        """Measure the inference over the two bound trees, or state why it was not measured."""

        if self.before_code is None or self.after_code is None:
            return RenameObservations(available=False, detail=_MISSING_TREE_RENAME_DETAIL)
        return self.probe(self.before_code, self.after_code)


def with_rename_inferences(
    movements: tuple[ReviewRelationshipMovement, ...],
    routes: tuple[ReviewRelationshipMovement, ...],
    sources: RenameInferenceSources,
) -> tuple[ReviewRelationshipMovement, ...]:
    """Attach Git's labelled rename inference to the movements whose recorded addresses differ.

    The inference is asked for at most once per review and only when a movement actually names two
    different recorded addresses, so a review whose associations did not move runs no extra Git
    command at all. Every other movement keeps what it already displayed, and the stream keeps its own
    order: nothing is reordered, created or dropped because of an inference.
    """

    candidates = tuple(movement for movement in movements if moved_pairs(movement))
    if not candidates:
        return (*movements, *routes)
    observation = sources.observe()
    command = rename_command(sources)
    annotated = tuple(
        _with_inference(movement, observation, command) if movement in candidates else movement
        for movement in movements
    )
    return (*annotated, *routes)


def rename_command(sources: RenameInferenceSources) -> str:
    """The exact Git command the inference is measured with, naming both bound tree objects.

    The two substituted ids are the objects the comparison bound, so a reader reproduces the
    inference without this module -- and a side that named no tree says so in the command rather than
    leaving a placeholder that would read as an unsubstituted template.
    """

    return RENAME_INFERENCE_COMMAND.format(
        before_tree=_tree_word(sources.before_code),
        after_tree=_tree_word(sources.after_code),
    )


def _tree_word(side: TreeSide | None) -> str:
    """One side's tree object as it appears in a reproducible command."""

    if side is None or side.tree_id is None:
        return "<no such tree requested>"
    return side.tree_id


def moved_pairs(movement: ReviewRelationshipMovement) -> tuple[tuple[str, str], ...]:
    """The recorded address pairs of one movement that Git's inference could relabel.

    One pair per recorded before address that differs from the recorded after address, in sorted
    order. A movement whose recorded sides are several old addresses and one new one therefore asks
    the inference about each of them, and the answer names the one Git paired -- which is exactly the
    extra, *labelled* fact an inference may add: which of the recorded old addresses looks like the
    file that moved. It changes no side, no identity and no association, and a movement whose
    addresses are all the same asks nothing at all.
    """

    if movement.after is None or movement.after.path is None:
        return ()
    after_path = movement.after.path
    return tuple(
        (before_path, after_path)
        for before_path in sorted({side.path for side in movement.before if side.path is not None})
        if before_path != after_path
    )


def _with_inference(
    movement: ReviewRelationshipMovement,
    observation: RenameObservations,
    command: str,
) -> ReviewRelationshipMovement:
    """Return one movement carrying the rename inference its recorded address pairs earned."""

    pairs = moved_pairs(movement)
    if not pairs:  # pragma: no cover - the caller selected this movement for having two addresses
        return movement
    return movement.model_copy(
        update={"rename_inference": _inference_for(pairs, observation, command)}
    )


def _inference_for(
    pairs: tuple[tuple[str, str], ...], observation: RenameObservations, command: str
) -> ReviewRenameInference:
    """One labelled inference: Git's pairing, Git's measured absence of one, or no measurement."""

    if not observation.available:
        return ReviewRenameInference(
            state="unavailable",
            command=command,
            statement=(
                "no rename inference was measured over the two bound code trees "
                f"({observation.detail}); the movement displayed beside this is the recorded "
                "anchors, and no inference about the source is claimed either way"
            ),
        )
    pairing = next(
        (
            (before_path, after_path, pair)
            for before_path, after_path in pairs
            for pair in observation.pairs
            if pair.before_path == before_path and pair.after_path == after_path
        ),
        None,
    )
    if pairing is None:
        return ReviewRenameInference(
            state="not_paired",
            command=command,
            statement=(
                "Git's own rename detection over the two bound code trees reported no rename pairing "
                f"among the recorded address pairs this movement displays "
                f"({_addressed(pairs)}); the movement displayed beside this is the recorded anchors, "
                "and a path that changed without a rename pairing is a deletion and an addition "
                "rather than a move"
            ),
        )
    before_path, after_path, pair = pairing
    return ReviewRenameInference(
        state="inferred",
        before_path=before_path,
        after_path=after_path,
        similarity=pair.similarity,
        command=command,
        statement=(
            f"Git's own rename detection pairs {before_path} with {after_path}"
            f"{'' if pair.similarity is None else f' at similarity {pair.similarity}'}; this is a "
            "similarity inference about the source between the two bound code trees and is not proof "
            "that the invariant moved -- the movement displayed beside it is the recorded anchors, "
            "and no identity, attribution or association is derived from this inference"
        ),
    )


def _addressed(pairs: tuple[tuple[str, str], ...]) -> str:
    """One sentence fragment naming every recorded address pair that was asked about."""

    return "; ".join(f"{before_path} -> {after_path}" for before_path, after_path in pairs)


def _rename_pairs(output: str) -> tuple[RenamePair, ...] | None:
    """Read Git's NUL-delimited raw rename records, or ``None`` when the output is not that format.

    A rename record carries two paths -- the old name and then the new one -- while every other
    status carries one, so the records are walked rather than parsed positionally. Paths are carried
    verbatim, never trimmed and never re-encoded, because the name *is* the address the same file is
    addressed by everywhere else in this surface. A token stream that does not match this declared
    format returns ``None`` rather than a shorter pairing list, because a partial read of Git's own
    answer would be reported as "Git found no rename".
    """

    tokens = output.split("\0")
    if tokens and tokens[-1] == "":
        tokens.pop()
    pairs: list[RenamePair] = []
    index = 0
    while index < len(tokens):
        metadata = tokens[index]
        if not metadata.startswith(":"):
            return None
        fields = metadata[1:].split(" ")
        if len(fields) != 5 or not fields[4]:
            return None
        width = 2 if fields[4][0] in _TWO_PATH_STATUSES else 1
        paths = tokens[index + 1 : index + 1 + width]
        if len(paths) != width or any(not path for path in paths):
            return None
        if width == 2:
            pairs.append(
                RenamePair(
                    before_path=paths[0], after_path=paths[1], similarity=fields[4][1:] or None
                )
            )
        index += 1 + width
    return tuple(pairs)
