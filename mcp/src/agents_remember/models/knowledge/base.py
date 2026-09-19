"""The frozen knowledge model base and the shared identifier validators.

Every persisted knowledge value is a frozen, extra-forbidden model. Immutability here is a
property of the process-local value, not of the row: refusing an update to a stored revision
is a storage rule enforced by schema triggers and the operation's own preconditions.
"""

from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict

# One canonical identifier spelling: lowercase hyphenated UUID text. A stored identity that
# is not this exact form would make two rows equal in one comparison and different in
# another, so the form is validated rather than normalized at the boundary.
UUID_PATTERN = r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
SHA256_PATTERN = r"^[0-9a-f]{64}$"
GIT_OBJECT_PATTERN = r"^[0-9a-f]{40}$|^[0-9a-f]{64}$"

# The maximum lengths keep one authored record from becoming an unbounded payload. They are
# generous for prose and far below any SQLite limit; a legitimate value never approaches them.
PROSE_MAX_LENGTH = 20000
LABEL_MAX_LENGTH = 512
REFERENCE_MAX_LENGTH = 1024
PATH_MAX_LENGTH = 4096

PROPOSED_STATE: Literal["proposed"] = "proposed"
ACCEPTED_STATE: Literal["accepted"] = "accepted"
KnowledgeState = Literal["proposed", "accepted"]


class KnowledgeModel(BaseModel):
    """Base class for the knowledge vocabulary: strict, frozen, no undeclared field."""

    model_config = ConfigDict(extra="forbid", frozen=True)


def require_consistent_acceptance(
    state_at_origin: KnowledgeState, acceptance_ref: str | None
) -> None:
    """Refuse an origin state whose acceptance reference contradicts it.

    Accepted origin data is accepted because a named authority accepted it, so the reference is
    required; a proposed revision that carried one would claim an acceptance that never happened.
    Both rules are properties of the authored value, so every revision aggregate in this
    vocabulary applies them at construction rather than only at the storage boundary.
    """

    if state_at_origin == ACCEPTED_STATE:
        if not (acceptance_ref or "").strip():
            raise ValueError("accepted origin data requires a nonempty acceptance_ref")
        return
    if acceptance_ref is not None:
        raise ValueError("a proposed revision must not carry an acceptance_ref")


def require_plain_git_path(value: str, *, what: str) -> str:
    """Refuse a repository-relative path spelled with Git pathspec *magic*; admit glob characters.

    A recorded path or a path seed is *addressed at a tree object*: it is handed to ``git ls-tree``
    as an argument. What makes such an argument something other than an address is **pathspec
    magic**, and Git introduces magic with a leading ``:`` -- ``:(exclude)src/x.py`` and
    ``:!src/x.py`` make ``ls-tree`` exit non-zero with "pathspec magic not supported by this
    command", and ``:(top)src/x.py`` and ``:/src/x.py`` are accepted and answer about a *different*
    location than the confined one the record names. Either way the spelling, not the tree, decides
    the answer, so the caller would be told the path is absent when Git was never asked about it.

    The characters ``*``, ``?`` and ``[`` are **not** magic to ``ls-tree``: measured against
    ``git 2.54.0``, ``git ls-tree <tree> -- 'src/a[1].py'`` resolves exactly that entry with
    ``rc=0`` even when ``src/a1.py`` also exists, and ``src/a?b.py`` and ``src/a*b.py`` behave the
    same way. (``git ls-files`` *does* glob them, which is where the opposite intuition comes from,
    but it is not the command this path is handed to.) Refusing them here would make a legitimate
    anchor un-authorable and un-seedable, and -- through the read path's own confinement check --
    would report a file the tree really holds as ``path_absent``, which is a false statement about
    the repository rather than a refusal of a malformed spelling. They are therefore admitted, and
    only a leading ``:`` is refused as magic.

    This is checkable without Git, so it is checked at the same boundary that already refuses an
    absolute, drive, UNC, backslash, NUL, empty, ``.`` or ``..`` spelling: a malformed anchor or
    seed dies in the vocabulary rather than in the tree lookup. ``what`` names the value being
    refused, so a caller is told which spelling was rejected.
    """

    if value.startswith(":"):
        raise ValueError(
            f"{what} must be a plain tree path, not a Git pathspec: {value!r}. A spelling that "
            "begins with ':' is read as pathspec magic by Git and answers a different question "
            "than the recorded path asks."
        )
    return value


def normalized_uuid(value: uuid.UUID | str) -> str:
    """Return the canonical stored spelling of an identifier.

    Accepts a ``UUID`` or any UUID text, and refuses anything else. Non-canonical input is
    rejected rather than quietly rewritten: a caller that supplies an identity must supply
    the identity that will be stored.
    """

    if isinstance(value, uuid.UUID):
        return str(value)
    canonical = str(value).strip().lower()
    parsed = uuid.UUID(canonical)
    if str(parsed) != canonical:
        raise ValueError(f"identifier must use the canonical lowercase UUID spelling: {value!r}")
    return canonical
