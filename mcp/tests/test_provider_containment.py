"""Provider containment (260707-HFX-L1): unconfigured on disk ⇒ no launch, ever.

The 2026-07-07 WSL OOM proved two bypasses of the settings gate: the boot
snapshot (running servers never re-read the authority file) and benchmark
self-arming (the case manifest synthesized+persisted its own providers map).
These tests pin the containment layer that closes both, plus the aggregate
setup lock and the metrics feed.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from agents_remember.application import worktree_tools
from agents_remember.kernel.primitives.runtime_config import (
    McpRuntimeConfig,
    ProviderScope,
    RepositoryScope,
)
from agents_remember.observer.lifecycle_state import LifecycleState


def _bound_lifecycle() -> LifecycleState:
    """The session lifecycle a real start leaves behind: bound, and no longer fleeting."""

    return LifecycleState(
        id="01J000000000000000000000AB",
        state="running",
        phase="build",
        fleeting=False,
        started_at="2026-09-18T00:00:00+00:00",
        enclosure="/tmp/coordination/enclosures/other/contract.md",
        repo_id="repo",
        scope="repo",
    )


def _armed_boot_config(tmp: Path, *, disk_providers: dict) -> McpRuntimeConfig:
    """A config whose BOOT SNAPSHOT is armed while the disk says ``disk_providers``."""
    authority = tmp / "authority.json"
    authority.write_text(json.dumps({"version": 1, "providers": disk_providers}), encoding="utf-8")
    coordination_root = tmp / "coord"
    workspace_root = tmp / "ws"
    return McpRuntimeConfig(
        config_path=authority,
        coordination_root=coordination_root,
        workspace_root=workspace_root,
        transcript_root=coordination_root / "logs" / "mcp",
        repositories={
            "repo": RepositoryScope(repo_id="repo", path=workspace_root / "repo"),
        },
        providers={
            "grepai-memory": ProviderScope(
                provider_id="grepai-memory",
                runtime_root=coordination_root / "providers" / "runners" / "grepai" / "i1",
                log_root=coordination_root / "logs" / "providers" / "grepai" / "i1",
                instance_id="i1",
            ),
        },
    )


class WorktreeStartVetoTests(unittest.TestCase):
    def test_stale_armed_snapshot_is_vetoed_by_disk(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = _armed_boot_config(Path(tmp), disk_providers={})
            captured: dict[str, object] = {}

            def fake_start(
                args: worktree_tools.git_worktree_manager.WorktreeArgs,
            ) -> worktree_tools.git_worktree_manager.WorktreeCommandResult:
                captured["provider_setup_config"] = args.provider_setup_config
                return worktree_tools.git_worktree_manager.WorktreeCommandResult(
                    returncode=0, payload={"state": "blocked"}
                )

            with mock.patch.object(worktree_tools.git_worktree_manager, "start_result", fake_start):
                result = worktree_tools.worktree_start_tool(
                    config,
                    worktree_tools.TaskIdentity(repo_id="repo", task_name="t", worktree_name="w"),
                )
        # The launch side-channel never materializes: no settings file, no setup config.
        self.assertIsNone(captured["provider_setup_config"])
        veto = result["providersAuthority"]
        self.assertEqual(veto["bootSnapshotProviders"], ["grepai-memory"])


class WorktreeStartGateTruthfulnessTests(unittest.TestCase):
    """What the start gate actually tests, and what it does not check at all (D-17).

    The refusal used to read *"worktree_start refuses to repoint the active persistent lifecycle"*,
    which describes a protection that does not exist: the test reads only the session's **current**
    lifecycle and only its ``fleeting`` flag, so no other leaf, enclosure or session can block a
    start. And the tool never checked a leaf's declared dependencies at all -- a leaf previewed as
    ``would-start`` while a requirement its packet requires was unlanded. Both facts now travel with
    the response instead of living in a session's memory.
    """

    def test_the_refusal_states_the_session_binding_rather_than_a_protected_lifecycle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = _armed_boot_config(Path(tmp), disk_providers={})
            amb = SimpleNamespace(current=_bound_lifecycle())
            with mock.patch.object(worktree_tools, "ambient", return_value=amb):
                result = worktree_tools.worktree_start_tool(
                    config,
                    worktree_tools.TaskIdentity(repo_id="repo", task_name="t", worktree_name="w"),
                )

        self.assertFalse(result["ok"])
        self.assertEqual(result["state"], "lifecycle-switch-required")
        self.assertIn("already bound to another one", str(result["summary"]))
        self.assertIn(
            "No other leaf, enclosure or session can block this start", str(result["summary"])
        )
        # The mis-stated claim is gone, not paraphrased into the same meaning.
        self.assertNotIn("persistent lifecycle", str(result["summary"]))

    def test_a_start_result_reports_that_the_declared_dependencies_were_not_checked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = _armed_boot_config(Path(tmp), disk_providers={})

            def fake_start(
                args: worktree_tools.git_worktree_manager.WorktreeArgs,
            ) -> worktree_tools.git_worktree_manager.WorktreeCommandResult:
                return worktree_tools.git_worktree_manager.WorktreeCommandResult(
                    returncode=0, payload={"state": "would-start"}
                )

            with mock.patch.object(worktree_tools.git_worktree_manager, "start_result", fake_start):
                result = worktree_tools.worktree_start_tool(
                    config,
                    worktree_tools.TaskIdentity(repo_id="repo", task_name="t", worktree_name="w"),
                )

        # The tool states the check it did not perform instead of letting a caller read
        # ``would-start`` as "this leaf's declared dependencies are satisfied".
        self.assertEqual(result["state"], "would-start")
        self.assertEqual(result["eligibility"]["requiresCheck"], "not-performed")
        self.assertIn("Requires lines", str(result["eligibility"]["detail"]))


if __name__ == "__main__":
    unittest.main()
