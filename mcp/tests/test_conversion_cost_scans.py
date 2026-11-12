"""Deterministic conversion costs, with parser and byte semantics pinned."""

from collections import Counter
from pathlib import Path
from unittest.mock import patch

from agents_remember.memory.conversion.code_objects import CodeObjects
from agents_remember.memory_quality.style.document_shape import inline_scan, tables
from agents_remember.models.knowledge_files import anchor_content
from knowledge_conversion_test_support import code_repository, commit_files


def test_table_cells_are_scanned_once_only_on_pipe_bearing_lines() -> None:
    for size in (2, 20):
        lines = [
            "prose without a divider",
            r"escaped \| prose",
            "| single |",
            "---",
            "| value |",
            "",
            "| a | b |",
            "| --- | --- |",
            r"| ``x|`y` `` | slash \| text |",
            "| too | many | cells |",
            "plain terminator",
            "```md",
            "| fenced |",
            "```",
            "| invalid | header |",
            "| --- |",
            "",
        ] + [f"line {index}" for index in range(size)]
        with patch.object(
            inline_scan, "cell_boundaries", wraps=inline_scan.cell_boundaries
        ) as scan:
            found = tables.tables(inline_scan.unfenced_lines(lines))
        observed = Counter(call.args[0] for call in scan.call_args_list)
        eligible = Counter(line for _, line in inline_scan.unfenced_lines(lines) if "|" in line)
        assert observed <= eligible
        assert [
            (header.index, header.cells, [(row.index, row.cells) for row in body])
            for header, body in found
        ] == [
            (2, ["single"], [(4, ["value"])]),
            (
                6,
                ["a", "b"],
                [
                    (8, ["``x|`y` ``", r"slash \| text"]),
                    (9, ["too", "many", "cells"]),
                ],
            ),
        ]


def test_constructed_rows_keep_the_same_table_boundary_behavior() -> None:
    header = tables.Row(0, "| value |", ["value"])
    delimiter = tables.Row(1, "---", ["---"])
    body = tables.Row(2, "| body |", ["body"])
    assert tables.starts_table(header, delimiter)
    assert tables.read_body([header, delimiter, body], 2) == ([body], 3)
    assert tables.rows_of([(0, "| value |")]) == [header]


def test_each_blob_is_split_once_per_conversion_objects_instance(tmp_path: Path) -> None:
    code = code_repository(tmp_path / "code")
    source = "def value():\r\n    return 1\r\n# lone\rCR\nlast"
    head = commit_files(code.root, {"same.py": source}, "blob")
    blob = CodeObjects(code.root).tree(head)["same.py"]
    expected = anchor_content.content_identity(b"def value():\r\n    return 1\r\n")
    for size in (2, 20):
        with patch.object(anchor_content, "blob_lines", wraps=anchor_content.blob_lines) as split:
            for _ in range(2):
                objects = CodeObjects(code.root)
                for _ in range(size):
                    assert objects.line_count(blob) == 4
                    for path in ("same.py", "alias.py"):
                        assert (
                            objects.content(
                                blob, {"kind": "line_range", "start": 1, "end": 2}, path
                            )
                            == expected
                        )
                        assert (
                            objects.content(blob, {"kind": "symbol", "name": "value"}, path)
                            == expected
                        )
                    assert objects.content(
                        blob, {"kind": "file"}, "same.py"
                    ) == anchor_content.content_identity(source.encode())
                    assert (
                        objects.content(
                            blob, {"kind": "line_range", "start": 2, "end": 5}, "same.py"
                        )
                        is None
                    )
            assert split.call_count == 2
