"""L6 closeout coverage tests for citation helper modules."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest import mock

import pytest

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.errors import CitationCacheError
from agents_remember.memory_quality.style.citations import (
    extents,
    grammars,
    model,
    resolution,
    source_index_state,
    structures,
    symbol_index,
)
from agents_remember.memory_quality.style.citations.source_index_cache import ManagedCacheAuthority


def anchor(kind: str, text: str) -> model.Anchor:
    return model.Anchor(kind=kind, text=text)


class TestStructures:
    def test_fingerprint_no_grammar(self) -> None:
        digest = structures.fingerprint(
            anchor(model.SYMBOL, "x"),
            "plain.txt",
            ["x = 1"],
            extents.Extent(1, 2, extents.DEFINITION),
        )
        assert digest

    def test_fingerprint_call_and_fragment(self) -> None:
        lines = ["def f():\n", "    return g()\n", "\n"]
        call = extents.Extent(2, 2, extents.CALL)
        digest = structures.fingerprint(anchor(model.SYMBOL, "g"), "code.py", lines, call)
        assert digest
        digest = structures.fingerprint(
            anchor(model.SYMBOL, "g"), "code.py", lines, extents.Extent(1, 1, extents.CALL)
        )
        assert digest

    def test_binding_node(self) -> None:
        view = structures.StructuralView("code.py", ["def f():\n", "    return 1\n"])
        extent = extents.Extent(1, 3, extents.DEFINITION)
        node = view.binding_node(anchor(model.SYMBOL, "f"), extent, grammars.PYTHON)
        assert node is not None
        node = view.binding_node(anchor(model.HEADING, "# f"), extent, grammars.PYTHON)
        assert node is None
        node = view.binding_node(
            anchor(model.SYMBOL, "f"), extents.Extent(1, 1, extents.CALL), grammars.PYTHON
        )
        assert node is None
        node = view.binding_node(
            anchor(model.SYMBOL, "missing"),
            extents.Extent(1, 3, extents.DEFINITION),
            grammars.PYTHON,
        )
        assert node is None
        tree = view.tree(grammars.PYTHON)
        assert tree.root_node is not None


class TestModelAndResolution:
    def test_skip_quoted(self) -> None:
        assert model.skip_quoted('"a\\"b"', 0) == 6
        assert model.skip_quoted('"unterminated', 0) == 1

    def test_operation_trees_mismatch(self, tmp_path: Path) -> None:
        onboarding = tmp_path / "onboarding"
        onboarding.mkdir()
        code = tmp_path / "code"
        code.mkdir()
        trees = resolution.Trees(code_root=code, memory_root=tmp_path / "other")
        with pytest.raises(CitationCacheError, match="different onboarding memory root"):
            resolution.operation_trees(onboarding, trees)

    def test_operation_trees_with_authority(self, tmp_path: Path) -> None:
        onboarding = tmp_path / "onboarding"
        onboarding.mkdir()
        code = tmp_path / "code"
        code.mkdir()
        authority = SimpleNamespace(validate_roots=lambda a, b: None)
        trees = resolution.Trees(
            code_root=code,
            memory_root=tmp_path,
            cache_authority=cast(ManagedCacheAuthority, authority),
        )
        result = resolution.operation_trees(onboarding, trees)
        assert result is trees


class TestGrammarsAndSymbolIndex:
    def test_typescript_anchor_identifier_branches(self) -> None:
        assert grammars.typescript_anchor_identifier("foo()") == "foo"
        assert grammars.typescript_anchor_identifier("Array<string>") == "Array"
        assert grammars.typescript_anchor_identifier("x = 1") is None

    def test_symbol_index_empty_and_oserror(self, tmp_path: Path) -> None:
        code = tmp_path / "code"
        code.mkdir()
        (code / "a.py").write_text("def f():\n    pass\n", encoding="utf-8")
        trees = resolution.Trees(code_root=code, memory_root=tmp_path / "memory")
        (tmp_path / "memory").mkdir()
        sightings = symbol_index.locate_uncached((), trees)
        assert sightings == {}
        with mock.patch.object(Path, "read_text", side_effect=OSError("nope")):
            sightings = symbol_index.locate_uncached((anchor(model.SYMBOL, "f"),), trees)
        assert sightings[anchor(model.SYMBOL, "f")].files == 0


class TestSourceIndexState:
    def test_bounded_integer_errors(self) -> None:
        with pytest.raises(source_index_state.SourceIndexManifestError):
            source_index_state._bounded_integer("1", minimum=0, maximum=10, name="x")
        with pytest.raises(source_index_state.SourceIndexManifestError):
            source_index_state._bounded_integer(11, minimum=0, maximum=10, name="x")

    def test_canonical_root_errors(self) -> None:
        with pytest.raises(source_index_state.SourceIndexManifestError):
            source_index_state._canonical_root(1, "root")
        with pytest.raises(source_index_state.SourceIndexManifestError):
            source_index_state._canonical_root("relative/path", "root")
