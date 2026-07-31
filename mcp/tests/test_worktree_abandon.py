"""Tests for worktree provider teardown + abandon (#3).

End-to-end abandon (force path, real worktree) is exercised by
test_tool_response_conformance; these cover the novel logic in isolation:
docker-resource derivation, dry-run teardown, branch safety, and blocker reporting.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import cast

from agents_remember.worktrees.modules.abandon import _abandon_blockers, _abandon_branch
from agents_remember.worktrees.modules.guidance import lifecycle_guidance
from agents_remember.worktrees.modules.provider_teardown import (
    _reclaim_image,
    _reclaim_ownership,
    _reclaim_result,
    _reclaim_unavailable_reason,
    _worktree_provider_docker_resources,
    teardown_worktree_providers,
)
from agents_remember.worktrees.worktree_contract import WorktreeContract
from test_worktree_support import git, init_repo


def _settings() -> dict:
    return {
        "contextProviders": {
            "providers": {
                "codegraphcontext-code": {
                    "runtime": {
                        "runner": {
                            "containerNameTemplate": "ar-cgc-watcher-projects-demo-ar-<repoId>"
                        }
                    },
                    "backend": {
                        "containerName": "ar-cgc-falkordb-projects-demo-ar-agents-remember",
                        "network": {"name": "ar-cgc-code-projects-demo-ar"},
                    },
                    "roots": [{"repoId": "agents-remember"}],
                },
                "grepai-memory": {
                    "runtime": {
                        "runner": {"containerName": "ar-grepai-watcher-projects-demo-ar"},
                        "network": {"name": "ar-grepai-memory-projects-demo-ar"},
                    },
                    "backend": {"containerName": "ar-grepai-postgres-projects-demo-ar"},
                    "embedder": {"backend": {"containerName": "ar-grepai-ollama-projects-demo-ar"}},
                    "roots": [{"projectId": "agents-remember"}],
                },
            }
        }
    }


class DockerResourceDerivationTests(unittest.TestCase):
    def test_derives_every_container_and_network(self) -> None:
        containers, networks = _worktree_provider_docker_resources(_settings())
        self.assertEqual(
            set(containers),
            {
                "ar-cgc-watcher-projects-demo-ar-agents-remember",  # template expanded per repo
                "ar-cgc-falkordb-projects-demo-ar-agents-remember",
                "ar-grepai-watcher-projects-demo-ar",
                "ar-grepai-postgres-projects-demo-ar",
                "ar-grepai-ollama-projects-demo-ar",
            },
        )
        self.assertEqual(
            set(networks),
            {"ar-cgc-code-projects-demo-ar", "ar-grepai-memory-projects-demo-ar"},
        )

    def test_empty_settings_yield_no_resources(self) -> None:
        containers, networks = _worktree_provider_docker_resources({})
        self.assertEqual(containers, [])
        self.assertEqual(networks, [])


class ReclaimImageTests(unittest.TestCase):
    def test_picks_a_backend_image_for_ownership_reclaim(self) -> None:
        settings = {
            "contextProviders": {
                "providers": {
                    "codegraphcontext-code": {"backend": {"image": "falkordb/falkordb:v4.18.7"}},
                    "grepai-memory": {
                        "backend": {"image": "pgvector/pgvector:pg16"},
                        "embedder": {"backend": {"image": "ollama/ollama:latest"}},
                    },
                }
            }
        }
        # First provider's backend image is enough (it created root-owned data, so it's local).
        self.assertEqual(_reclaim_image(settings), "falkordb/falkordb:v4.18.7")

    def test_none_when_no_images(self) -> None:
        self.assertIsNone(_reclaim_image({"contextProviders": {"providers": {}}}))
        self.assertIsNone(_reclaim_image(None))


class ReclaimOwnershipTests(unittest.TestCase):
    def test_no_image_returns_not_ok_without_calling_docker(self) -> None:
        result = _reclaim_ownership(Path("/tmp/x"), None, None)
        self.assertFalse(result["ok"])
        self.assertIn("no reclaim image", result["error"])

    def test_unavailable_reason_distinguishes_missing_image_vs_platform(self) -> None:
        self.assertIn("image", _reclaim_unavailable_reason(None))
        self.assertIn("platform", _reclaim_unavailable_reason("img"))

    def test_reclaim_result_maps_returncode(self) -> None:
        ok = _reclaim_result({"returncode": 0}, "img", "1000:1000")
        self.assertTrue(ok["ok"])
        fail = _reclaim_result({"returncode": 1, "stderr": "boom"}, "img", "1000:1000")
        self.assertFalse(fail["ok"])
        self.assertEqual(fail["error"], "boom")


class TeardownDryRunTests(unittest.TestCase):
    def test_dry_run_lists_resources_without_touching_docker_or_disk(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            group = Path(tmp) / "worktrees" / "agents-remember" / "demo-ar"
            settings_path = group / "provider-runtime" / "settings" / "provider-settings.json"
            settings_path.parent.mkdir(parents=True, exist_ok=True)
            settings_path.write_text(json.dumps(_settings()), encoding="utf-8")
            contract = cast(
                WorktreeContract,
                SimpleNamespace(worktree_group=group, coordination_root=Path(tmp)),
            )

            result = teardown_worktree_providers(contract, dry_run=True)

            self.assertEqual(result["state"], "would-teardown")
            self.assertTrue(result["settingsFound"])
            self.assertEqual(len(result["containers"]), 5)
            self.assertEqual(len(result["networks"]), 2)
            self.assertTrue(all(c.get("would_remove") for c in result["containers"]))
            self.assertTrue(result["providerRuntime"].get("would_remove"))
            # dry-run must not delete anything
            self.assertTrue(settings_path.exists())

    def test_missing_settings_reports_not_found(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            group = Path(tmp) / "worktrees" / "agents-remember" / "demo-ar"
            group.mkdir(parents=True, exist_ok=True)
            contract = cast(
                WorktreeContract,
                SimpleNamespace(worktree_group=group, coordination_root=Path(tmp)),
            )
            result = teardown_worktree_providers(contract, dry_run=True)
            self.assertFalse(result["settingsFound"])
            self.assertEqual(result["containers"], [])


class AbandonBranchSafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name) / "repo"
        init_repo(self.repo, "main")
        git(self.repo, "checkout", "-b", "ar/work")
        (self.repo / "x.txt").write_text("x\n", encoding="utf-8")
        git(self.repo, "add", "x.txt")
        git(self.repo, "commit", "-m", "unmerged work")
        git(self.repo, "checkout", "main")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_no_force_refuses_unmerged_and_reports_commits(self) -> None:
        result = _abandon_branch(self.repo, "ar/work", "main", dry_run=False, force=False)
        self.assertFalse(result["deleted"])
        self.assertEqual(result["reason"], "unmerged")
        self.assertTrue(result["unmergedCommits"])
        # the branch is preserved (commits not lost)
        self.assertIn("ar/work", git(self.repo, "branch", "--list", "ar/work"))

    def test_force_discards_unmerged_branch(self) -> None:
        result = _abandon_branch(self.repo, "ar/work", "main", dry_run=False, force=True)
        self.assertTrue(result["deleted"])
        self.assertEqual(git(self.repo, "branch", "--list", "ar/work").strip(), "")


class AbandonBlockerTests(unittest.TestCase):
    def test_unmerged_branch_and_dirty_worktree_are_blockers(self) -> None:
        worktrees: dict[str, dict[str, object]] = {
            "code": {"removed": False, "reason": "worktree is dirty"},
            "memory": {"removed": True},
        }
        branches: dict[str, dict[str, object]] = {
            "code": {"deleted": False, "reason": "unmerged", "unmergedCommits": ["abc x"]},
            "memory": {"deleted": False, "reason": "already-absent"},
        }
        blockers = _abandon_blockers(worktrees, branches)
        self.assertEqual(len(blockers), 2)

    def test_clean_removal_has_no_blockers(self) -> None:
        worktrees: dict[str, dict[str, object]] = {"code": {"removed": True}}
        branches: dict[str, dict[str, object]] = {"code": {"deleted": True}}
        self.assertEqual(_abandon_blockers(worktrees, branches), [])


class AbandonLifecyclePhaseTests(unittest.TestCase):
    """05l Gap A: an abandoned worktree must project the ``abandoned`` phase."""

    def test_abandoned_cleanup_projects_abandoned_phase(self) -> None:
        # Was falling through lifecycle_guidance to "worktree-started" (a fully-active phantom);
        # the cleanup=="abandoned" branch surfaces "abandoned" so the teardown can render (05k).
        guidance = lifecycle_guidance(cast(WorktreeContract, SimpleNamespace(cleanup="abandoned")))
        self.assertEqual(guidance["phase"], "abandoned")
        self.assertEqual(guidance["nextOperation"], "done")


if __name__ == "__main__":
    unittest.main()
