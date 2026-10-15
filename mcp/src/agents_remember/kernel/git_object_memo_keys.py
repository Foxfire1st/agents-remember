"""Exact object-name eligibility for request-scoped Git lookup reuse."""

from __future__ import annotations

import re

_OBJECT_ID = r"(?:[0-9a-f]{40}|[0-9a-f]{64})"
_FULL_SHA = re.compile(rf"^{_OBJECT_ID}$")
#: An exact object name with a tree/commit peel suffix. Only these two peels qualify
#: for the memo (with ``--no-replace-objects``): any other suffix starts from objects
#: ref movement can redirect.
_PEEL = re.compile(rf"^{_OBJECT_ID}\^\{{(?:tree|commit)\}}$")


def _is_object_id(value: str) -> bool:
    """Whether ``value`` is an exact SHA-1 (40) or SHA-256 (64) hex object name."""
    return len(value) in (40, 64) and all(char in "0123456789abcdef" for char in value.lower())


def _all_bare_shas(args: list[str]) -> bool:
    """Whether every positional is an exact SHA with no peel, path, or range suffix.

    Only bare names qualify for the memo: a ``^{...}`` peel, ``:<path>`` lookup, or
    ``A..B`` range starts from objects replace refs or ref movement can redirect,
    so those forms always run fresh (F20).
    """

    return bool(args) and all(_is_object_id(arg) for arg in args)


def _memo_rev_parse(positionals: list[str], no_replace: bool) -> bool:
    """Whether a ``rev-parse`` argv names only exact objects, replace-proofed.

    Bare SHAs are replace-inert on their own. A tree/commit peel starts from an
    object replace refs can redirect, so it qualifies only behind a leading
    ``--no-replace-objects`` -- the flag makes the read name the true object,
    whose bytes are immutable (F17 without weakening F20).
    """

    if _all_bare_shas(positionals):
        return True
    return (
        no_replace and bool(positionals) and all(bool(_PEEL.fullmatch(arg)) for arg in positionals)
    )


def _memo_cat_file(rest: list[str], no_replace: bool) -> bool:
    """Whether a ``cat-file`` argv is replace-inert, content-free, and single-object.

    The bare-SHA existence probe qualifies on its own: ``-e`` asks whether the object
    is there (a success is immutable; a failure is never kept by the runner, so a later
    creation still probes fresh). A tree/commit peel qualifies only behind a leading
    ``--no-replace-objects``. Everything else reads bytes, types, or
    replace-redirectable names and always runs fresh (F20):

    * ``-p`` pretty-prints through replace refs;
    * ``-t`` reports the (possibly replaced) object's type;
    * ``<type> <sha>`` resolves the name through replace refs before dumping it;
    * unflagged ``<sha>^{...}`` / ``<sha>:<path>`` peels and path lookups start from
      the (possibly replaced) object.
    """
    if not rest or rest[0] == "--batch":
        return False
    if rest[0] == "-e":
        # An existence probe of one object: a success is immutable (the object
        # is there), a failure is never kept by the runner (F20), so a later
        # creation still probes fresh. Within one request scope this collapses
        # the repeated guard probes of the same trees (MIK-R81 rule 7, F17).
        if len(rest) == 2 and bool(_FULL_SHA.fullmatch(rest[1])):
            return True
        return len(rest) == 2 and no_replace and bool(_PEEL.fullmatch(rest[1]))
    return False
