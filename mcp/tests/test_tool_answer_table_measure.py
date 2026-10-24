"""The final-measure and partial-write cases of the tool-answer log.

Split text-identical out of test_tool_answer_table.py under MIK-R196 rule 10."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

import agents_remember.application.tool_answer_log as tool_log_module
from agents_remember.application.tool_answer_log import (
    TOOL_LOG_BASENAME_PREFIX,
    LineRequest,
    ToolCaller,
    ToolCallRecord,
    append_call_line,
    append_jsonl_line,
    record_tool_call,
    reset_log_budgets,
    resolve_log_roots,
    tool_call_context,
)
from agents_remember.application.tool_response import complete_tool_response
from agents_remember.models.tokens import count_response_tokens
from agents_remember.models.tools.tool_answer import TOOL_ANSWER_OVERFLOW_MAX_TOKENS, bound_for
from tool_answer_table_support import _big_blob, _server_info_case, _stub_config


class ToolAnswerFinalMeasureTests(unittest.TestCase):
    """The guard measures the final delivered mapping (rule 4, rule 3).

    Attaching the log location can itself take an exact-edge answer over its
    bound; a late line failure must not push a delivered answer back over
    it. The line and the observer both carry the delivered count.
    """

    def _complete(
        self, coord: Path, tool: str, whole: dict[str, Any]
    ) -> tuple[dict[str, Any], list[Any]]:
        reset_log_budgets()
        cfg = _stub_config(coord)
        seen: list[Any] = []
        with (
            mock.patch(
                "agents_remember.application.tool_response.record_tool_completed",
                side_effect=lambda *args, **kwargs: seen.append((args, kwargs)),
            ),
            tool_call_context(tool, {}, config=cfg),
        ):
            answer = complete_tool_response(tool, dict(whole), config=cfg)
        return answer, seen

    def _read_line(self, coord: Path) -> dict[str, Any]:
        files = sorted((coord / "logs" / "mcp").glob(f"{TOOL_LOG_BASENAME_PREFIX}*.jsonl"))
        self.assertEqual(len(files), 1)
        return json.loads(files[0].read_text(encoding="utf-8").splitlines()[-1])

    def test_exact_edge_answer_guarded_by_its_log_location(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            coord = Path(tmp)
            bound = bound_for("memory_quality_check")
            whole = {"ok": True, "blob": ""}
            size = 0
            while not bound - 40 <= count_response_tokens(whole) <= bound - 5:
                size += 64
                whole["blob"] = "x" * size
            self.assertLessEqual(count_response_tokens(whole), bound)
            answer, seen = self._complete(coord, "memory_quality_check", whole)
            delivered = count_response_tokens(answer)
            self.assertTrue(answer.get("overflow"))
            self.assertLessEqual(delivered, TOOL_ANSWER_OVERFLOW_MAX_TOKENS)
            (line,) = [self._read_line(coord)]
            self.assertEqual(line["answerTokens"], delivered)
            self.assertTrue(line["guardCut"])
            self.assertEqual(seen[-1][1]["delivered_tokens"], delivered)

    def test_ordinary_answer_observer_matches_delivered(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            coord = Path(tmp)
            whole = _server_info_case()
            answer, seen = self._complete(coord, "server_info", whole)
            delivered = count_response_tokens(answer)
            self.assertNotIn("code", answer)
            (line,) = [self._read_line(coord)]
            self.assertEqual(line["answerTokens"], delivered)
            self.assertEqual(seen[-1][1]["delivered_tokens"], delivered)

    def test_two_late_failures_keep_first_error_and_recount(self) -> None:
        real_write = tool_log_module.os.write
        real_evict = tool_log_module._evict_older_days
        line_failures: list[int] = []
        evict_calls: list[int] = []

        def flaky_write(fd: int, data: Any) -> int:
            raw = bytes(data)
            if b'"answerTokens"' in raw and not line_failures:
                line_failures.append(1)
                raise OSError("forced line failure")
            return real_write(fd, data)

        def flaky_evict(*args: Any, **kwargs: Any) -> bool:
            evict_calls.append(1)
            if len(evict_calls) == 3:
                return False
            return real_evict(*args, **kwargs)

        with tempfile.TemporaryDirectory() as tmp:
            coord = Path(tmp)
            whole = {"ok": True, "blob": _big_blob()}
            with (
                mock.patch.object(tool_log_module.os, "write", side_effect=flaky_write),
                mock.patch.object(tool_log_module, "_evict_older_days", side_effect=flaky_evict),
            ):
                answer, seen = self._complete(coord, "memory_quality_check", whole)
            delivered = count_response_tokens(answer)
            self.assertEqual(line_failures, [1], "the line failure never fired")
            self.assertLessEqual(delivered, TOOL_ANSWER_OVERFLOW_MAX_TOKENS)
            # The first sentence stays; the retry failure only counts as
            # dropped. The delivered answer, not the pre-cut count, is observed.
            self.assertEqual(
                answer.get("logError"),
                "the tool call was not recorded: the log file cannot be written.",
            )
            self.assertEqual(seen[-1][1]["delivered_tokens"], delivered)

    def test_ordinary_exact_edge_with_line_failure_is_reguarded(self) -> None:
        real_write = tool_log_module.os.write
        failed: list[int] = []

        def flaky_once(fd: int, data: Any) -> int:
            raw = bytes(data)
            if b'"answerTokens"' in raw and not failed:
                failed.append(1)
                raise OSError("forced line failure")
            return real_write(fd, data)

        with tempfile.TemporaryDirectory() as tmp:
            coord = Path(tmp)
            # Learn the attached location's size on a passing run, then find
            # the largest blob whose final mapping still fits the bound: the
            # predicate is monotone, so binary search always terminates and
            # never skips past the edge.
            probe, _ = self._complete(coord, "memory_quality_check", {"ok": True, "blob": ""})
            assert probe["log"] is not None
            location_tokens = count_response_tokens({"log": probe["log"]})
            bound = bound_for("memory_quality_check")
            low, high = 0, 200000
            while low < high:
                mid = (low + high + 1) // 2
                final = count_response_tokens({"ok": True, "blob": "x" * mid}) + location_tokens
                if final <= bound:
                    low = mid
                else:
                    high = mid - 1
            whole: dict[str, Any] = {"ok": True, "blob": "x" * low}
            final_before_failure = count_response_tokens(whole) + location_tokens
            # Near the edge: the late failure is what pushes it over.
            self.assertLessEqual(final_before_failure, bound)
            self.assertGreater(final_before_failure, bound - 40)
            reset_log_budgets()
            with mock.patch.object(tool_log_module.os, "write", side_effect=flaky_once):
                answer, seen = self._complete(coord, "memory_quality_check", whole)
            delivered = count_response_tokens(answer)
            self.assertEqual(failed, [1], "the line failure never fired")
            self.assertTrue(answer.get("overflow"))
            self.assertLessEqual(delivered, TOOL_ANSWER_OVERFLOW_MAX_TOKENS)
            (line,) = [self._read_line(coord)]
            self.assertEqual(line["answerTokens"], delivered)
            self.assertTrue(line["guardCut"])
            self.assertEqual(seen[-1][1]["delivered_tokens"], delivered)

    def test_full_short_form_fits_including_bookkeeping(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            coord = Path(tmp)
            error = "".join(f"token-{index}-failure; " for index in range(40))
            whole: dict[str, Any] = {"ok": False, "error": error, "blob": _big_blob()}
            for index in range(500):
                whole[f"f{index:03d}"] = f"scalar-value-{index}"
            answer, seen = self._complete(coord, "memory_quality_check", whole)
            delivered = count_response_tokens(answer)
            self.assertTrue(answer.get("overflow"))
            self.assertTrue(answer.get("errorCut"))
            # The dropped count itself is part of the measured form: with no
            # scalar retained to shed, the error shrinks until everything fits.
            self.assertLessEqual(delivered, TOOL_ANSWER_OVERFLOW_MAX_TOKENS)
            kept_scalars = [key for key in answer if key.startswith("f")]
            self.assertEqual(answer.get("droppedFields", 0) + len(kept_scalars), 500)
            (line,) = [self._read_line(coord)]
            self.assertEqual(line["answerTokens"], delivered)
            self.assertEqual(seen[-1][1]["delivered_tokens"], delivered)

    def test_cut_answer_late_failure_reguards_at_the_short_ceiling(self) -> None:
        real_write = tool_log_module.os.write
        failed: list[int] = []

        def flaky_once(fd: int, data: Any) -> int:
            raw = bytes(data)
            if b'"answerTokens"' in raw and not failed:
                failed.append(1)
                raise OSError("forced line failure")
            return real_write(fd, data)

        with tempfile.TemporaryDirectory() as tmp:
            coord = Path(tmp)
            error = "".join(f"token-{index}-failure; " for index in range(40))
            whole: dict[str, Any] = {"ok": False, "error": error}
            for index in range(500):
                whole[f"f{index:03d}"] = f"scalar-value-{index}"
            reset_log_budgets()
            with mock.patch.object(tool_log_module.os, "write", side_effect=flaky_once):
                answer, seen = self._complete(coord, "memory_quality_check", whole)
            delivered = count_response_tokens(answer)
            self.assertEqual(failed, [1], "the line failure never fired")
            self.assertTrue(answer.get("overflow"))
            # The re-guard ceiling is the short-form ceiling even though the
            # tool bound is higher: the late sentence re-triggers the cut.
            self.assertLessEqual(delivered, TOOL_ANSWER_OVERFLOW_MAX_TOKENS)
            (line,) = [self._read_line(coord)]
            self.assertEqual(line["answerTokens"], delivered)
            self.assertTrue(line["guardCut"])
            self.assertEqual(seen[-1][1]["delivered_tokens"], delivered)

    def test_line_failure_overflow_stays_in_bound_and_counts_agree(self) -> None:
        real_write = os.write
        failed: list[int] = []

        def flaky(fd: int, data: Any) -> int:
            raw = bytes(data)
            # Only the log line carries answerTokens; the detail whole does
            # not. Fail the line's first attempt, then let the retry land.
            if b'"answerTokens"' in raw and not failed:
                failed.append(1)
                raise OSError("forced line failure")
            return real_write(fd, data)

        with tempfile.TemporaryDirectory() as tmp:
            coord = Path(tmp)
            whole = {"ok": True, "blob": _big_blob()}
            with mock.patch.object(tool_log_module.os, "write", side_effect=flaky):
                answer, seen = self._complete(coord, "memory_quality_check", whole)
            delivered = count_response_tokens(answer)
            self.assertEqual(failed, [1], "the line failure never fired")
            self.assertLessEqual(delivered, TOOL_ANSWER_OVERFLOW_MAX_TOKENS)
            self.assertIsNotNone(answer.get("logError"))
            (line,) = [self._read_line(coord)]
            self.assertEqual(line["answerTokens"], delivered)
            self.assertEqual(seen[-1][1]["delivered_tokens"], delivered)


class ToolPartialWriteTests(unittest.TestCase):
    """A short write is detected, never claimed as a whole record (rule 3)."""

    def test_short_write_is_recovered(self) -> None:
        real_write = os.write
        calls = []

        def partial_once(fd: int, data: Any) -> int:
            calls.append(1)
            if len(calls) == 1:
                return real_write(fd, bytes(data[:10]))
            return real_write(fd, data)

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ar-tools-2026-10-08.jsonl"
            with mock.patch.object(tool_log_module.os, "write", side_effect=partial_once):
                error = append_jsonl_line(path, {"ok": True, "tool": "ping"})
            self.assertIsNone(error)
            (parsed,) = [
                json.loads(line)
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertEqual(parsed, {"ok": True, "tool": "ping"})

    def test_partial_then_stalled_prefix_does_not_join_next_record(self) -> None:
        real_write = tool_log_module.os.write
        calls: list[int] = []

        def ten_then_stuck(fd: int, data: Any) -> int:
            calls.append(1)
            if len(calls) == 1:
                return real_write(fd, bytes(data[:10]))
            return 0

        with tempfile.TemporaryDirectory() as tmp:
            coord = Path(tmp)
            reset_log_budgets()
            dest = resolve_log_roots(_stub_config(coord))
            assert dest.coord_log_dir is not None
            record = ToolCallRecord(
                tool_name="second",
                operation="second",
                arguments={},
                caller=ToolCaller(),
                ok=True,
                code=None,
                duration_ms=1.0,
                answer_tokens=10,
                whole_result={"ok": True},
                result_bytes=12,
                guard_cut=False,
                needs_detail=False,
                enclosure_dir=None,
                coord_log_dir=dest.coord_log_dir,
            )
            with mock.patch.object(tool_log_module.os, "write", side_effect=ten_then_stuck):
                outcome = record_tool_call(record)
            self.assertIsNotNone(outcome.log_error)
            outcome = record_tool_call(record)
            self.assertIsNone(outcome.log_error)
            raw = next(dest.coord_log_dir.glob(f"{TOOL_LOG_BASENAME_PREFIX}*.jsonl")).read_text(
                encoding="utf-8"
            )
            parsed: list[dict[str, Any]] = []
            for line in raw.splitlines():
                if not line.strip():
                    continue
                try:
                    parsed.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
            # The stalled prefix stands alone; the valid record parses.
            self.assertEqual(len(parsed), 1)
            self.assertEqual(parsed[0]["tool"], "second")
            self.assertEqual(parsed[0]["dropped"], 1)

    def test_concurrent_writers_never_interleave_records(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "storm.jsonl"
            errors: list[BaseException] = []

            def worker(index: int) -> None:
                try:
                    failure = append_jsonl_line(path, {"tool": f"w{index}", "n": index})
                    assert failure is None, failure
                except BaseException as exc:
                    errors.append(exc)

            threads = [threading.Thread(target=worker, args=(index,)) for index in range(8)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=60)
            self.assertEqual(errors, [])
            parsed = []
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    parsed.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
            self.assertEqual(len(parsed), 8)
            self.assertEqual(
                sorted(entry["tool"] for entry in parsed),
                [f"w{index}" for index in range(8)],
            )

    def test_stalled_writer_leaves_concurrent_neighbor_parseable(self) -> None:
        real_write = tool_log_module.os.write

        first_holder: list[threading.Thread] = []
        a_armed = [True]

        def ten_then_stuck(fd: int, data: Any) -> int:
            if first_holder and threading.current_thread() is first_holder[0]:
                if a_armed[0]:
                    a_armed[0] = False
                    return real_write(fd, bytes(data[:10]))
                return 0
            return real_write(fd, data)

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "neighbors.jsonl"
            results: dict[str, Any] = {}

            def stalled() -> None:
                results["stalled"] = append_jsonl_line(path, {"tool": "first", "pad": "x" * 200})

            def whole() -> None:
                results["whole"] = append_jsonl_line(path, {"tool": "second"})

            first, second = (
                threading.Thread(target=stalled),
                threading.Thread(target=whole),
            )
            first_holder.append(first)
            with mock.patch.object(tool_log_module.os, "write", side_effect=ten_then_stuck):
                first.start()
                second.start()
                first.join(timeout=60)
                second.join(timeout=60)
            self.assertIsNotNone(results["stalled"])
            self.assertIsNone(results["whole"])
            parsed = []
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    parsed.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
            self.assertEqual([entry["tool"] for entry in parsed], ["second"])

    def test_concurrent_stall_is_counted_on_the_next_persisted_line(self) -> None:
        real_write = tool_log_module.os.write
        first_holder: list[threading.Thread] = []
        a_armed = [True]

        def ten_then_stuck(fd: int, data: Any) -> int:
            if first_holder and threading.current_thread() is first_holder[0]:
                if a_armed[0]:
                    a_armed[0] = False
                    return real_write(fd, bytes(data[:10]))
                return 0
            return real_write(fd, data)

        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            reset_log_budgets()

            def budgeted(tool: str) -> Any:
                return append_call_line(
                    LineRequest(
                        log_dir=folder,
                        budget_bytes=10_000_000,
                        line={
                            "tool": tool,
                            "operation": tool,
                            "answer_tokens": 10,
                            "ok": True,
                        },
                    ),
                    day="2026-10-08",
                )

            results: dict[str, Any] = {}
            first = threading.Thread(
                target=lambda: results.setdefault("stalled", budgeted("first"))
            )
            second = threading.Thread(
                target=lambda: results.setdefault("whole", budgeted("second"))
            )
            first_holder.append(first)
            with mock.patch.object(tool_log_module.os, "write", side_effect=ten_then_stuck):
                first.start()
                second.start()
                first.join(timeout=60)
                second.join(timeout=60)
            self.assertIsNotNone(results["stalled"])
            self.assertIsNone(results["whole"])
            third = budgeted("third")
            self.assertIsNone(third)
            lines = []
            for line in (
                (folder / "ar-tools-2026-10-08.jsonl").read_text(encoding="utf-8").splitlines()
            ):
                if not line.strip():
                    continue
                try:
                    lines.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
            by_tool = {entry["tool"]: entry for entry in lines if isinstance(entry, dict)}
            # The stalled write is counted on the next applicable persisted
            # line no matter which writer won the race.
            self.assertIn(by_tool["second"]["dropped"], (0, 1))
            accounted = by_tool["second"]["dropped"] + by_tool["third"]["dropped"]
            self.assertEqual(accounted, 1)
            self.assertEqual(sorted(by_tool), ["second", "third"])

    def test_stuck_write_is_dropped_and_counted(self) -> None:
        def stuck(_fd: int, _data: Any) -> int:
            return 0

        with tempfile.TemporaryDirectory() as tmp:
            coord = Path(tmp)
            reset_log_budgets()
            dest = resolve_log_roots(_stub_config(coord))
            assert dest.coord_log_dir is not None
            record = ToolCallRecord(
                tool_name="ping",
                operation="ping",
                arguments={},
                caller=ToolCaller(),
                ok=True,
                code=None,
                duration_ms=1.0,
                answer_tokens=10,
                whole_result={"ok": True},
                result_bytes=12,
                guard_cut=False,
                needs_detail=False,
                enclosure_dir=None,
                coord_log_dir=dest.coord_log_dir,
            )
            with mock.patch.object(tool_log_module.os, "write", side_effect=stuck):
                outcome = record_tool_call(record)
            self.assertIsNotNone(outcome.log_error)
            log_dir = dest.coord_log_dir
            files = sorted(log_dir.glob(f"{TOOL_LOG_BASENAME_PREFIX}*.jsonl"))
            self.assertTrue(all(not f.read_text(encoding="utf-8").strip() for f in files))
            outcome = record_tool_call(record)
            self.assertIsNone(outcome.log_error)
            lines = [
                json.loads(line)
                for file in sorted(log_dir.glob(f"{TOOL_LOG_BASENAME_PREFIX}*.jsonl"))
                for line in file.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertEqual(len(lines), 1)
            self.assertEqual(lines[0]["dropped"], 1)
