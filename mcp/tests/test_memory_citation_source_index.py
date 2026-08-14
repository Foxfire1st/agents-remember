"""Snapshot, parity, reuse, and publication tests for the citation source index."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest import mock

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.memory_quality.style.citations import (
    model,
    source_index,
    source_index_database,
    symbol_index,
)
from agents_remember.memory_quality.style.citations.resolution import Trees


@contextmanager
def _publication_failure(boundary: str, paths: source_index.CachePaths) -> Iterator[None]:
    original_replace = source_index.atomic_replace
    original_write = source_index.atomic_write_text
    if boundary == "before_database_replace":
        with mock.patch.object(
            source_index,
            "atomic_replace",
            side_effect=RuntimeError(boundary),
        ):
            yield
        return

    def replace_database(source: Path, destination: Path) -> None:
        original_replace(source, destination)
        if boundary == "after_database_replace" and destination == paths.database:
            raise RuntimeError(boundary)

    def write_metadata(path: Path, text: str) -> None:
        original_write(path, text)
        if boundary == "after_manifest_replace" and path == paths.manifest:
            raise RuntimeError(boundary)
        if boundary == "after_readiness_publish" and path == paths.readiness:
            raise RuntimeError(boundary)
        if boundary == "before_readiness_publish" and path == paths.readiness:
            path.unlink(missing_ok=True)
            raise RuntimeError(boundary)

    with (
        mock.patch.object(source_index, "atomic_replace", new=replace_database),
        mock.patch.object(source_index, "atomic_write_text", new=write_metadata),
    ):
        yield


class IndexCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.code = self.root / "code"
        self.memory = self.root / "memory"
        self.cache = self.root / "cache"
        self.code.mkdir()
        self.memory.mkdir()
        self.trees = Trees(code_root=self.code, memory_root=self.memory)
        self.environment = mock.patch.dict(os.environ, {"XDG_CACHE_HOME": str(self.cache)})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def write(self, relative: str, body: str) -> Path:
        path = self.code / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
        return path

    def acquire(self) -> tuple[dict[str, Any], str]:
        with source_index.open_repository_index(self.trees) as index:
            return index.telemetry(), index.snapshot_id

    def build(self) -> dict[str, Any]:
        return source_index.build_repository_index(self.trees)

    def frozen_refusal_without_discovery(self, snapshot: str) -> str:
        with (
            mock.patch.object(
                source_index,
                "_tree_state",
                side_effect=AssertionError("frozen refusal inspected source"),
            ),
            mock.patch.object(
                source_index,
                "_reclaim_legacy_cache_roots",
                side_effect=AssertionError("frozen refusal reclaimed cache"),
            ),
            mock.patch.object(
                source_index,
                "_build_and_publish",
                side_effect=AssertionError("frozen refusal rebuilt/fell back"),
            ),
            mock.patch.object(
                source_index_database.Database,
                "validate_application_integrity",
                side_effect=AssertionError("frozen refusal traversed integrity"),
            ),
            self.assertRaises(source_index.SourceIndexError) as raised,
        ):
            source_index.open_repository_index(self.trees, expected_snapshot=snapshot)
        return str(raised.exception)


class SemanticParityTests(IndexCase):
    def test_cached_and_direct_resolution_match_every_anchor_shape(self) -> None:
        self.write(
            "pkg/alpha.py",
            "def alpha():\n    return 'shared words', 'x', 'yz'\n\nalpha()\n",
        )
        self.write(
            "web/component.ts",
            "export function beta() {\n  sendMessage(\n    'shared words',\n+  );\n}\nconst gamma = beta;\n",
        )
        self.write("web/view.tsx", "export function Panel() { return <div>Panel</div>; }\n")
        self.write("data/config.json", '{"fallbackName": "gamma", "copy": "shared words"}\n')
        self.write(
            "README.md",
            "# Root\nfirst shared words\n## Child\nbody\n# Root\nsecond\n",
        )
        self.write("data/unicode.txt", ("λ0é0" * 128) + "\n")
        anchors = (
            model.Anchor(model.SYMBOL, "alpha"),
            model.Anchor(model.SYMBOL, "beta"),
            model.Anchor(model.SYMBOL, "Panel"),
            model.Anchor(model.SYMBOL, "fallbackName"),
            model.Anchor(model.SYMBOL, "missing"),
            model.Anchor(model.HEADING, "# Root"),
            model.Anchor(model.QUOTE, "shared words"),
            model.Anchor(model.QUOTE, "x"),
            model.Anchor(model.QUOTE, "yz"),
            model.Anchor(model.QUOTE, "0"),
            model.Anchor(model.QUOTE, "Shared Words"),
        )

        expected = symbol_index.locate_uncached(anchors, self.trees)
        with source_index.open_repository_index(self.trees) as index:
            actual = symbol_index.locate(anchors, self.trees, index=index)

        self.assertEqual(actual, expected)
        self.assertEqual(list(actual), list(dict.fromkeys(anchors)))

    def test_quote_lookup_reads_candidates_not_the_full_corpus(self) -> None:
        self.write("target.txt", "a uniquely searchable quotation\n")
        for index in range(20):
            self.write(f"fill/{index}.txt", f"ordinary filler number {index}\n")
        anchor = model.Anchor(model.QUOTE, "uniquely searchable quotation")

        with source_index.open_repository_index(self.trees) as index:
            seen = symbol_index.locate((anchor,), self.trees, index=index)[anchor]
            telemetry: dict[str, Any] = index.telemetry()

        self.assertEqual(seen.files, 1)
        self.assertEqual(telemetry["quoteFullCorpusScans"], 0)
        self.assertEqual(telemetry["quoteIndexLookups"], 1)
        self.assertLess(telemetry["quoteCandidateStreamsRead"], telemetry["quoteCorpusStreams"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
