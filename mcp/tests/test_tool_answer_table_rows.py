"""The answer table's row gates and authored ownership cases (MIK-R108 rules 6-8).

Split text-identical out of test_tool_answer_table.py under MIK-R196 rule 10."""

from __future__ import annotations

import unittest
from typing import Any

from agents_remember.models.tokens import count_response_tokens
from agents_remember.models.tools.tool_answer import (
    TOOL_ANSWER_EXCEPTION_MAX_TOKENS,
    TOOL_ANSWER_OVERFLOW_MAX_TOKENS,
    fields_outside_row,
    interim_leaf_for,
    is_valid_tool_code,
    shaped_bound_for,
    short_form,
)
from agents_remember.models.tools.tool_registry import TOOL_RESPONSE_MODELS
from tool_answer_l110_rows import L110_ROW_DATA
from tool_answer_l111_test_support import ToolAnswerRow
from tool_answer_table_support import (
    AUTHORED_INTERIM_OWNERS,
    TOOL_ANSWER_TABLE,
    _big_blob,
    authored_row_owners,
    check_tool_answer_roster,
    validate_row_case,
    validate_tool_answer_row,
)


class ToolAnswerRowGateTests(unittest.TestCase):
    """Negative controls: each rule-6 failure mode fails its gate."""

    def _shaped_row(self, **overrides: Any) -> ToolAnswerRow:
        largest = {"ok": True, "state": "y" * 300}
        largest_measured = count_response_tokens(largest)
        params: dict[str, Any] = {
            "tool": "shaped",
            "operations": (None,),
            "fields": frozenset({"state"}),
            "uses": {"state": "resume after poll"},
            "bound": largest_measured + largest_measured // 2,
            "interim_leaf": None,
            "ordinary_case": {"ok": True, "state": "started"},
            "largest_case": largest,
        }
        params.update(overrides)
        return ToolAnswerRow(**params)

    def test_missing_row_fails(self) -> None:
        reduced = {t: leaf for t, leaf in AUTHORED_INTERIM_OWNERS.items() if t != "task_doc"}
        with self.assertRaises(AssertionError):
            check_tool_answer_roster(set(TOOL_RESPONSE_MODELS), reduced)

    def test_valid_shaped_row_passes(self) -> None:
        # The probe's legitimate shaped row: no interim owner, a nonempty
        # use, a bound, and measured ordinary/largest case payloads.
        failures = validate_tool_answer_row(("shaped", None), self._shaped_row())
        self.assertEqual(failures, [])

    def test_field_outside_row_fails(self) -> None:
        self.assertEqual(
            fields_outside_row({"ok": True, "state": "s", "rogue": 1}, frozenset({"state"})),
            {"rogue"},
        )

    def test_field_without_stated_use_fails(self) -> None:
        row = self._shaped_row(uses={})
        failures = validate_tool_answer_row(("shaped", None), row)
        self.assertTrue(any("no stated use" in failure for failure in failures))

    def test_empty_use_fails(self) -> None:
        row = self._shaped_row(uses={"state": ""})
        failures = validate_tool_answer_row(("shaped", None), row)
        self.assertTrue(any("no stated use" in failure for failure in failures))

    def test_use_for_unknown_field_fails(self) -> None:
        row = self._shaped_row(uses={"state": "resume after poll", "ghost": "resume"})
        failures = validate_tool_answer_row(("shaped", None), row)
        self.assertTrue(any("unknown field" in failure for failure in failures))

    def test_shaped_row_with_interim_leaf_fails(self) -> None:
        row = self._shaped_row(interim_leaf="L112")
        failures = validate_tool_answer_row(("shaped", None), row)
        self.assertTrue(any("interim leaf" in failure for failure in failures))

    def test_interim_row_without_leaf_fails(self) -> None:
        row = ToolAnswerRow(
            tool="shaped",
            operations=(None,),
            fields=None,
            uses={},
            bound=4000,
            interim_leaf=None,
            ordinary_case=None,
            largest_case=None,
        )
        failures = validate_tool_answer_row(("shaped", None), row)
        self.assertTrue(any("no owning leaf" in failure for failure in failures))

    def test_case_over_the_row_bound_fails(self) -> None:
        row = self._shaped_row(
            bound=100,
            ordinary_case={"ok": True, "state": "s"},
            largest_case={"ok": True, "blob": _big_blob()},
        )
        failures = validate_tool_answer_row(("shaped", None), row)
        self.assertTrue(any("over the row bound" in failure for failure in failures))

    def test_bound_over_twice_the_largest_case_fails(self) -> None:
        row = self._shaped_row(
            bound=4000,
            ordinary_case={"ok": True, "state": "s"},
            largest_case={"ok": True, "state": "s"},
        )
        failures = validate_tool_answer_row(("shaped", None), row)
        self.assertTrue(any("twice the largest" in failure for failure in failures))

    def test_shaped_row_without_case_payloads_fails(self) -> None:
        row = self._shaped_row(ordinary_case=None, largest_case=None)
        failures = validate_tool_answer_row(("shaped", None), row)
        self.assertTrue(any("without case payloads" in failure for failure in failures))

    def test_production_proven_row_without_case_payloads_passes(self) -> None:
        # A shaped row that names the real tool paths its cases are derived
        # through needs no static payloads; the derivation tests prove it live.
        row = self._shaped_row(ordinary_case=None, largest_case=None, production_paths=("sync",))
        failures = validate_tool_answer_row(("shaped", None), row)
        self.assertEqual(failures, [])

    def test_invalid_code_case_fails_the_row(self) -> None:
        row = self._shaped_row()
        failures = validate_row_case(
            ("shaped", None), row, "largest case", {"ok": False, "code": "y" * 121}
        )
        self.assertTrue(any("invalid code" in failure for failure in failures))

    def test_guard_hit_case_fails_the_row(self) -> None:
        row = self._shaped_row()
        failures = validate_row_case(
            ("shaped", None), row, "largest case", {"ok": True, "blob": _big_blob()}
        )
        self.assertTrue(any("over the row bound" in failure for failure in failures))

    def test_case_field_outside_row_fails(self) -> None:
        row = self._shaped_row()
        failures = validate_row_case(
            ("shaped", None), row, "ordinary case", {"ok": True, "rogue": 1}
        )
        self.assertTrue(any("outside its row" in failure for failure in failures))

    def test_invalid_code_is_replaced(self) -> None:
        self.assertFalse(is_valid_tool_code("y" * 121))
        whole = {"ok": False, "code": "y" * 121, "error": "the call failed."}
        short, _dropped, _cut = short_form(
            whole,
            answer_tokens=10,
            log=None,
            log_error=None,
            count=count_response_tokens,
        )
        self.assertEqual(short["code"], "invalid-code")

    def test_interim_row_case_hitting_the_guard_gets_the_short_form(self) -> None:
        big = {"ok": True, "code": "started", "blob": _big_blob(), "count": 7}
        tokens = count_response_tokens(big)
        self.assertGreater(tokens, TOOL_ANSWER_EXCEPTION_MAX_TOKENS)
        short, _dropped, _cut = short_form(
            big,
            answer_tokens=tokens,
            log=None,
            log_error=None,
            count=count_response_tokens,
        )
        self.assertTrue(short["overflow"])
        self.assertEqual(short["code"], "started")
        self.assertLessEqual(count_response_tokens(short), TOOL_ANSWER_OVERFLOW_MAX_TOKENS)


class ToolAnswerOwnershipTests(unittest.TestCase):
    """Every current interim row names its exact rule-7 leaf; landed finals name none."""

    def test_implementation_matches_the_authored_roster(self) -> None:
        owners = authored_row_owners()
        mismatches = [
            tool for tool in TOOL_RESPONSE_MODELS if interim_leaf_for(tool) != owners.get(tool)
        ]
        self.assertEqual(mismatches, [])
        for tool in L110_ROW_DATA:
            self.assertIsNone(interim_leaf_for(tool), tool)
            _fields, _uses, bound, _paths = L110_ROW_DATA[tool]
            self.assertEqual(shaped_bound_for(tool), bound, tool)

    def test_removed_tools_have_no_row_and_no_owner(self) -> None:
        for tool in (
            "cgc_symbol_search",
            "cgc_callers",
            "cgc_callees",
            "cgc_dependencies",
            "cgc_complexity",
            "cgc_visualize",
            "grepai_search",
            "grepai_trace",
            "provider_status",
            "provider_diagnostics",
            "provider_watchers",
            "message_parent",
            "message_child",
            "rename_child",
            "rename_self",
            "lifecycle_start",
            "lifecycle_resume",
            "lifecycle_turn_end_notification",
            "lifecycle_end",
            "switch_lifecycle",
            "lifecycle_phase",
            "lifecycle_gate",
            "gate_decide",
            "gate_list",
            "ping",
            "role_capsule_compile",
            "skill_catalog_list",
            "skill_catalog_read",
            "codex_benchmark_prepare",
            "codex_benchmark_run",
            "skills_install",
            "citation_migrate",
            "drift_check",
        ):
            with self.subTest(tool=tool):
                self.assertNotIn((tool, None), TOOL_ANSWER_TABLE)
                self.assertNotIn(tool, authored_row_owners())
                self.assertIsNone(interim_leaf_for(tool))

    def test_exception_rows_name_their_leaf(self) -> None:
        self.assertEqual(interim_leaf_for("role_message"), "L111")
        # server_info is a landed L112 exception passthrough: bounded by the
        # rule-4 table, with no interim owner.
        self.assertIsNone(interim_leaf_for("server_info"))
