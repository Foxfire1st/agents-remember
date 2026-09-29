"""The one definition of an anchor's ``content`` bytes (MIK-R21 rule 3, ruling Q5 of 260928-MIK-L12).

MIK-R03, R08, R12 and R24 all hash through :mod:`agents_remember.models.knowledge_files.
anchor_content`, so these cases pin which bytes a range names: lines split on ``\\n`` only, each
keeping its own terminator, never decoded.
"""

from __future__ import annotations

import hashlib

import pytest
from agents_remember.models.knowledge_files.anchor_content import (
    RangeOutsideBlobError,
    blob_lines,
    content_identity,
    line_count,
    range_bytes,
)


@pytest.mark.parametrize(
    ("blob", "start", "end", "expected"),
    [
        (b"a\nb\nc\n", 2, 3, b"b\nc\n"),
        (b"a\r\nb\r\nc\r\n", 1, 2, b"a\r\nb\r\n"),
        (b"a\nx\ry\x0bz\x85\nlast", 2, 3, b"x\ry\x0bz\x85\nlast"),
        (b"\xff\xfe\n\xc3\x28\n", 2, 2, b"\xc3\x28\n"),
    ],
    ids=["lf", "crlf", "lone-cr-and-no-final-newline", "invalid-utf8"],
)
def test_a_line_range_names_its_lines_with_their_own_terminators(
    blob: bytes, start: int, end: int, expected: bytes
) -> None:
    assert range_bytes(blob, start, end) == expected
    assert content_identity(range_bytes(blob, start, end)) == (
        f"sha256:{hashlib.sha256(expected).hexdigest()}"
    )


def test_line_count_file_content_and_ranges_outside_the_blob() -> None:
    assert blob_lines(b"a\nb") == [b"a\n", b"b"]
    assert line_count(b"a\nb\n") == 2
    assert line_count(b"a\nb") == 2
    assert line_count(b"") == 0
    whole = b"one\r\ntwo"
    assert content_identity(whole) == f"sha256:{hashlib.sha256(whole).hexdigest()}"
    with pytest.raises(RangeOutsideBlobError, match="holds 2"):
        range_bytes(b"a\nb\n", 2, 3)
    with pytest.raises(RangeOutsideBlobError):
        range_bytes(b"a\n", 0, 1)
