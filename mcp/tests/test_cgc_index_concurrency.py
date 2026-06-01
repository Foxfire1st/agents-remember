"""Fan-in cap for cgc reindex-all (multi-repo workspaces must not melt the box).

Each cgc indexer self-throttles to ~10 in-flight FalkorDB queries; reindexing every repo at
once overran the shared FalkorDB queue and pegged the CPU. cgc_index_concurrency bounds how
many repos reindex simultaneously (default 2, overridable via AR_CGC_INDEX_CONCURRENCY), so a
large workspace degrades to "slower" instead of breaking.
"""

from __future__ import annotations

import os
import unittest
from unittest import mock

from agents_remember.providers.cgc.lifecycle.process_control import (
    DEFAULT_CGC_INDEX_CONCURRENCY,
    cgc_index_concurrency,
)


class IndexConcurrencyTests(unittest.TestCase):
    def test_default_caps_below_repo_count(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AR_CGC_INDEX_CONCURRENCY", None)
            self.assertEqual(cgc_index_concurrency(8), DEFAULT_CGC_INDEX_CONCURRENCY)

    def test_never_exceeds_repo_count(self) -> None:
        with mock.patch.dict("os.environ", {"AR_CGC_INDEX_CONCURRENCY": "10"}):
            self.assertEqual(cgc_index_concurrency(3), 3)

    def test_at_least_one(self) -> None:
        with mock.patch.dict("os.environ", {"AR_CGC_INDEX_CONCURRENCY": "0"}):
            self.assertEqual(cgc_index_concurrency(5), 1)
        with mock.patch.dict("os.environ", {"AR_CGC_INDEX_CONCURRENCY": "1"}):
            self.assertEqual(cgc_index_concurrency(5), 1)

    def test_env_override_raises_cap(self) -> None:
        with mock.patch.dict("os.environ", {"AR_CGC_INDEX_CONCURRENCY": "6"}):
            self.assertEqual(cgc_index_concurrency(20), 6)

    def test_bad_override_falls_back_to_default(self) -> None:
        with mock.patch.dict("os.environ", {"AR_CGC_INDEX_CONCURRENCY": "lots"}):
            self.assertEqual(cgc_index_concurrency(8), DEFAULT_CGC_INDEX_CONCURRENCY)

    def test_zero_layouts_returns_one(self) -> None:
        with mock.patch.dict("os.environ", {"AR_CGC_INDEX_CONCURRENCY": "4"}):
            self.assertEqual(cgc_index_concurrency(0), 1)


if __name__ == "__main__":
    unittest.main()
