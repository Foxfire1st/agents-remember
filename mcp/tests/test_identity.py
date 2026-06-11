"""Tests for provider identity helpers (scoped Docker/DNS-safe names)."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from agents_remember.providers.identity import (
    MAX_SCOPED_NAME,
    provider_instance_id,
    scoped_name,
)

# The worktree case that produced FalkorDB "label too long": this joined name is
# a single 68-char DNS label, over the 63-char RFC-1035 limit.
_OVERFLOWING_RAW = "ar-cgc-falkordb-projects-l01-lifecycle-reshape-ar-agents-remember"


class ScopedNameTests(unittest.TestCase):
    def test_short_name_is_unchanged(self) -> None:
        name = scoped_name("ar-cgc-falkordb", "projects", "agents-remember")
        self.assertEqual(name, "ar-cgc-falkordb-projects-agents-remember")
        self.assertLessEqual(len(name), MAX_SCOPED_NAME)

    def test_long_name_is_capped_to_dns_label_limit(self) -> None:
        # Precondition: the unbounded join would overflow.
        self.assertGreater(len(_OVERFLOWING_RAW), MAX_SCOPED_NAME)
        name = scoped_name(
            "ar-cgc-falkordb",
            "projects-l01-lifecycle-reshape-ar",
            "agents-remember",
        )
        self.assertLessEqual(len(name), MAX_SCOPED_NAME)

    def test_cap_is_deterministic(self) -> None:
        args = ("ar-cgc-falkordb", "projects-l01-lifecycle-reshape-ar", "agents-remember")
        self.assertEqual(scoped_name(*args), scoped_name(*args))

    def test_cap_distinguishes_distinct_long_inputs(self) -> None:
        a = scoped_name("ar-cgc-falkordb", "projects-l01-lifecycle-reshape-aa", "agents-remember")
        b = scoped_name("ar-cgc-falkordb", "projects-l01-lifecycle-reshape-bb", "agents-remember")
        self.assertNotEqual(a, b)
        self.assertLessEqual(len(a), MAX_SCOPED_NAME)
        self.assertLessEqual(len(b), MAX_SCOPED_NAME)

    def test_worktree_instance_id_replaces_dots_for_compose_project_names(self) -> None:
        runtime_root = Path(
            "/home/example/Projects/ar-coordination/worktrees/agents-remember/"
            "release-mcp-2.3.3-ar/provider-runtime"
        )

        instance_id = provider_instance_id("worktree", runtime_root, workspace_name="Projects")
        compose_project = scoped_name("agents-remember-grepai", instance_id)

        self.assertEqual(instance_id, "projects-release-mcp-2-3-3-ar")
        self.assertEqual(compose_project, "agents-remember-grepai-projects-release-mcp-2-3-3-ar")
        self.assertRegex(compose_project, re.compile(r"^[a-z0-9][a-z0-9_-]*$"))
