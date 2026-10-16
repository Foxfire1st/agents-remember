"""Read Git's packed reference names and values without interpreting objects."""

from pathlib import Path


def packed_references(common: Path) -> dict[str, str]:
    try:
        text = (common / "packed-refs").read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    references = {}
    for line in text.splitlines():
        if line.startswith(("#", "^")) or not line:
            continue
        value, separator, name = line.partition(" ")
        if not separator or not value or not name.strip():
            raise OSError("unreadable packed reference record")
        references[name.strip()] = value.strip()
    return references
