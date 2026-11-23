"""One ordinary stdio server stays bounded across repeated native work."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path

from mcp_memory_bounded_fixture import build_fixture
from mcp_memory_bounded_receipts import read_completed_workload
from mcp_memory_bounded_stdio_support import CASES, run_selection

_REPOSITORY = Path(__file__).resolve().parents[2]


def _verify_applied_writes(memory: Path, written: tuple[str, ...]) -> None:
    for cycle, relative in enumerate(written, 1):
        path = (memory / relative).resolve(strict=True)
        assert path.is_relative_to(memory.resolve())
        record = json.loads(path.read_bytes())
        assert record["schema"] == "ar-weakness/v1"
        assert record["origin"]["handoffEntry"] == f"memory-sample-{cycle}"


def test_native_stdio_memory_stays_bounded(tmp_path: Path) -> None:
    source_selection = os.environ.get("AR_TEST_MEMORY_SERVER_ROOT")
    if source_selection is None:
        source, interpreter = _REPOSITORY, Path(sys.executable)
    else:
        source = Path(source_selection).resolve(strict=True)
        interpreter = source / "mcp" / ".venv" / "bin" / "python"
    fixture = build_fixture(tmp_path / "world", source, interpreter)
    capture = tmp_path / "capture"
    asyncio.run(run_selection(fixture, source, capture))
    receipt = read_completed_workload(capture, fixture.logs, fixture.root, CASES)
    _verify_applied_writes(fixture.memory_root, receipt.written_paths)
    terminal = {"source_checkout": str(source), **asdict(receipt)}
    (capture / "terminal.json").write_text(
        json.dumps(terminal, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    assert not receipt.exceeded, terminal
