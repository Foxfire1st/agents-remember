"""Small writer integration boundaries (MIK-R54), not acceptance."""

from pathlib import Path

import pytest
from agents_remember.application.knowledge_writer.code_anchors import (
    AnchorResolutionError,
    CodeSnapshot,
)
from knowledge_writer_test_support import build_world


def test_symbol_refuses_invalid_utf8_by_path(tmp_path: Path) -> None:
    world = build_world(tmp_path)
    path = "pkg/broken.py"
    (world.code / path).write_bytes(b"def valid():\n    return 1\n# \xff\n")
    snapshot = CodeSnapshot.capture(world.code)
    with pytest.raises(AnchorResolutionError, match=r"broken\.py.*UTF-8"):
        snapshot.resolve(path, {"kind": "symbol", "name": "valid"})
    assert snapshot.resolve(path, {"kind": "line_range", "start": 1, "end": 2})


@pytest.mark.parametrize(
    ("source", "reason"),
    [
        ("it.skip('case', () => {});", r"\.skip"),
        ("it.todo('case');", r"\.todo"),
        ("it.each([1])('case', () => {});", r"\.each"),
        ("it('case' + suffix, () => {});", "computed"),
    ],
)
def test_unsupported_test_title_names_why_and_line_range(
    tmp_path: Path, source: str, reason: str
) -> None:
    world = build_world(tmp_path)
    path = "pkg/cases.test.tsx"
    (world.code / path).write_text(source, encoding="utf-8")
    snapshot = CodeSnapshot.capture(world.code)
    with pytest.raises(AnchorResolutionError, match=reason + ".*line_range"):
        snapshot.resolve(path, {"kind": "symbol", "name": "case"})
