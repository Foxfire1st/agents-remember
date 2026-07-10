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
    effort_session_commands,
    effort_vocabulary,
    find_harness,
    invalid_effort_detail,
    is_detected,
    knob_argv,
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


class KnobMappingTests(unittest.TestCase):
    """The per-harness knob→flag mapping (260703-L16): claude maps model/effort onto its launch
    flags with a two-vehicle effort vocabulary (flag values vs session values); Codex maps its
    real API enum onto ``--config``; Pi remains env-only."""

    def _claude(self):
        claude = find_harness("claude")
        assert claude is not None
        return claude

    def test_claude_maps_model_and_effort_onto_launch_flags(self) -> None:
        self.assertEqual(
            knob_argv(self._claude(), model="opus", effort="max"),
            ["--model", "opus", "--effort", "max"],
        )
        self.assertEqual(knob_argv(self._claude(), effort="high"), ["--effort", "high"])
        self.assertEqual(knob_argv(self._claude()), [])

    def test_claude_effort_vocabulary_is_the_two_vehicle_union(self) -> None:
        # Empirical (2026-07-07): the flag set low..max, plus the session-only ultracode the
        # interactive /effort command accepts but the --effort launch flag warn-degrades on.
        self.assertEqual(
            effort_vocabulary(self._claude()),
            ("low", "medium", "high", "xhigh", "max", "ultracode"),
        )

    def test_session_level_effort_rides_a_session_command_not_the_flag(self) -> None:
        claude = self._claude()
        self.assertEqual(knob_argv(claude, effort="ultracode"), [])
        self.assertEqual(
            effort_session_commands(claude, "ultracode"), ["/effort ultracode"]
        )
        # A flag-vocabulary value never leaks into the session vehicle.
        self.assertEqual(effort_session_commands(claude, "max"), [])

    def test_invalid_effort_detail_names_harness_and_both_value_sets(self) -> None:
        detail = invalid_effort_detail(self._claude(), "turbo")
        assert detail is not None
        self.assertIn("'turbo'", detail)
        self.assertIn("'claude'", detail)
        self.assertIn("low, medium, high, xhigh, max", detail)
        self.assertIn("ultracode", detail)
        # In-vocabulary values (either vehicle) pass.
        self.assertIsNone(invalid_effort_detail(self._claude(), "max"))
        self.assertIsNone(invalid_effort_detail(self._claude(), "ultracode"))

    def test_codex_knobs_use_explicit_argv_and_pi_remains_env_only(self) -> None:
        codex = find_harness("codex")
        pi = find_harness("pi")
        assert codex is not None and pi is not None
        self.assertEqual(
            knob_argv(codex, model="gpt-5.6-sol", effort="xhigh"),
            ["--model", "gpt-5.6-sol", "--config", "model_reasoning_effort=xhigh"],
        )
        self.assertIn("medium", effort_vocabulary(codex))
        self.assertEqual(
            effort_vocabulary(codex),
            ("none", "minimal", "low", "medium", "high", "xhigh"),
        )
        for invalid in ("max", "ultracode", "auto", "anything"):
            detail = invalid_effort_detail(codex, invalid)
            self.assertIsNotNone(detail)
            self.assertIn("none, minimal, low, medium, high, xhigh", detail or "")
        self.assertEqual(knob_argv(pi, model="gpt-5", effort="anything"), [])
        self.assertEqual(effort_session_commands(pi, "anything"), [])
        self.assertEqual(effort_vocabulary(pi), ())
        self.assertIsNone(invalid_effort_detail(pi, "anything"))


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
