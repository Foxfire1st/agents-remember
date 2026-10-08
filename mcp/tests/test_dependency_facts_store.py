"""Content-derived facts reuse, invalidation and disposable-store safety."""

from __future__ import annotations

import ast
import json
import os
from dataclasses import fields
from pathlib import Path
from unittest.mock import patch

import pytest
from agents_remember_test_support.testing import dependency_facts as facts


def _tree(root: Path, size: int) -> None:
    (root / "pkg").mkdir()
    (root / "tests").mkdir()
    (root / "pkg/__init__.py").write_text("", encoding="utf-8")
    for index in range(size):
        (root / f"pkg/module{index}.py").write_text(
            'from . import child\nvalue = "fixture.json"\n', encoding="utf-8"
        )
    (root / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n', encoding="utf-8"
    )


@pytest.fixture
def fixture_tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    monkeypatch.setattr(facts, "git_untracked_files", lambda *_: [])
    # The repository readers normally supply relative Git paths.
    monkeypatch.setattr(
        facts, "git_ls_files", lambda _: [path.relative_to(root) for path in root.rglob("*.py")]
    )
    return root


@pytest.mark.parametrize("size", [2, 5])
def test_reuse_invalidation_and_bounded_compaction(fixture_tree: Path, size: int) -> None:
    root = fixture_tree
    _tree(root, size)
    store = root.parent / "facts.json"
    unstored = facts.RepositoryDependencyFacts.build(root, store=None)
    reads: dict[Path, int] = {}
    original_read = Path.read_bytes

    def counted_read(path: Path) -> bytes:
        if path.is_relative_to(root):
            reads[path] = reads.get(path, 0) + 1
        return original_read(path)

    with (
        patch.object(ast, "parse", wraps=ast.parse) as parse,
        patch.object(Path, "read_bytes", counted_read),
    ):
        stored = facts.RepositoryDependencyFacts.build(root, store=store)
        assert parse.call_count == size + 1
        assert len(reads) == size + 1 and set(reads.values()) == {1}
        for field in fields(stored):
            assert getattr(stored, field.name) == getattr(unstored, field.name), field.name
        parse.reset_mock()
        assert facts.RepositoryDependencyFacts.build(root, store=store) == stored
        parse.assert_not_called()
        changed = root / "pkg/module0.py"
        changed.write_text('import os\nvalue = "different.json"\n', encoding="utf-8")
        refreshed = facts.RepositoryDependencyFacts.build(root, store=store)
        assert parse.call_count == 1
        assert parse.call_args.kwargs["filename"] == str(changed)
        assert refreshed.imports[Path("pkg/module0.py")] == frozenset({"os"})
        parse.reset_mock()
        changed.rename(root / "pkg/renamed.py")
        renamed = facts.RepositoryDependencyFacts.build(root, store=store)
        assert parse.call_count == 1
        assert Path("pkg/module0.py") not in renamed.modules
        assert renamed.modules[Path("pkg/renamed.py")] == "pkg.renamed"
        test_path = root / "tests/test_sample.py"
        test_path.write_text("import pkg.renamed\n", encoding="utf-8")
        parse.reset_mock()
        population = facts.RepositoryDependencyFacts.build(root, store=store)
        assert parse.call_count == 1
        assert population.tests == (Path("tests/test_sample.py"),)
        assert population.importers["pkg.renamed"] == frozenset({Path("tests/test_sample.py")})
        test_path.unlink()
        parse.reset_mock()
        assert facts.RepositoryDependencyFacts.build(root, store=store) == renamed
        parse.assert_not_called()
        entries = json.loads(store.read_text("utf-8"))
        entries.update({f"obsolete-{i}": [] for i in range(4 * (size + 1))})
        store.write_text(json.dumps(entries), encoding="utf-8")
        parse.reset_mock()
        compacted = facts.RepositoryDependencyFacts.build(root, store=store)
        parse.assert_not_called()
        assert compacted == renamed
        assert len(json.loads(store.read_text("utf-8"))) == size + 1


def test_parse_and_dynamic_plugin_errors_are_never_cached(fixture_tree: Path) -> None:
    _tree(fixture_tree, 1)
    source = fixture_tree / "pkg/module0.py"
    store = fixture_tree.parent / "facts.json"
    for invalid in ("def broken(\n", "pytest_plugins = discover_plugins()\n"):
        source.write_text(invalid, encoding="utf-8")
        with patch.object(ast, "parse", wraps=ast.parse) as parse:
            first = facts.RepositoryDependencyFacts.build(fixture_tree, store=store)
            assert first.parse_error
            assert not first.imports and not first.string_literals
            parse.reset_mock()
            again = facts.RepositoryDependencyFacts.build(fixture_tree, store=store)
            assert again == first
            assert parse.call_count == 1
            assert parse.call_args.kwargs["filename"] == str(source)


def test_unusable_store_and_bad_entries_recompute_correctly(fixture_tree: Path) -> None:
    _tree(fixture_tree, 1)
    store = fixture_tree.parent / "facts.json"
    expected = facts.RepositoryDependencyFacts.build(fixture_tree, store=None)
    for contents in ("{bad", "[]", "null"):
        store.write_text(contents, encoding="utf-8")
        assert facts.RepositoryDependencyFacts.build(fixture_tree, store=store) == expected
    entries = json.loads(store.read_text("utf-8"))
    store.write_text(json.dumps({key: [[], [], 1, False] for key in entries}), encoding="utf-8")
    with patch.object(ast, "parse", wraps=ast.parse) as parse:
        assert facts.RepositoryDependencyFacts.build(fixture_tree, store=store) == expected
        assert parse.call_count == 2
    original = store.read_bytes()
    with patch.object(facts.os, "open", side_effect=PermissionError("unreadable")):
        assert facts.RepositoryDependencyFacts.build(fixture_tree, store=store) == expected
    assert store.read_bytes() == original
    with patch.object(facts.os, "getuid", return_value=os.getuid() + 1):
        assert facts.RepositoryDependencyFacts.build(fixture_tree, store=store) == expected
    assert store.read_bytes() == original
    target = fixture_tree.parent / "target.json"
    store.rename(target)
    store.symlink_to(target)
    assert facts.RepositoryDependencyFacts.build(fixture_tree, store=store) == expected
    assert store.is_symlink() and target.read_bytes() == original
    store.unlink()
    store.mkdir()
    assert facts.RepositoryDependencyFacts.build(fixture_tree, store=store) == expected
    assert store.is_dir()


def test_module_package_python_and_implementation_identity(fixture_tree: Path) -> None:
    _tree(fixture_tree, 1)
    store = fixture_tree.parent / "facts.json"
    facts.RepositoryDependencyFacts.build(fixture_tree, store=store)
    source = fixture_tree / "pkg/module0.py"
    package = fixture_tree / "pkg/module0"
    package.mkdir()
    source.rename(package / "__init__.py")
    with patch.object(ast, "parse", wraps=ast.parse) as parse:
        changed = facts.RepositoryDependencyFacts.build(fixture_tree, store=store)
        assert parse.call_count == 1
        assert "pkg.module0.child" in changed.imports[Path("pkg/module0/__init__.py")]
    with (
        patch.object(facts.sys, "version_info", (*facts.sys.version_info[:3], "final", 1)),
        patch.object(ast, "parse", wraps=ast.parse) as parse,
    ):
        assert facts.RepositoryDependencyFacts.build(fixture_tree, store=store) == changed
        assert parse.call_count == 2
    original_read = Path.read_bytes
    implementation = Path(facts.__file__).resolve()

    def changed_implementation(path: Path) -> bytes:
        content = original_read(path)
        return content + b"\n" if path == implementation else content

    with (
        patch.object(Path, "read_bytes", changed_implementation),
        patch.object(ast, "parse", wraps=ast.parse) as parse,
    ):
        assert facts.RepositoryDependencyFacts.build(fixture_tree, store=store) == changed
        assert parse.call_count == 2


@pytest.mark.parametrize("depth", [1200, 12000])
def test_nested_wrong_shaped_entries_cannot_break_facts(fixture_tree: Path, depth: int) -> None:
    _tree(fixture_tree, 1)
    store = fixture_tree.parent / "facts.json"
    expected = facts.RepositoryDependencyFacts.build(fixture_tree, store=None)
    store.write_text('{"x":' + "[" * depth + "0" + "]" * depth + "}", encoding="utf-8")
    assert facts.RepositoryDependencyFacts.build(fixture_tree, store=store) == expected
    assert len(json.loads(store.read_text("utf-8"))) == 2
    assert not list(store.parent.glob("tmp*"))
