"""The two record kinds whose state is read off the code: weaknesses and dependents.

**A weakness (MIK-R118).** The record is read as written, and its anchor is observed at the
selection's code tree with the same rule every entry uses
(:func:`~agents_remember.application.knowledge_currentness.observe.observe_entry`): the row says
``anchorState: current`` when the recorded content still stands at the symbol, ``stale`` when it
moved or the symbol no longer resolves, and ``unverifiable`` when no code tree was resolved, naming
why. Nothing is refused for a stale anchor; the row is marked.

**A dependent (MIK-R124 rule 3).** Whether a document is stale is *derived and never stored*. A
link has moved when

* an ``anchor`` link's recorded content no longer stands at its anchor at the read's code tree (the
  observation above), when the locator no longer resolves, or when the file is gone;
* a ``file`` link's path holds a different blob at that tree, or is gone;
* a ``record`` link's stored ``revision`` differs from the recorded one.

A dependent is stale when at least one link has moved, current when none has, and unverifiable when
a link cannot be observed at all. The row names every link that moved with what was observed.

Nothing here writes, and no byte of a record changes when its document becomes stale.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from pydantic import ValidationError

from agents_remember.application.knowledge_currentness.observe import (
    CodeTree,
    EntryObservation,
    observe_entry,
    open_code_tree,
)
from agents_remember.application.knowledge_reader.selection import READ_FAILURES, ReaderSelection
from agents_remember.kernel.git_command import (
    GIT_METADATA_TIMEOUT_SECONDS,
    GitRunnerOptions,
    run_git,
)
from agents_remember.memory.knowledge_index import Entry
from agents_remember.models.knowledge_files.records import (
    DependentRecord,
    WeaknessRecord,
)

__all__ = [
    "DEPENDENT_VIEW",
    "WEAKNESS_VIEW",
    "dependent_counts",
    "dependent_list",
    "dependent_state",
    "open_weakness_counts",
    "weakness_list",
]

WEAKNESS_VIEW: Final = "weakness"
DEPENDENT_VIEW: Final = "dependent"

_MOVED: Final = frozenset({"stale", "moved_or_absent"})


@dataclass(frozen=True)
class AnchorObservation:
    """A weakness anchor or a dependent anchor link at one code tree."""

    state: str
    reason: str | None
    observed: str | None

    def to_document(self) -> dict[str, Any]:
        document: dict[str, Any] = {"state": self.state}
        if self.reason is not None:
            document["reason"] = self.reason
        if self.observed is not None:
            document["observedContent"] = self.observed
        return document


def _anchor_observation(
    tree: Any, path: str, anchor: Mapping[str, Any], cache: dict[str, Any]
) -> AnchorObservation:
    """Observe one recorded anchor at a code tree, once per anchor."""

    key = str(sorted(anchor.items()))
    if key in cache:
        return cache[key]
    entry = Entry("record", "realization", "", path, "", {"anchor": dict(anchor)})
    observed: EntryObservation = observe_entry(entry, open_code_tree(tree))
    result = AnchorObservation(observed.state, observed.reason, observed.observed_content)
    cache[key] = result
    return result


def _weaknesses(selection: ReaderSelection) -> list[WeaknessRecord]:
    """Every weakness record of the tree that its own model reads, in ID order."""

    records = []
    for record in selection.index.records_of_kind("weakness").value:
        try:
            records.append(WeaknessRecord.model_validate(record.document))
        except ValidationError:  # an unreadable record is the validator's finding, not a row
            continue
    return sorted(records, key=lambda one: one.id)


def weakness_list(
    selection: ReaderSelection,
    *,
    folder: str | None = None,
    kind: str | None = None,
    include_closed: bool = False,
    observe: bool = True,
) -> list[dict[str, Any]]:
    """The weaknesses of the tree: all, those under ``folder``, those of one ``kind``.

    Open ones unless ``include_closed``; a row names the id, the file, the kind, the sentence and
    the measurement, and the anchor's state at the selected code tree.
    """

    cache: dict[str, Any] = {}
    rows = []
    for record in _weaknesses(selection):
        if not include_closed and record.state != "open":
            continue
        path = record.anchor.path or ""
        if folder is not None and not (path == folder or path.startswith(f"{folder}/")):
            continue
        if kind is not None and record.kind != kind:
            continue
        row: dict[str, Any] = {
            "id": record.id,
            "file": path,
            "kind": record.kind,
            "statement": record.statement,
            "state": record.state,
        }
        if record.measurement is not None:
            row["measurement"] = record.measurement.to_document()
        if record.closed is not None:
            row["closed"] = record.closed.to_document()
        if observe:
            row["anchorState"] = _anchor_observation(
                selection.code_tree, path, record.anchor.to_document(), cache
            ).to_document()
        rows.append(row)
    return rows


def open_weakness_counts(selection: ReaderSelection) -> dict[str, int]:
    """How many open weaknesses each file carries: the one line a card's read names."""

    counts: dict[str, int] = {}
    for record in _weaknesses(selection):
        if record.state != "open" or record.anchor.path is None:
            continue
        counts[record.anchor.path] = counts.get(record.anchor.path, 0) + 1
    return counts


def _dependents(selection: ReaderSelection) -> list[DependentRecord]:
    records = []
    for record in selection.index.records_of_kind("dependent").value:
        try:
            records.append(DependentRecord.model_validate(record.document))
        except ValidationError:
            continue
    return sorted(records, key=lambda one: (one.document, one.id))


def dependent_state(
    selection: ReaderSelection, record: DependentRecord
) -> tuple[str, list[dict[str, Any]]]:
    """``(state, moved links)`` for one dependent at the selection's code tree (rule 3)."""

    def revision_of(record_id: str) -> int | None:
        found = selection.index.record(record_id).value
        return None if found is None else found.revision

    return dependent_movement(
        record,
        revision_of=revision_of,
        code=open_code_tree(selection.code_tree),
        tree=selection.code_tree,
    )


@dataclass(frozen=True)
class _Observe:
    """What one dependent observation reads: the memory tree's revisions, the code tree, the cache."""

    revision_of: Callable[[str], int | None]
    code: Any
    tree: Any
    cache: dict[str, Any]


def dependent_movement(
    record: DependentRecord,
    *,
    revision_of: Callable[[str], int | None],
    code: Any,
    tree: Any = None,
) -> tuple[str, list[dict[str, Any]]]:
    """``(state, moved links)`` for one dependent, over any memory tree's records and code tree.

    The same derivation serves a mounted read and a landing: a link has moved when its recorded
    value no longer stands where it was recorded (rule 3). Nothing is written.
    """

    observe = _Observe(revision_of=revision_of, code=code, tree=tree, cache={})
    moved: list[dict[str, Any]] = []
    unverifiable: list[dict[str, Any]] = []
    for position, link in enumerate(record.stands_on, start=1):
        document = _moved_link(position, link, observe)
        if document is None:
            continue
        if document.get("state") == "unverifiable":
            unverifiable.append(document)
            continue
        moved.append(document)
    if moved:
        return ("stale", [*moved, *unverifiable])
    if unverifiable:
        return ("unverifiable", unverifiable)
    return ("current", [])


def _moving_commit(opened: Any, path: str) -> dict[str, Any]:
    """The commit that last changed ``path`` to the value this read observed (F15).

    A stale row says what moved, not only which values differ: the link's movement is attributed
    to the code commit that made it -- including the commit that **deleted** the file, which is the
    movement a stale row most often has to explain. A hash is named only when that commit's recorded
    value at the path is the value the read observed (or the absence it observed); otherwise the
    change is uncommitted or comes from another line, and the row says so instead of guessing.
    """

    tree = getattr(opened, "tree", None)
    if tree is None:
        return {"movingCommitUnknown": "no code tree was resolved for this read"}
    commit = _selected_commit(tree)
    if commit is None:
        return {
            "movingCommitUnknown": "the selected tree has no verified committed origin: uncommitted or unpaired tree"
        }
    try:
        listed = run_git(
            tree.repository,
            ["log", "-1", "--format=%H", commit, "--", path],
            GitRunnerOptions(timeout=GIT_METADATA_TIMEOUT_SECONDS),
        )
    except (OSError, subprocess.SubprocessError) as error:
        return {"movingCommitUnknown": f"the history could not be read ({type(error).__name__})"}
    commit = listed.stdout.strip()
    if listed.returncode != 0 or not commit:
        return {"movingCommitUnknown": f"no readable commit changed {path}"}
    return _movement_attribution(commit, path, opened, tree)


def _movement_attribution(commit: str, path: str, opened: Any, tree: Any) -> dict[str, Any]:
    """The newest commit's part in the movement, or why it cannot be called the mover."""

    observed = opened.files.get(path)
    recorded = _blob_at(tree.repository, commit, path)
    if observed is None and recorded is None:
        # The newest commit touching the path removed it: that deletion *is* the movement, and
        # saying "uncommitted or another line" would hide the one commit that explains it.
        return {"movedBy": commit, "movingCommit": commit, "movedByDeletion": True}
    if observed is None or recorded != observed:
        return {
            "movingCommitUnknown": (
                f"the observed value of {path} is not {commit}'s: the change is uncommitted or "
                "comes from another line"
            )
        }
    return {"movedBy": commit, "movingCommit": commit}


def _anchor_moving_commit(opened: Any, anchor: Any) -> dict[str, Any]:
    """The commit that last changed the *anchored content*, not the last commit on its path (F15).

    A commit that edits an unrelated constant in the same file is not the commit that moved an
    anchor whose bytes it left alone. So the attribution walks the path's commits newest-first and
    names the newest one whose anchored content differs from its parent's while matching what this
    read observes. Nothing is named when that transition cannot be found.
    """

    if anchor.path is None:
        return {"movingCommitUnknown": "the anchor names no file"}
    origin = _selected_commit(opened.tree)
    if origin is None:
        return {
            "movingCommitUnknown": "the selected tree has no verified committed origin: uncommitted or unpaired tree"
        }
    target = _anchor_at(opened, anchor, opened.tree.tree)
    if target.state == "unverifiable":
        return {"movingCommitUnknown": f"the anchored content of {anchor.path} could not be read"}
    for commit, parent in _path_commits(opened.tree.repository, origin, anchor.path):
        here = _anchor_at(opened, anchor, commit)
        if here.state == "unverifiable" or here.observed_content != target.observed_content:
            continue
        before = None if parent is None else _anchor_at(opened, anchor, parent)
        if before is not None and before.state == "unverifiable":
            return {"movingCommitUnknown": "the parent anchor could not be observed"}
        if (before is None and target.observed_content is not None) or (
            before is not None and before.observed_content != target.observed_content
        ):
            attribution: dict[str, Any] = {"movedBy": commit, "movingCommit": commit}
            if target.observed_content is None:
                attribution["movedByDeletion"] = True
            return attribution
    return {
        "movingCommitUnknown": (
            f"no commit on {anchor.path} changed the anchored content from what this read "
            "observes: the change is uncommitted or comes from another line"
        )
    }


def _anchor_at(opened: Any, anchor: Any, revision: str) -> EntryObservation:
    """Observe an anchor without treating an unreadable snapshot as an absent symbol."""

    return observe_entry(
        Entry("", "realization", "", anchor.path or "", "", {"anchor": anchor.to_document()}),
        open_code_tree(CodeTree(opened.tree.repository, revision)),
    )


def _selected_commit(tree: CodeTree) -> str | None:
    """Use the selected commit, or HEAD only when its entire tree is the selected tree.

    A captured dirty tree has no committed origin. A historical tree without its commit also
    carries insufficient provenance; neither is attributed using later history at HEAD.
    """

    try:
        exact = run_git(
            tree.repository,
            ["rev-parse", "--verify", "--quiet", f"{tree.tree}^{{commit}}"],
            GitRunnerOptions(timeout=GIT_METADATA_TIMEOUT_SECONDS),
        )
        if exact.returncode == 0:
            return exact.stdout.strip()
        head = run_git(
            tree.repository,
            ["rev-parse", "HEAD", "HEAD^{tree}"],
            GitRunnerOptions(timeout=GIT_METADATA_TIMEOUT_SECONDS),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    values = head.stdout.splitlines()
    return (
        values[0] if head.returncode == 0 and len(values) == 2 and values[1] == tree.tree else None
    )


def _path_commits(
    repository: Path, revision: str, path: str, limit: int = 100
) -> list[tuple[str, str | None]]:
    """The path's commits newest-first as ``(commit, parent)``, bounded."""

    try:
        done = run_git(
            repository,
            ["log", f"-n{limit}", "--format=%H %P", revision, "--", path],
            GitRunnerOptions(timeout=GIT_METADATA_TIMEOUT_SECONDS),
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if done.returncode != 0:
        return []
    commits: list[tuple[str, str | None]] = []
    for line in done.stdout.splitlines():
        parts = line.split()
        if parts:
            commits.append((parts[0], parts[1] if len(parts) > 1 else None))
    return commits


def _blob_at(repository: Path, commit: str, path: str) -> str | None:
    """The blob a commit records at ``path``, or ``None`` when it cannot be read."""

    try:
        result = run_git(
            repository,
            ["rev-parse", "--verify", "--quiet", f"{commit}:{path}"],
            GitRunnerOptions(timeout=GIT_METADATA_TIMEOUT_SECONDS),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    shown = result.stdout.strip()
    return shown if result.returncode == 0 and shown else None


def _moved_link(
    position: int,
    link: Any,
    observe: _Observe,
) -> dict[str, Any] | None:
    """One link's movement at the read's tree, or ``None`` when it still stands as recorded."""

    where = {"position": position, "kind": link.kind}
    if link.kind == "record":
        moved = _record_link_moved(where, link, observe.revision_of)
        if moved is None:
            return None
        # A record link moves in the memory tree: no code commit made the change.
        return {
            **moved,
            "movingCommitUnknown": (
                "a record link moves when the named record's revision changes; that change is a "
                "memory commit, not a code commit"
            ),
        }
    if observe.code.problem is not None:
        return {**where, "state": "unverifiable", "reason": observe.code.problem}
    if link.kind == "file":
        moved = _file_link_moved(observe.code, where, link)
        return None if moved is None else {**moved, **_moving_commit(observe.code, link.path)}
    moved = _anchor_link_moved(observe.tree, where, link, observe.cache)
    if moved is None or moved.get("state") != "stale":
        return moved
    return {**moved, **_anchor_moving_commit(observe.code, link.anchor)}


def _record_link_moved(
    where: dict[str, Any], link: Any, revision_of: Callable[[str], int | None]
) -> dict[str, Any] | None:
    """A record link stands while the stored revision is the recorded one."""

    revision = revision_of(link.id)
    if revision is None:
        return {**where, "state": "stale", "reason": f"the tree holds no record {link.id}"}
    if revision != link.revision:
        return {
            **where,
            "id": link.id,
            "state": "stale",
            "recordedRevision": link.revision,
            "revision": revision,
        }
    return None


def _file_link_moved(code: Any, where: dict[str, Any], link: Any) -> dict[str, Any] | None:
    """A whole-file link stands while the path's blob is the recorded blob."""

    observed = code.files.get(link.path)
    if observed is None:
        return {**where, "path": link.path, "state": "stale", "reason": "the file is gone"}
    if observed != link.blob:
        return {
            **where,
            "path": link.path,
            "state": "stale",
            "recordedBlob": link.blob,
            "observedBlob": observed,
        }
    return None


def _anchor_link_moved(
    tree: Any, where: dict[str, Any], link: Any, cache: dict[str, Any]
) -> dict[str, Any] | None:
    """An anchor link stands while its recorded content stands at the anchor."""

    anchor = link.anchor
    if anchor.path is None:  # the model refuses this; kept so a hand-edited file cannot crash
        return {**where, "state": "unverifiable", "reason": "the anchor names no file"}
    observed = _anchor_observation(tree, anchor.path, anchor.to_document(), cache)
    if observed.state == "unverifiable":
        return {**where, "path": anchor.path, "state": "unverifiable", "reason": observed.reason}
    if observed.state == "current":
        return None
    return {
        **where,
        "path": anchor.path,
        "state": "stale",
        "reason": observed.reason,
        "recordedContent": anchor.content,
        "observedContent": observed.observed,
    }


def dependent_list(
    selection: ReaderSelection,
    *,
    folder: str | None = None,
    stale_only: bool = False,
    stands_on: str | None = None,
    code_path: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    """The dependents of the tree: all, under a folder of documents, the stale ones, or those
    that stand on a file of the code.

    ``stands_on`` names a code path: the row is kept when one of its links sits in that file (an
    anchor link in it, a whole-file link to it, or ``code_path`` naming it). With ``code_path`` a
    set of paths, the same test is applied to each.
    """

    wanted = {stands_on} if stands_on is not None else set(code_path or ())
    rows: list[dict[str, Any]] = []
    for record in _dependents(selection):
        if folder is not None and not record.document.startswith(f"{folder.rstrip('/')}/"):
            continue
        if wanted and not _stands_on_any(record, wanted):
            continue
        state, moved = dependent_state(selection, record)
        if stale_only and state != "stale":
            continue
        row: dict[str, Any] = {
            "id": record.id,
            "document": record.document,
            "state": state,
            "confirmed": record.confirmed.to_document(),
            "revision": record.revision,
        }
        if moved:
            row["movedLinks"] = moved
        rows.append(row)
    return rows


def _stands_on_any(record: DependentRecord, paths: set[str]) -> bool:
    for link in record.stands_on:
        if link.kind == "anchor" and link.anchor.path in paths:
            return True
        if link.kind == "file" and link.path in paths:
            return True
    return False


def dependent_counts(selection: ReaderSelection) -> dict[str, int]:
    """How many documents stand on each code file: the one line a card's read names."""

    counts: dict[str, int] = {}
    for record in _dependents(selection):
        for path in _standing_paths(record):
            counts[path] = counts.get(path, 0) + 1
    return counts


def _standing_paths(record: DependentRecord) -> set[str]:
    found: set[str] = set()
    for link in record.stands_on:
        if link.kind == "anchor" and link.anchor.path is not None:
            found.add(link.anchor.path)
        elif link.kind == "file":
            found.add(link.path)
    return found


def dependent_state_at(selection: ReaderSelection, record_id: str) -> str | None:
    """The derived state of one dependent by id, or ``None`` when the tree holds none."""

    found = selection.index.record(record_id).value
    if found is None or found.kind != "dependent":
        return None
    try:
        record = DependentRecord.model_validate(found.document)
    except ValidationError:
        return "unverifiable"
    try:
        state, _moved = dependent_state(selection, record)
    except (*READ_FAILURES, ValueError) as error:
        return f"unverifiable: {error}"
    return state
