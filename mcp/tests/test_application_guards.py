"""Tests for the shared application-layer authority guards (F10).

The confinement check used to be copy-pasted across every entry point and had no
direct test for its rejection path. These tests pin both guards in one place.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agents_remember.errors import AuthorityError
from agents_remember.kernel.authority import require_repo, require_within_coordination
from agents_remember.kernel.primitives.runtime_config import (
    McpRuntimeConfig,
    RepositoryScope,
)


def _config(tmp: Path) -> McpRuntimeConfig:
    coord = (tmp / "coord").resolve()
    coord.mkdir(parents=True, exist_ok=True)
    return McpRuntimeConfig(
        config_path=coord / "mcp.settings.json",
        coordination_root=coord,
        workspace_root=(tmp / "ws").resolve(),
        transcript_root=coord / "logs",
        repositories={
            "demo": RepositoryScope(repo_id="demo", path=(tmp / "ws" / "demo").resolve())
        },
    )


class RequireRepoTests(unittest.TestCase):
    def test_returns_scope_for_allowed_repo(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            config = _config(Path(raw))
            self.assertEqual(require_repo(config, "demo").repo_id, "demo")

    def test_rejects_unknown_repo_with_authority_error(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            config = _config(Path(raw))
            with self.assertRaisesRegex(AuthorityError, "not allowed"):
                require_repo(config, "nope")
            # Still a ValueError so existing handlers keep working.
            self.assertTrue(issubclass(AuthorityError, ValueError))


class RequireWithinCoordinationTests(unittest.TestCase):
    def test_accepts_path_inside_root(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            config = _config(Path(raw))
            resolved = require_within_coordination(config, "tasks/demo", "contract_path")
            self.assertEqual(resolved, config.coordination_root / "tasks" / "demo")

    def test_rejects_absolute_escape(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            config = _config(Path(raw))
            outside = (Path(raw) / "outside" / "x").resolve()
            with self.assertRaisesRegex(AuthorityError, "must stay inside"):
                require_within_coordination(config, str(outside), "contract_path")

    def test_rejects_relative_traversal_escape(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            config = _config(Path(raw))
            with self.assertRaisesRegex(AuthorityError, "must stay inside"):
                require_within_coordination(config, "../escape", "contract_path")


if __name__ == "__main__":
    unittest.main()
