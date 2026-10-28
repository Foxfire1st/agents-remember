"""The review's blob-keyed memos, kept on disk between processes (D569).

A cold process -- the dashboard after a restart, a new worklist child -- otherwise parses, diffs and
searches again everything a previous process already answered. Four of the review's memos hold
answers that are functions of the exact inputs named in their keys, so they are written to disk and
read back by the next process:

* :data:`~agents_remember.memory.conversion.code_objects.SYMBOL_SPANS` -- the extents that bind a
  name in one blob at one path (the tree-sitter parse);
* :data:`~agents_remember.application.knowledge_worklist.code.BLOB_HUNKS` -- the zero-context hunks
  of two blobs (one ``git diff`` each);
* :data:`~agents_remember.application.knowledge_worklist.code.TREE_GREPS` -- the paths of one tree
  holding a text (one ``git grep`` each);
* :data:`~agents_remember.application.knowledge_worklist.code.LATEST_GREPS` beside it -- for each
  text, the tree last searched, from which the next tree's listing is carried over. It is an
  origin, not an answer: a listing carried from any searched tree is the same, and an origin the
  repository no longer holds searches the whole tree.

Each entry's key names every input of its answer: complete Git object ids, the path and the name.
What the key does not name is how the answer was produced -- this build's source, the tree-sitter
grammars and the Git that answered -- and that is the store's **namespace**
(:func:`memo_namespace`): an answer stored by another build, grammar or Git is not found. So no entry
has an input outside its key and the namespace, and none carries reads to re-observe. Presence is
still asked as before: a kept answer about a blob or tree is used only after this run read or probed
that object, which is what each memo's own reader already requires.

The envelope is step 1's (:class:`~agents_remember.serving.review_answer_reuse.DiskAnswers`): one
stored answer per memo, keyed ``("review-memo", <memo>)``, under
``<coordination root>/runtime/review-memos`` beside the review answers. Its digest, identity and
size checks apply unchanged, and an unreadable, invalid or foreign store is **absent**: the memo
starts empty and everything is computed as it would have been. No age is read or written into a
decision. Only size evicts: a memo keeps at most its own capacity of entries, and a stored memo at
most :data:`STORED_BYTES` of them, most recently used kept.

A process **restores** the memos once per store (:func:`restore_review_memos`), before its first
computation, and **saves** each memo that changed since (:func:`save_review_memos`), after the
answer it computed was handed over: the save only queues the write on the process's answer writer,
so no response waits for it. A write merges what is on disk with what this process holds, so the
dashboard and its worklist children add to one store. Two writers racing on one memo can lose
the other's newest entries; those are absent next time and computed again.

Not kept on disk: the knowledge documents parsed by their bytes
(:data:`~agents_remember.memory.knowledge_index.build.DOCUMENT_PARSES`) and the route chains
(:data:`~agents_remember.application.knowledge_worklist.route_context.ROUTE_CHAINS`). Restoring
either means validating the same models a parse builds, so it would cost about what it saves, and
neither value has a JSON form whose round trip is the same object by construction.
"""

from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from functools import cache, partial
from pathlib import Path
from threading import Lock
from time import time
from typing import Any, Final

from pydantic import BaseModel, ConfigDict

from agents_remember.application.knowledge_worklist.code import (
    BLOB_HUNKS,
    LATEST_GREPS,
    TREE_GREPS,
    Hunk,
)
from agents_remember.kernel.git_command import run_git
from agents_remember.memory.conversion.code_objects import SYMBOL_SPANS
from agents_remember.memory.knowledge.read_anchor_memo import BoundedMemo, is_complete_object_id
from agents_remember.memory_quality.style.citations.grammars import GRAMMAR_PACKAGES
from agents_remember.serving.review_answer_reuse import (
    ANSWER_WRITES,
    AnswerCodec,
    DiskAnswers,
    DiskBudget,
)

__all__ = [
    "MEMO_DIRECTORY",
    "STORED_BYTES",
    "memo_namespace",
    "restore_review_memos",
    "save_review_memos",
]

MEMO_DIRECTORY: Final = ("runtime", "review-memos")
#: The most bytes one memo's stored answer may take; together they bound the store's directory.
STORED_BYTES: Final = {
    "symbol-spans": 32 * 1024 * 1024,
    "blob-hunks": 16 * 1024 * 1024,
    "tree-greps": 32 * 1024 * 1024,
    "latest-greps": 2 * 1024 * 1024,
}
_ENVELOPE_ROOM: Final = 16 * 1024
_FORMAT: Final = "review-memo/v1"


class _Rows(BaseModel):
    """One memo's entries, least recently used first, grouped by the repository they name."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    repositories: dict[str, list[list[Any]]]


type _Entry = tuple[Any, Any]


@dataclass(frozen=True)
class _Stored:
    """How one memo's entries are written as rows and read back, exactly."""

    name: str
    memo: BoundedMemo[Any, Any]
    row: Callable[[Any, Any], list[Any]]
    entry: Callable[[str, list[Any]], _Entry | None]


def _span_row(key: tuple[str, str, str, str], spans: tuple[tuple[int, int], ...]) -> list[Any]:
    return [key[1], key[2], key[3], [list(span) for span in spans]]


def _span_entry(repository: str, row: list[Any]) -> _Entry | None:
    blob, path, name, spans = row
    if not (_object(blob) and _text(path) and _text(name) and isinstance(spans, list)):
        return None
    if not all(_ints(span, 2) for span in spans):
        return None
    return (repository, blob, path, name), tuple((span[0], span[1]) for span in spans)


def _hunk_row(key: tuple[str, str, str], hunks: tuple[Hunk, ...] | None) -> list[Any]:
    rows = None
    if hunks is not None:
        rows = [[one.old_start, one.old_count, one.new_start, one.new_count] for one in hunks]
    return [key[1], key[2], rows]


def _hunk_entry(repository: str, row: list[Any]) -> _Entry | None:
    old, new, rows = row
    if not (_object(old) and _object(new)):
        return None
    if rows is None:
        return (repository, old, new), None
    if not isinstance(rows, list) or not all(_ints(one, 4) for one in rows):
        return None
    return (repository, old, new), tuple(Hunk(*one) for one in rows)


def _grep_row(key: tuple[str, str, str], paths: tuple[str, ...]) -> list[Any]:
    return [key[1], key[2], list(paths)]


def _grep_entry(repository: str, row: list[Any]) -> _Entry | None:
    tree, leaf, paths = row
    if not (_object(tree) and _text(leaf) and isinstance(paths, list)):
        return None
    if not all(isinstance(path, str) for path in paths):
        return None
    return (repository, tree, leaf), tuple(paths)


def _latest_row(key: tuple[str, str], tree: str) -> list[Any]:
    return [key[1], tree]


def _latest_entry(repository: str, row: list[Any]) -> _Entry | None:
    leaf, tree = row
    if not (_text(leaf) and _object(tree)):
        return None
    return (repository, leaf), tree


def _object(value: object) -> bool:
    return isinstance(value, str) and is_complete_object_id(value)


def _text(value: object) -> bool:
    return isinstance(value, str)


def _ints(value: object, count: int) -> bool:
    return (
        isinstance(value, list)
        and len(value) == count
        and all(type(item) is int and item >= 0 for item in value)
    )


STORED: Final = (
    _Stored("symbol-spans", SYMBOL_SPANS, _span_row, _span_entry),
    _Stored("blob-hunks", BLOB_HUNKS, _hunk_row, _hunk_entry),
    _Stored("tree-greps", TREE_GREPS, _grep_row, _grep_entry),
    _Stored("latest-greps", LATEST_GREPS, _latest_row, _latest_entry),
)
_CODEC: Final[AnswerCodec[_Rows]] = AnswerCodec(_Rows, (_FORMAT,))


@dataclass
class _Store:
    disk: DiskAnswers
    # Each memo's change count when it last agreed with this store (restored or saved).
    agreed: dict[str, int]


_LOCK = Lock()
_STORES: dict[tuple[Path, str], _Store | None] = {}


def memo_namespace(source_digest: str, anchor: Path) -> str:
    """The producing build, grammars and Git of every stored answer: the store's exact scope."""

    return (
        "review-memo:"
        + hashlib.sha256(
            json.dumps(
                [_FORMAT, source_digest, _grammar_identity(), _git_identity(anchor)],
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
    )


@cache
def _grammar_identity() -> list[list[str]]:
    """The bytes of tree-sitter and of each grammar package this process parses with."""

    packages = sorted({"tree_sitter", *(module for module, _ in GRAMMAR_PACKAGES.values())})
    return [[package, _package_digest(package)] for package in packages]


def _package_digest(package: str) -> str:
    try:
        spec = importlib.util.find_spec(package)
    except (ImportError, ValueError):
        return "absent"
    locations = [] if spec is None else list(spec.submodule_search_locations or ())
    if not locations:
        return "absent"
    digest = hashlib.sha256()
    for root in sorted(Path(location) for location in locations):
        for path in sorted(root.rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                digest.update(path.relative_to(root).as_posix().encode("utf-8") + b"\0")
                digest.update(path.read_bytes())
    return digest.hexdigest()


@cache
def _git_identity(anchor: Path) -> str:
    try:
        result = run_git(anchor, ["--version"])
    except OSError:
        return "unknown"
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def restore_review_memos(coordination_root: Path, source_digest: str | None) -> None:
    """Fill the memos from this coordination root's store, once per process and store.

    Nothing is restored without a producing build. A memo whose stored answer is absent or
    invalid starts as it is; entries already held are kept and stay the most recently used.
    """

    store = _store(coordination_root, source_digest)
    if store is None:
        return
    for stored in STORED:
        with _LOCK:
            if stored.name in store.agreed:
                continue
            store.agreed[stored.name] = -1  # this process asks the store once per memo
        entries = _load(store.disk, stored)
        if entries:
            held = stored.memo.items()
            stored.memo.clear()
            for key, value in (*entries, *held):
                stored.memo.put(key, value)
        with _LOCK:
            store.agreed[stored.name] = stored.memo.changes


def save_review_memos() -> None:
    """Hand the process's answer writer each memo that changed since its store last agreed with it.

    Returns at once: the write -- merged with what the store holds -- runs on
    :data:`~agents_remember.serving.review_answer_reuse.ANSWER_WRITES`, beside every other stored
    review answer, with its bounded queue, its drop count and its failure log. A dropped or failed
    write leaves the store as it was; the memo is written with the next save that finds it changed.
    """

    with _LOCK:
        stores = [store for store in _STORES.values() if store is not None]
    for store in stores:
        for stored in STORED:
            if _changed(store, stored):
                ANSWER_WRITES.submit(
                    partial(_write, store, stored), f"the review memo {stored.name}"
                )


def _changed(store: _Store, stored: _Stored) -> bool:
    with _LOCK:
        return store.agreed.get(stored.name, stored.memo.changes) != stored.memo.changes


def _write(store: _Store, stored: _Stored) -> None:
    """One memo merged with its stored answer and written (on the answer writer's thread)."""

    changes = stored.memo.changes
    if not _changed(store, stored):
        return  # an earlier queued write already stored this state
    held = stored.memo.items()
    merged = _newest(stored, (*(_load(store.disk, stored) or ()), *held))
    store.disk.save_batch(((_question(stored), _rows(stored, merged)),), (), _CODEC, _budget())
    with _LOCK:
        store.agreed[stored.name] = changes


def _store(coordination_root: Path, source_digest: str | None) -> _Store | None:
    if not source_digest:
        return None
    directory = coordination_root.joinpath(*MEMO_DIRECTORY)
    with _LOCK:
        known = (directory, source_digest)
        if known in _STORES:
            return _STORES[known]
    store = None
    with contextlib.suppress(OSError, ValueError):
        disk = DiskAnswers.open(directory, memo_namespace(source_digest, coordination_root))
        store = None if disk is None else _Store(disk, {})
    with _LOCK:
        return _STORES.setdefault(known, store)


def _question(stored: _Stored) -> tuple[object, ...]:
    return ("review-memo", stored.name)


def _budget() -> DiskBudget:
    # One stored answer per memo; the directory holds the four of them.
    return DiskBudget(time(), sum(STORED_BYTES.values()), len(STORED_BYTES))


def _load(disk: DiskAnswers, stored: _Stored) -> list[_Entry] | None:
    """The stored memo's entries, least recently used first, or ``None`` when absent or invalid."""

    found = disk.load(_question(stored), (), _CODEC, _budget())
    if found is None:
        return None
    entries: list[_Entry] = []
    for repository, rows in found.repositories.items():
        if not repository:
            return None
        for row in rows:
            entry = _entry(stored, repository, row)
            if entry is None:
                return None
            entries.append(entry)
    return entries


def _entry(stored: _Stored, repository: str, row: list[Any]) -> _Entry | None:
    try:
        return stored.entry(repository, row)
    except (TypeError, ValueError):  # a row of another shape: the stored memo is invalid
        return None


def _newest(stored: _Stored, entries: Iterable[_Entry]) -> list[_Entry]:
    """``entries`` as the memo would hold them after putting each in order (only size evicts)."""

    ordered: dict[Any, Any] = {}
    for key, value in entries:
        ordered.pop(key, None)
        ordered[key] = value
    return list(ordered.items())[-stored.memo.capacity :]


def _rows(stored: _Stored, entries: list[_Entry]) -> _Rows:
    """The newest entries whose rows fit the memo's stored bytes, grouped by repository."""

    room = STORED_BYTES[stored.name] - _ENVELOPE_ROOM
    kept: list[tuple[str, list[Any]]] = []
    for key, value in reversed(entries):
        row = stored.row(key, value)
        try:
            size = len(json.dumps(row, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        except UnicodeEncodeError:  # a name that is not UTF-8 text is not stored; it is computed
            continue
        room -= size + 1
        if room < 0:
            break
        kept.append((key[0], row))
    repositories: dict[str, list[list[Any]]] = {}
    for repository, row in reversed(kept):
        repositories.setdefault(repository, []).append(row)
    return _Rows(repositories=repositories)
