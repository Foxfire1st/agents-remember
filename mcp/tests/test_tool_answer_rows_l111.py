"""MIK-R111 shaped-row tests (Expected Evidence 6).

Extracted from test_tool_answer_table.py under the file-size rail (F01.19).
"""

from __future__ import annotations

import unittest

from agents_remember.models.tools.tool_answer import L111_SHAPED_TOOLS
from test_tool_answer_table import TOOL_ANSWER_TABLE


class ToolAnswerL111ShapedRowsTests(unittest.TestCase):
    """MIK-R111 Expected Evidence 6: shaped rows for the packet tools, no interim row."""

    def test_no_interim_row_names_l111(self) -> None:
        # L111 has landed: every owned tool carries a shaped row, none an
        # interim one.
        named = [key for key, row in TOOL_ANSWER_TABLE.items() if row.interim_leaf == "L111"]
        self.assertEqual(named, [])

    def test_every_l111_tool_has_a_shaped_row(self) -> None:
        for tool in sorted(L111_SHAPED_TOOLS):
            keys = [key for key in TOOL_ANSWER_TABLE if key[0] == tool]
            self.assertTrue(keys, f"{tool} has no row")
            for key in keys:
                row = TOOL_ANSWER_TABLE[key]
                with self.subTest(row=key):
                    self.assertIsNone(row.interim_leaf)
                    self.assertIsNotNone(row.fields)
                    self.assertTrue(row.uses)
                    self.assertIsNotNone(row.ordinary_case)
                    self.assertIsNotNone(row.largest_case)


if __name__ == "__main__":
    unittest.main()
