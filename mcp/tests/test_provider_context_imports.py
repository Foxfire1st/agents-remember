"""Regression guard for the providers.context facade import cycle (issue #54 series).

Importing a cgc/grepai context submodule FIRST used to initialize the
``providers.context`` facade while ``cgc.context`` was still partially
initialized, caching the facade without the CGC constants (fired on
Python 3.11 in focused test runs). Fixed by relocating ``common.py`` to
``providers/context_common.py``; these tests pin that fix. Each scenario
needs a fresh interpreter, hence subprocess.
"""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

MCP_SRC = Path(__file__).resolve().parents[1] / "src"


class ProviderContextImportOrderTests(unittest.TestCase):
    def test_facade_complete_when_cgc_cleanup_imports_first(self) -> None:
        self.assert_fresh_import_ok(
            "import agents_remember.providers.cgc.context.cleanup; "
            "from agents_remember.providers.context import CGC_NETWORK_NAME, GREPAI_NETWORK_NAME"
        )

    def test_facade_complete_when_settings_imports_first(self) -> None:
        self.assert_fresh_import_ok("import agents_remember.providers.settings")

    def test_facade_complete_when_grepai_layout_imports_first(self) -> None:
        self.assert_fresh_import_ok(
            "import agents_remember.providers.grepai.context.layout; "
            "from agents_remember.providers.context import CGC_NETWORK_NAME"
        )

    def assert_fresh_import_ok(self, statement: str) -> None:
        result = subprocess.run(
            [sys.executable, "-c", statement],
            cwd=MCP_SRC,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)


if __name__ == "__main__":
    unittest.main()
