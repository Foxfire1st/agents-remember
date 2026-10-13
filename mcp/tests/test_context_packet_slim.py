"""``context_packet`` answers where the caller works and what needs action (MIK-R109 rule 4).

Through the registered payload builder on hermetic fixtures: the answer
holds the roots, the task, one line per state that needs action (and none
when nothing does), the serving build as its commit and the pair identity
as its two commits; an ordinary answer is under 600 tokens. The whole
packet stays whole behind the answer's log.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
MCP_TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(MCP_SRC))
sys.path.insert(0, str(MCP_TESTS))

from agents_remember.application.context_packet import (
    ContextPacketRequest,
    build_context_packet,
    slim_context_packet,
)
from agents_remember.mcp.tools.core import context_packet_payload
from agents_remember.models.tokens import count_response_tokens
from agents_remember.models.tools.tool_registry import TOOL_RESPONSE_MODELS
from test_context_packet import initialize_context_fixture, write_and_load_config


class ContextPacketSlimTests(unittest.TestCase):
    maxDiff = None

    def test_slim_answer_holds_roots_pair_and_build(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            initialize_context_fixture(root)
            config = write_and_load_config(root)
            out = context_packet_payload(
                config,
                ContextPacketRequest(repo_id="agents-remember"),
                serving_build_commit="0" * 40,
            )
            TOOL_RESPONSE_MODELS["context_packet"].model_validate(out)
            self.assertEqual(out["contextPacketVersion"], 3, out)
            self.assertTrue(out["ok"], out)
            self.assertEqual(set(out["roots"]) >= {"code", "memory"}, True, out["roots"])
            self.assertNotIn("task", out, out)
            self.assertNotIn("actions", out, out)
            self.assertEqual(out["servingBuild"], "0" * 40, out)
            # The fixture memory is not a checkout: no pair without two commits.
            self.assertNotIn("pair", out, out)
            self.assertNotIn("repo", out, out)
            self.assertNotIn("paths", out, out)
            self.assertNotIn("providers", out, out)
            self.assertNotIn("worktree", out, out)
            self.assertNotIn("memory", out, out)
            self.assertLess(count_response_tokens(out), 600, out)

    def test_action_lines_name_behind_and_drifted_only_when_present(self) -> None:
        full: dict[str, Any] = {
            "ok": True,
            "repo": {"root": "/code", "head": "a" * 40},
            "paths": {"memoryRoot": "/memory"},
            "worktree": {
                "state": "active",
                "sourceLineage": {
                    "edges": [
                        {
                            "descendantBranch": "ar/leaf",
                            "sourceBranch": "ar/master",
                            "behind": 2,
                        },
                        {
                            "descendantBranch": "ar/master",
                            "sourceBranch": "ar/super",
                            "behind": 0,
                        },
                    ]
                },
            },
            "freshness": {"code": {"behind": 1}, "memory": {"behind": 0}},
            "drift": {"actionableCount": 3, "reportPath": "/reports/drift.json"},
        }
        slim = slim_context_packet(full, serving_build_commit="b" * 40)
        self.assertEqual(
            slim["actions"],
            [
                "the ar/leaf worktree is 2 behind ar/master",
                "the code checkout is 1 behind its upstream",
                "3 onboarding cards drifted; report at /reports/drift.json",
            ],
            slim,
        )
        current: dict[str, Any] = {
            "ok": True,
            "repo": {"root": "/code", "head": "a" * 40},
            "paths": {"memoryRoot": "/memory"},
            "worktree": {"state": "inactive"},
            "freshness": {"status": "not-checked"},
            "drift": {"status": "notChecked"},
        }
        slim_now = slim_context_packet(current)
        self.assertNotIn("actions", slim_now, slim_now)

    def test_the_whole_packet_stays_whole_for_the_command_line(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            initialize_context_fixture(root)
            config = write_and_load_config(root)
            full = build_context_packet(config, ContextPacketRequest(repo_id="agents-remember"))
            self.assertEqual(full["contextPacketVersion"], 2, full)
            for key in ("repo", "paths", "memory", "worktree", "providers", "drift"):
                self.assertIn(key, full, full)

    def test_a_partial_index_is_an_action_line(self) -> None:
        full: dict[str, Any] = {
            "ok": True,
            "repo": {"root": "/code", "head": "a" * 40},
            "paths": {"memoryRoot": "/memory"},
            "worktree": {"state": "inactive"},
            "index": {
                "state": "partial",
                "problems": [{"path": "knowledge/broken.json", "detail": "skipped"}],
            },
        }
        slim = slim_context_packet(full)
        self.assertEqual(
            slim["actions"],
            [
                "the memory knowledge index is partial (1 files skipped); "
                "run knowledge_integrity_check for the skipped files, then reread"
            ],
            slim,
        )
        complete = dict(full, index={"state": "complete", "problems": []})
        self.assertNotIn("actions", slim_context_packet(complete))

    def test_a_registered_packet_names_a_partial_index(self) -> None:
        # MIK-R109 round-1 F19: the stale-index signal is observed through
        # the registered call, not only the slim unit. A memory tree whose
        # knowledge holds one skipped file answers the partial-index action
        # line on a taskless registered packet.
        import subprocess  # noqa: PLC0415

        from tool_entry_point_world import EntryPointWorld  # noqa: PLC0415

        world = EntryPointWorld()
        self.addCleanup(world.close)
        world.build()
        knowledge = world.memory / "knowledge"
        knowledge.mkdir(parents=True, exist_ok=True)
        (knowledge / "layout.json").write_text(
            json.dumps({"schema": "ar-memory-layout/v2", "conversion": "1"}),
            encoding="utf-8",
        )
        (knowledge / "broken.json").write_text("{not json", encoding="utf-8")
        subprocess.run(
            ["git", "-C", str(world.memory), "add", "-A"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(world.memory), "commit", "-q", "-m", "partial index"],
            check=True,
            capture_output=True,
        )
        outcome, out = world.call("context_packet", {"repo_id": "repo-a"})
        self.assertEqual(outcome, "RETURNED", out)
        self.assertIsInstance(out, dict)
        assert out is not None
        self.assertEqual(
            out.get("actions"),
            [
                "the memory knowledge index is partial (1 files skipped); "
                "run knowledge_integrity_check for the skipped files, then reread"
            ],
            out,
        )

    def test_registered_leaf_and_taskless_packets(self) -> None:
        from agents_remember.worktrees.worktree_contract import (  # noqa: PLC0415
            load_contract,
        )
        from tool_entry_point_world import EntryPointWorld  # noqa: PLC0415

        world = EntryPointWorld()
        self.addCleanup(world.close)
        world.build()
        leaf_context = {
            "task_document_ref": {"repository": "repo-a", "path": "m/1_leaf.json"},
            "contract_path": world.contract.as_posix(),
        }
        outcome, out = world.call("context_packet", {"repo_id": "repo-a"})
        self.assertEqual(outcome, "RETURNED", out)
        self.assertIsInstance(out, dict)
        assert out is not None
        self.assertEqual(out["contextPacketVersion"], 3, out)
        self.assertTrue(out["ok"], out)
        roots = out["roots"]
        self.assertTrue(Path(str(roots["code"])).is_dir(), out)
        # Roots other than code are code-relative when shorter, else
        # absolute: the caller joins them with the absolute code root,
        # which is the identity for an already-absolute value.
        self.assertTrue((Path(str(roots["code"])) / str(roots["memory"])).is_dir(), out)
        self.assertNotIn("actions", out, out)
        # The log preserves the delivered slim answer behind it: the
        # detail file holds what the framework received, and the answer's
        # row fields match it exactly.
        log = Path(str(out["log"]))
        self.assertTrue(log.is_file(), out)
        filed = json.loads(log.read_text(encoding="utf-8"))
        for key in (
            "contextPacketVersion",
            "ok",
            "roots",
            "task",
            "actions",
            "servingBuild",
            "pair",
        ):
            if key in filed or key in out:
                self.assertEqual(out.get(key), filed.get(key), f"log differs at {key}")
        # The whole packet is rebuilt on demand through the same builder
        # the dashboard and command line call: it carries the slim keys
        # and the omitted policy facts behind them.
        from agents_remember.kernel.primitives.runtime_config import (  # noqa: PLC0415
            McpRuntimeConfig,
            RepositoryScope,
        )

        packet_config = McpRuntimeConfig(
            config_path=world.coord / "settings.json",
            coordination_root=world.coord,
            workspace_root=world.coord,
            transcript_root=world.coord / "logs" / "mcp",
            repositories={
                "repo-a": RepositoryScope(
                    repo_id="repo-a", path=world.code, memory_root=world.memory
                ),
            },
        )
        whole = build_context_packet(packet_config, ContextPacketRequest(repo_id="repo-a"))
        for key in ("repo", "paths", "memory", "worktree"):
            self.assertIn(key, whole, sorted(whole))
        self.assertEqual(whole["repo"]["root"], roots["code"], whole)
        outcome, leaf = world.call(
            "context_packet", {"repo_id": "repo-a", "task_context": leaf_context}
        )
        self.assertEqual(outcome, "RETURNED", leaf)
        self.assertIsInstance(leaf, dict)
        assert leaf is not None
        self.assertEqual(leaf["contextPacketVersion"], 3, leaf)
        contract = load_contract(world.contract)
        task = leaf["task"]
        self.assertEqual(task["master"], contract.parent_task_name, leaf)
        self.assertTrue(task["id"], leaf)
        leaf_code = Path(str(leaf["roots"]["code"]))
        self.assertTrue(leaf_code.is_dir(), leaf)
        self.assertTrue((leaf_code / str(leaf["roots"]["taskDocument"])).is_file(), leaf)
        self.assertRegex(leaf.get("servingBuild", ""), r"\A[0-9a-f]{7,40}\Z", leaf)
        pair = leaf.get("pair", {})
        for side in ("code", "memory"):
            self.assertRegex(str(pair.get(side, "")), r"\A[0-9a-f]{40}\Z", leaf)


if __name__ == "__main__":
    unittest.main()
