"""One native persisted-worklist prerequisite for positive Writer fixtures."""

from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path

from agents_remember.application.knowledge_worklist import ExplicitSides, worklist_for_sides
from agents_remember.application.knowledge_worklist.base_cache import default_base_cache_directory
from agents_remember.application.knowledge_worklist.leaf import WORKLIST_FILE_NAME
from agents_remember.application.knowledge_writer import WriteRequest
from agents_remember.application.knowledge_writer import write_knowledge as native_write_knowledge
from agents_remember.application.knowledge_writer.code_anchors import CodeSnapshot
from agents_remember.application.knowledge_writer.report import WriteReport


def prepare_worklist(request: WriteRequest, *, contract: Path | None = None) -> WriteRequest:
    """Prepare missing positive prerequisites, preserving explicitly supplied worklists."""
    assert "PYTEST_CURRENT_TEST" in os.environ, "Writer fixture preparation runs only in pytest"
    if request.owner.kind == "leaf" and request.worklist is None and request.code_root.is_dir():
        worklist = worklist_for_sides(
            ExplicitSides(
                code_repository=request.code_root,
                base=request.code_base or "HEAD",
                memory_repository=request.memory_root,
                memory_base=request.memory_base or "HEAD",
                memory_candidate=request.memory_root,
                code_worktree=request.code_root,
                owner=request.owner.id,
                coordination_root=request.coordination_root,
                cache_directory=(
                    default_base_cache_directory(request.coordination_root)
                    if request.coordination_root is not None
                    else None
                ),
            )
        )
        if worklist is not None:
            path = (
                contract.parent / WORKLIST_FILE_NAME
                if contract is not None
                else Path(os.environ["XDG_CACHE_HOME"]) / "fixture-writer-worklist.json"
            )
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(worklist), encoding="utf-8")
            request = replace(request, worklist=json.loads(path.read_text(encoding="utf-8")))
    return request


def write_knowledge(request: WriteRequest, *, code: CodeSnapshot | None = None) -> WriteReport:
    return native_write_knowledge(prepare_worklist(request), code=code)
