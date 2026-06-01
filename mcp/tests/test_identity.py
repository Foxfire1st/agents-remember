"""Tests for provider identity helpers (scoped name DNS-label bounding)."""

from __future__ import annotations

import unittest

from agents_remember.providers.identity import (
    MAX_SCOPED_NAME,
    scoped_name,
)

# The worktree case that produced FalkorDB "label too long": this joined name is
# a single 68-char DNS label, over the 63-char RFC-1035 limit.
_OVERFLOWING_RAW = "ar-cgc-falkordb-projects-l01-lifecycle-reshape-ar-agents-remember-md"


class ScopedNameTests(unittest.TestCase):
    def test_short_name_is_unchanged(self) -> None:
        name = scoped_name("ar-cgc-falkordb", "projects", "agents-remember-md")
        self.assertEqual(name, "ar-cgc-falkordb-projects-agents-remember-md")
        self.assertLessEqual(len(name), MAX_SCOPED_NAME)

    def test_long_name_is_capped_to_dns_label_limit(self) -> None:
        # Precondition: the unbounded join would overflow.
        self.assertGreater(len(_OVERFLOWING_RAW), MAX_SCOPED_NAME)
        name = scoped_name(
            "ar-cgc-falkordb",
            "projects-l01-lifecycle-reshape-ar",
            "agents-remember-md",
        )
        self.assertLessEqual(len(name), MAX_SCOPED_NAME)

    def test_cap_is_deterministic(self) -> None:
        args = ("ar-cgc-falkordb", "projects-l01-lifecycle-reshape-ar", "agents-remember-md")
        self.assertEqual(scoped_name(*args), scoped_name(*args))

    def test_cap_distinguishes_distinct_long_inputs(self) -> None:
        a = scoped_name("ar-cgc-falkordb", "projects-l01-lifecycle-reshape-aa", "agents-remember-md")
        b = scoped_name("ar-cgc-falkordb", "projects-l01-lifecycle-reshape-bb", "agents-remember-md")
        self.assertNotEqual(a, b)
        self.assertLessEqual(len(a), MAX_SCOPED_NAME)
        self.assertLessEqual(len(b), MAX_SCOPED_NAME)
