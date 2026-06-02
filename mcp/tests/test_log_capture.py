"""Tests for the provider response log-summarizer (#3: oversized worktree_start response).

pg_dump|psql / ollama pull / cgc bundle-import output is hundreds of KB; a tool response only
needs the outcome. Logs are dropped on success, tailed on failure, and secrets in command
lines are redacted.
"""

from __future__ import annotations

import unittest

from agents_remember.providers.lifecycle.log_capture import (
    summarize_command_logs,
    tail_lines,
)


class TailLinesTests(unittest.TestCase):
    def test_short_text_is_unchanged(self) -> None:
        text = "a\nb\nc"
        self.assertEqual(tail_lines(text, 30), text)

    def test_long_text_keeps_only_the_tail(self) -> None:
        text = "\n".join(str(i) for i in range(100))
        result = tail_lines(text, 30)
        self.assertIn("70 earlier line(s) omitted", result)
        self.assertTrue(result.endswith("99"))
        self.assertNotIn("\n69\n", result)


class SummarizeCommandLogsTests(unittest.TestCase):
    def test_success_drops_stdout_and_stderr(self) -> None:
        node = {"ok": True, "stdout": "huge\noutput", "stderr": "noise"}
        summarize_command_logs(node)
        self.assertEqual(node["stdout"], "")
        self.assertEqual(node["stderr"], "")

    def test_returncode_zero_counts_as_success(self) -> None:
        node = {"returncode": 0, "stdout": "x" * 10}
        summarize_command_logs(node)
        self.assertEqual(node["stdout"], "")

    def test_failure_keeps_only_the_tail(self) -> None:
        node = {"ok": False, "stdout": "\n".join(str(i) for i in range(100))}
        summarize_command_logs(node, failure_tail=10)
        self.assertIn("omitted", node["stdout"])
        self.assertTrue(node["stdout"].endswith("99"))

    def test_redacts_password_in_command_list(self) -> None:
        node = {
            "ok": False,
            "stderr": "boom",
            "commands": {"dump": ["docker", "exec", "-e", "PGPASSWORD=secret", "pg_dump"]},
        }
        summarize_command_logs(node)
        self.assertIn("PGPASSWORD=***", node["commands"]["dump"])
        self.assertNotIn("PGPASSWORD=secret", node["commands"]["dump"])

    def test_recurses_into_nested_steps(self) -> None:
        payload = {
            "ok": True,
            "steps": [
                {"ok": True, "stdout": "drop me"},
                {"ok": False, "stdout": "\n".join(str(i) for i in range(50))},
            ],
        }
        summarize_command_logs(payload, failure_tail=5)
        self.assertEqual(payload["steps"][0]["stdout"], "")
        self.assertIn("omitted", payload["steps"][1]["stdout"])

    def test_each_node_evaluated_independently(self) -> None:
        # A failing child inside a successful parent keeps its tail.
        payload = {"ok": True, "child": {"ok": False, "stderr": "err line"}}
        summarize_command_logs(payload)
        self.assertEqual(payload["child"]["stderr"], "err line")

    def test_success_drops_duplicate_json_and_verbose_plumbing(self) -> None:
        node = {
            "ok": True,
            "seededFrom": "ws-ollama",  # a key fact -- must survive
            "json": {"big": "duplicate of payload"},
            "command": {"returncode": 0, "stdout": "x"},
            "compose": {"services": ["a", "b"]},
            "migration": {"moved": 3},
            "payload": {"ok": True},
        }
        summarize_command_logs(node)
        for dropped in ("json", "command", "compose", "migration"):
            self.assertNotIn(dropped, node)
        self.assertEqual(node["seededFrom"], "ws-ollama")
        self.assertIn("payload", node)

    def test_json_dropped_even_on_failure_but_command_kept(self) -> None:
        node = {
            "ok": False,
            "json": {"dup": 1},
            "command": ["docker", "exec", "-e", "PGPASSWORD=secret", "psql"],
            "stderr": "boom",
        }
        summarize_command_logs(node)
        self.assertNotIn("json", node)  # always redundant
        self.assertIn("command", node)  # kept for debugging a failure
        self.assertIn("PGPASSWORD=***", node["command"])
        self.assertEqual(node["stderr"], "boom")


if __name__ == "__main__":
    unittest.main()
