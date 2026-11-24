"""MIK-R21 rule 8: the canonical JSON formatter and its ``knowledge-format`` command.

The formatter changes formatting only. Each case checks both halves: the output is canonical and
stable under a second pass (idempotence), and the parsed value is unchanged apart from the order of
arrays of identified entries, which the packet defines as formatting.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from agents_remember.cli.__main__ import main
from agents_remember.models.knowledge_files.canonical import (
    CanonicalFormatError,
    format_bytes,
    format_text,
    is_canonical,
)

MESSY = (
    '{"schema":"ar-onboarding-file/v1","realizes":[{"role":"support","id":"RLZ-ZZZZZZ"},'
    '{"id":"RLZ-000000","role":"enforcement"}],\r\n "path":"mcp/é.py","references":{"2":{"targets":'
    '[{"kind":"invariant","id":"INV-7K3F9Q"}]},"10":{"note":"n","targets":[]}},'
    '"alternatives":[{"option":"b"},{"option":"a"}],"n":1.5,"flag":true,"none":[]}'
)


def test_formatter_is_idempotent_and_preserves_content() -> None:
    once = format_text(MESSY)
    assert format_text(once) == once
    assert is_canonical(once.encode("utf-8"))
    assert not is_canonical(MESSY.encode("utf-8"))
    assert once.endswith("}\n") and not once.endswith("\n\n")
    assert '\n  "alternatives": [\n    {\n      "option": "b"' in once, "two-space indentation"
    assert "é" in once, "UTF-8 is written as-is, not escaped"
    assert "\r" not in once
    formatted, original = json.loads(once), json.loads(MESSY)
    # Identified entries are sorted by id; every other array keeps its authored order.
    assert [entry["id"] for entry in formatted["realizes"]] == ["RLZ-000000", "RLZ-ZZZZZZ"]
    assert formatted["alternatives"] == original["alternatives"]
    original["realizes"].sort(key=lambda entry: entry["id"])
    assert formatted == original
    keys = list(formatted)
    assert keys == sorted(keys)
    assert list(formatted["references"]) == ["10", "2"], "keys sort as strings"


def test_only_identified_entry_positions_are_sorted() -> None:
    targets = [
        {"kind": "invariant", "id": "INV-ZZZZZZ"},
        {"kind": "record", "id": "ASM-000000"},
    ]
    document = {
        "references": {"1": {"targets": targets}},
        "proves": [{"id": "PRF-ZZZZZZ"}, {"id": "PRF-000000"}],
        "rows": [{"id": "b"}, {"id": "a"}],
        "other": [{"id": "b"}, {"id": "a"}],
    }
    formatted = json.loads(format_text(json.dumps(document)))
    assert formatted["references"]["1"]["targets"] == targets, "targets keep authored order"
    assert formatted["other"] == document["other"], "an undeclared position keeps its order"
    assert [entry["id"] for entry in formatted["proves"]] == ["PRF-000000", "PRF-ZZZZZZ"]
    assert [entry["id"] for entry in formatted["rows"]] == ["a", "b"]


@pytest.mark.parametrize(
    ("payload", "reason"),
    [
        (b'{"a": 1, "a": 2}', "repeats key"),
        (b'{"b": 1, "a": 2, "a": 3, "b": 4, "c": 5}', r"repeats key\(s\): \['a', 'b'\]"),
        (b'{"x": {"a": 1, "a": 1}}', "repeats key"),
        (b'{"a": NaN}', "not a JSON value"),
        ('﻿{"a": 1}'.encode(), "byte-order mark"),
        (b'{"a": "\xff"}', "UTF-8"),
        (b'{"a": ', "invalid JSON"),
    ],
)
def test_formatter_refuses_input_it_cannot_reformat_without_changing_content(
    payload: bytes, reason: str
) -> None:
    with pytest.raises(CanonicalFormatError, match=reason):
        format_bytes(payload)


@pytest.mark.parametrize(
    ("relative_tree", "relative_operand"),
    [
        pytest.param("memory", ".", id="ordinary-root"),
        pytest.param("memory", "onboarding/..", id="equivalent-operand"),
        pytest.param("memory", "..", id="contains-memory-root"),
        pytest.param("knowledge/memory", ".", id="unrelated-knowledge-ancestor"),
        pytest.param("onboarding/memory", ".", id="unrelated-onboarding-ancestor"),
    ],
)
def test_knowledge_format_command_checks_rewrites_and_skips_caches(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    relative_tree: str,
    relative_operand: str,
) -> None:
    tree = tmp_path / relative_tree
    (tree / "knowledge").mkdir(parents=True)
    (tree / "onboarding" / "mcp").mkdir(parents=True)
    (tree / "system").mkdir()
    (tree / ".git").mkdir()
    messy = tree / "knowledge" / "layout.json"
    messy.write_text('{"conversion":"1","schema":"ar-memory-layout/v2"}', encoding="utf-8")
    canonical = tree / "onboarding" / "mcp" / "overview.json"
    canonical.write_text(format_text('{"schema":"ar-onboarding-route/v1"}'), encoding="utf-8")
    cache = tree / "onboarding" / "mcp" / "overview.index.json"
    cache.write_text('{"b":1,"a":2}', encoding="utf-8")
    hidden = tree / "onboarding" / ".github" / "workflows" / "quality.yml.json"
    hidden.parent.mkdir(parents=True)
    hidden.write_text('{"path":".github/workflows/quality.yml"}', encoding="utf-8")
    hidden_knowledge = tree / "knowledge" / ".source" / "card.json"
    hidden_knowledge.parent.mkdir()
    hidden_knowledge.write_text('{"visible":true}', encoding="utf-8")
    settings = tree / "system" / "settings.json"
    settings.write_text('{"version":2}', encoding="utf-8")
    git_metadata = tree / ".git" / "fixture.json"
    git_bytes = b'{"preserve":true}'
    git_metadata.write_bytes(git_bytes)
    before = messy.read_bytes()

    assert main(["knowledge-format", "--check", str(tree)]) == 1
    assert messy.read_bytes() == before, "--check writes nothing"
    findings = capsys.readouterr().out
    for path in (messy, hidden, hidden_knowledge, settings):
        assert f"not canonical {path}" in findings

    assert main(["knowledge-format", "--check", str(tree / "onboarding")]) == 1
    assert capsys.readouterr().out.strip() == f"not canonical {hidden}"

    assert main(["knowledge-format", str(tree / relative_operand)]) == 0
    assert is_canonical(messy.read_bytes())
    assert json.loads(messy.read_bytes()) == json.loads(before)
    assert cache.read_text(encoding="utf-8") == '{"b":1,"a":2}', "the route-index cache is skipped"
    assert is_canonical(hidden.read_bytes())
    assert is_canonical(hidden_knowledge.read_bytes())
    assert is_canonical(settings.read_bytes())
    assert git_metadata.read_bytes() == git_bytes
    assert main(["knowledge-format", "--check", str(tree)]) == 0

    broken = tree / "knowledge" / "broken.json"
    broken.write_text('{"a": 1, "a": 2}', encoding="utf-8")
    capsys.readouterr()
    assert main(["knowledge-format", str(broken)]) == 2
    assert broken.read_text(encoding="utf-8") == '{"a": 1, "a": 2}', (
        "unparseable input is untouched"
    )
    assert "invalid" in capsys.readouterr().out


def test_knowledge_format_preserves_dot_directories_outside_memory_roots(tmp_path: Path) -> None:
    marker = tmp_path / "knowledge" / "layout.json"
    marker.parent.mkdir()
    marker.write_text('{"conversion":"1","schema":"ar-memory-layout/v2"}', encoding="utf-8")
    operand = tmp_path / "system"
    original = b'{"preserve":true}'
    sentinels = [
        operand / root_name / ".private" / "config.json"
        for root_name in ("knowledge", "onboarding")
    ]
    for path in sentinels:
        path.parent.mkdir(parents=True)
        path.write_bytes(original)
    settings = operand / "settings.json"
    settings.write_bytes(b'{"ordinary":true}')

    assert main(["knowledge-format", str(operand)]) == 0

    assert is_canonical(settings.read_bytes())
    assert [path.read_bytes() for path in sentinels] == [original, original]


def test_knowledge_format_prunes_dot_directories_in_unconverted_memory(tmp_path: Path) -> None:
    card = tmp_path / "knowledge" / ".private" / "card.json"
    card.parent.mkdir(parents=True)
    original = b'{"preserve":true}'
    card.write_bytes(original)
    settings = tmp_path / "settings.json"
    settings.write_bytes(b'{"ordinary":true}')

    assert main(["knowledge-format", str(tmp_path)]) == 0

    assert is_canonical(settings.read_bytes())
    assert card.read_bytes() == original
