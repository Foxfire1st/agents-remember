"""Tests for the harness launch registry (slice 6e-2b, ``serving.harnesses``).

Detection is exercised with an injected ``which`` so the suite is deterministic regardless of what
is installed on the test machine -- the registry's *shape* + the detected/undetected mapping are the
contract, not whether ``claude`` happens to be on this box's ``PATH``.
"""

from __future__ import annotations

import sys
import unittest
from collections.abc import Callable
from pathlib import Path

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.serving.harnesses import (
    HARNESSES,
    DetectedHarness,
    detect_harnesses,
    find_harness,
    is_detected,
)


def _which(*installed: str) -> Callable[[str], str | None]:
    """A ``shutil.which`` fake: resolves only the named commands to a path, else ``None``."""
    present = set(installed)

    def which(command: str) -> str | None:
        return f"/usr/bin/{command}" if command in present else None

    return which


class HarnessRegistryTests(unittest.TestCase):
    def test_supported_set_is_the_curated_three(self) -> None:
        self.assertEqual([h.id for h in HARNESSES], ["claude", "codex", "pi"])

    def test_each_harness_has_name_and_argv_is_its_command(self) -> None:
        for harness in HARNESSES:
            self.assertTrue(harness.name)
            self.assertTrue(harness.command)
            self.assertEqual(harness.argv, (harness.command,))

    def test_find_harness_known_and_unknown(self) -> None:
        claude = find_harness("claude")
        assert claude is not None
        self.assertEqual(claude.name, "Claude Code")
        self.assertIsNone(find_harness("gemini"))


class DetectionTests(unittest.TestCase):
    def test_is_detected_reflects_which(self) -> None:
        claude = find_harness("claude")
        assert claude is not None
        self.assertTrue(is_detected(claude, which=_which("claude")))
        self.assertFalse(is_detected(claude, which=_which()))

    def test_detect_harnesses_marks_each_and_preserves_order(self) -> None:
        detected = detect_harnesses(which=_which("claude", "codex"))
        self.assertEqual(
            detected,
            [
                DetectedHarness(id="claude", name="Claude Code", detected=True),
                DetectedHarness(id="codex", name="Codex", detected=True),
                DetectedHarness(id="pi", name="Pi.dev", detected=False),
            ],
        )


if __name__ == "__main__":
    unittest.main()
