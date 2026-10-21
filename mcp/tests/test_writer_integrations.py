"""Small writer integration boundaries (MIK-R54, MIK-R194), not acceptance."""

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
    ("source", "reason", "offers_range"),
    [
        ("it.skip('case', () => {});", r"uses .skip, which does not run", False),
        ("it.todo('case');", r"uses .todo, which does not run", False),
        (
            "it.skipIf(flag)('case', () => {});",
            r"uses .skipIf, which runs only when its condition holds",
            False,
        ),
        (
            "it.fails('case', () => {});",
            r"uses .fails, which passes only when its body fails",
            False,
        ),
        ("it.each([1])('case', () => {});", r"uses .each, which is not a definition", True),
        ("it('case' + suffix, () => {});", "has a computed title", True),
    ],
)
def test_unsupported_test_title_names_why_and_may_offer_a_line_range(
    tmp_path: Path, source: str, reason: str, offers_range: bool
) -> None:
    world = build_world(tmp_path)
    path = "pkg/cases.test.tsx"
    (world.code / path).write_text(source, encoding="utf-8")
    snapshot = CodeSnapshot.capture(world.code)
    with pytest.raises(AnchorResolutionError) as refused:
        snapshot.resolve(path, {"kind": "symbol", "name": "case"})
    message = str(refused.value)
    assert reason in message, message
    # A case that does not run proves nothing, so no line range reaches it either (rule 3).
    assert ("line_range" in message) is offers_range, message
    if offers_range:
        assert '"start": 1, "end": 1' in message, message
