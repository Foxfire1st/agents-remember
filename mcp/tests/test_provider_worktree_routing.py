"""Tests for worktree-aware provider query routing and grepai clone-reuse key alignment.

Covers two operational-stability fixes:
  #2 cgc_*/grepai_* can target a worktree's isolated provider stack (hybrid resolution).
  #1 a worktree's grepai logical `workspace` key tracks the WORKSPACE identity (== the clone
     source) so the seeded clone is reused instead of re-embedded.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import cast

from agents_remember.application.provider_tools import (
    _resolve_worktree_target,
    _worktree_provider_targets,
)
from agents_remember.mcp.config import McpRuntimeConfig
from agents_remember.providers.grepai.isolated import _isolated_grepai_base_fields
from agents_remember.providers.identity import provider_instance_id, scoped_name


def _write_worktree_state(
    coordination_root: Path,
    *,
    repo: str,
    group: str,
    task: str,
    settings_exists: bool = True,
) -> Path:
    pr_dir = coordination_root / "worktrees" / repo / group / "provider-runtime"
    settings_path = pr_dir / "settings" / "provider-settings.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    if settings_exists:
        settings_path.write_text(
            json.dumps(
                {
                    "contextProviders": {
                        "providers": {
                            "grepai-memory": {"workspace": "agents-remember-memory-projects"}
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
    state_path = pr_dir / "provider-state.json"
    state_path.write_text(
        json.dumps(
            {
                "schema": "ar-worktree-provider-state/v1",
                "repoName": repo,
                "taskName": task,
                "isolatedProviderSettings": {"path": str(settings_path)},
            }
        ),
        encoding="utf-8",
    )
    return settings_path


class WorktreeTargetResolutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        # The resolver only reads `coordination_root`; a minimal stub is enough.
        self.config = cast(McpRuntimeConfig, SimpleNamespace(coordination_root=self.root))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_no_worktrees_resolves_to_workspace(self) -> None:
        self.assertIsNone(
            _resolve_worktree_target(self.config, repo_id="agents-remember", worktree=None)
        )

    def test_single_worktree_is_the_default(self) -> None:
        settings = _write_worktree_state(
            self.root, repo="agents-remember", group="demo-ar", task="260601_demo"
        )
        target = _resolve_worktree_target(self.config, repo_id="agents-remember", worktree=None)
        assert target is not None
        self.assertEqual(target.settings_path, settings)
        self.assertEqual(target.repo_id, "agents-remember")

    def test_explicit_worktree_matches_by_task_substring(self) -> None:
        _write_worktree_state(
            self.root, repo="agents-remember", group="demo-ar", task="260601_demo"
        )
        target = _resolve_worktree_target(self.config, repo_id="agents-remember", worktree="demo")
        assert target is not None
        self.assertEqual(target.group, "demo-ar")

    def test_multiple_worktrees_without_explicit_is_ambiguous(self) -> None:
        _write_worktree_state(self.root, repo="agents-remember", group="a-ar", task="260601_a")
        _write_worktree_state(self.root, repo="agents-remember", group="b-ar", task="260601_b")
        with self.assertRaises(ValueError):
            _resolve_worktree_target(self.config, repo_id="agents-remember", worktree=None)

    def test_explicit_worktree_no_match_raises(self) -> None:
        _write_worktree_state(
            self.root, repo="agents-remember", group="demo-ar", task="260601_demo"
        )
        with self.assertRaises(ValueError):
            _resolve_worktree_target(self.config, repo_id="agents-remember", worktree="nope")

    def test_other_repo_worktree_does_not_default_for_this_repo(self) -> None:
        _write_worktree_state(self.root, repo="other-repo", group="x-ar", task="260601_x")
        self.assertIsNone(
            _resolve_worktree_target(self.config, repo_id="agents-remember", worktree=None)
        )

    def test_settings_file_missing_is_not_a_candidate(self) -> None:
        _write_worktree_state(
            self.root,
            repo="agents-remember",
            group="demo-ar",
            task="260601_demo",
            settings_exists=False,
        )
        self.assertEqual(_worktree_provider_targets(self.config), [])


class GrepaiWorkspaceAlignmentTests(unittest.TestCase):
    def test_worktree_workspace_tracks_workspace_identity_not_instance(self) -> None:
        coordination_root = Path("/home/example/Projects/ar-coordination")
        args = SimpleNamespace(coordination_root=coordination_root)
        isolated_root = coordination_root / "worktrees" / "agents-remember" / "demo-ar"

        fields = _isolated_grepai_base_fields(
            args, isolated_root=isolated_root, instance_id="projects-demo-ar"
        )

        expected = scoped_name(
            "agents-remember-memory",
            provider_instance_id(
                "workspace",
                coordination_root,
                workspace_name=coordination_root.parent.name,
            ),
        )
        # Matches the clone source's workspace key (what `_grepai_settings` builds for
        # workspace scope), so the seeded clone is reused instead of re-embedded.
        self.assertEqual(fields["workspace"], expected)
        self.assertEqual(fields["workspace"], "agents-remember-memory-projects")
        # And is NOT scoped by the worktree instance (the bug we fixed).
        self.assertNotIn("demo-ar", fields["workspace"])


if __name__ == "__main__":
    unittest.main()
