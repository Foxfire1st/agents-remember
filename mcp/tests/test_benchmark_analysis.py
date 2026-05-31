"""Tests for benchmark JSONL metric extraction (F9).

These pin the Codex event semantics the summary depends on: tokens come from the
`turn.completed.usage` object, command counts are `command_execution` items, and
the final answer is the last `agent_message`.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from agents_remember.benchmarks.runner_modules.analysis import analyze_jsonl


def _write_jsonl(path: Path, events: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8")


class BenchmarkAnalysisTests(unittest.TestCase):
    def test_extracts_codex_usage_commands_and_final_answer(self) -> None:
        events = [
            {"type": "thread.started", "thread_id": "t"},
            {"type": "turn.started"},
            {"type": "item.completed", "item": {"type": "command_execution", "command": "ls"}},
            {"type": "item.completed", "item": {"type": "command_execution", "command": "rg x"}},
            {"type": "item.completed", "item": {"type": "agent_message", "text": "first"}},
            {"type": "item.completed", "item": {"type": "agent_message", "text": "final answer"}},
            {
                "type": "turn.completed",
                "usage": {
                    "input_tokens": 1000,
                    "cached_input_tokens": 900,
                    "output_tokens": 50,
                    "reasoning_output_tokens": 12,
                },
            },
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "run.jsonl"
            _write_jsonl(path, events)
            metrics = analyze_jsonl(path)
        self.assertEqual(metrics["command_event_count"], 2)
        self.assertEqual(metrics["input_tokens"], 1000)
        self.assertEqual(metrics["cached_input_tokens"], 900)
        self.assertEqual(metrics["output_tokens"], 50)
        self.assertEqual(metrics["reasoning_output_tokens"], 12)
        self.assertEqual(metrics["final_answer"], "final answer")
        self.assertEqual(metrics["event_count"], 7)

    def test_sums_usage_across_multiple_turns(self) -> None:
        events = [
            {"type": "turn.completed", "usage": {"input_tokens": 100, "output_tokens": 10}},
            {"type": "turn.completed", "usage": {"input_tokens": 200, "output_tokens": 20}},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "run.jsonl"
            _write_jsonl(path, events)
            metrics = analyze_jsonl(path)
        self.assertEqual(metrics["input_tokens"], 300)
        self.assertEqual(metrics["output_tokens"], 30)

    def test_only_completed_command_items_count(self) -> None:
        events = [
            {"type": "item.completed", "item": {"type": "agent_message", "text": "hi"}},
            {"type": "item.started", "item": {"type": "command_execution"}},
            {"type": "item.completed", "item": {"type": "command_execution", "command": "ls"}},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "run.jsonl"
            _write_jsonl(path, events)
            metrics = analyze_jsonl(path)
        self.assertEqual(metrics["command_event_count"], 1)


if __name__ == "__main__":
    unittest.main()
