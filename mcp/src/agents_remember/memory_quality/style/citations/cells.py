"""Parse citation cells shared by the converter and converted card authoring."""

from __future__ import annotations

from agents_remember.memory_quality.style.citations import model

FINDING_COLUMN = "finding"
ANCHOR_COLUMN = "anchor"
SOURCE_COLUMN = "source"


def unescaped(cell: str) -> str:
    r"""One table cell's text with GFM's ``\|`` escape resolved to a literal pipe.

    GFM requires a literal pipe inside a table cell to be escaped, and a renderer shows the
    single character; this scanner reads the RAW cell text, so an anchor written as
    `` `dict \| None` `` arrived at the anchor grammar as ``dict \| None`` and could never
    match the source's ``dict | None``. The construct was then either reported as an absent
    anchor or, worse, satisfied by some other occurrence in the range -- which is why the
    escape is resolved here, where cell text becomes anchor text, rather than by asking the
    author to spell the code differently. Only ``\|`` is touched: every other backslash in a
    cell is content the anchor grammar or the source path owns (``\d``, ``\n``, a Windows
    separator), so a general markdown unescaper would corrupt the very spans this reads.
    """
    return cell.replace("\\|", "|")


def parse_row(line: int, anchor_cell: str, source_cell: str) -> model.Claim:
    anchors, skipped = model.anchors_in(unescaped(anchor_cell))
    citations, malformed = model.citations_in(unescaped(source_cell))
    return model.Claim(
        line=line,
        anchors=anchors,
        citations=citations,
        malformed=malformed,
        unchecked_spans=skipped,
    )
