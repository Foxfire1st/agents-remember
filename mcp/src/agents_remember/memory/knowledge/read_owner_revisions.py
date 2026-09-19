"""Observe one recorded prose owner revision against the memory object store.

A binding records *which revision of which document* a citation key was authored against. Resolving
that is a fact about a Git object, and this module reports which fact it is without ever doing any of
the things that would turn the observation into a promotion:

* **no fallback to the working tree, to ``HEAD``, to a branch name, or to a path.** The only object
  ever consulted is the one whose identity the binding already stored, addressed as
  ``<object_id>^{blob}``. A document that has moved on, been rewritten, or been deleted is *not*
  re-read from disk and is not re-resolved from its path: the recorded revision is either in the
  object store or it is not, and the second case is a reported state rather than a lookup that
  quietly succeeds against different bytes.
* **no content is returned for the resolution itself.** The observation carries the recorded
  identity, the path it was recorded at, and a status; the bytes are fetched separately and only
  when a caller has asked to compare a written key against them, because a read page is a facts-only
  packet rather than a document dump.
* **the stored identity is preserved on every outcome**, including the failures. A missing revision
  is never a reason to retire a stored attribution (``memory/knowledge/anchors.py``).

The one place this module and the shipped anchor reader differ is *what* they look at, and the
difference is deliberate rather than a second authority. ``read_anchors`` resolves a recorded path
inside a **requested tree** and reports whether that tree holds the recorded blob -- the code side,
where a path can have moved. Here the identity *is* the address: a binding's owner revision is
already an exact object identity, so there is no path to look up and nothing to relocate. The shipped
literals are reused verbatim anyway, because the facts coincide exactly:
``exact_recorded_blob`` means the recorded bytes are the ones addressed, and
``recorded_object_unavailable`` means they cannot be obtained.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agents_remember.kernel.git_command import run_git
from agents_remember.models.knowledge.citation import CitationBindingState, ProseOwnerRevision

__all__ = [
    "OWNER_REVISION_STATES",
    "OwnerRevisionObservation",
    "OwnerRevisionResolver",
    "owner_revision_resolver_for",
]


# The states one owner-revision observation can report, in the closed binding vocabulary. They are
# named here as a tuple rather than restated at each return site, so a case can assert that this
# module reports nothing outside the vocabulary the closure counts, and so "no memory repository was
# requested" has a declared answer instead of an accidental one.
OWNER_REVISION_STATES: tuple[CitationBindingState, ...] = (
    "exact_recorded_blob",
    "recorded_object_unavailable",
)


@dataclass(frozen=True)
class OwnerRevisionObservation:
    """One fact about one recorded owner revision: what was addressed, and what answered.

    ``detail`` is a *rendering of the recorded basis* -- the path, the identity and the state -- and
    not authored prose, so a verdict cannot be written into it. It is built by
    :func:`render_owner_revision_basis` so the admitted spelling has one owner.
    """

    owner_revision: ProseOwnerRevision
    state: CitationBindingState
    detail: str
    resolved: bool


def render_owner_revision_basis(
    owner_revision: ProseOwnerRevision, state: CitationBindingState
) -> str:
    """Return the recorded basis of one owner-revision observation, as its one admitted spelling."""

    return (
        f"the owner revision {owner_revision.document_path} at recorded blob "
        f"{owner_revision.blob_object_id} in {owner_revision.repository}: {state}"
    )


def _observation(
    owner_revision: ProseOwnerRevision, state: CitationBindingState
) -> OwnerRevisionObservation:
    """Build one observation whose detail is the rendering of its own recorded basis."""

    return OwnerRevisionObservation(
        owner_revision=owner_revision,
        state=state,
        detail=render_owner_revision_basis(owner_revision, state),
        resolved=state == "exact_recorded_blob",
    )


class OwnerRevisionResolver:
    """Resolve recorded owner revisions against exactly one memory repository object store.

    The repository root is fixed when the resolver is built rather than passed per revision, so one
    closure resolves every revision it names against one tree -- the same discipline the shipped
    anchor resolver applies, and the reason a mixed-root closure is not expressible here. A resolver
    built with no root resolves *nothing* and reports exactly that, which keeps "no memory repository
    was requested" distinguishable from "the recorded object is not there". Two facts a caller acts
    on differently.
    """

    def __init__(self, memory_root: Path | None) -> None:
        self.memory_root = None if memory_root is None else Path(memory_root)

    def observe(self, owner_revision: ProseOwnerRevision) -> OwnerRevisionObservation:
        """Return the observation one recorded owner revision earns against this object store."""

        if not self._root_is_usable():
            return _observation(owner_revision, "recorded_object_unavailable")
        return self._observe_against_object_store(owner_revision)

    def read_recorded_bytes(self, owner_revision: ProseOwnerRevision) -> bytes | None:
        """Return the recorded blob's bytes, or ``None`` when the recorded object cannot be read.

        This is the only place bytes are fetched, and it fetches the object the identity names
        rather than the document at its path. A caller that gets ``None`` reports the recorded
        revision as unavailable; it never opens the working tree to fill the gap.
        """

        if not self._root_is_usable():
            return None
        root = self.memory_root
        assert root is not None  # the usability check above narrowed this
        result = run_git(root, ["cat-file", "blob", owner_revision.blob_object_id])
        if result.returncode != 0:
            return None
        return result.stdout.encode("utf-8")

    def _root_is_usable(self) -> bool:
        return self.memory_root is not None and self.memory_root.is_dir()

    def _observe_against_object_store(
        self, owner_revision: ProseOwnerRevision
    ) -> OwnerRevisionObservation:
        """Ask the object store about exactly one recorded blob identity."""

        root = self.memory_root
        assert root is not None  # the caller checked usability before calling this
        try:
            result = run_git(root, ["cat-file", "-e", f"{owner_revision.blob_object_id}^{{blob}}"])
        except OSError:
            # A Git binary this process cannot run at all is the same fact as a lookup that
            # answered with an error: the recorded revision is unknown, not absent. It is reported
            # as unavailable rather than escaping as a raw OSError past the observation boundary.
            return _observation(owner_revision, "recorded_object_unavailable")
        if result.returncode != 0:
            return _observation(owner_revision, "recorded_object_unavailable")
        return _observation(owner_revision, "exact_recorded_blob")


def owner_revision_resolver_for(memory_root: Path | None) -> OwnerRevisionResolver:
    """Return the resolver one closure read asks for, bound to one memory repository root."""

    return OwnerRevisionResolver(memory_root)
