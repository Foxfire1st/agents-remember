"""The structural three-way merge of a crossing sync (MIK-R24 rule 8 steps 3 and 4).

Once every unconverted side is converted (step 2), the three converted trees merge file by file:

* a file only one side changed is taken from that side (a deletion included);
* **Markdown** merges three-way by line (``git merge-file``);
* **sidecars** merge by key: ``references`` by number, ``realizes``/``proves`` by entry ``id``, every
  other field by name; **records** and other JSON documents merge field by field.

**Items.** A reference, an entry or a record field is one item. Its *mechanical* fields are an
anchor's ``blob``, ``content`` and line numbers; everything else is authored. For an item both sides
changed:

* only one side changed it at all: that side's item, whole;
* one side changed authored fields and the other only mechanical ones: the authored side's, whole;
* both changed only mechanical fields: the incoming side's;
* deleted on one side and changed only mechanically on the other: deleted;
* authored fields changed differently on both sides (or a reference number both sides added with
  different targets): a **conflict** for the curator. A record conflict is resolved through the
  writer, whose ``revision`` becomes one more than the higher side's.

No anchor is re-resolved; staleness is reported later by MIK-R03 wherever the knowledge is read.
"""

from __future__ import annotations

import json
import tempfile
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from agents_remember.kernel.git_command import merge_file_bytes
from agents_remember.models.knowledge_files.canonical import canonical_text, parse_json

_MECHANICAL_ANCHOR: Final = frozenset({"blob", "content"})
_ENTRY_LISTS: Final = ("realizes", "proves")
_ABSENT: Final = object()


CONFLICT_MARKER: Final = "crossing-conflict"
"""The key of the explicit conflict marker a conflicted JSON item or file is written as.

No knowledge-file model admits it, so the validator refuses the file (MIK-R22 rule 1) until the
curator replaces the marker with the resolved item: a conflicted item can never be committed as a
silent "ours".
"""


@dataclass(frozen=True)
class Conflict:
    """One unresolved item: the file, the item's key, why, and the three sides' values."""

    path: str
    item: str
    reason: str
    sides: tuple[Any, Any, Any] = (None, None, None)

    def to_document(self) -> dict[str, Any]:
        document: dict[str, Any] = {"path": self.path, "item": self.item, "reason": self.reason}
        for name, value in zip(("base", "own", "incoming"), self.sides, strict=True):
            if value is not None and value is not _ABSENT:
                document[name] = value
        return document


def _conflicted(
    conflicts: list[Conflict], path: str, item: str, reason: str, sides: tuple[Any, Any, Any]
) -> dict[str, Any]:
    """Record one conflict and return the marker written in its place."""

    conflict = Conflict(path, item, reason, sides)
    conflicts.append(conflict)
    marker = conflict.to_document()
    del marker["path"]
    return {CONFLICT_MARKER: marker}


@dataclass
class MergeResult:
    """The merged files (``None`` = deleted), the conflicts, and per-file provenance counts."""

    files: dict[str, bytes | None] = field(default_factory=dict)
    conflicts: list[Conflict] = field(default_factory=list)
    taken: Counter[str] = field(default_factory=Counter)

    @property
    def conflicted_paths(self) -> tuple[str, ...]:
        return tuple(sorted({conflict.path for conflict in self.conflicts}))


def authored(value: Any) -> Any:
    """``value`` with every mechanical anchor field removed (blob, content, line numbers)."""

    if isinstance(value, dict):
        stripped: dict[str, Any] = {}
        is_anchor = "locator" in value and "blob" in value
        for key, inner in value.items():
            if is_anchor and key in _MECHANICAL_ANCHOR:
                continue
            if is_anchor and key == "locator" and inner.get("kind") == "line_range":
                stripped[key] = {"kind": "line_range"}
                continue
            stripped[key] = authored(inner)
        return stripped
    if isinstance(value, list):
        return [authored(inner) for inner in value]
    return value


def merge_item(base: Any, ours: Any, theirs: Any) -> tuple[Any, str | None]:
    """Merge one item (``_ABSENT`` for a missing one); return (result, conflict reason or None)."""

    if ours == theirs:
        return ours, None
    if base == ours:
        return theirs, None
    if base == theirs:
        return ours, None
    if _ABSENT in (base, ours, theirs):
        return _absent_on_a_side(base, ours, theirs)
    return _both_changed(base, ours, theirs)


def _absent_on_a_side(base: Any, ours: Any, theirs: Any) -> tuple[Any, str | None]:
    """Added on both sides, or deleted on one: equal authored content merges, anything else conflicts."""

    if base is _ABSENT:
        if authored(ours) == authored(theirs):
            return theirs, None
        return ours, "added on both sides with different content"
    kept = theirs if ours is _ABSENT else ours
    if authored(kept) == authored(base):
        return _ABSENT, None
    return kept, "deleted on one side and changed on the other"


def _both_changed(base: Any, ours: Any, theirs: Any) -> tuple[Any, str | None]:
    """An item both sides changed: authored beats mechanical; only authored against authored conflicts."""

    ours_authored = authored(ours) != authored(base)
    theirs_authored = authored(theirs) != authored(base)
    if ours_authored != theirs_authored:
        return (ours if ours_authored else theirs), None
    if not ours_authored or authored(ours) == authored(theirs):
        return theirs, None
    return ours, "authored fields changed differently on both sides"


def _keyed(document: Any, key: str) -> dict[str, Any]:
    if not isinstance(document, dict):
        return {}
    value = document.get(key)
    if key in _ENTRY_LISTS:
        return {str(item["id"]): item for item in value or []}
    return dict(value or {})


def _merge_json(
    path: str, base: Any, ours: Any, theirs: Any, conflicts: list[Conflict]
) -> dict[str, Any]:
    """Merge three JSON documents by key: keyed collections by item, other fields by name."""

    merged: dict[str, Any] = {}
    documents = [one for one in (base, ours, theirs) if isinstance(one, dict)]
    keys = sorted({key for document in documents for key in document})
    for key in keys:
        if key == "references" or key in _ENTRY_LISTS:
            items = _merge_collection(path, key, (base, ours, theirs), conflicts)
            if key in _ENTRY_LISTS:
                if items or any(isinstance(one, dict) and key in one for one in (ours, theirs)):
                    merged[key] = [items[identifier] for identifier in sorted(items)]
            else:
                merged[key] = {number: items[number] for number in sorted(items, key=int)}
            continue
        sides = (_field(base, key), _field(ours, key), _field(theirs, key))
        value, reason = merge_item(*sides)
        if reason is not None:
            value = _conflicted(conflicts, path, key, reason, sides)
        if value is not _ABSENT:
            merged[key] = value
    return merged


def _field(document: Any, key: str) -> Any:
    return document.get(key, _ABSENT) if isinstance(document, dict) else _ABSENT


def _merge_collection(
    path: str, key: str, documents: tuple[Any, Any, Any], conflicts: list[Conflict]
) -> dict[str, Any]:
    sides = [_keyed(document, key) for document in documents]
    merged: dict[str, Any] = {}
    for identifier in sorted({name for side in sides for name in side}):
        values = (
            sides[0].get(identifier, _ABSENT),
            sides[1].get(identifier, _ABSENT),
            sides[2].get(identifier, _ABSENT),
        )
        value, reason = merge_item(*values)
        if reason is not None:
            label = f"{key}.{identifier}"
            if key == "references" and identifier not in sides[0]:
                reason = (
                    f"reference [{identifier}] added on both sides with different targets; "
                    "renumber one side"
                )
            value = _conflicted(conflicts, path, label, reason, values)
        if value is not _ABSENT:
            merged[identifier] = value
    return merged


def _merge_markdown(
    repository: Path, base: bytes | None, ours: bytes, theirs: bytes
) -> tuple[bytes, bool]:
    """Three-way line merge through ``git merge-file``; returns (merged bytes, conflicted)."""

    with tempfile.TemporaryDirectory(prefix="ar-crossing-") as scratch:
        root = Path(scratch)
        paths = []
        for name, data in (("ours", ours), ("base", base or b""), ("theirs", theirs)):
            (root / name).write_bytes(data)
            paths.append(root / name)
        merged, conflicts = merge_file_bytes(
            repository, paths[0], paths[1], paths[2], ("ours", "base", "theirs")
        )
    return merged, conflicts > 0


def _merge_json_file(
    path: str, versions: tuple[bytes | None, bytes | None, bytes | None], result: MergeResult
) -> None:
    """A JSON document both sides changed: merged by key, or kept whole with a conflict."""

    before, mine, incoming = (
        parse_json(one.decode("utf-8")) if one else _ABSENT for one in versions
    )
    conflicts: list[Conflict] = []
    if mine is _ABSENT or incoming is _ABSENT:
        value, reason = merge_item(before, mine, incoming)
        if reason is not None:
            value = _conflicted(conflicts, path, "(file)", reason, (before, mine, incoming))
        result.files[path] = None if value is _ABSENT else _dump(value)
    else:
        result.files[path] = _dump(_merge_json(path, before, mine, incoming, conflicts))
    result.conflicts.extend(conflicts)


def _merge_text_file(
    path: str,
    versions: tuple[bytes | None, bytes | None, bytes | None],
    result: MergeResult,
    repository: Path,
) -> None:
    """A Markdown (or other text) file both sides changed: a three-way line merge."""

    before, mine, incoming = versions
    if mine is None or incoming is None:
        result.files[path] = mine if incoming is None else incoming
        result.conflicts.append(
            Conflict(path, "(file)", "deleted on one side and changed on the other")
        )
        return
    merged_text, conflicted = _merge_markdown(repository, before, mine, incoming)
    result.files[path] = merged_text
    if conflicted:
        result.conflicts.append(Conflict(path, "(lines)", "lines changed on both sides"))


def merge_trees(
    base: Mapping[str, bytes],
    ours: Mapping[str, bytes],
    theirs: Mapping[str, bytes],
    *,
    repository: Path,
) -> MergeResult:
    """Merge three converted knowledge/onboarding trees structurally (rule 8 steps 3 and 4)."""

    result = MergeResult()
    for path in sorted(set(base) | set(ours) | set(theirs)):
        versions = (base.get(path), ours.get(path), theirs.get(path))
        before, mine, incoming = versions
        if incoming in (mine, before):
            result.files[path] = mine
            result.taken["same" if mine == incoming else "own"] += 1
            continue
        if before == mine:
            result.files[path] = incoming
            result.taken["incoming"] += 1
            continue
        result.taken["merged"] += 1
        if path.endswith(".json") and all(one is None or _is_json(one) for one in versions):
            _merge_json_file(path, versions, result)
        else:
            _merge_text_file(path, versions, result, repository)
    return result


def _is_json(data: bytes) -> bool:
    try:
        json.loads(data)
    except ValueError:
        return False
    return True


def _dump(document: Any) -> bytes:
    return canonical_text(document).encode("utf-8")
