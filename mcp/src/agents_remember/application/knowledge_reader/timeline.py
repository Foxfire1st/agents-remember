"""The timeline of a truth view, newest first, from its three sources (MIK-R29 rule 4).

1. **The record file's ``git log``, with meaning diffs.** Each commit that changed the record's
   JSON file (followed across renames) is one event; its meaning diff names every field whose value
   differs from the parent's, with both values. A working-tree selection whose record file differs
   from ``HEAD`` adds one ``uncommitted`` event with the same diff.
2. **History rows about it from every leaf** (MIK-R07): every row whose subject is the record, as
   the selected tree's index holds them, each dated by the last commit that changed its history
   file (a file not yet committed is ``uncommitted``).
3. **``git log`` of the sidecar entries that realize or prove it**: each commit in which one of
   those entries was added, removed, ``moved`` (to another source path) or ``re-anchored`` (its
   locator, blob or content identity changed at the same path). Moves across files are found
   through the entry ID wherever it appeared under ``onboarding/`` (``-G``, which lists only the
   files whose diff names an ID: the file an entry left and the file it entered); re-anchors through
   the log of the entry's current sidecar.

Only the converted part of the memory history is read: the log starts at the first commit that
added the layout marker, because no record or entry exists before it. Every source reports its own
state; a source that could not be read says so and never reads as "no history".

**Cache.** A timeline is a function of the memory repository, the selection's revision, the
selected tree (its key covers a working tree's uncommitted state) and the record, so a complete one
is remembered in a bounded table (:data:`TIMELINES`) and served again for the same four. A timeline
with a source that could not be read is never remembered: the next read asks again.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Final

from agents_remember.application.knowledge_reader.files import read_memory_file
from agents_remember.application.knowledge_reader.selection import (
    READ_FAILURES,
    ReaderReadError,
    ReaderSelection,
)
from agents_remember.kernel.git_command import (
    GIT_METADATA_TIMEOUT_SECONDS,
    GitRunnerOptions,
    read_git_blobs_bytes,
    run_git,
)
from agents_remember.memory.knowledge.read_anchor_memo import BoundedMemo
from agents_remember.memory.knowledge_index import Entry, HistoryRow, Record
from agents_remember.models.knowledge_files.documents import LAYOUT_MARKER_PATH, ONBOARDING_ROOT

__all__ = ["TIMELINES", "meaning_diff", "record_timeline"]

_ZERO: Final = "0" * 40
_RECORD_SEPARATOR: Final = "\x1e"
_FIELD_SEPARATOR: Final = "\x1f"
_FORMAT: Final = f"--format={_RECORD_SEPARATOR}%H{_FIELD_SEPARATOR}%cI{_FIELD_SEPARATOR}%s"
_OPTIONS: Final = GitRunnerOptions(timeout=GIT_METADATA_TIMEOUT_SECONDS)
_UNCOMMITTED: Final = "uncommitted"
TimelineKey = tuple[str, str, str, str]
# Bounded in entries; one timeline is a few kilobytes to a few tens of kilobytes of events.
TIMELINES: Final[BoundedMemo[TimelineKey, dict[str, Any]]] = BoundedMemo(256)


@dataclass(frozen=True)
class _Change:
    """One file a commit changed: its old and new path and blob (``_ZERO`` for none)."""

    old_path: str
    new_path: str
    old_blob: str
    new_blob: str


@dataclass(frozen=True)
class _Commit:
    sha: str
    date: str
    subject: str
    changes: tuple[_Change, ...]


def record_timeline(
    selection: ReaderSelection,
    record: Record,
    entries: Sequence[Entry],
    history: Sequence[HistoryRow],
) -> dict[str, Any]:
    """The timeline of ``record``: its file's log, the rows about it, and its entries' log.

    The answer is shared with later reads of the same tree and record; callers do not mutate it.
    """

    key: TimelineKey = (
        str(selection.memory_repository),
        str(selection.revision),
        selection.index.state.key,
        record.id,
    )
    remembered = TIMELINES.get(key)
    if remembered is not None:
        return remembered[0]
    timeline = _timeline(selection, record, entries, history)
    if all(source["state"] == "read" for source in timeline["sources"].values()):
        TIMELINES.put(key, timeline)
    return timeline


def _timeline(
    selection: ReaderSelection,
    record: Record,
    entries: Sequence[Entry],
    history: Sequence[HistoryRow],
) -> dict[str, Any]:
    events: list[dict[str, Any]] = []
    sources: dict[str, dict[str, Any]] = {}
    for name, collect in (
        ("record", lambda: _record_events(selection, record)),
        ("history", lambda: _history_events(selection, history)),
        ("entries", lambda: _entry_events(selection, entries)),
    ):
        if selection.revision is None:
            sources[name] = {"state": "unavailable", "detail": "the memory tree has no commit"}
            continue
        try:
            found = collect()
        except READ_FAILURES as error:
            sources[name] = {"state": "unavailable", "detail": f"{type(error).__name__}: {error}"}
            continue
        sources[name] = {"state": "read", "events": len(found)}
        events.extend(found)
    ranks = _ranks(selection) if events else {}
    events.sort(key=lambda event: _newest_first(event, ranks))
    return {"sources": sources, "events": events}


_SOURCE_ORDER: Final = {"record": 0, "entries": 1, "history": 2}


def _newest_first(event: Mapping[str, Any], ranks: Mapping[str, int]) -> tuple[int, int, int]:
    """Uncommitted events first, then by the commit's position in the history (newest first).

    The position, not the date, orders commits: two commits of the same second, or a commit dated
    before its parent, still read in the order Git records them.
    """

    commit = event.get("commit")
    order = _SOURCE_ORDER.get(str(event.get("source")), len(_SOURCE_ORDER))
    if commit is None:
        return (0, 0, order)
    return (1, ranks.get(str(commit), len(ranks)), order)


def _ranks(selection: ReaderSelection) -> dict[str, int]:
    if selection.revision is None:
        return {}
    result = run_git(selection.memory_repository, ["rev-list", *_range(selection)], _OPTIONS)
    shas = result.stdout.split() if result.returncode == 0 else []
    return {sha: position for position, sha in enumerate(shas)}


# --------------------------------------------------------------------------------------------------
# Source 1: the record file
# --------------------------------------------------------------------------------------------------


def _record_events(selection: ReaderSelection, record: Record) -> list[dict[str, Any]]:
    """Every commit that changed the record's file, found by its ID (the slug may be renamed).

    Record files are named ``<ID>-<slug>.json``, and the ID is the identity: the log names every
    file of that ID in the record's directory, so a slug rename is one commit that deletes one name
    and adds the other, read as ``renamed``. (Git's ``--follow`` would find the same, at the cost of
    rename detection over the conversion commit's thousands of added files.)
    """

    directory = record.path.rpartition("/")[0]
    pattern = f"{directory}/{record.id}-*.json"
    commits = _log(selection, ["--no-renames", "--", pattern])
    blobs = _blobs(selection, (c for commit in commits for c in commit.changes))
    events = []
    for commit in commits:
        change = _paired(commit.changes)
        if change is None:
            continue
        before = _json(blobs.get(change.old_blob))
        after = _json(blobs.get(change.new_blob))
        events.append(
            {
                "source": "record",
                "commit": commit.sha,
                "date": commit.date,
                "subject": commit.subject,
                "path": change.new_path if change.new_blob != _ZERO else change.old_path,
                "change": _file_change(change),
                "meaning": meaning_diff(before, after),
            }
        )
    uncommitted = _uncommitted_record(selection, record)
    return [*uncommitted, *events]


def _paired(changes: Sequence[_Change]) -> _Change | None:
    """One commit's change of a record: a delete of one name and an add of another is a rename."""

    removed = next((one for one in changes if one.new_blob == _ZERO), None)
    added = next((one for one in changes if one.old_blob == _ZERO), None)
    if removed is not None and added is not None:
        return _Change(removed.old_path, added.new_path, removed.old_blob, added.new_blob)
    return changes[0] if changes else None


def _uncommitted_record(selection: ReaderSelection, record: Record) -> list[dict[str, Any]]:
    if not selection.reads_directory:
        return []
    working = read_memory_file(selection, record.path)
    committed = run_git(
        selection.memory_repository, ["cat-file", "blob", f"HEAD:{record.path}"], _OPTIONS
    )
    head_text = committed.stdout if committed.returncode == 0 else None
    if working.text == head_text:
        return []
    return [
        {
            "source": "record",
            "commit": None,
            "date": None,
            "subject": _UNCOMMITTED,
            "path": record.path,
            "change": "added" if head_text is None else "modified",
            "meaning": meaning_diff(_json_text(head_text), _json_text(working.text)),
        }
    ]


def meaning_diff(
    before: Mapping[str, Any] | None, after: Mapping[str, Any] | None
) -> list[dict[str, Any]]:
    """Every field whose value differs between two versions of a record, with both values."""

    old, new = before or {}, after or {}
    return [
        {"field": name, "before": old.get(name), "after": new.get(name)}
        for name in sorted(set(old) | set(new))
        if name != "schema" and old.get(name) != new.get(name)
    ]


def _file_change(change: _Change) -> str:
    if change.old_blob == _ZERO:
        return "added"
    if change.new_blob == _ZERO:
        return "deleted"
    return "renamed" if change.old_path != change.new_path else "modified"


# --------------------------------------------------------------------------------------------------
# Source 2: history rows
# --------------------------------------------------------------------------------------------------


def _history_events(
    selection: ReaderSelection, history: Sequence[HistoryRow]
) -> list[dict[str, Any]]:
    dated: dict[str, tuple[str | None, str | None]] = {}
    for path in sorted({row.path for row in history}):
        dated[path] = _last_change(selection, path)
    return [
        {
            "source": "history",
            "commit": dated[row.path][0],
            "date": dated[row.path][1],
            "subject": _UNCOMMITTED if dated[row.path][0] is None else None,
            "owner": row.owner,
            "ownerKind": row.owner_kind,
            "closed": row.closed,
            "row": row.id,
            "disposition": row.disposition,
            "document": row.document,
            "path": row.path,
        }
        for row in history
    ]


def _last_change(selection: ReaderSelection, path: str) -> tuple[str | None, str | None]:
    result = run_git(
        selection.memory_repository,
        ["log", "-1", "--format=%H%x1f%cI", str(selection.revision), "--", path],
        _OPTIONS,
    )
    if result.returncode != 0 or _FIELD_SEPARATOR not in result.stdout:
        return None, None
    sha, date = result.stdout.strip().split(_FIELD_SEPARATOR)
    if selection.reads_directory and _differs_from_head(selection, path):
        return None, None
    return sha, date


def _differs_from_head(selection: ReaderSelection, path: str) -> bool:
    working = read_memory_file(selection, path)
    committed = run_git(selection.memory_repository, ["cat-file", "blob", f"HEAD:{path}"], _OPTIONS)
    return committed.returncode != 0 or working.text != committed.stdout


# --------------------------------------------------------------------------------------------------
# Source 3: the realizing and proving entries
# --------------------------------------------------------------------------------------------------


def _entry_events(selection: ReaderSelection, entries: Sequence[Entry]) -> list[dict[str, Any]]:
    if not entries:
        return []
    wanted = {entry.id: entry for entry in entries}
    commits = _entry_commits(selection, wanted, sorted({entry.sidecar for entry in entries}))
    blobs = _blobs(selection, _sidecar_changes(commits))
    events: list[dict[str, Any]] = []
    for commit in commits:
        before = _entries_in(commit, blobs, old=True, wanted=wanted)
        after = _entries_in(commit, blobs, old=False, wanted=wanted)
        for entry_id in sorted(set(before) | set(after)):
            event = _entry_event(
                commit, wanted[entry_id], before.get(entry_id), after.get(entry_id)
            )
            if event is not None:
                events.append(event)
    return events


def _sidecar_changes(commits: Sequence[_Commit]) -> list[_Change]:
    return [
        change
        for commit in commits
        for change in commit.changes
        if _is_sidecar(change.old_path) or _is_sidecar(change.new_path)
    ]


def _entry_event(
    commit: _Commit,
    entry: Entry,
    before: Mapping[str, Any] | None,
    after: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    change = _entry_change(before, after)
    if change is None:
        return None
    return {
        "source": "entries",
        "commit": commit.sha,
        "date": commit.date,
        "subject": commit.subject,
        "entry": entry.id,
        "entryKind": entry.kind,
        "invariant": entry.invariant,
        "change": change,
        "before": before,
        "after": after,
    }


def _entry_commits(
    selection: ReaderSelection, wanted: Mapping[str, Entry], sidecars: Sequence[str]
) -> list[_Commit]:
    """Commits touching the entries: by ID anywhere under ``onboarding/`` (adds, removes, moves
    across files), and by the entries' current sidecars (re-anchors), each once with all changes."""

    pattern = "|".join(sorted(wanted))  # -G is an extended regular expression
    moved = _log(selection, [f"-G{pattern}", "--", f"{ONBOARDING_ROOT}/"])
    anchored = _log(selection, ["--", *sidecars])
    commits: dict[str, _Commit] = {}
    for commit in (*moved, *anchored):
        known = commits.get(commit.sha)
        merged = commit.changes if known is None else (*known.changes, *commit.changes)
        commits[commit.sha] = _Commit(
            commit.sha, commit.date, commit.subject, tuple(dict.fromkeys(merged))
        )
    return list(commits.values())


def _is_sidecar(path: str) -> bool:
    return path.startswith(f"{ONBOARDING_ROOT}/") and path.endswith(".json")


def _entries_in(
    commit: _Commit, blobs: Mapping[str, bytes], *, old: bool, wanted: Mapping[str, Entry]
) -> dict[str, dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    for change in commit.changes:
        path = change.old_path if old else change.new_path
        document = _json(blobs.get(change.old_blob if old else change.new_blob))
        if document is not None and _is_sidecar(path):
            found.update(_wanted_entries(document, path, wanted))
    return found


def _wanted_entries(
    document: Mapping[str, Any], sidecar: str, wanted: Mapping[str, Entry]
) -> dict[str, dict[str, Any]]:
    entries = [*(document.get("realizes") or ()), *(document.get("proves") or ())]
    return {
        str(entry["id"]): {
            "path": document.get("path"),
            "sidecar": sidecar,
            "anchor": entry.get("anchor"),
        }
        for entry in entries
        if isinstance(entry, dict) and entry.get("id") in wanted
    }


def _entry_change(before: Mapping[str, Any] | None, after: Mapping[str, Any] | None) -> str | None:
    if before is None and after is None:
        return None
    if before is None:
        return "added"
    if after is None:
        return "removed"
    if before.get("path") != after.get("path") or before.get("sidecar") != after.get("sidecar"):
        return "moved"
    if (before.get("anchor") or {}) != (after.get("anchor") or {}):
        return "re-anchored"
    return None


# --------------------------------------------------------------------------------------------------
# Git
# --------------------------------------------------------------------------------------------------


def _log(selection: ReaderSelection, arguments: list[str]) -> list[_Commit]:
    result = run_git(
        selection.memory_repository,
        ["log", "--raw", "--no-abbrev", _FORMAT, *_range(selection), *arguments],
        _OPTIONS,
    )
    if result.returncode != 0:
        raise ReaderReadError(result.stderr.strip() or "git log failed")
    return [
        commit
        for chunk in result.stdout.split(_RECORD_SEPARATOR)
        if (commit := _commit(chunk)) is not None
    ]


def _range(selection: ReaderSelection) -> list[str]:
    """The converted part of the history up to the selection: ``<first marker commit>^..<rev>``."""

    return list(_converted_range(selection.memory_repository, str(selection.revision)))


@lru_cache(maxsize=64)
def _converted_range(repository: Path, revision: str) -> tuple[str, ...]:
    """Cached per (repository, commit): a commit's history never changes."""

    result = run_git(
        repository,
        ["log", "--diff-filter=A", "--format=%H", revision, "--", LAYOUT_MARKER_PATH],
        _OPTIONS,
    )
    added = result.stdout.split()
    if result.returncode != 0 or not added:
        return (revision,)
    parent = run_git(repository, ["rev-parse", "--verify", "--quiet", f"{added[-1]}^"], _OPTIONS)
    return (f"{parent.stdout.strip()}..{revision}",) if parent.returncode == 0 else (revision,)


def _commit(chunk: str) -> _Commit | None:
    lines = chunk.strip("\n").splitlines()
    if not lines or lines[0].count(_FIELD_SEPARATOR) != 2:
        return None
    sha, date, subject = lines[0].split(_FIELD_SEPARATOR)
    changes = tuple(change for line in lines[1:] if (change := _raw(line)) is not None)
    return _Commit(sha, date, subject, changes)


def _raw(line: str) -> _Change | None:
    """One ``--raw`` line: ``:<mode> <mode> <old> <new> <status>\\t<path>[\\t<new path>]``."""

    if not line.startswith(":"):
        return None
    meta, _, paths = line.partition("\t")
    fields = meta.split()
    if len(fields) < 5:
        return None
    names = paths.split("\t")
    old_path = names[0]
    new_path = names[1] if len(names) > 1 else names[0]
    return _Change(old_path, new_path, fields[2], fields[3])


def _blobs(selection: ReaderSelection, changes: Iterable[_Change]) -> dict[str, bytes]:
    wanted = {blob for change in changes for blob in (change.old_blob, change.new_blob)}
    wanted.discard(_ZERO)
    return read_git_blobs_bytes(selection.memory_repository, wanted) if wanted else {}


def _json(data: bytes | None) -> dict[str, Any] | None:
    if data is None:
        return None
    return _json_text(data.decode("utf-8", errors="replace"))


def _json_text(text: str | None) -> dict[str, Any] | None:
    if text is None:
        return None
    try:
        value = json.loads(text)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None
