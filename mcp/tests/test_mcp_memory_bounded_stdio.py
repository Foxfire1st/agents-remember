"""One ordinary stdio server stays bounded across repeated native work."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path

from agents_remember.application.file_briefs.folder import brief_location
from agents_remember.kernel.git_command import run_git
from agents_remember.models.knowledge_files.documents import history_path
from agents_remember.models.knowledge_files.history import HistoryFile
from agents_remember.models.knowledge_files.history_product import record_content_digest
from agents_remember.models.knowledge_files.writer_stamp import WriterStamp
from mcp_memory_bounded_fixture import LEAF_ID, build_fixture
from mcp_memory_bounded_receipts import CYCLES, read_completed_workload
from mcp_memory_bounded_stdio_support import CASES, run_selection

_REPOSITORY = Path(__file__).resolve().parents[2]


def _verify_applied_writes(memory: Path, written: tuple[str, ...], history_file: str) -> None:
    history = HistoryFile.model_validate_json((memory / history_file).read_bytes())
    assert history.leaf == LEAF_ID and not history.rows
    assert history.written is not None and len(history.written) == CYCLES
    record_ids: set[str] = set()
    for cycle, relative in enumerate(written, 1):
        path = (memory / relative).resolve(strict=True)
        assert path.is_relative_to(memory.resolve())
        record = json.loads(path.read_bytes())
        assert record["schema"] == "ar-weakness/v1"
        assert record["revision"] == 1
        handoff = f"memory-sample-{cycle}"
        assert record["origin"]["handoffEntry"] == handoff
        assert path.name == f"{record['id']}-{handoff}.json"
        revision = history.written_revision(record["id"], record["revision"])
        assert revision is not None
        assert revision.writer == WriterStamp.model_validate(record["writer"])
        assert revision.writer.by == "agent" and revision.writer.leaf == LEAF_ID
        assert revision.content_digest == record_content_digest(record)
        record_ids.add(record["id"])
    assert len(record_ids) == CYCLES


def _verify_ignored_brief(memory: Path, original_ignore: bytes) -> None:
    ignore = (memory / ".gitignore").read_bytes()
    assert ignore.startswith(original_ignore)
    assert ignore.splitlines().count(b"/briefs/") == 1
    brief = brief_location(memory, "pkg/observe.py")
    assert brief.is_file()
    relative = brief.relative_to(memory).as_posix()
    ignored = run_git(memory, ["check-ignore", "-v", "--", relative])
    assert ignored.returncode == 0 and ignored.stdout.startswith(".gitignore:")
    assert ignored.stdout.rstrip("\n").endswith(f":/briefs/\t{relative}")
    tracked = run_git(memory, ["ls-files", "--", ".gitignore", "briefs/"])
    assert tracked.returncode == 0 and tracked.stdout.splitlines() == [".gitignore"]


def test_native_stdio_memory_stays_bounded(tmp_path: Path) -> None:
    source_selection = os.environ.get("AR_TEST_MEMORY_SERVER_ROOT")
    if source_selection is None:
        source, interpreter = _REPOSITORY, Path(sys.executable)
    else:
        source = Path(source_selection).resolve(strict=True)
        interpreter = source / "mcp" / ".venv" / "bin" / "python"
    fixture = build_fixture(tmp_path / "world", source, interpreter)
    memory = fixture.memory_root
    original_ignore = (memory / ".gitignore").read_bytes()
    assert b"/briefs/" not in original_ignore.splitlines()
    capture = tmp_path / "capture"
    asyncio.run(run_selection(fixture, source, capture))
    history_file = history_path(LEAF_ID)
    receipt = read_completed_workload(capture, fixture.logs, fixture.root, CASES, history_file)
    assert all(not (memory / relative).exists() for relative in receipt.previewed_paths)
    _verify_applied_writes(memory, receipt.written_paths, history_file)
    _verify_ignored_brief(memory, original_ignore)
    terminal = {"source_checkout": str(source), **asdict(receipt)}
    (capture / "terminal.json").write_text(
        json.dumps(terminal, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    assert not receipt.exceeded, terminal
