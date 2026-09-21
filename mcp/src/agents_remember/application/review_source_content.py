"""One review inventory entry, opened into the exact content its bound pair holds (ICR-R03).

A review lists what a task changed. This module answers the next question a reviewer asks about one
of those rows -- *what does each of the two bound objects actually hold at this path* -- and it
answers it from the objects the listing named and from nothing else:

* **The generation is an input, not a lookup.** The two tree ids arrive with the request, are read
  back from the inventory the caller is looking at, and are used to address the content. The leaf's
  review is re-resolved to *measure* whether those ids are still the pair it binds now, and the
  answer travels as ``currentness``; a newer candidate is never silently substituted for the
  requested generation, and a working tree, ``HEAD`` or a branch is never a source of bytes.
* **Every side states its own truth.** A side is ``present`` (a regular file's text), ``absent``
  (this endpoint holds no entry at this path), ``binary``, ``symlink`` (the content is a link
  target), ``submodule`` (a recorded pointer with no file bytes at all) or ``unavailable`` (the
  entry could not be read, with the reason). ``absent`` and ``unavailable`` stay apart: one is a
  measured fact about the endpoint, the other a measurement that was not made.
* **The content is real and bounded, never a reference.** Text is carried up to
  :data:`EXPANSION_TEXT_BYTES` with ``truncated`` set and the object's exact size beside it, so a
  bounded expansion says it is one and the whole object stays reachable by its recorded identity.

What this module does not own: it does not diff (the shipped ``DiffPane`` renders the two texts), it
does not re-measure the change set (the inventory owner does that, and this module calls it), and it
selects nothing. The one addressable population is the inventory's own entries: a path that the
requested generation's measurement does not list is refused by name rather than read, so this route
cannot be used as a general file reader for arbitrary paths at arbitrary objects.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agents_remember.application.review_candidate_resolution import (
    ReviewCandidateResolution,
    refusal,
    require_current_candidate_identity,
    resolve_review_candidate,
)
from agents_remember.application.review_source_inventory import (
    review_inventory,
    source_tree_side,
)
from agents_remember.kernel.git_command import read_git_blob_bytes, run_git
from agents_remember.kernel.git_preparation import GitPreparationError, require_git_object_id
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.knowledge.review import (
    ReviewChangedFile,
    ReviewFileStatus,
    ReviewRefusal,
    ReviewSourceInventory,
)
from agents_remember.models.knowledge.review_source_content import (
    ReviewSourceContentRequest,
    ReviewSourceContentResult,
    ReviewSourceCurrentness,
    ReviewSourceExpansion,
    ReviewSourcePathBound,
    ReviewSourceSide,
)
from agents_remember.serving.scope import decode_capped, language_for

__all__ = [
    "EXPANSION_TEXT_BYTES",
    "SOURCE_CONTENT_REFERENCE",
    "read_review_source_content",
]

# The reference one expansion publishes for itself: the operation and the measurement rather than a
# cached artifact, because a caller acts on it by reading the two object identities beside it.
SOURCE_CONTENT_REFERENCE = "review:source-content-of-one-inventory-entry-at-the-bound-tree-pair"

# How much text one side may carry. It is the same bound the shipped file reader applies to the
# content it serves, so a reviewed file and a browsed file truncate at the same place; the exact
# object size and the object's identity travel beside the text so nothing is lost silently.
EXPANSION_TEXT_BYTES = 2 * 1024 * 1024

# The window Git itself inspects when it decides a blob is binary. It is a *reason* for this
# surface's own verdict, never the verdict: the bytes are read and classified here.
_BINARY_SNIFF_BYTES = 8000

# Git's own entry modes, as ``ls-tree`` prints them. The change inventory reads the same modes for a
# different fact (whether a change is renderable at all); this module interprets them for the
# content read, which is why the two name them separately.
_SUBMODULE_MODE = "160000"
_SYMLINK_MODE = "120000"
_TREE_MODE = "040000"

# The states whose object is a blob this surface may print whole from its recorded identity.
_BLOB_STATES: tuple[str, ...] = ("present", "binary", "symlink")

_ABSENT_DETAIL = (
    "this endpoint holds no entry at this path, which is a measured fact about the requested tree "
    "and not an empty file: the change status beside it says how the path relates to the other side"
)
_SYMLINK_DETAIL = (
    "this endpoint's entry is a symlink, so the text shown is the link target recorded in the tree "
    "and not the bytes of a document"
)
_SYMLINK_UNTEXTUAL_DETAIL = (
    "this endpoint's entry is a symlink whose target is not valid UTF-8, so no lossless text form of "
    "it exists; the entry's identity and size are stated instead"
)
_SUBMODULE_DETAIL = (
    "this endpoint's entry is a submodule pointer to the recorded commit {object_id}: the entry has "
    "no file bytes of its own, and the content shown is that pointer rather than the submodule's "
    "working tree"
)


def read_review_source_content(
    config: McpRuntimeConfig, request: ReviewSourceContentRequest
) -> ReviewSourceContentResult:
    """Open one inventory entry's actual content against the exact generation the caller named.

    The task context is resolved first and the request's generation is checked against it, so the
    two ways an expansion can be inadmissible -- a baseline that is not this leaf's recorded base,
    and a path this surface cannot address -- are answered before any object is read. The bytes then
    come from the requested trees, and the leaf's current candidate is measured afterwards so the
    answer can say whether the caller is reading the candidate's own source or a superseded
    generation's.
    """

    resolved = resolve_review_candidate(
        config, request.repository_id, request.master, request.leaf_id
    )
    if isinstance(resolved, ReviewRefusal):
        return _refused(request.repository_id, resolved)
    inadmissible = _inadmissible(request, resolved)
    if inadmissible is not None:
        return _refused(request.repository_id, inadmissible)
    return _content(request, resolved)


def _refused(repository_id: str, why: ReviewRefusal) -> ReviewSourceContentResult:
    """One refused expansion: the typed refusal and no content beside it."""

    return ReviewSourceContentResult(state="refused", repository_id=repository_id, refusal=why)


def _inadmissible(
    request: ReviewSourceContentRequest, resolved: ReviewCandidateResolution
) -> ReviewRefusal | None:
    """The named refusal this request earns before any object is read, or ``None`` when admissible.

    Four facts are checked and each is a distinct refusal: the two ids must be complete Git object
    identities (an abbreviation, a ref or an option spelling addresses nothing exact), the baseline
    must be the recorded base of *this* leaf's review (the review compares that base and no other
    generation), the after generation must name a *tree* rather than a commit, a blob or a tag, and
    the path must be a spelling this surface can hand to Git as a pathspec.
    """

    for side, tree_id in (
        ("before", request.before_code_tree_id),
        ("after", request.after_code_tree_id),
    ):
        try:
            require_git_object_id(tree_id)
        except GitPreparationError:
            return refusal(
                "source_content_unresolved",
                (
                    f"the requested {side} code tree {tree_id!r} is not a complete Git object "
                    "identity, so no exact generation can be addressed; an abbreviation, a branch "
                    "name and a symbolic revision are all refused rather than resolved"
                ),
                next_action=(
                    "expand an entry from the inventory the review returned, whose own "
                    "before_code_tree_id and after_code_tree_id are complete object identities"
                ),
                offending_input=_input(tree_id),
            )
    if request.before_code_tree_id != resolved.baseline_code_tree_id:
        return refusal(
            "source_content_unresolved",
            (
                f"the requested baseline tree {request.before_code_tree_id} is not the baseline "
                f"this leaf's review binds ({resolved.baseline_code_tree_id}), so the content asked "
                "for belongs to a comparison this review did not make; the surface reads the "
                "recorded base of this leaf's enclosure and substitutes no other generation"
            ),
            next_action=(
                "reopen the review and expand an entry from the inventory its response published"
            ),
            offending_input=_input(request.before_code_tree_id),
        )
    not_a_tree = _non_tree_generation(resolved.candidate_code_root, request.after_code_tree_id)
    if not_a_tree is not None:
        return refusal(
            "source_content_unresolved",
            not_a_tree,
            next_action=(
                "expand an entry from the inventory the review returned, whose after code tree id "
                "is the captured candidate tree"
            ),
            offending_input=_input(request.after_code_tree_id),
        )
    if not _addressable(request.path):
        return refusal(
            "source_content_unresolved",
            (
                f"the requested path {request.path!r} is not a repository-relative path this "
                "surface can address inside a tree, so no content was read for it"
            ),
            next_action="expand a path exactly as the inventory listed it",
            offending_input=_input(request.path),
        )
    return None


def _non_tree_generation(root: Path | None, tree_id: str) -> str | None:
    """Why one named generation is not a *tree*, or ``None`` when it is one or is not held here.

    ``after_code_tree_id`` is documented as a tree id and the review binds trees: the recorded base
    commit names a tree and the candidate is the captured tree. An object this repository holds and
    that is not a tree -- a commit id, a blob, a tag -- is refused by name rather than peeled or
    served, because answering a tree question with another object's content is the substitution this
    read exists to prevent. A ``HEAD`` commit id is the reachable case: it can be diffed and looked
    up, so only the object's own type separates it from the generation the listing published.

    An object this repository does **not** hold is deliberately not refused here: a missing object is
    a measurement this read reports on the side it affects (``unavailable``, with its reason), and
    refusing would hide the readable side of a comparison that is otherwise inspectable.
    """

    if root is None:
        return None
    result = run_git(root, ["cat-file", "-t", tree_id])
    if result.returncode != 0:
        return None
    kind = result.stdout.strip()
    if kind == "tree":
        return None
    return (
        f"the requested after generation {tree_id} names a {kind or 'unknown'} object this "
        "repository holds, and this read addresses code trees: a commit, a blob and a tag are "
        "refused by name rather than peeled into a tree or served as the generation the listing "
        "published"
    )


def _content(
    request: ReviewSourceContentRequest, resolved: ReviewCandidateResolution
) -> ReviewSourceContentResult:
    """The expansion itself: the measured entry, both sides' content, and the generation statement."""

    before_root = resolved.baseline_code_root
    after_root = resolved.candidate_code_root
    inventory = review_inventory(
        source_tree_side(request.before_code_tree_id, before_root),
        source_tree_side(request.after_code_tree_id, after_root),
    )
    admission = _admit(request, resolved, inventory, before_root, after_root)
    if isinstance(admission, ReviewRefusal):
        return _refused(request.repository_id, admission)
    before = _side_content(before_root, request.before_code_tree_id, request.path)
    after = _side_content(after_root, request.after_code_tree_id, request.path)
    currentness, currentness_detail = _currentness(request.after_code_tree_id, resolved)
    return ReviewSourceContentResult(
        state="content",
        repository_id=request.repository_id,
        expansion=ReviewSourceExpansion(
            path=request.path,
            status=_status(admission.entry),
            mode_change=False if admission.entry is None else admission.entry.mode_change,
            language=language_for(Path(request.path)),
            before=before,
            after=after,
            before_code_tree_id=request.before_code_tree_id,
            after_code_tree_id=request.after_code_tree_id,
            currentness=currentness,
            currentness_detail=currentness_detail,
            path_bound=admission.path_bound,
            path_bound_detail=admission.path_bound_detail,
            reference=SOURCE_CONTENT_REFERENCE,
            command=_reproduction(
                request.path,
                (before_root, request.before_code_tree_id, before),
                (after_root, request.after_code_tree_id, after),
            ),
        ),
    )


@dataclass(frozen=True)
class _Admission:
    """Which measured change set admitted one path, and the entry it carried when it was the requested one."""

    entry: ReviewChangedFile | None
    path_bound: ReviewSourcePathBound
    path_bound_detail: str


def _admit(
    request: ReviewSourceContentRequest,
    resolved: ReviewCandidateResolution,
    inventory: ReviewSourceInventory,
    before_root: Path | None,
    after_root: Path | None,
) -> _Admission | ReviewRefusal:
    """The measured change set that admits the requested path, or the refusal that none does.

    Two measurements can admit a path and the second exists only because the first may be unavailable.
    The requested generation's own change set is asked first; a path it lists is admitted with that
    entry's own status. When that measurement could not be made, the request is bounded by the change
    set **this leaf's review actually publishes** -- its recorded baseline against the candidate tree
    it binds now -- so a path is still read only from a measured pair. A path no measurement admits is
    refused in every state, which is what keeps this route a change-set read rather than a general file
    reader over the recorded base.
    """

    entry = _entry_for(inventory, request.path)
    if entry is not None:
        return _Admission(
            entry=entry,
            path_bound="requested_generation",
            path_bound_detail=(
                "the requested generation's own change set is the measurement that lists this path"
            ),
        )
    if inventory.state == "measured":
        return _not_listed(request, inventory)
    leaf = review_inventory(
        source_tree_side(resolved.baseline_code_tree_id, before_root),
        source_tree_side(resolved.candidate_code_tree_id, after_root),
    )
    if leaf.state == "measured" and _entry_for(leaf, request.path) is not None:
        return _Admission(
            entry=None,
            path_bound="leaf_change_set",
            path_bound_detail=(
                f"the requested generation could not be measured ({inventory.detail}), so the "
                f"change set this leaf's review publishes -- its recorded baseline against the "
                f"candidate tree it binds now, {leaf.listed_total} changed path(s) -- is the "
                "measurement that lists this path"
            ),
        )
    return _unconfined(request, inventory, leaf)


def _unconfined(
    request: ReviewSourceContentRequest,
    requested: ReviewSourceInventory,
    leaf: ReviewSourceInventory,
) -> ReviewRefusal:
    """The refusal for a path no measured change set admits while the requested pair could not be read."""

    bound = (
        "the change set this leaf's review publishes lists "
        f"{leaf.listed_total} changed path(s) and does not list this one"
        if leaf.state == "measured"
        else (
            "the change set this leaf's review publishes could not be measured either "
            f"({leaf.detail}), so no measurement admits this path"
        )
    )
    return refusal(
        "source_content_unresolved",
        (
            f"the requested after generation could not be measured against this leaf's recorded "
            f"baseline ({requested.detail}), and {bound}: no content was read for the requested "
            f"path {request.path!r}. An entry is expanded from a measured change set -- the requested "
            "generation's "
            "or, when that cannot be measured, the one this leaf's review publishes -- and this "
            "route reads no path outside one"
        ),
        next_action=(
            "expand a path the leaf's own inventory listed, or reopen the review so the requested "
            "generation is measured again"
        ),
        offending_input=_input(request.path),
    )


def _not_listed(
    request: ReviewSourceContentRequest, inventory: ReviewSourceInventory
) -> ReviewRefusal:
    """The refusal for a path the requested generation's own measurement does not list."""

    return refusal(
        "source_content_unresolved",
        (
            f"the requested path {request.path!r} is not one of the {inventory.listed_total} changed "
            "path(s) this surface measured between the requested trees, so no content was read for "
            "it; an entry is expanded from the inventory's own measurement and this route reads no "
            "path outside it"
        ),
        next_action=(
            "expand a path the inventory listed for this generation, or reopen the review if the "
            "generation has moved"
        ),
        offending_input=_input(request.path),
    )


def _status(entry: ReviewChangedFile | None) -> ReviewFileStatus:
    """The entry's measured status, or ``unknown`` when the pair's change set was not measured.

    An unmeasured pair is not a reason to answer nothing: each side is still read on its own, and the
    status states that the change classification was not made rather than guessing one from the
    bytes that happen to be there.
    """

    return "unknown" if entry is None else entry.status


def _entry_for(inventory: ReviewSourceInventory, path: str) -> ReviewChangedFile | None:
    """The inventory's own entry for one path, matched exactly as the address it is."""

    for entry in inventory.entries:
        if entry.path == path:
            return entry
    return None


def _currentness(
    requested_after: str, resolved: ReviewCandidateResolution
) -> tuple[ReviewSourceCurrentness, str]:
    """Whether the requested candidate generation is still the one this leaf's review binds now.

    The recheck is the shipped one: the capture owner recomputes the candidate identity and any
    moved input means the leaf no longer holds the candidate this read started from, so "current"
    cannot be claimed and is not.
    """

    moved = require_current_candidate_identity(resolved)
    if moved is not None:
        return (
            "unmeasured",
            (
                "whether the requested generation is still the leaf's current candidate could not "
                f"be measured: {moved.detail}. The content beside it is the requested generation's, "
                "byte for byte, and no newer generation was substituted for it"
            ),
        )
    if requested_after == resolved.candidate_code_tree_id:
        return (
            "current",
            (
                "the requested candidate tree is the tree this leaf's review binds now, so the "
                "content beside it is the candidate's own current source for this path"
            ),
        )
    return (
        "superseded",
        (
            f"the leaf's candidate tree has moved since this generation was listed (requested "
            f"{requested_after}, bound now {resolved.candidate_code_tree_id}), so the content beside "
            "it is the requested generation's, byte for byte, and not the candidate's current "
            "source; the leaf's current source is read by reopening the review"
        ),
    )


# --- one endpoint's content -------------------------------------------------------------------


@dataclass(frozen=True)
class _TreeEntry:
    """One ``ls-tree`` record: Git's mode and type for the path, and the object it names."""

    mode: str
    kind: str
    object_id: str


@dataclass(frozen=True)
class _Unreadable:
    """One lookup that did not answer, with the sentence that says which measurement failed."""

    detail: str


def _side_content(root: Path | None, tree_id: str, path: str) -> ReviewSourceSide:
    """One bound endpoint's content at one path: its own state and, when textual, its own text.

    The three outcomes are kept apart deliberately. A root the resolution did not bind and a lookup
    that did not answer are ``unavailable`` (nothing was measured); a lookup that answered with no
    entry is ``absent`` (the endpoint was measured and holds nothing there); anything else is read
    from the object the entry names.
    """

    if root is None:
        return _unavailable(
            "this side named an exact code tree without the repository root that holds it, so the "
            "tree could not be read and no working tree or HEAD was substituted for it"
        )
    found = _tree_entry(root, tree_id, path)
    if isinstance(found, _Unreadable):
        return _unavailable(found.detail)
    if found is None:
        return ReviewSourceSide(state="absent", detail=_ABSENT_DETAIL)
    return _entry_content(root, found)


def _entry_content(root: Path, entry: _TreeEntry) -> ReviewSourceSide:
    """One entry's content, classified by what the entry *is* before what its bytes happen to be."""

    if entry.kind == "tree" or entry.mode == _TREE_MODE:
        return _unavailable(
            "this path is a tree object at this endpoint rather than a file's content, and this "
            "surface renders no directory listing as source"
        )
    if entry.kind == "commit" or entry.mode == _SUBMODULE_MODE:
        return ReviewSourceSide(
            state="submodule",
            object_id=entry.object_id,
            detail=_SUBMODULE_DETAIL.format(object_id=entry.object_id),
        )
    raw = _blob_bytes(root, entry.object_id)
    if raw is None:
        return _unavailable(
            f"the blob {entry.object_id} this endpoint's entry names could not be read, so this "
            "side's content is unknown rather than empty"
        )
    if entry.mode == _SYMLINK_MODE:
        return _symlink_content(raw, entry)
    return _blob_content(raw, entry)


def _blob_content(raw: bytes, entry: _TreeEntry) -> ReviewSourceSide:
    """A regular file's bytes as text, or the stated reason they cannot be carried as text."""

    decoded = _decoded(raw)
    if decoded is None:
        return ReviewSourceSide(
            state="binary",
            object_id=entry.object_id,
            byte_length=len(raw),
            detail=_binary_detail(raw),
        )
    text, truncated = decoded
    return ReviewSourceSide(
        state="present",
        text=text,
        object_id=entry.object_id,
        byte_length=len(raw),
        truncated=truncated,
        detail=_text_detail(len(raw), truncated),
    )


def _symlink_content(raw: bytes, entry: _TreeEntry) -> ReviewSourceSide:
    """A symlink's target as text when it has one, and its identity when it does not."""

    decoded = _decoded(raw)
    if decoded is None:
        return ReviewSourceSide(
            state="symlink",
            object_id=entry.object_id,
            byte_length=len(raw),
            detail=_SYMLINK_UNTEXTUAL_DETAIL,
        )
    target, truncated = decoded
    return ReviewSourceSide(
        state="symlink",
        text=target,
        object_id=entry.object_id,
        byte_length=len(raw),
        truncated=truncated,
        detail=_SYMLINK_DETAIL,
    )


def _decoded(raw: bytes) -> tuple[str, bool] | None:
    """The blob's text and whether it was bounded, or ``None`` when no text form exists.

    Two different measurements produce ``None`` and both are stated by the caller: bytes Git itself
    would call binary, and bytes that are simply not valid UTF-8 -- a text file in another encoding
    has no lossless spelling in this vocabulary, so it is reported as unrenderable rather than
    re-encoded into a name and a document this repository does not hold.
    """

    if b"\x00" in raw[:_BINARY_SNIFF_BYTES]:
        return None
    try:
        return decode_capped(raw, EXPANSION_TEXT_BYTES)
    except UnicodeDecodeError:
        return None


def _binary_detail(raw: bytes) -> str:
    """Why one side's bytes cannot be rendered, in the reader's own terms."""

    if b"\x00" in raw[:_BINARY_SNIFF_BYTES]:
        return (
            f"this endpoint holds {len(raw)} byte(s) whose first {_BINARY_SNIFF_BYTES} contain a NUL "
            "byte, so the content is binary and is not rendered as text; the exact object identity "
            "and size are stated instead and the whole object stays readable from them"
        )
    return (
        f"this endpoint holds {len(raw)} byte(s) that are not valid UTF-8, so no lossless text form "
        "of the content exists in this vocabulary and it is not re-encoded into one; the exact "
        "object identity and size are stated instead"
    )


def _text_detail(size: int, truncated: bool) -> str:
    """What one carried text is: the whole object, or a stated prefix of it."""

    if truncated:
        return (
            f"this endpoint's object is {size} byte(s) and the text above is the first "
            f"{EXPANSION_TEXT_BYTES} byte(s) of it, cut on a character boundary: a bounded expansion "
            "rather than the whole object, which stays readable by the identity beside it"
        )
    return f"the complete content of this endpoint's {size}-byte object"


def _unavailable(detail: str) -> ReviewSourceSide:
    """One side whose content could not be measured, with the reason it could not."""

    return ReviewSourceSide(state="unavailable", detail=detail)


# --- the tree read ----------------------------------------------------------------------------


def _tree_entry(root: Path, tree_id: str, path: str) -> _TreeEntry | _Unreadable | None:
    """One path's entry in one tree, or ``_Unreadable``, or ``None`` when the tree holds none.

    ``ls-tree`` is asked with a **literal** pathspec, because a measured pathname is an address and
    not a pattern: without the magic, a name beginning with ``:`` would be read as pathspec magic and
    a name holding ``*`` or ``[`` could match a neighbour -- measured on ``git 2.54.0``, where
    ``ls-tree -- ':colon.py'`` exits 0 and prints nothing while ``:(literal):colon.py`` prints the
    entry. The answer is checked against the requested path before it is used, so a record for some
    other path is a failed lookup rather than a silent substitution.
    """

    try:
        result = run_git(root, ["ls-tree", "-z", tree_id, "--", f":(literal){path}"])
    except OSError as failure:
        return _Unreadable(
            f"the entry at this path could not be looked up in tree {tree_id} at {root} because Git "
            f"could not be run ({failure}), so this side's content is unknown rather than absent"
        )
    if result.returncode != 0:
        return _Unreadable(
            f"the entry at this path could not be looked up in tree {tree_id} at {root} (git "
            f"ls-tree exited {result.returncode}), so this side's content is unknown rather than "
            "absent and no other tree was substituted"
        )
    return _parsed_record(result.stdout, path)


def _parsed_record(output: str, path: str) -> _TreeEntry | _Unreadable | None:
    """The one record ``ls-tree`` answered with, checked against the path that was asked about."""

    record = output.split("\0", 1)[0]
    if not record:
        return None
    header, _separator, named = record.partition("\t")
    fields = header.split()
    if len(fields) < 3 or named != path:
        return _Unreadable(
            "the tree lookup answered with a record this surface does not read as the requested "
            "path's entry, so this side's content is unknown rather than another entry's bytes"
        )
    return _TreeEntry(mode=fields[0], kind=fields[1], object_id=fields[2])


def _blob_bytes(root: Path, object_id: str) -> bytes | None:
    """The exact bytes of one blob, or ``None`` when this repository does not hold it.

    The read is byte-exact and undecoded (the kernel's own blob reader, which requires a complete
    object identity), so a file's content is never normalized on its way to a reader.
    """

    try:
        return read_git_blob_bytes(root, object_id)
    except (GitPreparationError, OSError):
        return None


# --- the reproduction line --------------------------------------------------------------------


def _reproduction(
    path: str,
    before: tuple[Path | None, str, ReviewSourceSide],
    after: tuple[Path | None, str, ReviewSourceSide],
) -> str:
    """The exact commands that reproduce both sides, naming every object the answer rests on."""

    return "\n".join(
        (
            f"before: {_side_command(*before, path=path)}",
            f"after: {_side_command(*after, path=path)}",
        )
    )


def _side_command(root: Path | None, tree_id: str, side: ReviewSourceSide, *, path: str) -> str:
    """One side's reproduction: locate the entry, then print the object whole when it has one."""

    where = "<no root bound>" if root is None else str(root)
    lookup = f"git -C {where} ls-tree -l {tree_id} -- {_quoted(path)}"
    if side.state in _BLOB_STATES and side.object_id is not None:
        return f"{lookup} ; git -C {where} cat-file blob {side.object_id}"
    if side.state == "submodule" and side.object_id is not None:
        return f"{lookup} ; git -C {where} cat-file -t {side.object_id}"
    return lookup


def _quoted(path: str) -> str:
    """One path spelled so the printed command can be pasted, whatever the name contains."""

    if not any(character.isspace() for character in path):
        return path
    return "'" + path.replace("'", "'\\''") + "'"


def _addressable(path: str) -> bool:
    """Whether one path is a repository-relative spelling this surface will hand to Git.

    The measured population is already confined to the inventory's own entries, so this is the
    second of two checks and not the first: it refuses the spellings Git could not have reported for
    a changed path (an empty name, a NUL, an absolute path, a ``..`` segment) before any argv is
    built from it.
    """

    if not path or "\x00" in path or path.startswith("/"):
        return False
    return ".." not in path.split("/")


def _input(text: str) -> str:
    """One offending input, bounded to the field that carries it.

    A path is bounded by the path limit and an offending input by the shorter reference limit, so a
    very long path is truncated here rather than failing the refusal that names it.
    """

    return text if len(text) <= 512 else f"{text[:508]}..."
