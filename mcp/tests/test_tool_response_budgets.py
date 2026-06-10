"""Tool responses stay under budget; bulk detail lands in pruned report files.

S4 of the 2026-06-10 task: the flooders (`runtime_install` >50k chars,
`provider_diagnostics` 5.7k tokens, `provider_watchers` 1.5k) move their
passthrough bulk to `temp/tool-reports/<tool>/` and keep a compact outcome
plus `reportPath` inline.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.mcp.tool_reports import (
    prune_tool_reports,
    redact_secrets,
    write_tool_report,
)
from agents_remember.mcp.tools.core import compact_runtime_install_payload
from agents_remember.mcp.tools.memory import compact_carryover_payload
from agents_remember.mcp.tools.providers import (
    compact_diagnostics_payload,
    compact_watchers_payload,
)

INLINE_BUDGET_CHARS = 4_000  # ~1k tokens; generous for outcomes, tiny vs the raw payloads


def fat_candidate(index: int, decision: str) -> dict:
    path = f"mcp/src/agents_remember/providers/some/long/module_{index:03d}.py"
    return {
        "source_path": path,
        "branch_onboarding": f"C:/ew/ar-coordination/worktrees/repo/task-ar/memory-task/onboarding/{path}.md",
        "official_onboarding": f"C:/ew/ar-coordination/memory-repos/ar-repo/onboarding/{path}.md",
        "evidence": "exact-landed-commit",
        "decision": decision,
        "reason": "all 1 source branch commit(s) touching this path are ancestors of official code ref",
        "official_exists": True,
    }


def fat_command(name: str) -> dict:
    return {
        "command": ["docker", "compose", "-f", "-", name, "-e", "PGPASSWORD=secret"],
        "stdout": "line\n" * 200,
        "stderr": "noise\n" * 200,
        "returncode": 0,
        "compose": {"project": "p", "baseFile": "x" * 200, "overrideSha256": "f" * 64},
    }


class ToolReportFileTests(unittest.TestCase):
    def test_write_creates_report_and_returns_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = write_tool_report(root, "demo", {"ok": True, "bulk": ["x"] * 50})
            self.assertTrue(path.exists())
            self.assertIn("tool-reports", path.as_posix())
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["ok"], True)

    def test_secrets_are_redacted_in_reports(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = {"steps": [fat_command("up")]}
            path = write_tool_report(root, "demo", payload)
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("secret", text)
            self.assertIn("PGPASSWORD=***", text)

    def test_prune_keeps_last_five(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            for index in range(8):
                report = folder / f"report-{index}.json"
                report.write_text("{}", encoding="utf-8")
                age = (8 - index) * 60.0
                stamp = time.time() - age
                os.utime(report, (stamp, stamp))
            prune_tool_reports(folder)
            remaining = sorted(p.name for p in folder.glob("*.json"))
            self.assertEqual(len(remaining), 5)
            self.assertEqual(remaining, [f"report-{i}.json" for i in range(3, 8)])

    def test_prune_drops_reports_older_than_max_age(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            fresh = folder / "fresh.json"
            stale = folder / "stale.json"
            fresh.write_text("{}", encoding="utf-8")
            stale.write_text("{}", encoding="utf-8")
            old = time.time() - 8 * 86400
            os.utime(stale, (old, old))
            prune_tool_reports(folder)
            self.assertTrue(fresh.exists())
            self.assertFalse(stale.exists())

    def test_redact_secrets_walks_nested_structures(self) -> None:
        nested = {"a": [{"b": "x PGPASSWORD=topsecret y"}]}
        redacted = redact_secrets(nested)
        self.assertEqual(redacted["a"][0]["b"], "x PGPASSWORD=*** y")


class CompactPayloadBudgetTests(unittest.TestCase):
    def test_runtime_install_compaction_stays_under_budget(self) -> None:
        full = {
            "ok": True,
            "summary": {"copiedFiles": 5, "removedPaths": 489},
            "messages": [f"message {i}" for i in range(40)],
            "providerWatcherRebind": {
                "attempted": True,
                "ok": True,
                "runs": [
                    {
                        "phase": phase,
                        "action": "start",
                        "ok": True,
                        "results": [fat_command("up") for _ in range(6)],
                    }
                    for phase in ("pre-stop", "post-start", "post-status")
                ],
            },
        }
        compact = compact_runtime_install_payload(full, "C:/tmp/report.json")
        text = json.dumps(compact)
        self.assertLess(len(text), INLINE_BUDGET_CHARS, msg=f"{len(text)} chars")
        self.assertEqual(compact["reportPath"], "C:/tmp/report.json")
        self.assertEqual(len(compact["providerWatcherRebind"]["phases"]), 3)
        self.assertEqual(len(compact["messages"]), 6)  # 5 + overflow marker
        self.assertNotIn("runs", compact["providerWatcherRebind"])

    def test_diagnostics_compaction_stays_under_budget(self) -> None:
        full = {
            "ok": True,
            "configured": True,
            "enabled": True,
            "state": "ready",
            "currentStateFile": "C:/state/current.json",
            "currentState": {"providers": {f"p{i}": fat_command("status") for i in range(4)}},
            "items": [
                {"id": "cgc", "state": "ready", "ok": True, "rawStatus": fat_command("status")},
                {"id": "grepai", "state": "ready", "ok": True, "rawStatus": fat_command("status")},
            ],
            "recoveryActions": [],
            "rawStatus": {"results": [fat_command("status") for _ in range(4)]},
        }
        compact = compact_diagnostics_payload(full, "C:/tmp/diag.json")
        text = json.dumps(compact)
        self.assertLess(len(text), INLINE_BUDGET_CHARS, msg=f"{len(text)} chars")
        self.assertNotIn("rawStatus", compact)
        self.assertNotIn("currentState", compact)
        self.assertEqual(compact["currentStateFile"], "C:/state/current.json")
        for item in compact["items"]:
            self.assertNotIn("rawStatus", item)

    def test_carryover_plan_compaction_stays_under_budget(self) -> None:
        candidates = [fat_candidate(i, "auto-carry") for i in range(80)]
        candidates += [fat_candidate(80 + i, "review-required") for i in range(12)]
        candidates += [fat_candidate(92 + i, "reject") for i in range(8)]
        full = {
            "state": "would-carryover",
            "official_code_ref": "main",
            "official_code_head": "a" * 40,
            "source_code_ref": "feature",
            "source_code_head": "b" * 40,
            "old_base": "c" * 40,
            "official_memory": "C:/ew/ar-coordination/memory-repos/ar-repo",
            "source_memory": "C:/ew/ar-coordination/worktrees/repo/task-ar/memory-task",
            "counts": {"auto-carry": 80, "review-required": 12, "reject": 8},
            "candidates": candidates,
        }
        compact = compact_carryover_payload(full, "C:/tmp/plan.json")
        text = json.dumps(compact)
        self.assertLess(len(text), INLINE_BUDGET_CHARS, msg=f"{len(text)} chars")
        self.assertNotIn("candidates", compact)
        self.assertEqual(compact["reportPath"], "C:/tmp/plan.json")
        self.assertEqual(compact["counts"]["auto-carry"], 80)
        # action-relevant groups stay enumerable; oversized groups carry an overflow marker
        self.assertEqual(len(compact["decisions"]["review-required"]), 12)
        self.assertEqual(len(compact["decisions"]["auto-carry"]), 26)  # 25 + marker
        self.assertIn("more in report", compact["decisions"]["auto-carry"][-1])

    def test_carryover_apply_compaction_drops_duplicate_arrays(self) -> None:
        candidates = [fat_candidate(i, "auto-carry") for i in range(30)]
        full = {
            "state": "carried-over",
            "intent_note": "carry the landed memory",
            "counts": {"auto-carry": 30},
            "candidates": candidates,
            "carried": list(candidates),  # apply duplicates every record verbatim
            "memory_content_commit": "d" * 40,
            "ledger_commit": "e" * 40,
        }
        compact = compact_carryover_payload(full, "C:/tmp/apply.json")
        text = json.dumps(compact)
        self.assertLess(len(text), INLINE_BUDGET_CHARS, msg=f"{len(text)} chars")
        self.assertNotIn("candidates", compact)
        self.assertNotIn("carried", compact)
        # the facts the model acts on stay inline
        self.assertEqual(compact["memory_content_commit"], "d" * 40)
        self.assertEqual(compact["ledger_commit"], "e" * 40)
        self.assertEqual(compact["intent_note"], "carry the landed memory")
        self.assertEqual(len(compact["carriedPaths"]), 26)  # 25 + marker

    def test_carryover_report_retains_full_records(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            full = {
                "state": "would-carryover",
                "candidates": [fat_candidate(i, "auto-carry") for i in range(40)],
            }
            path = write_tool_report(root, "memory_carryover_plan", full, label="plan")
            stored = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(len(stored["candidates"]), 40)
            self.assertEqual(
                stored["candidates"][0]["evidence"],
                "exact-landed-commit",
            )

    def test_watchers_compaction_stays_under_budget(self) -> None:
        full = {
            "ok": True,
            "operation": "provider_watchers",
            "action": "restart",
            "steps": [
                {
                    "provider": "watchers",
                    "action": action,
                    "ok": True,
                    "partial": False,
                    "results": [
                        {
                            "provider": "grepai",
                            "action": action,
                            "ok": True,
                            **fat_command(action),
                        },
                        {
                            "provider": "codegraphcontext",
                            "action": action,
                            "ok": True,
                            "results": [fat_command(action) for _ in range(3)],
                        },
                    ],
                }
                for action in ("stop", "start")
            ],
        }
        compact = compact_watchers_payload(full, "C:/tmp/watchers.json")
        text = json.dumps(compact)
        self.assertLess(len(text), INLINE_BUDGET_CHARS, msg=f"{len(text)} chars")
        self.assertEqual(len(compact["steps"]), 2)
        for step in compact["steps"]:
            for result in step["results"]:
                self.assertNotIn("stdout", result)
                self.assertNotIn("compose", result)


if __name__ == "__main__":
    unittest.main()
