"""Precision and recall for the three memory-document checks 260731-EFA-L6 added.

The `PrecisionFixtures` case is the point of this file. Every string in it is COPIED FROM
A LIVE DOCUMENT in the external memory tree, named in the test, and every one of them must
produce zero findings -- so the precision of these checks is a regression suite rather than
a claim. `ClosingDiffScopeTests` is the other half: it builds a real memory repository and
asserts what the offset rule does and does not reach.
"""

from __future__ import annotations

import contextlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.memory_quality.check import run_memory_quality_check
from agents_remember.memory_quality.style import changed_lines as changed_lines_module
from agents_remember.memory_quality.style.document_shape import diff_markers, inline_scan, tables
from agents_remember.memory_quality.style.update_history import history_order, history_order_fix

# Verbatim from onboarding/dashboard/src/panels/session-cockpit/SessionsView.test.tsx.md
# and onboarding/mcp/src/agents_remember/observer/reducer.py.md. Both are correct prose
# whose first character is '+'. Flagging either is what gets this check switched off.
LEGITIMATE_PLUS_LINES = (
    "+3 integration cases from 260715-FEUI-L2; +7 from 260715-FEUI-L6 incl. the fix round; +2",
    "+enclosure/repoId from the envelope), `_ended_updates` (L405-L407 - the ONE way",
)
# Verbatim from onboarding/mcp/tests/test_packaged_assets_and_context_values.py.md line 30.
# The code span ends in a backslash, so its closing backtick is preceded by one.
WINDOWS_PREFIX_ROW = (
    "| `LongPathTests` | `long_path` prefixes Windows paths with the "
    "`\\\\?\\` extended-length marker. |"
)
# Verbatim from onboarding/dashboard/src/panels/EngineRoom.tsx.md line 91: escaped pipes
# in prose, BETWEEN code spans.
ESCAPED_PIPE_OUTSIDE_SPAN_ROW = (
    "| §4.2 3-zone room (`Panel fill` -> `roomShell` -> header + `roomGrid` "
    "[stack \\| `roomStage` \\| `roomZone`]). | - | [panels/EngineRoom.tsx](EngineRoom.tsx) |"
)
# Verbatim from onboarding/dashboard/src/dev/benchProbes.ts.md line 40: escaped pipes
# INSIDE a code span, which is a different rule reaching the same answer.
ESCAPED_PIPE_INSIDE_SPAN_ROW = (
    '| `CockpitBenchTransition` | `"launch-failures" \\| "set-turn-ended" \\| '
    '"defer-next-open" \\| "release-open"` - the steps a driver can drive a scenario '
    "through mid-test |"
)
# Verbatim from onboarding/dashboard/src/panels/RailChat.tsx.md line 89, wrapped into a
# table cell: a double-backtick span quoting text that itself contains backticks.
MULTI_BACKTICK_ROW = (
    "| `Pane` | pass ``ariaLabel={`terminal: ${session.label}`}`` so each pane's "
    '`role="group"` landmark is named |'
)
# Verbatim from onboarding/dashboard/src/panels/overview.md lines 708-709 -- the pair the
# old naive-as-UTC comparison read as correctly ordered.
LIVE_MIXED_FRAME_PAIR = (
    "- 2026-06-19T05:48 - Task 6 slice 6e-3: the Chats view gained a `SessionComposer`.",
    "- 2026-06-19T06:39+02:00 - No route impact: an engine-room crash fix guards a read.",
)


def write(root: Path, name: str, body: str) -> Path:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body if body.endswith("\n") else body + "\n", encoding="utf-8")
    return path


def document(*lines: str) -> str:
    return "\n".join(("# Example", "", *lines, ""))


def history_document(*bullets: str) -> str:
    return "\n".join(("# Example", "", "## Update History", "", *bullets, ""))


def table_document(*rows: str) -> str:
    return document("| Class | Unit |", "| --- | --- |", *rows)


class PrecisionFixtures(unittest.TestCase):
    """Known-good constructs, copied from live memory documents, that must not be flagged."""

    def assert_clean(self, body: str) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir) / "onboarding"
            write(root, "example.md", body)
            result = run_memory_quality_check(root)
            self.assertTrue(result["ok"], result["findings"])
            self.assertEqual(result["findingCount"], 0, result["findings"])

    def test_a_plus_with_no_space_and_no_hash_is_prose(self) -> None:
        self.assert_clean(document(*LEGITIMATE_PLUS_LINES))

    def test_code_span_ending_in_a_backslash_does_not_swallow_the_row(self) -> None:
        self.assert_clean(table_document(WINDOWS_PREFIX_ROW))
        # The cell CONTENTS, not just the count. In the live document this cell happens to
        # be the row's last, so the wrong scan order merges the trailing pipe into it and
        # still arrives at two cells -- the count alone does not see the bug.
        self.assertEqual(
            inline_scan.split_row(WINDOWS_PREFIX_ROW),
            [
                "`LongPathTests`",
                "`long_path` prefixes Windows paths with the `\\\\?\\` extended-length marker.",
            ],
        )

    def test_the_same_windows_cell_in_a_three_column_table_keeps_its_columns(self) -> None:
        """The tree carries both table shapes; in the wider one the merge is a ragged row.

        ``| Finding | Citations | Source Path |`` is the three-column shape used across
        ``dashboard/`` onboarding. Put the live two-column row's Windows cell in the middle
        of it and the wrong scan order eats the rest of the line, turning a correct row
        into a reported defect -- which is the false positive that decided this check.
        """
        row = WINDOWS_PREFIX_ROW + " L12-L20 |"
        self.assertEqual(len(inline_scan.split_row(row)), 3)
        self.assert_clean(document("| Class | Unit | Citations |", "| --- | --- | --- |", row))

    def test_escaped_pipe_outside_a_code_span_is_not_a_divider(self) -> None:
        self.assert_clean(
            document(
                "| Finding | Citations | Source Path |",
                "| --- | --- | --- |",
                ESCAPED_PIPE_OUTSIDE_SPAN_ROW,
            )
        )
        self.assertEqual(len(inline_scan.split_row(ESCAPED_PIPE_OUTSIDE_SPAN_ROW)), 3)

    def test_escaped_pipe_inside_a_code_span_is_not_a_divider(self) -> None:
        self.assert_clean(table_document(ESCAPED_PIPE_INSIDE_SPAN_ROW))
        self.assertEqual(len(inline_scan.split_row(ESCAPED_PIPE_INSIDE_SPAN_ROW)), 2)

    def test_multi_backtick_span_is_one_span(self) -> None:
        self.assert_clean(table_document(MULTI_BACKTICK_ROW))
        self.assertEqual(len(inline_scan.split_row(MULTI_BACKTICK_ROW)), 2)

    def test_a_diff_quoted_in_a_fenced_block_is_the_document_working(self) -> None:
        self.assert_clean(document("```diff", "+## Contract", "+ wrapped continuation", "```"))

    def test_a_ragged_table_quoted_in_a_fenced_block_is_not_read(self) -> None:
        self.assert_clean(document("```markdown", "| a | b |", "| - | - |", "| 1 | 2 | 3 |", "```"))

    def test_prose_with_pipes_and_no_delimiter_row_is_not_a_table(self) -> None:
        self.assert_clean(
            document(
                'The union is `"launch" | "reset" | "release"` and the shell form is',
                "`docker exec x tar -cf - models | docker exec -i y tar -xf -`, both prose.",
            )
        )

    def test_an_offset_bearing_history_is_clean_and_reports_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir) / "onboarding"
            write(
                root,
                "example.md",
                history_document(
                    "- 2026-05-24T00:37+02:00: Newest.",
                    "- 2026-05-23T22:10+02:00: Older.",
                ),
            )
            result = run_memory_quality_check(root)
            self.assertTrue(result["ok"])
            self.assertEqual(result["findingCount"], 0)


class InlineScanTests(unittest.TestCase):
    """The ordering rule itself: code spans located before escapes are applied."""

    def test_a_backslash_never_suppresses_a_backtick_run(self) -> None:
        self.assertEqual(inline_scan.code_span_ranges("`\\\\?\\`"), [(0, 6)])

    def test_an_unmatched_backtick_run_opens_nothing(self) -> None:
        self.assertEqual(inline_scan.code_span_ranges("a ` b"), [])

    def test_a_run_matches_only_an_equal_length_run(self) -> None:
        self.assertEqual(inline_scan.code_span_ranges("``a`b``"), [(0, 7)])

    def test_a_pipe_inside_a_code_span_is_not_a_divider(self) -> None:
        self.assertEqual(inline_scan.split_row("| `a|b` | c |"), ["`a|b`", "c"])

    def test_gfm_drops_one_leading_and_one_trailing_pipe(self) -> None:
        self.assertEqual(inline_scan.split_row("| a | b |"), ["a", "b"])
        self.assertEqual(inline_scan.split_row("a | b"), ["a", "b"])
        self.assertEqual(inline_scan.split_row("| a | b ||"), ["a", "b", ""])

    def test_a_longer_fence_is_not_closed_by_a_shorter_one(self) -> None:
        lines = ["````markdown", "```", "| a |", "````", "after"]
        self.assertEqual(inline_scan.unfenced_lines(lines), [(4, "after")])


class DiffMarkerTests(unittest.TestCase):
    def check(self, body: str) -> dict:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir) / "onboarding"
            write(root, "example.md", body)
            return diff_markers.check_onboarding_root(root)

    def test_a_leading_plus_hash_is_reported_as_a_broken_heading(self) -> None:
        result = self.check(document("+## Contract"))
        self.assertFalse(result["ok"])
        self.assertEqual(result["findingCount"], 1)
        finding = result["findings"][0]
        self.assertEqual(finding["code"], "leaked_diff_marker_heading")
        self.assertEqual(finding["path"], "example.md")
        self.assertEqual(finding["line"], 3)
        self.assertIn("renders as literal text", finding["message"])

    def test_a_leading_plus_space_is_reported_as_a_spurious_bullet(self) -> None:
        result = self.check(document("A sentence wrapped at a conjunction", "+ and its tail."))
        self.assertFalse(result["ok"])
        self.assertEqual(result["findings"][0]["code"], "leaked_diff_marker_bullet")
        self.assertEqual(result["findings"][0]["line"], 4)
        self.assertIn("rejoin", result["findings"][0]["message"])

    def test_an_indented_plus_is_an_ordinary_nested_bullet(self) -> None:
        self.assertTrue(self.check(document("- parent", "  + child"))["ok"])


class TableTests(unittest.TestCase):
    def check(self, body: str) -> dict:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir) / "onboarding"
            write(root, "example.md", body)
            return tables.check_onboarding_root(root)

    def test_an_extra_cell_is_reported_against_the_header(self) -> None:
        result = self.check(
            document(
                "| Finding | Source Path |",
                "| --- | --- |",
                "| Ordinary row. | [a.py](a.py) |",
                "| Row carrying a citation column the header lacks. | L12-L20 | [a.py](a.py) |",
            )
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["findingCount"], 1)
        finding = result["findings"][0]
        self.assertEqual(finding["code"], "table_row_cell_count_mismatch")
        self.assertEqual(finding["line"], 6)
        self.assertEqual(finding["previousLine"], 3)
        self.assertIn("3 cells", finding["message"])
        self.assertIn("header (line 3) has 2", finding["message"])
        self.assertIn("LOSING DATA", finding["message"])

    def test_a_missing_cell_is_reported(self) -> None:
        result = self.check(table_document("| only one |"))
        self.assertEqual(result["findings"][0]["code"], "table_row_cell_count_mismatch")
        self.assertIn("1 cells", result["findings"][0]["message"])
        self.assertIn("Nothing is lost", result["findings"][0]["message"])

    def test_a_delimiter_row_of_the_wrong_width_is_not_a_table(self) -> None:
        self.assertTrue(self.check(document("| a | b |", "| --- |", "| 1 | 2 | 3 |"))["ok"])

    def test_a_table_stops_at_a_blank_line(self) -> None:
        self.assertTrue(self.check(table_document("| a | b |", "", "prose | with a pipe"))["ok"])

    def test_a_second_table_in_the_same_file_is_read(self) -> None:
        result = self.check(
            document(
                "| a | b |",
                "| - | - |",
                "| 1 | 2 |",
                "",
                "| c | d |",
                "| - | - |",
                "| 3 | 4 | 5 |",
            )
        )
        self.assertEqual(result["findingCount"], 1)
        self.assertEqual(result["findings"][0]["line"], 9)


class RemediationTests(unittest.TestCase):
    """The remediation must be complete, because it is followed.

    An earlier draft told a curator to widen the header. Applied literally to the four
    documents this check found, that would have repaired 7 long rows and broken the 19
    short rows sharing those tables -- a net regression produced by obeying the check.
    """

    def check(self, body: str) -> dict:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir) / "onboarding"
            write(root, "example.md", body)
            return tables.check_onboarding_root(root)

    def test_the_long_row_remediation_names_all_three_edits(self) -> None:
        message = tables.ragged_message(found=3, expected=2, header_line=3)
        self.assertIn("widen the header row", message)
        self.assertIn("widen the DELIMITER row", message)
        self.assertIn("pad every SHORT row", message)
        self.assertIn(tables.NO_CITATION_MARKER, message)
        self.assertIn("never leave the cell empty", tables.ragged_message(1, 2, 3))

    def test_the_message_says_which_direction_destroys_information(self) -> None:
        self.assertIn("LOSING DATA", tables.ragged_message(found=3, expected=2, header_line=3))
        self.assertIn("Nothing is lost", tables.ragged_message(found=1, expected=2, header_line=3))

    def test_widening_the_header_alone_is_the_regression_the_message_prevents(self) -> None:
        # The shape of the four real documents: one long row among several short ones.
        before = document(
            "| Finding | Source Path |",
            "| --- | --- |",
            "| Short row one. | [a.py](a.py) |",
            "| Short row two. | [b.py](b.py) |",
            "| Row carrying a citation. | L12-L20 | [c.py](c.py) |",
        )
        self.assertEqual(self.check(before)["findingCount"], 1)

        header_only = document(
            "| Finding | Citations | Source Path |",
            "| --- | --- |",
            "| Short row one. | [a.py](a.py) |",
            "| Short row two. | [b.py](b.py) |",
            "| Row carrying a citation. | L12-L20 | [c.py](c.py) |",
        )
        # Header widened, delimiter not: GFM stops seeing a table, so the check stops
        # seeing one too and reports NOTHING while the document renders as prose.
        self.assertEqual(self.check(header_only)["findingCount"], 0)

        both_rows = document(
            "| Finding | Citations | Source Path |",
            "| --- | --- | --- |",
            "| Short row one. | [a.py](a.py) |",
            "| Short row two. | [b.py](b.py) |",
            "| Row carrying a citation. | L12-L20 | [c.py](c.py) |",
        )
        # Header and delimiter widened, short rows not padded: one defect became two.
        self.assertEqual(self.check(both_rows)["findingCount"], 2)

        complete = document(
            "| Finding | Citations | Source Path |",
            "| --- | --- | --- |",
            f"| Short row one. | {tables.NO_CITATION_MARKER} | [a.py](a.py) |",
            f"| Short row two. | {tables.NO_CITATION_MARKER} | [b.py](b.py) |",
            "| Row carrying a citation. | L12-L20 | [c.py](c.py) |",
        )
        self.assertTrue(self.check(complete)["ok"])


class UpdateHistoryTimezoneTests(unittest.TestCase):
    """The offset rule, and the closeout diff that scopes it."""

    def check(self, *bullets: str) -> dict:
        """An unversioned root: no HEAD, so nothing is historical and every line is in scope."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir) / "onboarding"
            write(root, "example.md", history_document(*bullets))
            return history_order.check_onboarding_root(root)

    def test_a_naive_timestamp_fails_the_check(self) -> None:
        result = self.check("- 2026-06-19T05:48: No offset.")
        self.assertFalse(result["ok"])
        self.assertEqual(result["findingCount"], 1)
        finding = result["findings"][0]
        self.assertEqual(finding["code"], "update_history_timestamp_naive")
        self.assertEqual(finding["line"], 5)
        self.assertEqual(finding["timestamp"], "2026-06-19T05:48")
        self.assertIn("+02:00", finding["message"])

    def test_the_live_mixed_frame_pair_is_caught_instead_of_passing_silently(self) -> None:
        result = self.check(*LIVE_MIXED_FRAME_PAIR)
        self.assertFalse(result["ok"])
        codes = [finding["code"] for finding in result["findings"]]
        self.assertEqual(codes, ["update_history_timestamp_naive"])
        self.assertEqual(result["findings"][0]["timestamp"], "2026-06-19T05:48")

    def test_ordering_between_two_naive_stamps_is_still_enforced(self) -> None:
        result = self.check(
            "- 2026-06-19T05:48: Older, on top.",
            "- 2026-06-19T09:00: Newer, below it.",
        )
        codes = sorted(finding["code"] for finding in result["findings"])
        self.assertEqual(
            codes,
            [
                "update_history_not_newest_first",
                "update_history_timestamp_naive",
                "update_history_timestamp_naive",
            ],
        )

    def test_ordering_between_two_offset_stamps_survives_a_naive_one_between_them(self) -> None:
        result = self.check(
            "- 2026-06-19T06:39+02:00: Oldest of the offset pair.",
            "- 2026-06-19T05:48: Naive, in between.",
            "- 2026-06-19T09:00+02:00: Newer than the top entry.",
        )
        order = [f for f in result["findings"] if f["code"] == "update_history_not_newest_first"]
        self.assertEqual(len(order), 1)
        self.assertEqual(order[0]["previousTimestamp"], "2026-06-19T06:39+02:00")

    def test_the_two_existing_refusals_are_unchanged(self) -> None:
        missing = self.check("- Missing timestamp.")
        self.assertEqual(missing["findings"][0]["code"], "update_history_timestamp_missing")
        invalid = self.check("- 2026-13-45T99:99+02:00: Not a datetime.")
        self.assertEqual(invalid["findings"][0]["code"], "update_history_timestamp_invalid")

    def test_zulu_counts_as_a_stated_offset(self) -> None:
        self.assertTrue(self.check("- 2026-06-19T05:48Z: UTC.")["ok"])

    def test_the_fixer_refuses_to_sort_a_section_that_mixes_frames(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir) / "onboarding"
            path = write(root, "example.md", history_document(*LIVE_MIXED_FRAME_PAIR))
            before = path.read_text(encoding="utf-8")
            result = history_order_fix.fix_onboarding_root(root)
            self.assertFalse(result["ok"])
            self.assertEqual(result["skippedFiles"], ["example.md"])
            self.assertEqual(result["changedFiles"], [])
            self.assertEqual(path.read_text(encoding="utf-8"), before)

    def test_the_fixer_still_sorts_a_section_written_in_one_frame(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir) / "onboarding"
            path = write(
                root,
                "example.md",
                history_document("- 2026-06-19T05:48: Older.", "- 2026-06-19T09:00: Newer."),
            )
            result = history_order_fix.fix_onboarding_root(root)
            self.assertTrue(result["ok"])
            self.assertEqual(result["changedFiles"], ["example.md"])
            text = path.read_text(encoding="utf-8")
            self.assertLess(text.index("Newer"), text.index("Older"))


class ClosingDiffScopeTests(unittest.TestCase):
    """The rule applies to what this closeout wrote, and to nothing else.

    Each case builds the real thing the gate stands in: a memory repository whose
    Update History is already committed, then edited the way a closeout edits it.
    """

    def git(self, repo: Path, *args: str) -> None:
        result = subprocess.run(
            ["git", *args], cwd=repo, text=True, capture_output=True, check=False
        )
        if result.returncode != 0:
            raise AssertionError(result.stderr or result.stdout)

    def memory_repo(self, tmp_dir: str, *bullets: str) -> Path:
        repo = Path(tmp_dir) / "memory"
        root = repo / "onboarding"
        write(root, "example.md", history_document(*bullets))
        self.git(repo, "init", "-q")
        self.git(repo, "config", "user.email", "l6@example.invalid")
        self.git(repo, "config", "user.name", "L6")
        self.git(repo, "add", "-A")
        self.git(repo, "commit", "-qm", "history as it already stands")
        return root

    def test_naive_stamps_already_committed_are_left_alone(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = self.memory_repo(
                tmp_dir,
                "- 2026-06-19T09:00: Historical, nobody alive knows the frame.",
                "- 2026-06-19T05:48: Historical too.",
            )
            result = history_order.check_onboarding_root(root)
            self.assertTrue(result["ok"], result["findings"])
            self.assertEqual(result["findingCount"], 0)

    def test_a_naive_bullet_this_closeout_adds_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = self.memory_repo(tmp_dir, "- 2026-06-19T05:48: Historical.")
            write(
                root,
                "example.md",
                history_document(
                    "- 2026-06-20T11:00: Added by this closeout, no offset.",
                    "- 2026-06-19T05:48: Historical.",
                ),
            )
            result = history_order.check_onboarding_root(root)
            self.assertFalse(result["ok"])
            self.assertEqual(
                [(f["code"], f["line"]) for f in result["findings"]],
                [("update_history_timestamp_naive", 5)],
            )

    def test_the_same_bullet_with_an_offset_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = self.memory_repo(tmp_dir, "- 2026-06-19T05:48: Historical.")
            write(
                root,
                "example.md",
                history_document(
                    "- 2026-06-20T11:00+02:00: Added by this closeout.",
                    "- 2026-06-19T05:48: Historical.",
                ),
            )
            result = history_order.check_onboarding_root(root)
            self.assertTrue(result["ok"], result["findings"])

    def test_editing_an_untouched_bullet_pulls_it_into_scope(self) -> None:
        """The grant is zero, not N: any edit to a historical line makes the rule apply."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = self.memory_repo(tmp_dir, "- 2026-06-19T05:48: Historical.")
            write(root, "example.md", history_document("- 2026-06-19T05:48: Reworded."))
            result = history_order.check_onboarding_root(root)
            self.assertFalse(result["ok"])
            self.assertEqual(result["findings"][0]["code"], "update_history_timestamp_naive")

    def test_a_file_git_has_never_seen_is_new_in_full(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = self.memory_repo(tmp_dir, "- 2026-06-19T05:48: Historical.")
            write(root, "added.md", history_document("- 2026-06-20T11:00: No offset."))
            result = history_order.check_onboarding_root(root)
            self.assertEqual(
                [(f["path"], f["code"]) for f in result["findings"]],
                [("added.md", "update_history_timestamp_naive")],
            )

    def test_a_staged_edit_is_in_scope_as_much_as_an_unstaged_one(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = self.memory_repo(tmp_dir, "- 2026-06-19T05:48: Historical.")
            write(
                root,
                "example.md",
                history_document(
                    "- 2026-06-20T11:00: Staged, still no offset.",
                    "- 2026-06-19T05:48: Historical.",
                ),
            )
            self.git(root.parent, "add", "-A")
            result = history_order.check_onboarding_root(root)
            self.assertFalse(result["ok"])
            self.assertEqual(result["findings"][0]["code"], "update_history_timestamp_naive")

    def test_a_renamed_document_is_not_treated_as_newly_written(self) -> None:
        """A pure rename contributes no newly written lines."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = self.memory_repo(tmp_dir, "- 2026-06-19T05:48: Historical.")
            self.git(root.parent, "mv", "onboarding/example.md", "onboarding/moved.md")
            result = history_order.check_onboarding_root(root)
            self.assertTrue(result["ok"], result["findings"])

    def test_ordering_is_enforced_across_the_whole_file_regardless_of_the_diff(self) -> None:
        """Scope narrows the offset rule only. A history out of order is out of order."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = self.memory_repo(
                tmp_dir,
                "- 2026-06-19T05:48+02:00: Older, on top.",
                "- 2026-06-19T09:00+02:00: Newer, below it.",
            )
            result = history_order.check_onboarding_root(root)
            self.assertFalse(result["ok"])
            self.assertEqual(result["findings"][0]["code"], "update_history_not_newest_first")


class StyleSurfaceTests(unittest.TestCase):
    def test_every_style_check_runs_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir) / "onboarding"
            write(root, "example.md", history_document("- 2026-06-19T05:48+02:00: Clean."))
            result = run_memory_quality_check(root)
            self.assertTrue(result["ok"], result["findings"])
            self.assertEqual(result["findingCount"], 0)
            self.assertEqual(
                sorted(result["checks"]),
                [
                    # The two citation checks have no code tree without drift context and
                    # say so in their own blocks rather than contributing a finding.
                    "style.citations.claim_reopen",
                    "style.citations.range_resolution",
                    "style.document_shape.diff_markers",
                    "style.document_shape.entity_catalog_alignment",
                    "style.document_shape.tables",
                    "style.update_history.history_order",
                ],
            )

    def test_findings_from_every_check_reach_one_aggregated_list(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir) / "onboarding"
            write(root, "shape.md", document("+## Broken", "| a | b |", "| - | - |", "| 1 |"))
            write(root, "order.md", history_document("- 2026-06-19T05:48: Naive."))
            result = run_memory_quality_check(root)
            self.assertFalse(result["ok"])
            self.assertEqual(
                sorted(finding["code"] for finding in result["findings"]),
                [
                    "leaked_diff_marker_heading",
                    "table_row_cell_count_mismatch",
                    "update_history_timestamp_naive",
                ],
            )


class DefensiveBranchTests(unittest.TestCase):
    """The guards that keep a sweep from crashing on a tree that is not what it expects."""

    def git(self, repo: Path, *args: str) -> None:
        result = subprocess.run(
            ["git", *args], cwd=repo, text=True, capture_output=True, check=False
        )
        if result.returncode != 0:
            raise AssertionError(result.stderr or result.stdout)

    def test_a_repository_with_no_commit_yet_has_no_history_to_diff_against(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir) / "onboarding"
            write(root, "example.md", history_document("- 2026-06-19T05:48: Naive."))
            self.git(root.parent, "init", "-q")
            scope = changed_lines_module.changed_lines(root)
            self.assertFalse(scope.versioned)
            # ... so the rule applies in full rather than switching itself off.
            self.assertTrue(scope.covers(root / "example.md", 5))

    def test_a_relative_root_scopes_to_the_same_lines_as_its_absolute_spelling(self) -> None:
        """Path spelling must not widen the changed-line population."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            repo = Path(tmp_dir) / "memory"
            root = repo / "onboarding"
            write(root, "historical.md", history_document("- 2026-06-19T05:48+02:00: Recorded."))
            self.git(repo, "init", "-q")
            self.git(repo, "config", "user.email", "l6@example.invalid")
            self.git(repo, "config", "user.name", "L6")
            self.git(repo, "add", "-A")
            self.git(repo, "commit", "-qm", "before")
            write(root, "written-now.md", history_document("- 2026-06-20T06:00+02:00: Added."))

            with contextlib.chdir(tmp_dir):
                here = Path.cwd()
                absolute = changed_lines_module.changed_lines(here / "memory" / "onboarding")
                relative = changed_lines_module.changed_lines(Path("memory") / "onboarding")

                self.assertTrue(absolute.versioned)
                self.assertTrue(
                    relative.versioned,
                    "a relative root reported no history, so covers() answers True for the "
                    "whole tree and the diff scope has switched itself off",
                )
                self.assertEqual(relative.lines, absolute.lines)
                # The half that fails loudly if the anchoring is removed: an untouched
                # historical row must stay out of scope through EITHER spelling.
                self.assertFalse(relative.covers(here / "memory/onboarding/historical.md", 1))
                self.assertTrue(relative.covers(here / "memory/onboarding/written-now.md", 1))

    def test_git_failing_contributes_nothing_rather_than_raising(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            not_a_repository = Path(tmp_dir)
            lines: dict[Path, set[int]] = {}
            changed_lines_module.collect_diff_lines(not_a_repository, not_a_repository, lines)
            changed_lines_module.collect_untracked_lines(not_a_repository, not_a_repository, lines)
            self.assertEqual(lines, {})

    def test_a_deleted_file_has_no_new_side_to_attribute_lines_to(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            repo = Path(tmp_dir) / "memory"
            root = repo / "onboarding"
            write(root, "gone.md", history_document("- 2026-06-19T05:48+02:00: Recorded."))
            self.git(repo, "init", "-q")
            self.git(repo, "config", "user.email", "l6@example.invalid")
            self.git(repo, "config", "user.name", "L6")
            self.git(repo, "add", "-A")
            self.git(repo, "commit", "-qm", "before")
            (root / "gone.md").unlink()
            scope = changed_lines_module.changed_lines(root)
            self.assertEqual(scope.lines, {})

    def test_an_untracked_entry_that_is_not_a_readable_file_is_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            repo = Path(tmp_dir) / "memory"
            root = repo / "onboarding"
            write(root, "example.md", history_document("- 2026-06-19T05:48+02:00: Recorded."))
            self.git(repo, "init", "-q")
            self.git(repo, "config", "user.email", "l6@example.invalid")
            self.git(repo, "config", "user.name", "L6")
            self.git(repo, "add", "-A")
            self.git(repo, "commit", "-qm", "before")
            (root / "dangling.md").symlink_to(root / "nothing-here.md")
            scope = changed_lines_module.changed_lines(root)
            self.assertEqual(scope.lines, {})

    def test_a_directory_named_like_a_document_is_not_read_as_one(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir) / "onboarding"
            (root / "route.md").mkdir(parents=True)
            write(root, "real.md", document("ordinary prose"))
            for check in (diff_markers, tables, history_order):
                result = check.check_onboarding_root(root)
                self.assertEqual(result["filesChecked"], 1, check.CHECK_NAME)

    def test_a_fence_indented_past_three_spaces_is_not_a_fence(self) -> None:
        self.assertIsNone(inline_scan.fence_delimiter("    ```python"))
        self.assertIsNotNone(inline_scan.fence_delimiter("   ```python"))

    def test_a_fence_between_a_header_and_its_delimiter_row_means_no_table(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir) / "onboarding"
            write(root, "example.md", document("| a | b |", "```", "```", "| - | - |", "| 1 |"))
            self.assertTrue(tables.check_onboarding_root(root)["ok"])

    def test_a_fence_inside_a_table_ends_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir) / "onboarding"
            write(
                root,
                "example.md",
                document("| a | b |", "| - | - |", "| 1 | 2 |", "```", "```", "| 3 |"),
            )
            self.assertTrue(tables.check_onboarding_root(root)["ok"])


if __name__ == "__main__":
    unittest.main()
