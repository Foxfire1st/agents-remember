"""Custody measurement and explicit release for the code objects a comparison once bound.

A captured candidate tree is not in any commit. It was written by the capture owner through a private
index, so nothing points at it except an object reference that survives reclamation: a retention
commit whose tree is the captured tree and whose parent is the recorded base commit, pointed at by
``refs/ar/retained-code/...``. Nothing writes those pins any more (the reviewer compares Git trees,
MIK-R25); this module reads and releases the ones earlier versions wrote:

* **Custody is measured, never assumed -- and only *named* history counts.** :func:`code_object_custody`
  asks whether the tree is held by the durable history the caller names: the protected source branch
  and the commits a task record landed. It deliberately does **not** sweep every local branch tip,
  because a leaf's own work branch is disposable -- ``worktree_abandon`` force-deletes it, and
  ``worktree remove`` plus ``branch -D`` is an ordinary end to a leaf -- so a commit that exists only
  on it is not custody at all. A tree that no named history holds is ``retained``, which keeps the pin:
  that is the safe direction to be wrong in, and it is why a custody measurement never *releases*
  anything by itself. A tree that survives only deeper in a named branch's history is likewise still
  reported ``retained``, because only the ref's own tip is examined.
* **Release is explicit and leaves a record.** :func:`release_retained_code_object` refuses to delete
  a ref that no longer points at the commit it recorded -- deleting *that* would discard whatever
  object somebody else's history now depends on -- and returns a
  :class:`ReleasedCodeObject` naming the ref, the tree, the measured custody and the reason. That
  value is what a reader stores as the unavailable-history record, so a later reopen answers "this
  history was deleted, here is why" instead of silently resolving to whatever is at the path today.

Nothing here decides *when* a comparison is finished with a tree, or what the retention means. This
module moves one object reference and reports what it found; the caller owns the policy, the record
and the lifetime.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from agents_remember.errors import CodeObjectRetentionError
from agents_remember.kernel.git_command import run_git

__all__ = [
    "CUSTODY_COMMITTED_HISTORY",
    "CUSTODY_RETAINED",
    "CUSTODY_UNREADABLE",
    "RETAINED_CODE_REF_NAMESPACE",
    "CodeObjectCustody",
    "CodeObjectObservation",
    "CustodyNames",
    "ReleasedCodeObject",
    "RetainedCodeObject",
    "code_object_custody",
    "code_object_observation",
    "object_readable",
    "release_retained_code_object",
    "retained_object_readable",
]

# The one ref namespace this module creates refs under. A namespace of its own rather than a branch:
# retention is not a line of development, it must not be fetched, pushed or merged, and a reader can
# find every pin this feature holds with one ``git for-each-ref`` over exactly this prefix.
RETAINED_CODE_REF_NAMESPACE = "refs/ar/retained-code"

# The two answers custody can have. ``retained`` means this feature's ref is the only thing keeping
# the object alive; ``committed-history`` means a durable branch already holds it and the pin has
# become redundant.
CUSTODY_RETAINED: Literal["retained"] = "retained"
CUSTODY_COMMITTED_HISTORY: Literal["committed-history"] = "committed-history"
CodeObjectCustody = Literal["retained", "committed-history"]

# The third answer a *measurement* can have, and the reason it is separate from the custody type: an
# object that does not resolve is not held by anything, so reporting it as ``retained`` would claim a
# pin is holding bytes that are gone -- which is exactly the state a released-and-reclaimed history is
# in. A record never stores this value; only a reader that is looking at the world right now reports it.
CUSTODY_UNREADABLE: Literal["absent"] = "absent"
CodeObjectObservation = Literal["retained", "committed-history", "absent"]

_GIT_OBJECT = r"^[0-9a-f]{40}$|^[0-9a-f]{64}$"


@dataclass(frozen=True)
class CustodyNames:
    """The durable history one custody measurement is allowed to find the tree in.

    ``durable_refs`` are full ref names whose history outlives the work that produced the tree -- a
    leaf's protected source branch -- and ``recorded_commits`` are the exact commits a task record
    landed. They travel as one value because a measurement is only comparable with another measurement
    that asked the same names, and because an empty set is a *statement*: nothing durable was named,
    so nothing durable holds the tree and the pin stays.
    """

    durable_refs: tuple[str, ...] = field(default_factory=tuple)
    recorded_commits: tuple[str, ...] = field(default_factory=tuple)


class RetainedCodeObject(BaseModel):
    """One retention pin: the ref, the commit it points at, and the two objects it keeps alive.

    Every field is a fact about the repository this module observed, and the four travel together:
    releasing requires the *exact* commit the ref was created for, so a record that carried only a
    ref name could not tell "the object I pinned" from "whatever that ref points at now".
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    ref: str = Field(min_length=1, max_length=1024)
    commit: str = Field(pattern=_GIT_OBJECT)
    tree: str = Field(pattern=_GIT_OBJECT)
    base_commit: str = Field(pattern=_GIT_OBJECT)


class ReleasedCodeObject(BaseModel):
    """One explicit release: what was deleted, what custody was measured, and why it was deleted.

    This is the value a caller stores as the unavailable-history record. ``custody_at_release`` is
    the three-way measurement taken *before* the ref was deleted, because that is the difference
    between a release that discarded the last copy of a tree and one that merely stopped duplicating
    history a branch already holds -- and a reader of the record cannot recover that distinction
    afterwards. It includes the ``absent`` case honestly: releasing a pin whose tree had already been
    reclaimed is exactly the situation a record is most needed for.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    ref: str = Field(min_length=1, max_length=1024)
    commit: str = Field(pattern=_GIT_OBJECT)
    tree: str = Field(pattern=_GIT_OBJECT)
    custody_at_release: CodeObjectObservation
    reason: str = Field(min_length=1, max_length=20000)
    recorded_at: str = Field(min_length=1, max_length=512)


def object_readable(code_repo: Path, object_id: str) -> bool:
    """Return whether one object is present and readable in this repository.

    A question, not a check: "the object this comparison bound is still here" is a fact a caller
    reports in its own voice -- a refusal while freezing, an unavailable channel while reopening --
    so this answers with a boolean instead of raising.
    """

    if not object_id:
        return False
    return run_git(code_repo, ["cat-file", "-e", object_id]).returncode == 0


def code_object_custody(
    code_repo: Path, tree: str, names: CustodyNames | None = None
) -> CodeObjectCustody:
    """Whether the durable history the caller *names* holds this tree.

    ``names`` carries the refs whose history survives the work that produced the tree -- a leaf's
    protected source branch, spelled as a full ref -- and the exact commits a task record landed. Both
    are examined and nothing else is: the question is not "is this tree
    somewhere in this repository", it is "will it still be there when this leaf's disposable state is
    gone". A leaf's own work branch is deleted by ``worktree_abandon`` and by ordinary cleanup, so a
    commit that lives only on it is not custody, and naming no refs at all therefore means *nothing*
    durable holds the tree.

    The measurement is bounded by the number of names and never walks history: a tree that survives
    only deeper in a named branch's past is still reported ``retained``. Keeping a redundant pin is the
    safe direction to be wrong in; releasing one that was the only reference is not.

    A caller that has not established the tree is readable should ask
    :func:`code_object_observation` instead: "nothing durable holds it" and "it is not there at all"
    are different facts, and this function only answers the first.
    """

    for commit in _named_commits(code_repo, names):
        if _commit_tree(code_repo, commit) == tree:
            return CUSTODY_COMMITTED_HISTORY
    return CUSTODY_RETAINED


def code_object_observation(
    code_repo: Path, tree: str, names: CustodyNames | None = None
) -> CodeObjectObservation:
    """The three-way measurement: absent, or which durable history holds it.

    This is the reader's question, and it is deliberately not the record's: a manifest stores whether
    a *pin was needed* when it was frozen, while a reopen reports what the repository holds now. An
    object that does not resolve is ``absent`` -- never ``retained``, which would say a pin is holding
    bytes that are no longer there.
    """

    if not object_readable(code_repo, tree):
        return CUSTODY_UNREADABLE
    return code_object_custody(code_repo, tree, names)


def retained_object_readable(code_repo: Path, retained: RetainedCodeObject) -> bool:
    """Whether a recorded pin still resolves to the exact commit it was created for.

    Both halves are asked, because they are two different failures: the ref may be gone (the pin was
    released) or it may point somewhere else (something re-pointed it). A reader that asked only
    whether the object still exists could not tell either from a healthy pin.
    """

    return _ref_commit(code_repo, retained.ref) == retained.commit


def release_retained_code_object(
    code_repo: Path,
    retained: RetainedCodeObject,
    *,
    reason: str,
    recorded_at: str,
    names: CustodyNames | None = None,
) -> ReleasedCodeObject:
    """Delete one pin explicitly, and return the record that says so.

    The ref is deleted **only** while it still points at the commit the record names. A ref that has
    moved is refused, because deleting it would discard an object this pin never bound -- which is
    how a "release" comes to delete somebody else's only copy while reporting that it cleaned up its
    own.

    An already-absent ref is not a failure: a retry of an interrupted release converges on the same
    record instead of reporting a second, contradictory outcome.

    ``names`` is the history the release measures custody against, and it comes from the record that
    created the pin: the same names the freeze measured, so the release record states a custody
    comparable with the one the manifest recorded.
    """

    custody = code_object_observation(code_repo, retained.tree, names)
    current = _ref_commit(code_repo, retained.ref)
    if current is not None and current != retained.commit:
        raise CodeObjectRetentionError(
            "code-object-ref-moved",
            f"the retention ref {retained.ref} points at {current}, not at the recorded commit "
            f"{retained.commit}; refusing to delete a ref that no longer names what this pin bound",
        )
    if current is not None:
        delete = run_git(code_repo, ["update-ref", "-d", retained.ref, retained.commit])
        if delete.returncode != 0:
            raise CodeObjectRetentionError(
                "code-object-ref-unwritable",
                f"the retention ref {retained.ref} could not be deleted: "
                f"{_diagnostic(delete.stderr, delete.stdout)}",
            )
    return ReleasedCodeObject(
        ref=retained.ref,
        commit=retained.commit,
        tree=retained.tree,
        custody_at_release=custody,
        reason=reason,
        recorded_at=recorded_at,
    )


# -- the Git questions this module asks ---------------------------------------------------------


def _ref_commit(code_repo: Path, ref: str) -> str | None:
    """The commit one ref resolves to, or ``None`` when the ref is absent."""

    resolved = run_git(code_repo, ["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"])
    return resolved.stdout.strip() if resolved.returncode == 0 and resolved.stdout.strip() else None


def _named_commits(code_repo: Path, names: CustodyNames | None) -> tuple[str, ...]:
    """The exact commits the caller named: each durable ref resolved, then the recorded commits.

    Resolved through ``^{commit}`` so a ref that names no commit -- a missing branch, a tag on a blob
    -- contributes nothing rather than being compared as if it were history. Duplicates are dropped so
    the same commit named twice is examined once.
    """

    named = names or CustodyNames()
    resolved: list[str] = []
    for ref in named.durable_refs:
        commit = _commit_of(code_repo, ref)
        if commit:
            resolved.append(commit)
    for commit in named.recorded_commits:
        if _commit_of(code_repo, commit):
            resolved.append(commit)
    return tuple(dict.fromkeys(resolved))


def _commit_of(code_repo: Path, name: str) -> str:
    """One commit a ref or an id resolves to, or the empty string when it resolves to none."""

    if not name:
        return ""
    found = run_git(code_repo, ["rev-parse", "--verify", "--quiet", f"{name}^{{commit}}"])
    return found.stdout.strip() if found.returncode == 0 else ""


def _commit_tree(code_repo: Path, commit: str) -> str:
    """The tree one commit names, or the empty string when it cannot be read."""

    resolved = run_git(code_repo, ["rev-parse", "--verify", "--quiet", f"{commit}^{{tree}}"])
    return resolved.stdout.strip() if resolved.returncode == 0 else ""


def _diagnostic(stderr: str, stdout: str) -> str:
    """One Git failure's own words, transport-safe, for a refusal that has to be actionable."""

    return (
        " ".join((stderr.strip() or stdout.strip() or "git reported no detail").split())
        or "git reported no detail"
    )
