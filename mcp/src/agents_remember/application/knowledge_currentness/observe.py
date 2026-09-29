"""One entry's state at one code tree, and the observation cache (MIK-R03 rules 1 and 5).

An **entry** (a realization or a proof, MIK-R21) is observed at a code tree T with the MIK-R08
definitions, reusing the worklist's own resolution (:meth:`CodeTrees.resolve`: a ``symbol`` is the
extent the shipped extractor binds uniquely, a ``line_range`` is mapped from the anchor's ``blob``
through the zero-context diff, a ``file`` is every line) and the one content identity
(:mod:`agents_remember.models.knowledge_files.anchor_content`):

========================  ===========================================================================
``current``               T's blob for the path is the entry's ``blob``, or the range resolved in T's
                          blob has the entry's ``content``.
``stale``                 The range content differs, the locator does not resolve uniquely (a symbol
                          bound twice or nowhere, a line range with no mapping), or the path is absent
                          at T (no regular file there).
``unverifiable``          No code tree was requested, the blob changed and the locator kind cannot
                          be re-resolved (a kind this reader does not know, or a symbol in a file no
                          shipped grammar reads), or a needed Git object is unavailable (the tree,
                          T's blob, the blob a line range was recorded against, or a Git call that
                          failed or timed out). The reason is always named.
========================  ===========================================================================

The checks run in the table's order (L03 ruling N1): no tree observes nothing; an absent path is
``stale`` and an unchanged blob is ``current`` whatever the locator kind; only then is an
unsupported kind ``unverifiable``.

Only the tree the caller resolved is consulted: there is no fallback to a working tree or ``HEAD``.
Nothing is written, re-anchored or judged.

**The observation cache.** What a locator resolves to in a blob is a function of that blob, the
locator and the extractor, so an observation -- the content identity of the resolved range, or "does
not resolve" -- is remembered per process in a bounded LRU table (:data:`OBSERVATIONS`: 8,192
entries, about 5 MB, under 10 MB) and reused across reads. The key is ``(blob, locator, extractor
version)``, where the locator is spelled completely: its canonical JSON, the path whose suffix chooses the grammar, and, for a line range, the
blob its lines were recorded against (the mapping starts there). The extractor version names this
observation rule and the measured grammar versions, so a changed extractor never reads an old
answer. Only answers are remembered; an observation that failed (``unverifiable``) is asked again.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Literal

from agents_remember.application.knowledge_worklist.code import CodeReadError, CodeTrees
from agents_remember.memory.knowledge.read_anchor_memo import BoundedMemo
from agents_remember.memory.knowledge_index import Entry
from agents_remember.memory_quality.style.citations import grammars

__all__ = [
    "EXTRACTOR_VERSION",
    "OBSERVATIONS",
    "CodeTree",
    "EntryObservation",
    "EntryState",
    "ObservationKey",
    "OpenedCodeTree",
    "observation_key",
    "observe_entry",
    "open_code_tree",
]

EntryState = Literal["current", "stale", "unverifiable"]
ObservationKey = tuple[str, str, str, str, str]

_OBSERVATION_RULE: Final = "anchor-observation/v1"
EXTRACTOR_VERSION: Final = ";".join(
    (
        _OBSERVATION_RULE,
        *(f"{name}={version}" for name, version in sorted(grammars.MEASURED_VERSIONS.items())),
    )
)
_SUPPORTED_LOCATORS: Final = frozenset({"symbol", "line_range", "file"})
# Bounded in entries. Measured at about 600 bytes per entry (key strings plus value), 8,192
# entries hold about 5 MB, and stay under 10 MB even at about 1.2 KB per entry (long paths). A
# whole-repository computation on the real memory needs about 180 entries.
_OBSERVATION_CAPACITY: Final = 8_192

# blob, locator (canonical JSON), path, recorded blob (line ranges only), extractor version ->
# the content identity of the resolved range, or ``None`` when the locator does not resolve.
OBSERVATIONS: Final[BoundedMemo[ObservationKey, str | None]] = BoundedMemo(_OBSERVATION_CAPACITY)

NO_TREE_REQUESTED: Final = "no code tree was requested"


@dataclass(frozen=True)
class CodeTree:
    """The code tree a read asks currentness at: a tree (or commit) ID in one repository's store."""

    repository: Path
    tree: str

    def to_document(self) -> dict[str, str]:
        return {"repositoryRoot": str(self.repository), "treeId": self.tree}


@dataclass(frozen=True)
class OpenedCodeTree:
    """A requested tree, read once: its regular files by path, or why it cannot be read."""

    tree: CodeTree | None
    trees: CodeTrees | None
    files: Mapping[str, str]
    problem: str | None


def open_code_tree(tree: CodeTree | None) -> OpenedCodeTree:
    """Read ``tree``'s file list once; ``None`` or an unreadable tree names why nothing resolves."""

    if tree is None:
        return OpenedCodeTree(tree=None, trees=None, files={}, problem=NO_TREE_REQUESTED)
    try:
        trees = CodeTrees.open(tree.repository, tree.tree, tree.tree)
        files = trees.base()
    except (CodeReadError, OSError, ValueError, subprocess.SubprocessError) as error:
        return OpenedCodeTree(
            tree=tree,
            trees=None,
            files={},
            problem=f"the code tree {tree.tree} in {tree.repository} cannot be read: {error}",
        )
    return OpenedCodeTree(tree=tree, trees=trees, files=files, problem=None)


@dataclass(frozen=True)
class EntryObservation:
    """One entry's state at the requested tree, with the facts that decided it."""

    entry_id: str
    kind: Literal["realization", "proof"]
    invariant: str
    path: str
    state: EntryState
    reason: str | None
    recorded_blob: str
    observed_blob: str | None
    recorded_content: str
    observed_content: str | None

    def to_document(self) -> dict[str, Any]:
        document: dict[str, Any] = {
            "id": self.entry_id,
            "kind": self.kind,
            "path": self.path,
            "state": self.state,
            "recordedBlob": self.recorded_blob,
            "observedBlob": self.observed_blob,
            "recordedContent": self.recorded_content,
            "observedContent": self.observed_content,
        }
        if self.reason is not None:
            document["reason"] = self.reason
        return document


def observation_key(
    blob: str, path: str, locator: Mapping[str, Any], recorded_blob: str
) -> ObservationKey:
    """The cache key of one observation (see the module docstring)."""

    recorded = recorded_blob if locator.get("kind") == "line_range" else ""
    spelled = json.dumps(dict(locator), sort_keys=True, separators=(",", ":"))
    return (blob, spelled, path, recorded, EXTRACTOR_VERSION)


@dataclass(frozen=True)
class _Recorded:
    """What an entry recorded: its source path, locator, blob and content identity."""

    path: str
    locator: Mapping[str, Any]
    blob: str
    content: str

    @classmethod
    def of(cls, entry: Entry) -> _Recorded:
        anchor = entry.document.get("anchor") or {}
        return cls(
            path=entry.path,
            locator=anchor.get("locator") or {},
            blob=str(anchor.get("blob", "")),
            content=str(anchor.get("content", "")),
        )


_Verdict = tuple[EntryState, str | None, str | None]  # state, reason, observed content


def observe_entry(
    entry: Entry,
    code: OpenedCodeTree,
    *,
    cache: BoundedMemo[ObservationKey, str | None] = OBSERVATIONS,
) -> EntryObservation:
    """The state of ``entry`` at ``code`` (MIK-R03 rule 1). Reads only; never raises for Git."""

    recorded = _Recorded.of(entry)
    observed_blob = code.files.get(entry.path)
    state, reason, content = _verdict(recorded, observed_blob, code, cache)
    return EntryObservation(
        entry_id=entry.id,
        kind=entry.kind,
        invariant=entry.invariant,
        path=entry.path,
        state=state,
        reason=reason,
        recorded_blob=recorded.blob,
        observed_blob=observed_blob,
        recorded_content=recorded.content,
        observed_content=content,
    )


def _verdict(
    recorded: _Recorded,
    observed_blob: str | None,
    code: OpenedCodeTree,
    cache: BoundedMemo[ObservationKey, str | None],
) -> _Verdict:
    decided = _decided_without_resolving(recorded, observed_blob, code)
    if decided is not None:
        return decided
    assert observed_blob is not None  # an absent path was decided above
    try:
        observed = _observed_content(recorded, observed_blob, code, cache)
    except CodeReadError as error:
        return ("unverifiable", str(error), None)
    except (subprocess.SubprocessError, OSError) as error:  # a Git call failed or timed out
        return ("unverifiable", f"a Git read failed ({type(error).__name__}: {error})", None)
    if observed is None:
        return ("stale", _unresolved(recorded.locator.get("kind")), None)
    return _compared(recorded.content, observed)


def _decided_without_resolving(
    recorded: _Recorded, observed_blob: str | None, code: OpenedCodeTree
) -> _Verdict | None:
    """The packet table's checks that need no locator resolution, in its order (L03 ruling N1).

    No tree (or an unreadable one) observes nothing; then an absent path is ``stale`` and an
    unchanged blob is ``current``, whatever the locator kind; only a changed blob whose locator
    kind cannot be re-resolved is ``unverifiable``.
    """

    if code.problem is not None or code.trees is None:
        return ("unverifiable", code.problem or NO_TREE_REQUESTED, None)
    if observed_blob is None:
        return ("stale", f"the path {recorded.path!r} is absent at the code tree", None)
    if observed_blob == recorded.blob:
        return ("current", None, None)
    unsupported = _unsupported(recorded)
    return None if unsupported is None else ("unverifiable", unsupported, None)


def _compared(recorded: str, observed: str) -> _Verdict:
    if observed != recorded:
        return ("stale", "the range content differs from the recorded content", observed)
    return ("current", None, observed)


def _unsupported(recorded: _Recorded) -> str | None:
    """Why the locator kind cannot be re-resolved in a changed blob, or ``None``."""

    kind = recorded.locator.get("kind")
    if kind not in _SUPPORTED_LOCATORS:
        return f"the locator kind {kind!r} is unsupported"
    if kind == "symbol" and grammars.grammar_of(recorded.path) is None:
        return (
            f"the locator kind 'symbol' is unsupported for {recorded.path!r}: "
            "no shipped grammar reads it"
        )
    return None


def _observed_content(
    recorded: _Recorded,
    blob: str,
    code: OpenedCodeTree,
    cache: BoundedMemo[ObservationKey, str | None],
) -> str | None:
    """The content identity of the recorded range in ``blob``, or ``None`` when it does not resolve.

    Remembered per :func:`observation_key`; a :class:`CodeReadError` (a needed object is
    unavailable) is raised and never remembered.
    """

    key = observation_key(blob, recorded.path, recorded.locator, recorded.blob)
    remembered = cache.get(key)
    if remembered is not None:
        return remembered[0]
    trees = code.trees
    if (
        trees is None
    ):  # guarded by _decided_without_resolving; kept so a direct call cannot misread it
        raise CodeReadError(code.problem or NO_TREE_REQUESTED)
    if recorded.locator.get("kind") == "line_range" and not trees.has_blob(recorded.blob):
        raise CodeReadError(
            f"the blob {recorded.blob} the line range was recorded against is unavailable"
        )
    resolved = trees.resolve(recorded.path, recorded.locator, recorded.blob, blob)
    observed = None if resolved is None else resolved.content
    cache.put(key, observed)
    return observed


def _unresolved(kind: object) -> str:
    if kind == "symbol":
        return "the symbol does not resolve uniquely at the code tree"
    if kind == "line_range":
        return "the line range has no mapping to the code tree"
    return "the locator does not resolve at the code tree"
