"""The tool-answer table and its tests (MIK-R108 rules 3, 4, 6, 7, 8).

Rule 6: the test suite has one table with a row for every registered tool,
and for every operation of a tool that has operations. A row holds the fields
the answer may hold and the bound. Rows whose leaf proves cases through live
tool runs name those real paths instead of carrying static payloads; rows
with static payloads hold an ordinary case and the largest case the tool's
own limits allow. The test fails when a registered tool has no row, when an
answer holds a field outside its row, or when a case is larger than the
bound. A leaf of this master that lands later and adds or changes a tool
adds or changes the row.

This leaf (L108) builds rules 1-6 and 8 and changes the envelope of every
answer. Until its leaf lands, a tool stands in the table as an interim row
(rule 7): the bound 4,000, no list of fields, and the name of its leaf. The
guard holds it to the bound.

MIK-R196 rule 10: the table's remaining cases live in the sibling
test_tool_answer_table_rows.py, _guards.py, _log.py, _measure.py,
_enclosure.py and _install.py modules, and the shared table and case
helpers live in tool_answer_table_support.py beside them. The cases this
module keeps are the ones the evidence lifecycle catalog names by node.
"""

from __future__ import annotations

import json
import os
import unittest

from agents_remember.application.task_docs.task_doc_tools import VALID_OPERATIONS
from agents_remember.models.tokens import count_response_tokens
from agents_remember.models.tools.tool_answer import (
    INTERIM_LEAVES,
    INTERIM_TOOL_LEAF,
    TOOL_ANSWER_EXCEPTION_MAX_TOKENS,
    TOOL_ANSWER_EXCEPTIONS,
    TOOL_ANSWER_LIST_MAX,
    bound_for,
    fields_outside_row,
)
from agents_remember.models.tools.tool_registry import TOOL_RESPONSE_MODELS
from tool_answer_l109_cases import (
    _produce_l109_knowledge_diff_largest,
    _produce_l109_knowledge_read_largest,
    _produce_l109_read_ar_files_largest,
)
from tool_answer_table_support import (
    _L109_FIXTURES,
    AUTHORED_INTERIM_OWNERS,
    L109_SHAPED_ROWS,
    ROW_CASE_FRAMEWORK_KEYS,
    TOOL_ANSWER_TABLE,
    authored_row_owners,
    check_tool_answer_roster,
    validate_row_case,
    validate_tool_answer_row,
)

# The two names this module's path keeps exporting for its sibling readers:
# test_legacy_refusal_envelope.py imports ROW_CASE_FRAMEWORK_KEYS from here and
# test_landing_state_refusals.py reads TOOL_ANSWER_TABLE through
# ``import test_tool_answer_table as _rows_table``. Both values now live in
# tool_answer_table_support.py, so this module re-exports the same objects.
__all__ = ["ROW_CASE_FRAMEWORK_KEYS", "TOOL_ANSWER_TABLE"]


class ToolAnswerTableTests(unittest.TestCase):
    def test_every_registered_tool_has_an_authored_row_and_no_extras(self) -> None:
        check_tool_answer_roster(set(TOOL_RESPONSE_MODELS), authored_row_owners())
        rowed = {tool for tool, _operation in TOOL_ANSWER_TABLE}
        self.assertEqual(rowed, set(TOOL_RESPONSE_MODELS))

    def test_missing_authored_row_fails(self) -> None:
        reduced = {
            tool: leaf for tool, leaf in authored_row_owners().items() if tool != "server_info"
        }
        with self.assertRaises(AssertionError):
            check_tool_answer_roster(set(TOOL_RESPONSE_MODELS), reduced)

    def test_task_doc_row_covers_every_operation(self) -> None:
        rowed = {operation for tool, operation in TOOL_ANSWER_TABLE if tool == "task_doc"}
        self.assertEqual(rowed, set(VALID_OPERATIONS))

    def test_every_row_passes_the_gates(self) -> None:
        failures = [
            failure
            for key, row in TOOL_ANSWER_TABLE.items()
            for failure in validate_tool_answer_row(key, row)
        ]
        self.assertEqual(failures, [])

    def test_no_bound_above_the_exception_max(self) -> None:
        for key, row in TOOL_ANSWER_TABLE.items():
            self.assertLessEqual(
                row.bound, TOOL_ANSWER_EXCEPTION_MAX_TOKENS, f"row {key} over 4,000"
            )

    def test_exception_table_first_rows(self) -> None:
        self.assertIn(("role_message", None), TOOL_ANSWER_EXCEPTIONS)
        self.assertIn(("server_info", None), TOOL_ANSWER_EXCEPTIONS)
        self.assertEqual(bound_for("role_message"), TOOL_ANSWER_EXCEPTION_MAX_TOKENS)
        self.assertEqual(bound_for("server_info"), TOOL_ANSWER_EXCEPTION_MAX_TOKENS)
        for key, (bound, _reason) in TOOL_ANSWER_EXCEPTIONS.items():
            self.assertLessEqual(bound, TOOL_ANSWER_EXCEPTION_MAX_TOKENS, f"{key}")

    def test_interim_rows_hold_the_bound_and_name_a_leaf(self) -> None:
        for key, row in TOOL_ANSWER_TABLE.items():
            if key in TOOL_ANSWER_EXCEPTIONS or row.fields is not None:
                continue
            if row.fields is not None:
                continue  # landed shaped rows (L110, L111, L112 finals) hold their own bound
            self.assertEqual(row.bound, TOOL_ANSWER_EXCEPTION_MAX_TOKENS, f"row {key}")
            self.assertIsNone(row.fields, f"row {key}")
            self.assertIn(row.interim_leaf, INTERIM_LEAVES, f"row {key}")

    def test_curator_coherence_row_is_shaped(self) -> None:
        # MIK-R110 rule 3: the first row whose leaf landed is shaped, with fields, uses,
        # named production paths and no interim owner.
        row = TOOL_ANSWER_TABLE[("curator_coherence", None)]
        self.assertIsNone(row.interim_leaf)
        self.assertIsNotNone(row.fields)
        self.assertEqual(
            [failure for failure in validate_tool_answer_row(("curator_coherence", None), row)],
            [],
        )

    def test_each_later_leaf_still_owns_rows(self) -> None:
        # A leaf does not land while a row names it: only L100 still
        # owns interim rows. L109 has landed (MIK-R109 rules 1-4), L110 has
        # landed (MIK-R110 rule 6) and L112 has landed (MIK-R112 rule 9); each
        # owns none, and the next tests say so for each. L111's leaf is still
        # open, but every roster row it names is shaped by its own applier, so
        # no row names it either (the shaped-rows tests say so).
        owners = {row.interim_leaf for row in TOOL_ANSWER_TABLE.values()}
        self.assertIn("L100", owners, "L100 owns no row")
        for leaf in ("L109", "L110", "L111", "L112"):
            self.assertNotIn(leaf, owners, f"{leaf} still owns a row")

    def test_no_row_names_l112(self) -> None:
        # MIK-R112 rule 9: L112 has landed. No interim row names it -- not
        # in the authored roster, not in the built table, and not in the
        # implementation's interim map.
        self.assertNotIn("L112", set(AUTHORED_INTERIM_OWNERS.values()))
        self.assertNotIn("L112", {row.interim_leaf for row in TOOL_ANSWER_TABLE.values()})
        self.assertNotIn("L112", set(INTERIM_TOOL_LEAF.values()))

    def test_no_built_row_names_the_landed_l110(self) -> None:
        # MIK-R110 rule 6: L110 has landed. No row names it -- not in the
        # authored roster, not in the built table (interim or shaped), and
        # not in the implementation's interim map.
        self.assertNotIn("L110", set(AUTHORED_INTERIM_OWNERS.values()))
        named = sorted(
            f"{tool}:{operation}"
            for (tool, operation), row in TOOL_ANSWER_TABLE.items()
            if row.interim_leaf == "L110"
        )
        self.assertEqual(named, [])

    def test_no_row_names_l109(self) -> None:
        # MIK-R109 rules 1-4: L109 has landed. No interim row names it -- not
        # in the authored roster, not in the built table, and not in the
        # implementation's interim map -- and its four rebuilt reads hold
        # final shaped rows in the table with fields, uses and both cases.
        self.assertNotIn("L109", set(AUTHORED_INTERIM_OWNERS.values()))
        self.assertNotIn("L109", {row.interim_leaf for row in TOOL_ANSWER_TABLE.values()})
        self.assertNotIn("L109", set(INTERIM_TOOL_LEAF.values()))
        self.assertNotIn("L109", INTERIM_LEAVES)
        for tool in (
            "read_ar_files",
            "knowledge_read",
            "knowledge_diff",
            "context_packet",
        ):
            with self.subTest(tool=tool):
                row = TOOL_ANSWER_TABLE[(tool, None)]
                self.assertIsNone(row.interim_leaf, f"{tool} still names an owner")
                self.assertIsNotNone(row.fields, f"{tool} has no shaped fields")
                self.assertTrue(row.uses, f"{tool} has no field uses")
                self.assertIsNotNone(row.ordinary_case, f"{tool} has no ordinary case")
                self.assertIsNotNone(row.largest_case, f"{tool} has no largest case")

    def test_l109_shaped_rows_are_the_table_rows(self) -> None:
        # L109 has landed, so the shaped rows below (fields, bounds,
        # operation-produced cases) are the table rows, not side evidence.
        # Each holds the row's field contract, a measured bound, and both
        # cases; the largest case carries no overflow fallback.
        for tool in (
            "read_ar_files",
            "knowledge_read",
            "knowledge_diff",
            "context_packet",
        ):
            with self.subTest(tool=tool):
                row = TOOL_ANSWER_TABLE[(tool, None)]
                self.assertIs(L109_SHAPED_ROWS[(tool, None)], row)
                failures = validate_row_case(
                    (tool, None), row, "largest case", dict(row.largest_case or {})
                )
                self.assertEqual(failures, [], f"{tool} largest case fails its gates")
                largest = dict(row.largest_case or {})
                self.assertNotEqual(largest.get("overflow"), True, f"{tool} largest case overflows")

    def test_field_allowlist_mechanism(self) -> None:
        self.assertEqual(fields_outside_row({"ok": True, "x": 1}, None), set())
        self.assertEqual(
            fields_outside_row({"ok": True, "next": {}, "log": "f", "state": "s"}, {"state"}),
            set(),
        )
        self.assertEqual(
            fields_outside_row({"ok": True, "state": "s", "rogue": 1}, {"state"}),
            {"rogue"},
        )


class L109LargestProductionTests(unittest.TestCase):
    """MIK-R109 F01: the three largest cases are operation-produced and pinned.

    Each producer rebuilds its largest answer from a hermetic fixture and
    the test pins it byte-for-byte against its file; L109_REGEN=1 rewrites
    the files instead of asserting. The pins carry no overflow fallback:
    rowsOnPage counts the delivered rows, decisionRecordCount the named
    records beside their total, and every pin measures within its row bound.
    """

    def _pinned(self, name: str, produced: str) -> None:
        path = _L109_FIXTURES / f"{name}.json"
        if os.environ.get("L109_REGEN") == "1":
            path.write_text(produced, encoding="utf-8")
        self.assertEqual(
            produced,
            path.read_text(encoding="utf-8"),
            f"{name} is not operation-produced",
        )

    def test_read_ar_files_largest_is_operation_produced(self) -> None:
        produced, states = _produce_l109_read_ar_files_largest()
        self.assertEqual(states, {"current": True}, states)
        case = json.loads(produced)
        self.assertEqual(case["state"], "named", case)
        self.assertEqual(len(case["files"]), 20, case)
        self.assertNotEqual(case.get("overflow"), True, case)
        self.assertLessEqual(count_response_tokens(case), bound_for("read_ar_files"))
        self._pinned("read_ar_files_largest", produced)

    def test_knowledge_read_largest_is_operation_produced(self) -> None:
        produced = _produce_l109_knowledge_read_largest()
        case = json.loads(produced)
        self.assertEqual(case["state"], "page", case)
        self.assertNotEqual(case.get("overflow"), True, case)
        rows = case["payload"]["rows"]
        self.assertEqual(case["page"]["rowsOnPage"], len(rows), case)
        self.assertLessEqual(count_response_tokens(case), bound_for("knowledge_read"))
        self._pinned("knowledge_read_largest", produced)

    def test_knowledge_diff_largest_is_operation_produced(self) -> None:
        produced = _produce_l109_knowledge_diff_largest()
        case = json.loads(produced)
        self.assertEqual(case["state"], "compared", case)
        self.assertNotEqual(case.get("overflow"), True, case)
        self.assertLessEqual(len(case["decisionRecords"]), TOOL_ANSWER_LIST_MAX, case)
        self.assertLessEqual(count_response_tokens(case), bound_for("knowledge_diff"))
        self._pinned("knowledge_diff_largest", produced)


if __name__ == "__main__":
    unittest.main()
