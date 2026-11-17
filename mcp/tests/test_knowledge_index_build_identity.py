"""A derived index belongs to the package source that built it."""

import json
import subprocess
import sys
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest
from agents_remember.application.knowledge_read import open_read_context, read_knowledge_scope
from agents_remember.memory.knowledge_index import build as builder
from agents_remember.memory.knowledge_index import cache as module
from agents_remember.memory.knowledge_index.build import build_index
from agents_remember.memory.knowledge_index.cache import KnowledgeIndexCache
from agents_remember.memory.knowledge_index.tree import MemoryTreeSnapshot
from agents_remember.models.knowledge.read import (
    KnowledgeReadBudget,
    KnowledgeReadRequest,
    PathSeed,
)
from agents_remember.serving.build_info import process_serving_build, runtime_source_digest
from knowledge_index_test_support import (
    REVIEW_INVARIANT,
    REVIEW_PATH,
    init_repository,
    write_review_tree,
)


def loaded(monkeypatch: pytest.MonkeyPatch, digest: str | None) -> None:
    stamp = replace(process_serving_build(), source_digest=digest)
    monkeypatch.setattr(module, "process_serving_build", lambda: stamp)


def test_another_build_rebuilds_a_partial_index_and_the_same_build_reuses_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    memory = tmp_path / "memory"
    init_repository(memory)
    write_review_tree(memory)
    original = builder._parse_file

    def older_reader(
        parsed: builder.ParsedTree,
        path: str,
        text: str,
        read: Callable[[str], builder.FileModel],
    ) -> None:
        if REVIEW_INVARIANT in path:
            raise ValueError("record unsupported by the older build")
        original(parsed, path, text, read)

    loaded(monkeypatch, "sha256:" + "1" * 64)
    first = KnowledgeIndexCache(tmp_path / "cache")
    with monkeypatch.context() as patch:
        patch.setattr(builder, "_parse_file", older_reader)
        with first.for_directory(memory) as partial:
            assert not partial.state.complete
            assert partial.record(REVIEW_INVARIANT).value is None
            first_path = partial.database_path
    loaded(monkeypatch, "sha256:" + "2" * 64)
    second = KnowledgeIndexCache(first.directory)
    with second.for_directory(memory) as complete:
        assert complete.state.complete
        assert complete.record(REVIEW_INVARIANT).value is not None
        assert complete.database_path != first_path
    with second.for_directory(memory):
        assert second.last_outcome is not None and second.last_outcome.reused


def test_known_build_reuses_after_a_process_restart(tmp_path: Path) -> None:
    memory = tmp_path / "memory"
    init_repository(memory)
    write_review_tree(memory)
    command = """
import json, sys
from pathlib import Path
from agents_remember.memory.knowledge_index.cache import KnowledgeIndexCache
cache = KnowledgeIndexCache(Path(sys.argv[1]))
with cache.for_directory(Path(sys.argv[2])) as index:
    print(json.dumps([str(index.database_path), cache.last_outcome.reused]))
"""
    arguments = [sys.executable, "-c", command, str(tmp_path / "cache"), str(memory)]
    first = json.loads(subprocess.check_output(arguments, text=True))
    second = json.loads(subprocess.check_output(arguments, text=True))
    assert first[0] == second[0]
    assert first[1] is False and second[1] is True


def test_identity_uses_loaded_source_and_package_changes_but_not_documents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = tmp_path / "package"
    package.mkdir()
    source = package / "builder.py"
    source.write_text("VALUE = 1\n")
    first = runtime_source_digest(package)
    loaded(monkeypatch, first)
    before = KnowledgeIndexCache(tmp_path / "cache")
    source.write_text("VALUE = 2\n")
    second = runtime_source_digest(package)
    assert first != second
    # The already-loaded process keeps its identity after disk bytes move.
    assert KnowledgeIndexCache(before.directory).path_for("tree") == before.path_for("tree")
    loaded(monkeypatch, second)
    changed = KnowledgeIndexCache(before.directory)
    assert changed.path_for("tree") != before.path_for("tree")
    (package / "README.md").write_text("A document edit\n")
    assert runtime_source_digest(package) == second


def test_legacy_and_new_builds_never_find_each_others_file(tmp_path: Path) -> None:
    cache = KnowledgeIndexCache(tmp_path / "cache")
    key = "a" * 40
    snapshot = MemoryTreeSnapshot(key, "git", "native", {})
    legacy = cache.directory / f"{key}.sqlite"
    build_index(snapshot, legacy)
    assert cache._reuse(key) is None
    with cache._build(snapshot) as index:
        assert index.database_path != legacy
    legacy.unlink()
    assert not legacy.exists()
    assert cache.path_for(key).is_file()


def test_unknown_build_private_files_support_real_reads_and_are_not_shared(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    memory = tmp_path / "memory"
    init_repository(memory)
    write_review_tree(memory)
    known = KnowledgeIndexCache(tmp_path / "cache")
    with known.for_directory(memory) as retained:
        retained_path = retained.database_path
        key = retained.state.key
    loaded(monkeypatch, None)
    first = KnowledgeIndexCache(known.directory)
    second = KnowledgeIndexCache(first.directory)
    assert first.path_for(key) != second.path_for(key)
    assert first._reuse(key) is None
    with first.for_directory(memory) as index:
        assert index.database_path.is_file()
        assert index.database_path != retained_path
        context = open_read_context(index.database_path, index.repository_id)
        page = read_knowledge_scope(
            index.database_path,
            context,
            KnowledgeReadRequest(
                seed=PathSeed(path=REVIEW_PATH),
                budget=KnowledgeReadBudget(max_items=64),
            ),
        ).page
        assert page is not None and page.items
        assert second._reuse(key) is None
    with second.for_directory(memory) as other:
        assert other.database_path != first.path_for(key)
    assert len(list(first.directory.glob("*.sqlite"))) == 3
    first.max_bytes = 0
    first.evict()
    assert not list(first.directory.iterdir())
