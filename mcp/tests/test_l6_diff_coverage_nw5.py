"""L6 closeout diff-coverage tests for batch NW5.

Each test targets one exact changed line or untaken branch edge listed in
``/tmp/l6-cov-NW5.json``. Tests exercise the real helpers/fixtures and never
weaken the assertions they encode.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest import mock

import pytest

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.application.task_docs.task_doc_tools import TaskDocError, _exact_step_target
from agents_remember.kernel.primitives.runtime_config import (
    McpRuntimeConfig,
)
from agents_remember.memory_quality.style.citations import (
    grammars,
    source_index,
    source_index_state,
)
from agents_remember.memory_quality.style.citations.resolution import Trees
from agents_remember.memory_quality.style.citations.source_index import (
    SourceIndexError,
    _current_generation,
    _reclaim_legacy_root,
    _tree_state,
)
from agents_remember.memory_quality.style.citations.structures import StructuralView
from agents_remember.serving import operator_inbox_posts
from agents_remember.tasks import TaskDocument, write_task_doc
from agents_remember.worktrees import reopen as reopen_module
from agents_remember.worktrees.modules import finalize
from agents_remember.worktrees.modules.finalize import (
    FinalizeTaskDocumentError,
    FinalizeTaskTargets,
)
from agents_remember.worktrees.reopen import (
    ReopenTaskDocumentError,
    _plan_master_index_reset,
    _reopen_master_path,
    _validate_reopen_row_path,
)
from agents_remember.worktrees.worktree_contract import WorktreeContract


@pytest.fixture
def source_env(tmp_path: Path):
    """Isolated code/memory/cache trees for citation source-index tests."""
    code = tmp_path / "code"
    memory = tmp_path / "memory"
    cache = tmp_path / "cache"
    code.mkdir()
    memory.mkdir()
    (code / "a.py").write_text("x = 1\n", encoding="utf-8")
    with mock.patch.dict(os.environ, {"XDG_CACHE_HOME": str(cache)}):
        yield Trees(code_root=code, memory_root=memory), tmp_path


def _leaf(master: str | None = "") -> TaskDocument:
    return TaskDocument.model_validate(
        {
            "id": "L1",
            "slug": "leaf",
            "title": "Leaf",
            "kind": "subTask",
            "repo": "agents-remember",
            "type": "Docs",
            "createdAt": "2026-01-01T00:00",
            "master": master,
        }
    )


def _master(subtask_numbers: list[str]) -> TaskDocument:
    return TaskDocument.model_validate(
        {
            "id": "series",
            "slug": "task",
            "title": "Series",
            "kind": "master",
            "repo": "agents-remember",
            "type": "Master (Code)",
            "createdAt": "2026-01-01T00:00",
            "subTasks": [
                {"number": number, "name": "Leaf", "file": "leaf.json", "status": "Completed"}
                for number in subtask_numbers
            ],
        }
    )


def _contract(task_root: Path) -> WorktreeContract:
    return cast(WorktreeContract, SimpleNamespace(task_root=task_root))


class TestSourceIndexCurrentGeneration:
    def test_manifest_readiness_mismatch_returns_none(self, source_env) -> None:
        """source_index.py:486 and branch 481 -> 486."""
        trees, _ = source_env
        source_index.build_repository_index(trees)
        paths = source_index.cache_paths(trees)
        marker = json.loads(paths.readiness.read_text(encoding="utf-8"))
        marker["snapshotId"] = "0" * 64
        paths.readiness.write_text(json.dumps(marker), encoding="utf-8")
        assert (
            _current_generation(
                paths,
                trees,
                source_index.IndexMetrics(),
                check_content=True,
                verify_integrity=False,
            )
            is None
        )


class TestSourceIndexBuildBytes:
    def test_build_once_exceeds_index_bytes(self, source_env) -> None:
        """source_index.py:597 and branch 596 -> 597."""
        trees, _ = source_env
        source_index.build_repository_index(trees)
        paths = source_index.cache_paths(trees)
        with (
            mock.patch.object(source_index, "MAX_INDEX_BYTES", 1),
            pytest.raises(SourceIndexError, match="while building"),
        ):
            source_index._build_once(paths, trees, source_index.IndexMetrics())


class TestSourceIndexOpenWarm:
    def test_metadata_unchanged_on_second_check(self, source_env) -> None:
        """source_index.py:375 -> 379 (warm re-check without metadata refresh)."""
        trees, _ = source_env
        source_index.build_repository_index(trees)
        paths = source_index.cache_paths(trees)
        manifest = source_index.Manifest.from_json(paths.manifest)
        state = _tree_state(trees, source_index.IndexMetrics())
        changed = source_index.Validation(state, stale=False, metadata_changed=True)
        unchanged = source_index.Validation(state, stale=False, metadata_changed=False)
        with (
            mock.patch.object(
                source_index,
                "_validate",
                side_effect=[changed, unchanged],
            ),
            source_index.open_repository_index(trees) as index,
        ):
            assert index.snapshot_id == manifest.snapshot_id
            assert index.files_indexed == len(manifest.files)


class TestSourceIndexTreeState:
    def test_skips_memory_root_inside_code(self, source_env) -> None:
        """source_index.py:705, 706, and branch 704 -> 705."""
        trees, _ = source_env
        nested = Trees(code_root=trees.code_root, memory_root=trees.code_root)
        state = _tree_state(nested, source_index.IndexMetrics())
        assert state.files == ()
        assert state.directories == ()


class TestSourceIndexReclamation:
    def test_reclaim_legacy_root_missing_is_noop(self, tmp_path: Path) -> None:
        """source_index.py:858 and branch 845 -> 854."""
        missing = tmp_path / "citation-source-index-v9"
        assert _reclaim_legacy_root(missing, 0) is None


class TestReopenPlanning:
    def test_missing_default_master_returns_no_master(self, tmp_path: Path) -> None:
        """reopen.py:234 and branch 230 -> 234 (TOCTOU path via stubbed lookup)."""
        with mock.patch.object(
            reopen_module, "_reopen_master_path", return_value=tmp_path / "task.json"
        ):
            assert _plan_master_index_reset(
                _contract(tmp_path), tmp_path / "leaf.json", _leaf()
            ) == (None, "no-master")

    def test_non_master_parent_refuses(self, tmp_path: Path) -> None:
        """reopen.py:242 and branch 241 -> 242."""
        write_task_doc(
            tmp_path, _leaf(master=None).model_copy(update={"slug": "task", "id": "series"})
        )
        with pytest.raises(ReopenTaskDocumentError, match="not a master"):
            _plan_master_index_reset(_contract(tmp_path), tmp_path / "leaf.json", _leaf())

    def test_missing_index_entry_returns_no_index_entry(self, tmp_path: Path) -> None:
        """reopen.py:251 and branch 247 -> 251."""
        write_task_doc(tmp_path, _master(subtask_numbers=["OTHER"]))
        assert _plan_master_index_reset(_contract(tmp_path), tmp_path / "leaf.json", _leaf()) == (
            None,
            "no-index-entry",
        )

    def test_empty_row_file_returns(self, tmp_path: Path) -> None:
        """reopen.py:270 and branch 269 -> 270."""
        row = {"number": "L1", "name": "Leaf", "status": "Completed"}
        assert (
            _validate_reopen_row_path(tmp_path / "task.json", tmp_path / "leaf.json", "L1", row)
            is None
        )

    def test_master_reference_outside_task_root_refuses(self, tmp_path: Path) -> None:
        """reopen.py:286 and branch 285 -> 286."""
        with pytest.raises(ReopenTaskDocumentError, match="direct child"):
            _reopen_master_path(tmp_path, _leaf(master="../task.md"))


class TestStructuralIndex:
    def test_records_call_expressions(self) -> None:
        """structures.py:87 and branch 86 -> 87."""
        view = StructuralView("code.ts", ["const x = f();\n"])
        view.index(grammars.TYPESCRIPT)
        assert view._bindings is not None
        assert view._calls is not None
        assert len(view._calls) >= 1

    def test_skips_error_nodes(self) -> None:
        """structures.py:89 and branch 88 -> 89."""
        view = StructuralView("code.py", ["def broken(:\n"])
        view.index(grammars.PYTHON)
        assert view._bindings is not None
        assert view._calls is not None


class TestOperatorInboxPosts:
    def test_delivery_catalog_builds_from_config(self, tmp_path: Path) -> None:
        """operator_inbox_posts.py:82, 83, and branch 80 -> 82."""
        config = cast(McpRuntimeConfig, SimpleNamespace(coordination_root=tmp_path))
        catalog = operator_inbox_posts._delivery_catalog(config, None)
        assert catalog.path == tmp_path / "logs" / "dashboard" / "terminal-sessions.json"


class TestTaskDocTools:
    def test_exact_step_target_requires_step_id(self) -> None:
        """task_doc_tools.py:473 and branch 472 -> 473."""
        with pytest.raises(TaskDocError, match=r"skip_step requires step\.id"):
            _exact_step_target({}, {"id": "  "})


class TestSourceIndexState:
    def test_readiness_to_json_oversized(self) -> None:
        """source_index_state.py:132 and branch 131 -> 132."""
        ready = source_index_state.ReadyGeneration(
            generation_id="a" * 64,
            snapshot_id="b" * 64,
            code_root="/" + "x" * 20000,
            memory_root="/memory",
            files_indexed=1,
            source_bytes=1,
            database_bytes=1,
        )
        with pytest.raises(source_index_state.SourceIndexManifestError, match="oversized"):
            ready.to_json()


class TestFinalize:
    def test_reconcile_missing_document_root(self) -> None:
        """finalize.py:385 and branch 384 -> 385."""
        parent = _master(subtask_numbers=["L1"])
        targets = FinalizeTaskTargets(
            leaf_path=None,
            parent_path=Path("/root/task.json"),
            parent=parent,
            parent_row=parent.subTasks[0],
            completed_parent=parent,
        )
        with pytest.raises(FinalizeTaskDocumentError, match="no task-document root"):
            finalize._reconcile_task_documents(_contract(Path("/root")), targets, dry_run=False)
