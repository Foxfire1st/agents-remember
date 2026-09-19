"""Observe one recorded source anchor against the requested code tree.

An anchor is a *recorded* claim about where an obligation is realized. Resolving it can therefore
end in several ways that are all facts, and exactly one of them is "the recorded bytes are there".
This module reports which one, and it never does any of the things that would turn an observation
into a promotion:

* no fallback to the working tree, to ``HEAD``, to a branch name or to Markdown -- the only object
  ever consulted is the requested tree the caller named;
* no locator search and no line re-anchoring -- a recorded range is reported against the recorded
  blob, and a locator this increment cannot resolve is reported as unsupported rather than guessed;
* no content. The observation carries identities, an entry kind and a status; the bytes stay where
  they are, which is what keeps a read page a facts-only packet rather than a document dump.

The stored identity is preserved on every outcome, including the failures, because the recorded
attribution is a fact about what an author claimed and not a value that resolution may rewrite.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from agents_remember.kernel.git_command import run_git
from agents_remember.models.knowledge.read import AnchorResolution, KnowledgeReadContext

__all__ = ["anchor_resolver_for", "observe_anchor"]

# The shape the selection layer and the read seam agree on: one decoded realization row in, one
# observation or nothing out. Both resolvers below satisfy it, so the seam's type states what the
# selection layer actually calls instead of ``Any``.
AnchorResolver = Callable[[dict[str, Any]], "AnchorResolution | None"]

# Git's own entry modes, as ``ls-tree`` prints them. A non-blob entry is reported as what it is
# rather than resolved as a file, so a symlink or a submodule never masquerades as source bytes.
_TREE_ENTRY_MODES: dict[str, str] = {
    "120000": "symlink",
    "160000": "submodule",
    "040000": "tree",
}

# The refusal-free default for a context that requested no source resolution at all.
_NOT_REQUESTED = "not_requested"


def anchor_resolver_for(context: KnowledgeReadContext) -> AnchorResolver:
    """Return the anchor-resolution callable one read context asks for.

    A context with no exact code tree asked for no source resolution, so the resolver reports
    ``not_requested`` for every anchor **and still carries each recorded identity**. That is a
    supported state and not a degraded one: reading recorded knowledge does not require a Git object
    store, and answering with a fabricated resolution -- or with no recorded attribution at all --
    would be worse than answering that no tree was requested.
    """

    if context.code_tree_id is None or context.repository_root is None:
        return _UnrequestedAnchorResolver()
    return _TreeAnchorResolver(Path(context.repository_root), context.code_tree_id)


class _UnrequestedAnchorResolver:
    """The resolver for a read that named no code tree.

    It resolves nothing and reports exactly that, which keeps "no tree was requested" distinguishable
    from "the tree did not hold the recorded blob" -- two facts a caller acts on differently.
    """

    def __call__(self, claim: dict[str, Any]) -> AnchorResolution:
        return observe_anchor(
            claim,
            repository_root=Path(),
            tree_id="",
            tree_is_available=False,
            requested=False,
        )


class _TreeAnchorResolver:
    """One requested tree, resolved against exactly once per anchor.

    The tree's existence is checked when the resolver is built rather than per anchor, so an
    unavailable tree is one failure named once instead of an identical per-item error repeated
    across a page.
    """

    def __init__(self, repository_root: Path, tree_id: str) -> None:
        self.repository_root = repository_root
        self.tree_id = tree_id
        self.tree_is_available = _tree_exists(repository_root, tree_id)

    def __call__(self, claim: dict[str, Any]) -> AnchorResolution:
        return observe_anchor(
            claim,
            repository_root=self.repository_root,
            tree_id=self.tree_id,
            tree_is_available=self.tree_is_available,
        )


def observe_anchor(
    claim: dict[str, Any],
    *,
    repository_root: Path,
    tree_id: str,
    tree_is_available: bool = True,
    requested: bool = True,
) -> AnchorResolution:
    """Return the observation one recorded claim's anchor earns against the requested tree.

    ``claim`` is one decoded realization row: it carries the anchor's recorded path, source
    identity and locator, which is everything this function is allowed to look at.
    """

    recorded = str(_identity_object_id(claim.get("source_identity")))
    locator = claim["locator"]
    common = {
        "anchor_id": str(claim["anchor_id"]),
        "path": str(claim["path"]),
        "recorded_source_identity": recorded,
        "locator": locator,
    }
    if not requested:
        return AnchorResolution(
            **common,
            resolution=_NOT_REQUESTED,
            detail=(
                "this read named no code tree, so no source resolution was requested; the recorded "
                "claim and its blob identity are reported as authored"
            ),
        )
    if _locator_kind(locator) == "symbol":
        return AnchorResolution(
            **common,
            resolution="unsupported_locator",
            detail=(
                "the recorded locator names a symbol and no symbol extractor supports it in this "
                "increment; the recorded claim and its blob identity are unchanged"
            ),
        )
    if not tree_is_available:
        return AnchorResolution(
            **common,
            resolution="recorded_object_unavailable",
            detail=(
                f"the requested code tree {tree_id} is not available in the selected repository, "
                "and no working tree or HEAD is substituted for it"
            ),
        )
    try:
        entry = _tree_entry(repository_root, tree_id, str(claim["path"]))
    except _UnaddressableRecordedPath as unaddressable:
        return AnchorResolution(
            **common,
            resolution="unsupported_locator",
            detail=(
                f"the recorded path {unaddressable.path!r} is not a confined repository-relative "
                "tree path, so it was not addressed against the requested tree at all; this is a "
                "refusal of the spelling, not an observation that the tree lacks the entry"
            ),
        )
    except _TreeLookupFailed as failed:
        return AnchorResolution(
            **common,
            resolution="recorded_object_unavailable",
            detail=(
                f"the lookup of the recorded path in the requested code tree {failed.tree_id} did "
                f"not answer ({failed}), so the entry is unknown rather than absent and no working "
                "tree or HEAD is substituted for it"
            ),
        )
    return _observed_entry(common, entry)


def _observed_entry(common: dict[str, Any], entry: tuple[str, str, str] | None) -> AnchorResolution:
    """Turn one ``ls-tree`` entry into the observation it supports."""

    if entry is None:
        return AnchorResolution(
            **common,
            resolution="path_absent",
            detail=(
                "the requested tree contains no entry at this path; the recorded claim and its "
                "blob identity are preserved and the obligation is not retired"
            ),
        )
    mode, kind, object_id = entry
    # Git reports a symlink as kind ``blob`` with mode ``120000``: the *entry mode*, not the object
    # kind, is what distinguishes source bytes from a link, and reading the wrong one would follow a
    # recorded link as if its target were the recorded source.
    if mode in _TREE_ENTRY_MODES or kind != "blob":
        return AnchorResolution(
            **common,
            observed_source_identity=object_id,
            resolution="entry_not_blob",
            detail=(
                f"the requested tree holds a {_TREE_ENTRY_MODES.get(mode, kind)} at this path, "
                "which is not source bytes; no path is followed to manufacture one"
            ),
        )
    recorded = str(common["recorded_source_identity"])
    if object_id == recorded:
        return AnchorResolution(
            **common,
            observed_source_identity=object_id,
            resolution="exact_recorded_blob",
            detail="the requested tree holds the exact recorded blob at the recorded path",
        )
    return AnchorResolution(
        **common,
        observed_source_identity=object_id,
        resolution="recorded_blob_mismatch",
        detail=(
            "the requested tree holds different bytes at this path than the recorded blob, so the "
            "authored claim is reported against the recorded identity and is not promoted to a "
            "realization of the current bytes"
        ),
    )


def _locator_kind(locator: Any) -> str | None:
    """Return one decoded locator's discriminator, whether it decoded to a value or a mapping.

    A stored locator is read back as the typed union, and the discriminator decides which locator
    the observation is about. Reading it through one accessor means a locator that decoded as a
    mapping is classified the same way as one that decoded as a model, instead of falling through
    to the file branch and being reported as a resolved file it is not.
    """

    if isinstance(locator, dict):
        return str(locator.get("kind", "")) or None
    return getattr(locator, "kind", None)


def _identity_object_id(identity: Any) -> str:
    """Return one stored source identity's object id, however it decoded."""

    if isinstance(identity, dict):
        return str(identity.get("object_id", ""))
    return str(getattr(identity, "object_id", ""))


def _tree_exists(repository_root: Path, tree_id: str) -> bool:
    """Return whether the requested tree object is present in the selected repository."""

    result = run_git(repository_root, ["cat-file", "-e", f"{tree_id}^{{tree}}"])
    return result.returncode == 0


def _tree_entry(repository_root: Path, tree_id: str, path: str) -> tuple[str, str, str] | None:
    """Return ``(mode, kind, object_id)`` for one confined path in the requested tree, or ``None``.

    The confinement here is **structural, not filesystem resolution**, and that difference is
    deliberate. ``kernel.sidecar_pairing.confine_rel`` resolves a path against a real directory and
    therefore *follows a symlink*: asked about a recorded path that happens to be a link, it answers
    with the link's target, and the tree lookup would then describe the target instead of the entry
    the anchor actually recorded. An anchor is a location inside a Git tree, so the check this needs
    is that the stored spelling is a well-formed confined POSIX relative path -- which is also the
    check the storage boundary applied when the anchor was written.

    A ``None`` return means one thing only: **Git looked the path up and answered with no entry**,
    which is what ``path_absent`` reports. A spelling this function refuses before the lookup never
    reaches that return -- :func:`observe_anchor` refuses it as an unaddressable recorded path, so a
    caller is never told the tree lacks an entry it was never asked about. A lookup that fails for
    any other reason (a tree that disappeared between the availability check and now, or a Git
    failure) is likewise not an absence and is reported as unavailable rather than as ``path_absent``.
    """

    confined = _confined_posix_relative(path)
    if confined is None:
        raise _UnaddressableRecordedPath(path)
    try:
        result = run_git(repository_root, ["ls-tree", "-z", tree_id, "--", confined])
    except OSError as failed:
        # A Git binary this process cannot run at all is the same fact as a lookup that answered
        # with an error: the entry is unknown, not absent. It is raised as one named failure rather
        # than escaping as a raw ``OSError`` past the observation boundary.
        raise _TreeLookupFailed(tree_id, 0, str(failed)) from failed
    if result.returncode != 0:
        raise _TreeLookupFailed(tree_id, result.returncode)
    return _parse_ls_tree(result.stdout)


class _UnaddressableRecordedPath(Exception):
    """A recorded path whose spelling is not a confined repository-relative path.

    The typed boundaries refuse these spellings, so this is reachable only for a row written by
    something other than the typed write path. It is raised rather than folded into the absent
    return because the two are different facts: one says the tree was asked and holds nothing at
    that path, the other says the path could not be addressed at all.
    """

    def __init__(self, path: str) -> None:
        super().__init__(path)
        self.path = path


class _TreeLookupFailed(Exception):
    """A tree lookup that did not answer: the tree, Git's exit status, and why it could not run."""

    def __init__(self, tree_id: str, returncode: int, reason: str = "") -> None:
        super().__init__(f"git ls-tree {tree_id} exited {returncode} {reason}".strip())
        self.tree_id = tree_id
        self.returncode = returncode
        self.reason = reason


def _confined_posix_relative(path: str) -> str | None:
    """Return ``path`` when it is a well-formed confined repository-relative POSIX path, else ``None``.

    Nothing is resolved and nothing is touched on disk: this refuses an absolute path, a Windows
    drive or UNC spelling, a backslash, a NUL, any empty, ``.`` or ``..`` segment, and Git pathspec
    **magic** -- the leading-``:`` spellings such as ``:(exclude)…``, ``:!…``, ``:(top)…`` and
    ``:/…``. Refusing those is what keeps this lookup from answering a question Git was never asked
    *about this path*: ``ls-tree`` exits non-zero on magic it does not support and answers about a
    different location for the top-level forms, so without this check a malformed stored spelling
    would be published as a fact about the tree.

    The glob characters ``*``, ``?`` and ``[`` are **not** refused. ``git ls-tree`` addresses them
    literally -- measured on ``git 2.54.0``, ``src/a[1].py`` resolves that entry with ``rc=0`` even
    when ``src/a1.py`` exists -- so a repository really can hold such a path and a recorded anchor
    at one is an address like any other.
    """

    refusals = (
        not path,
        "\\" in path,
        "\x00" in path,
        path.startswith(("/", "~", ":")),
        PurePosixPath(path).is_absolute() or PureWindowsPath(path).is_absolute(),
        bool(PureWindowsPath(path).drive),
        any(part in {"", ".", ".."} for part in path.split("/")),
    )
    return None if any(refusals) else path


def _parse_ls_tree(stdout: str) -> tuple[str, str, str] | None:
    """Parse one ``ls-tree -z`` record into ``(mode, kind, object_id)``."""

    record = stdout.split("\0", 1)[0].strip()
    if not record:
        return None
    header, _, _separator = record.partition("\t")
    fields = header.split()
    if len(fields) < 3:
        return None
    return fields[0], fields[1], fields[2]
