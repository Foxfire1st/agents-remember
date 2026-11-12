"""The answer guard's shaping, overflow and cut cases (MIK-R108 rule 1).

Split text-identical out of test_tool_answer_table.py under MIK-R196 rule 10."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from agents_remember.application.tool_answer_log import TOOL_LOG_BASENAME_PREFIX, tool_call_context
from agents_remember.application.tool_response import complete_tool_response
from agents_remember.models.tokens import count_response_tokens
from agents_remember.models.tools.tool_answer import (
    ENVELOPE_STRIPPED_FIELDS,
    LOG_ERROR_LOCATION_OMITTED,
    TOOL_ANSWER_ERROR_MAX_CHARS,
    TOOL_ANSWER_EXCEPTION_MAX_TOKENS,
    TOOL_ANSWER_LIST_MAX,
    TOOL_ANSWER_MAX_TOKENS,
    TOOL_ANSWER_OVERFLOW_FIXED_MAX_TOKENS,
    TOOL_ANSWER_OVERFLOW_MAX_TOKENS,
    TOOL_CODE_MAX_CHARS,
    bound_for,
    is_valid_tool_code,
    next_from_next_step,
    short_form,
    truncate_lists,
)
from tool_answer_table_support import _big_blob, _server_info_case, _stub_config


class ToolAnswerGuardTests(unittest.TestCase):
    def test_small_answer_passes_through_unchanged(self) -> None:
        answer = {"ok": True, "state": "started"}
        tokens = count_response_tokens(answer)
        self.assertLessEqual(tokens, TOOL_ANSWER_MAX_TOKENS)
        self.assertEqual(bound_for("worktree_start"), 600)

    def test_over_bound_answer_gets_the_short_form(self) -> None:
        big = {"ok": True, "blob": _big_blob(), "count": 7, "verbose": "y" * 500}
        tokens = count_response_tokens(big)
        self.assertGreater(tokens, TOOL_ANSWER_EXCEPTION_MAX_TOKENS)
        short, _dropped, error_cut = short_form(
            big,
            answer_tokens=tokens,
            log="/logs/ar-tools/d.json",
            count=count_response_tokens,
        )
        self.assertTrue(short["ok"])
        self.assertTrue(short["overflow"])
        self.assertEqual(short["answerTokens"], tokens)
        self.assertEqual(short["log"], "/logs/ar-tools/d.json")
        self.assertEqual(short["count"], 7)
        self.assertNotIn("blob", short)
        self.assertNotIn("verbose", short)
        self.assertFalse(error_cut)
        self.assertLessEqual(count_response_tokens(short), TOOL_ANSWER_OVERFLOW_MAX_TOKENS)

    def test_refusal_keeps_code_and_error_in_the_short_form(self) -> None:
        refusal = {
            "ok": False,
            "code": "start-reservation-conflict",
            "error": "recover this reserved start instead of creating another enclosure.",
            "blob": _big_blob(),
        }
        short, _dropped, error_cut = short_form(
            refusal, answer_tokens=9999, log="/logs/d.json", count=count_response_tokens
        )
        self.assertEqual(short["code"], "start-reservation-conflict")
        self.assertIn("recover this reserved start", short["error"])
        self.assertFalse(error_cut)
        self.assertNotIn("errorCut", short)

    def test_short_form_is_measured_five_hundred_short_fields(self) -> None:
        # Rule 4, E2: an answer with 500 short top-level fields gives a short
        # form within 400 tokens, with the exact code and a dropped count.
        answer = {"ok": False, "code": "start-blocked"}
        answer.update({f"f{index:03d}": f"value-{index}" for index in range(500)})
        tokens = count_response_tokens(answer)
        self.assertGreater(tokens, TOOL_ANSWER_MAX_TOKENS)
        short, dropped, error_cut = short_form(
            answer,
            answer_tokens=tokens,
            log="/logs/d.json",
            count=count_response_tokens,
        )
        self.assertEqual(short["code"], "start-blocked")
        self.assertFalse(error_cut)
        self.assertGreater(dropped, 0)
        self.assertEqual(short["droppedFields"], dropped)
        self.assertLessEqual(count_response_tokens(short), TOOL_ANSWER_OVERFLOW_MAX_TOKENS)

    def test_short_form_is_measured_fifty_thousand_char_error(self) -> None:
        # Rule 4, E2: a 50,000-character error is cut to 600 with errorCut,
        # the code is exact, and the form is within 400 tokens.
        error = "".join(f"sentence {index} says what to do next. " for index in range(1500))
        self.assertGreater(len(error), 50000)
        refusal = {"ok": False, "code": "start-blocked", "error": error}
        short, _dropped, error_cut = short_form(
            refusal,
            answer_tokens=99999,
            log="/logs/d.json",
            count=count_response_tokens,
        )
        self.assertEqual(short["code"], "start-blocked")
        self.assertTrue(error_cut)
        self.assertTrue(short["errorCut"])
        self.assertEqual(len(short["error"]), TOOL_ANSWER_ERROR_MAX_CHARS)
        self.assertEqual(short["error"], error[:TOOL_ANSWER_ERROR_MAX_CHARS])
        self.assertLessEqual(count_response_tokens(short), TOOL_ANSWER_OVERFLOW_MAX_TOKENS)

    def test_short_form_fixed_part_and_location_fit_with_longest_code(self) -> None:
        # Rule 4: steps 1 and 2 are within 250 tokens, shown with the
        # longest refusal codes of the product and an enclosure location.
        self.assertEqual(TOOL_ANSWER_OVERFLOW_FIXED_MAX_TOKENS, 250)
        self.assertEqual(TOOL_CODE_MAX_CHARS, 120)
        for code in (
            "closeout-candidate-changed-during-admission",
            "certification-selection-observation-moved",
        ):
            self.assertTrue(is_valid_tool_code(code))
            self.assertLessEqual(len(code), TOOL_CODE_MAX_CHARS)
            short, _dropped, _cut = short_form(
                {"ok": False, "code": code, "error": "what to do. " * 40},
                answer_tokens=99999,
                log=(
                    "/coord/worktrees/agents-remember/108_x/logs/ar-tools-2026-10-08/"
                    "160323398195-worktree_closeout_preview-717bc84c.json"
                ),
                count=count_response_tokens,
            )
            self.assertEqual(short["code"], code)
            self.assertIn("log", short)
            fixed = {key: short[key] for key in short if key != "error"}
            self.assertLessEqual(
                count_response_tokens(fixed), TOOL_ANSWER_OVERFLOW_FIXED_MAX_TOKENS
            )
            self.assertLessEqual(count_response_tokens(short), TOOL_ANSWER_OVERFLOW_MAX_TOKENS)

    def test_location_over_the_fixed_bound_is_omitted_not_substituted(self) -> None:
        # Rule 4: a location that does not fit is not shortened and not
        # replaced by another path: the fixed no-path logError rides, with
        # no log.
        log = "/coord/" + "x" * 2000 + "/ar-tools-2026-10-08/1-ping-abcdef12.json"
        short, _dropped, _cut = short_form(
            {"ok": False, "code": "start-blocked", "error": "what to do."},
            answer_tokens=99999,
            log=log,
            count=count_response_tokens,
        )
        self.assertNotIn("log", short)
        self.assertEqual(short["logError"], LOG_ERROR_LOCATION_OMITTED)
        self.assertNotIn("resultKept", short)
        self.assertLessEqual(count_response_tokens(short), TOOL_ANSWER_OVERFLOW_MAX_TOKENS)

    def test_short_form_is_measured_six_hundred_multi_token_char_error(self) -> None:
        # Rule 4, E2: an error of 600 characters that are several tokens
        # each is cut from its end until the measured form fits 400.
        error = "汉" * TOOL_ANSWER_ERROR_MAX_CHARS
        self.assertEqual(len(error), 600)
        refusal = {"ok": False, "code": "start-blocked", "error": error}
        short, _dropped, error_cut = short_form(
            refusal,
            answer_tokens=99999,
            log="/logs/d.json",
            count=count_response_tokens,
        )
        self.assertEqual(short["code"], "start-blocked")
        self.assertTrue(error_cut)
        self.assertTrue(short["errorCut"])
        self.assertLessEqual(len(short["error"]), 600)
        self.assertLess(
            count_response_tokens({"error": short["error"]}),
            count_response_tokens({"error": error}),
        )
        self.assertLessEqual(count_response_tokens(short), TOOL_ANSWER_OVERFLOW_MAX_TOKENS)

    def test_invalid_code_is_not_passed_on(self) -> None:
        # Rule 4: a longer or non-ASCII code is a defect of the tool that
        # made it: the answer keeps its ok and carries invalid-code.
        for bad in ("x" * 121, "codedé:broken idea"):
            self.assertFalse(is_valid_tool_code(bad))
            short, _dropped, _cut = short_form(
                {"ok": False, "code": bad, "error": "what to do."},
                answer_tokens=9999,
                log="/logs/d.json",
                count=count_response_tokens,
            )
            self.assertFalse(short["ok"] is None)
            self.assertEqual(short["code"], "invalid-code")
            self.assertLessEqual(count_response_tokens(short), TOOL_ANSWER_OVERFLOW_MAX_TOKENS)
        self.assertFalse(is_valid_tool_code(""))
        self.assertFalse(is_valid_tool_code(None))
        self.assertTrue(is_valid_tool_code("start-blocked"))

    def test_invalid_code_keeps_original_whole_in_detail(self) -> None:
        # Rule 4: the original answer is whole in the detail file.
        with tempfile.TemporaryDirectory() as tmp:
            coord = Path(tmp)
            config = _stub_config(coord)
            bad = "y" * 121
            payload = {
                "ok": False,
                "code": bad,
                "error": "what to do.",
                "blob": _big_blob(),
            }
            with tool_call_context("memory_quality_check", {"query": "q"}, config=config):
                answer = complete_tool_response("memory_quality_check", dict(payload))
            self.assertEqual(answer["code"], "invalid-code")
            self.assertIn("log", answer)
            detail = json.loads(Path(answer["log"]).read_text(encoding="utf-8"))
            self.assertEqual(detail["code"], bad)

    def test_surveyed_product_codes_are_valid(self) -> None:
        # Rule 4, rule 6: the table test fails for a row whose cases
        # produce a malformed code. All rows are interim (no cases yet), so
        # this pins the surveyed refusal codes of the product: every one is
        # an ASCII identifier within the bound.
        surveyed = [
            "certification-selection-observation-moved",
            "prepared-memory-curator-refresh-required",
            "prior-red-gate-five-inputs-unreadable",
            "prior-red-gate-five-terminal-invalid",
            "candidate-source-observation-refused",
            "closeout-candidate-changed-during-admission",
            "entity_fingerprint_without_inventory",
            "start-blocked",
            "raised",
        ]
        for code in surveyed:
            self.assertTrue(is_valid_tool_code(code), f"surveyed code rejected: {code}")
            self.assertLessEqual(len(code), TOOL_CODE_MAX_CHARS)

    def test_short_form_bound_ignores_a_lower_row_bound(self) -> None:
        # Rule 4: a row with a lower bound does not lower the short form's
        # own bound of 400 tokens.
        self.assertEqual(TOOL_ANSWER_OVERFLOW_MAX_TOKENS, 400)
        answer: dict[str, Any] = {"ok": True}
        answer.update({f"f{index:03d}": f"value-{index}" for index in range(250)})
        tokens = count_response_tokens(answer)
        self.assertGreater(tokens, TOOL_ANSWER_MAX_TOKENS)
        short, _dropped, _cut = short_form(
            answer,
            answer_tokens=tokens,
            log="/logs/d.json",
            count=count_response_tokens,
        )
        self.assertLessEqual(count_response_tokens(short), TOOL_ANSWER_OVERFLOW_MAX_TOKENS)

    def test_both_on_a_shaped_row_through_the_choke(self) -> None:
        # Rule 4, E2: 500 short fields and a 50,000-character error on a
        # landed shaped row (MIK-R110: memory_quality_check carries the
        # governed 1,500 bound): the measured short form keeps the exact
        # code with droppedFields and errorCut.
        with tempfile.TemporaryDirectory() as tmp:
            coord = Path(tmp)
            config = _stub_config(coord)
            answer_in = {"ok": False, "code": "start-blocked"}
            answer_in.update({f"f{index:03d}": f"value-{index}" for index in range(500)})
            answer_in["error"] = "what to do next. " * 3000
            self.assertEqual(bound_for("memory_quality_check"), 1500)
            with tool_call_context("memory_quality_check", {"query": "q"}, config=config):
                answer = complete_tool_response("memory_quality_check", answer_in)
            self.assertTrue(answer["overflow"])
            self.assertEqual(answer["code"], "start-blocked")
            self.assertTrue(answer["errorCut"])
            self.assertGreater(answer.get("droppedFields", 0), 0)
            self.assertLessEqual(count_response_tokens(answer), TOOL_ANSWER_OVERFLOW_MAX_TOKENS)
            self.assertIn("log", answer)

    def test_failed_detail_write_gives_log_error_and_result_kept_false(self) -> None:
        short, _dropped, _cut = short_form(
            {"ok": True, "blob": _big_blob()},
            answer_tokens=9999,
            log_error="the whole result was not recorded: no space.",
            count=count_response_tokens,
        )
        self.assertIn("not recorded", short["logError"])
        self.assertFalse(short["resultKept"])
        self.assertNotIn("log", short)

    def test_envelope_stripped_from_every_answer(self) -> None:
        answer = complete_tool_response(
            "server_info",
            _server_info_case(),
        )
        for field in ENVELOPE_STRIPPED_FIELDS:
            self.assertNotIn(field, answer)
        self.assertTrue(answer["ok"])
        self.assertNotIn("nextStep", answer)
        self.assertNotIn("log", answer)

    def test_over_bound_tool_answer_through_the_choke(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            coord = Path(tmp)
            config = _stub_config(coord)
            with tool_call_context("memory_quality_check", {"query": "q"}, config=config):
                answer = complete_tool_response(
                    "memory_quality_check", {"ok": True, "blob": _big_blob()}
                )
            self.assertTrue(answer["overflow"])
            self.assertNotIn("blob", answer)
            self.assertIn("log", answer)
            # The short form is measured: what the caller got is within 400.
            self.assertLessEqual(count_response_tokens(answer), TOOL_ANSWER_OVERFLOW_MAX_TOKENS)
            detail = json.loads(Path(answer["log"]).read_text(encoding="utf-8"))
            self.assertEqual(detail["blob"], _big_blob())
            self.assertIn("tokens", detail)
            # The line holds the tokens of the delivered answer, with the cut flag.
            log_dir = coord / "logs" / "mcp"
            lines = [
                json.loads(line)
                for file in sorted(log_dir.glob(f"{TOOL_LOG_BASENAME_PREFIX}*.jsonl"))
                for line in file.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertEqual(len(lines), 1)
            self.assertTrue(lines[0]["guardCut"])
            self.assertEqual(lines[0]["answerTokens"], count_response_tokens(answer))

    def test_over_budget_root_path_through_the_choke(self) -> None:
        # Rule 4, E2: a transcript root whose path alone is over the bound.
        # The answer is within the bound and carries the fixed logError, the
        # detail file exists in that folder, and the line names it. The path
        # is neither cut nor substituted nor capped.
        with tempfile.TemporaryDirectory() as tmp:
            coord = Path(tmp)
            deep = coord
            for index in range(10):
                deep = deep / f"level-{index:02d}-{'y' * 180}"
            deep.mkdir(parents=True)
            self.assertGreater(len(deep.as_posix()), 1900)
            config = _stub_config(coord, transcript_root=deep)
            with tool_call_context("memory_quality_check", {"query": "q"}, config=config):
                answer = complete_tool_response(
                    "memory_quality_check", {"ok": True, "blob": _big_blob()}
                )
            self.assertTrue(answer["overflow"])
            self.assertNotIn("log", answer)
            self.assertEqual(answer["logError"], LOG_ERROR_LOCATION_OMITTED)
            self.assertLessEqual(count_response_tokens(answer), TOOL_ANSWER_OVERFLOW_MAX_TOKENS)
            details = list(deep.glob("ar-tools-*/*.json"))
            self.assertEqual(len(details), 1)
            self.assertEqual(details[0].parent.parent, deep)
            stored = json.loads(details[0].read_text(encoding="utf-8"))
            self.assertEqual(stored["blob"], _big_blob())
            lines = [
                json.loads(line)
                for file in sorted(deep.glob(f"{TOOL_LOG_BASENAME_PREFIX}*.jsonl"))
                for line in file.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertEqual(len(lines), 1)
            self.assertEqual(lines[0]["detail"], details[0].name)

    def test_list_over_twenty_keeps_count_and_first_entries(self) -> None:
        answer = truncate_lists({"items": list(range(25)), "ok": True})
        self.assertEqual(answer["items"]["count"], 25)
        self.assertEqual(answer["items"]["items"], list(range(20)))
        self.assertTrue(answer["items"]["truncated"])
        self.assertEqual(truncate_lists({"items": list(range(20))}), {"items": list(range(20))})
        self.assertEqual(TOOL_ANSWER_LIST_MAX, 20)

    def test_nested_lists_are_cut_through_retained_items(self) -> None:
        answer = truncate_lists({"outer": [{"inner": list(range(25))}]})
        (kept,) = answer["outer"]
        self.assertEqual(kept["inner"]["count"], 25)
        self.assertEqual(kept["inner"]["items"], list(range(20)))
        self.assertTrue(kept["inner"]["truncated"])
        wide = truncate_lists({"outer": [[1] * 25 for _ in range(3)]})
        for kept_list in wide["outer"]:
            self.assertEqual(kept_list["count"], 25)
            self.assertTrue(kept_list["truncated"])
        deep = truncate_lists({"outer": list(range(25))})
        self.assertEqual(deep["outer"]["count"], 25)
        self.assertEqual(len(deep["outer"]["items"]), 20)

    def test_next_carries_the_one_following_call(self) -> None:
        self.assertIsNone(next_from_next_step(None))
        self.assertEqual(
            next_from_next_step(
                {
                    "summary": "continue",
                    "nextOperation": "continue_work",
                    "nextTool": "worktree_status",
                    "nextArgs": {"repo_id": "agents-remember"},
                }
            ),
            {
                "summary": "continue",
                "tool": "worktree_status",
                "args": {"repo_id": "agents-remember"},
            },
        )
