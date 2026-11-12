"""Read Git's packed reference names and values without interpreting objects."""

import os
from pathlib import Path

from agents_remember.kernel.witnessed_memo import WitnessedMemo

type _Witness = tuple[int, int, int, int, int, int]

#: The last parse per ``packed-refs`` file, with the identity it was read under. A parse is
#: kept only when the identity was the same before and after its read. One file per repository
#: common directory, a handful per dashboard; an evicted file is simply parsed again.
_parsed: WitnessedMemo[Path, _Witness, dict[str, str]] = WitnessedMemo(256)


def _witness(path: Path) -> _Witness | None:
    """Device, inode, mode, size, mtime and ctime; ctime moves on a restored-mtime rewrite."""
    try:
        info = os.stat(path)
    except FileNotFoundError:
        return None
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def packed_references(common: Path) -> dict[str, str]:
    """The packed references, parsed again only when the file's identity moved."""
    path = common / "packed-refs"
    before = _witness(path)
    kept = _parsed.get(path, before) if before is not None else None
    if kept is not None:
        return dict(kept[0])
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        _parsed.discard(path)
        return {}
    references = {}
    for line in text.splitlines():
        if line.startswith(("#", "^")) or not line:
            continue
        value, separator, name = line.partition(" ")
        if not separator or not value or not name.strip():
            raise OSError("unreadable packed reference record")
        references[name.strip()] = value.strip()
    if before is not None and _witness(path) == before:
        _parsed.put(path, before, dict(references))
    return references
