"""Precision and recall for citation resolution over memory documents (260731-EFA-L6).

:class:`FalsePositiveFixtures` is the point of this file. Each mode enumerated in
``style/citations/range_resolution.py``'s docstring is a case below, built from a construct
that really exists in this tree, and each must stay unreported.

The other half is the opposite duty: every finding code this check can emit has a case that
provokes it and asserts the message is useful (L6-R16). :class:`BiteTests` holds that shut
by requiring the two sets to be equal, so a code added without a probe fails here.

:class:`DeletedClassTests` proves the two findings R27 made unrepresentable are gone rather
than dormant.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.memory_quality.check import (
    STYLE_CHECKS,
    DriftCheckContext,
    StyleCheckInputs,
    run_memory_quality_check,
)
from agents_remember.memory_quality.style.citations import (
    cells,
    model,
    prose,
    range_resolution,
    resolution,
)
from agents_remember.memory_quality.style.finding import QualityFinding

CARD_HEADER = (
    "# {path}",
    "",
    "| Field | Value |",
    "| --- | --- |",
    "| repository | agents-remember |",
    "| path | `{path}` |",
    "",
    "## Repo-Internal References",
    "",
    "| Finding | Anchor | Source |",
    "| --- | --- | --- |",
)
SUPERSEDED_HEADER = (
    "| Finding | Citations | Source Path |",
    "| --- | --- | --- |",
)
EVERY_CODE = frozenset(
    {
        "citation_table_columns_wrong",
        "citation_source_malformed",
        "citation_source_duplicate",
        "citation_anchor_missing",
        "citation_source_missing",
        "citation_range_out_of_bounds",
        "citation_anchor_absent_from_range",
        "citation_prose_malformed",
        "citation_prose_not_in_cit_form",
        "citation_prose_form_in_table_cell",
        "citation_source_vanished",
    }
)


def document(*rows: str, path: str) -> str:
    header = "\n".join(line.format(path=path) for line in CARD_HEADER)
    return header + "\n" + "\n".join(rows) + "\n"


def numbered(count: int, *, marker: str = "line") -> str:
    return "\n".join(f"const {marker}{index} = {index};" for index in range(1, count + 1)) + "\n"


class Tree:
    """A memory repository and the code repository it documents, both on disk."""

    def __init__(self, root: Path) -> None:
        self.code = root / "code"
        self.memory = root / "memory"
        self.onboarding = self.memory / "onboarding"
        self.code.mkdir(parents=True)
        self.onboarding.mkdir(parents=True)

    def write(self, base: Path, relative: str, body: str) -> Path:
        path = base / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
        return path

    def source(self, relative: str, body: str) -> Path:
        return self.write(self.code, relative, body)

    def memory_file(self, relative: str, body: str) -> Path:
        return self.write(self.memory, relative, body)

    def card(self, source_path: str, *rows: str, at: str | None = None) -> Path:
        return self.write(
            self.onboarding, at or f"{source_path}.md", document(*rows, path=source_path)
        )

    def run(self) -> dict:
        return range_resolution.check_onboarding_root(self.onboarding, self.code)


class TreeCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tree = Tree(Path(self._tmp.name))

    def assert_clean(self, result: dict) -> None:
        self.assertTrue(result["ok"], result["findings"])
        self.assertEqual(result["findingCount"], 0, result["findings"])
        self.assertEqual(result["reportOnlyFindingCount"], 0, result["reportOnlyFindings"])

    def codes(self, result: dict) -> list[str]:
        return [one["code"] for one in result["findings"]]


class FalsePositiveFixtures(TreeCase):
    """Every mode the module docstring enumerates, on a construct that exists in the tree."""

    def test_1_a_word_boundary_is_not_satisfied_by_a_longer_identifier(self) -> None:
        """`SERVED` must not pass on `SERVED_LIFECYCLE`, nor `taskName` on `enclosureTaskName`."""
        self.tree.source("serving/lifecycle.py", "SERVED_LIFECYCLE = 1\nenclosureTaskName = 2\n")
        self.tree.card(
            "serving/caller.py",
            "| The two names. | `SERVED`; `taskName` | serving/lifecycle.py:1-2 |",
        )
        result = self.tree.run()
        self.assertEqual(self.codes(result), ["citation_anchor_absent_from_range"] * 2)
        messages = " ".join(one["message"] for one in result["findings"])
        self.assertIn("the range holds ['SERVED_LIFECYCLE']", messages)
        self.assertIn("`taskName`", messages)

    def test_1b_the_same_names_pass_when_the_range_really_holds_them(self) -> None:
        self.tree.source("serving/lifecycle.py", "SERVED = 1\ntaskName = 2\n")
        self.tree.card(
            "serving/caller.py",
            "| The two names. | `SERVED`; `taskName` | serving/lifecycle.py:1-2 |",
        )
        self.assert_clean(self.tree.run())

    def test_2_a_backticked_span_that_is_not_identifier_shaped_is_not_an_anchor(self) -> None:
        """``rail-row-*`` is a CSS class prefix; the quoted form is how the claim is written."""
        self.tree.source("panels/rail.css", ".rail-row-worker { display: flex; }\n")
        self.tree.card(
            "panels/RailChat.tsx",
            '| The row grammar. | `rail-row-*`; "rail-row-" | panels/rail.css:1-1 |',
        )
        result = self.tree.run()
        self.assert_clean(result)
        self.assertEqual(result["uncheckedSpans"], 1)

    def test_3_a_symbol_cited_where_it_is_used_rather_than_declared(self) -> None:
        """Presence, never definition: the ~70% false-positive rule is not the one applied."""
        self.tree.source(
            "kernel/git_command.py", "def run_git():\n    pass\n" + numbered(40) + "run_git()\n"
        )
        self.tree.card(
            "kernel/git_facts.py",
            "| Git invocations are delegated to the shared runner. | `run_git` "
            "| kernel/git_command.py:43-43 |",
        )
        self.assert_clean(self.tree.run())

    def test_4_two_ranges_one_anchor_are_pooled_not_paired(self) -> None:
        """``PUBLIC_TOOLS`` cited with a sub-range pointing into its own body."""
        body = numbered(20) + "export const PUBLIC_TOOLS = [\n" + numbered(60) + "];\n"
        self.tree.source("mcp/tools/base.py", body)
        self.tree.card(
            "mcp/tools/base.py",
            "| The two terminal-catalog public tools. | `PUBLIC_TOOLS` "
            "| mcp/tools/base.py:21-77; mcp/tools/base.py:23-24 |",
        )
        self.assert_clean(self.tree.run())

    def test_5_a_row_that_resolves_nothing_is_not_measured(self) -> None:
        """A citation into a dependency's source is in neither tree, so it is counted."""
        self.tree.card(
            "cli/dashboard.py",
            '| The refusal is loud, not silent. | "You must pass the application as an '
            'import string" | uvicorn/main.py:604-607 |',
        )
        result = self.tree.run()
        self.assert_clean(result)
        self.assertEqual(result["unresolvedSources"], 1)
        self.assertEqual(result["rowsWithNoResolvedCitation"], 1)
        self.assertEqual(result["resolvedCitations"], 0)

    def test_6_the_tables_empty_state_is_not_a_citation(self) -> None:
        """``| No cross-repo references found. | — | — |`` names nothing and cites nothing."""
        self.tree.card(
            "kernel/anchor.py",
            "| No meaningful cross-repo references found. | — | — |",
            "| Nothing configured. | n/a | N/A |",
            "| Nothing at all. | - | |",
        )
        result = self.tree.run()
        self.assert_clean(result)
        self.assertEqual(result["citationRows"], 3)

    def test_7_a_citation_table_quoted_inside_a_fence_is_not_read(self) -> None:
        self.tree.card(
            "kernel/anchor.py",
            "",
            "```markdown",
            "| Finding | Anchor | Source |",
            "| --- | --- | --- |",
            "| Quoted. | `gone` | kernel/anchor.py:900-999 |",
            "```",
        )
        result = self.tree.run()
        self.assert_clean(result)
        self.assertEqual(result["citationTables"], 1)

    def test_a_metadata_table_is_not_an_evidence_table(self) -> None:
        self.tree.write(
            self.tree.onboarding,
            "kernel/plain.py.md",
            "# plain\n\n| Field | Value |\n| --- | --- |\n| repository | agents-remember |\n",
        )
        result = self.tree.run()
        self.assert_clean(result)
        self.assertEqual(result["citationTables"], 0)

    def test_a_row_shorter_than_its_header_is_skipped_rather_than_guessed(self) -> None:
        lines = [
            "| Finding | Anchor | Source |",
            "| --- | --- | --- |",
            "| Only two cells. | `x` |",
            "| Full row. | `x` | a.py:1-2 |",
        ]
        rows = cells.citation_tables(lines)[0].rows
        self.assertEqual([row.line for row in rows], [4])


class TableFormatTests(TreeCase):
    """The superseded shape fails, and the message names the whole migration."""

    def superseded(self) -> dict:
        self.tree.source("kernel/route_index.py", numbered(200))
        self.tree.write(
            self.tree.onboarding,
            "kernel/route_index.py.md",
            "# card\n\n"
            + "\n".join(SUPERSEDED_HEADER)
            + "\n| The renderer consumes one snapshot. | L101-L149 "
            "| [route_index.py](route_index.py) |\n",
        )
        return self.tree.run()

    def test_a_citations_and_source_path_table_fails(self) -> None:
        result = self.superseded()
        self.assertFalse(result["ok"])
        self.assertEqual(self.codes(result), ["citation_table_columns_wrong"])
        self.assertEqual(result["tablesNotInCurrentFormat"], 1)

    def test_the_remediation_names_all_three_edits(self) -> None:
        """A header widened without its delimiter stops the construct being a table at all."""
        message = self.superseded()["findings"][0]["message"]
        self.assertIn("| Finding | Anchor | Source |", message)
        self.assertIn("DELIMITER row", message)
        self.assertIn("| --- | --- | --- |", message)
        self.assertIn("1 body row(s)", message)
        self.assertIn("<path>:<start>-<end>", message)
        self.assertIn("backticked code identifier", message)
        self.assertIn("memory repository", message)

    def test_a_two_column_finding_table_is_claimed_too(self) -> None:
        """397 tables in this tree cite a path with no range at all; they migrate as well."""
        self.tree.write(
            self.tree.onboarding,
            "panels/TaskNotes.tsx.md",
            "# card\n\n| Finding | Source Path |\n| --- | --- |\n"
            "| The data client. | [notes.ts](notes.ts) |\n",
        )
        self.assertEqual(self.codes(self.tree.run()), ["citation_table_columns_wrong"])

    def test_a_conforming_table_is_parsed_rather_than_reported(self) -> None:
        self.tree.source("kernel/route_index.py", "def build_route_indexes():\n    pass\n")
        self.tree.card(
            "kernel/route_index.py",
            "| The census freezes membership. | `build_route_indexes` "
            "| kernel/route_index.py:1-2 |",
        )
        result = self.tree.run()
        self.assert_clean(result)
        self.assertEqual(result["tablesNotInCurrentFormat"], 0)
        self.assertEqual(result["resolvedCitations"], 1)


class SourceGrammarTests(TreeCase):
    """`path:start-end` in plain text, and what is reported instead."""

    def plant(self, source: str) -> dict:
        self.tree.source("kernel/store.py", numbered(30))
        self.tree.card("kernel/caller.py", f"| The window. | `line4` | {source} |")
        return self.tree.run()

    def test_a_markdown_link_is_malformed(self) -> None:
        result = self.plant("[store.py](agents-remember/kernel/store.py)")
        self.assertEqual(self.codes(result), ["citation_source_malformed"])
        self.assertIn("not a citation", result["findings"][0]["message"])
        self.assertIn("never a markdown link", result["findings"][0]["message"])

    def test_a_parent_step_is_malformed_rather_than_climbed(self) -> None:
        self.assertEqual(
            self.codes(self.plant("../../kernel/store.py:1-4")), ["citation_source_malformed"]
        )

    def test_an_absolute_path_and_a_url_are_malformed(self) -> None:
        self.assertEqual(
            self.codes(self.plant("/kernel/store.py:1-4")), ["citation_source_malformed"]
        )
        self.assertEqual(
            self.codes(self.plant("https://example.com/store.py:1-4")),
            ["citation_source_malformed"],
        )

    def test_a_path_with_no_range_is_malformed(self) -> None:
        self.assertEqual(self.codes(self.plant("kernel/store.py")), ["citation_source_malformed"])

    def test_a_malformed_source_and_its_missing_anchor_are_reported_together(self) -> None:
        """Both are knowable from their own cells; the malformed source only hides bounds."""
        self.tree.source("kernel/store.py", numbered(30))
        self.tree.card(
            "kernel/caller.py",
            "| The window. | — | [store](kernel/store.py:1-999) |",
        )

        result = self.tree.run()

        self.assertEqual(
            set(self.codes(result)), {"citation_source_malformed", "citation_anchor_missing"}
        )
        self.assertNotIn("citation_range_out_of_bounds", self.codes(result))

    def test_a_single_line_citation_is_legal(self) -> None:
        self.assert_clean(self.plant("kernel/store.py:4"))

    def test_a_reversed_range_is_normalised_rather_than_inverted(self) -> None:
        found, _malformed = model.citations_in("a.py:90-10")
        self.assertEqual((found[0].start, found[0].end), (90, 90))

    def test_backticks_around_a_source_are_tolerated(self) -> None:
        self.assert_clean(self.plant("`kernel/store.py:1-4`"))

    def test_several_sources_are_separated_by_a_semicolon_or_a_middot(self) -> None:
        self.tree.source("kernel/store.py", numbered(30))
        self.tree.source("kernel/other.py", numbered(30))
        self.tree.card(
            "kernel/caller.py",
            "| Two files. | `line4` · `line7` | kernel/store.py:1-4; kernel/other.py:5-8 |",
        )
        result = self.tree.run()
        self.assert_clean(result)
        self.assertEqual(result["resolvedCitations"], 2)


class AnchorGrammarTests(TreeCase):
    """The three anchor kinds, each matched by the rule its kind implies."""

    def test_a_backticked_identifier_matches_whole_identifiers(self) -> None:
        self.tree.source("kernel/route_index.py", "def build_route_indexes():\n    pass\n")
        self.tree.card(
            "kernel/route_index.py",
            "| The census. | `build_route_indexes` | kernel/route_index.py:1-2 |",
        )
        self.assert_clean(self.tree.run())

    def test_a_backticked_heading_matches_a_whole_line_of_a_document(self) -> None:
        self.tree.memory_file(
            "system/coding-guidelines.md", "# Guidelines\n\n## File Size Budget\n\nRules.\n"
        )
        self.tree.card(
            "kernel/caller.py",
            "| Reclamation has two rules. | `## File Size Budget` "
            "| system/coding-guidelines.md:1-5 |",
        )
        self.assert_clean(self.tree.run())

    def test_a_heading_outside_the_range_is_reported(self) -> None:
        self.tree.memory_file(
            "system/coding-guidelines.md", "# Guidelines\n\n## File Size Budget\n\nRules.\n"
        )
        self.tree.card(
            "kernel/caller.py",
            "| Reclamation has two rules. | `## File Size Budget` "
            "| system/coding-guidelines.md:4-5 |",
        )
        self.assertEqual(self.codes(self.tree.run()), ["citation_anchor_absent_from_range"])

    def test_a_quoted_literal_matches_across_a_line_break(self) -> None:
        """The claim quotes one sentence; the source wraps it. Whitespace is normalised."""
        self.tree.source(
            "cli/dashboard.py",
            'log.warning(\n    "You must pass the application\\n"\n    "as an import string"\n)\n',
        )
        self.tree.card(
            "cli/dashboard.py",
            '| The refusal is loud. | "as an import string" | cli/dashboard.py:1-4 |',
        )
        self.assert_clean(self.tree.run())

    def test_a_curly_quoted_literal_is_an_anchor_too(self) -> None:
        self.tree.source("cli/dashboard.py", "raise SystemExit('import string required')\n")
        self.tree.card(
            "cli/dashboard.py",
            "| The refusal. | “import string required” | cli/dashboard.py:1-1 |",
        )
        self.assert_clean(self.tree.run())

    def test_a_quote_the_file_does_not_carry_at_all_says_so(self) -> None:
        """The near-miss evidence is identifier-shaped, so a quote gets the plain answer."""
        self.tree.source("cli/dashboard.py", "raise SystemExit(1)\n")
        self.tree.card(
            "cli/dashboard.py",
            '| The refusal. | "silently no-op" | cli/dashboard.py:1-1 |',
        )
        finding = self.tree.run()["findings"][0]
        self.assertEqual(finding["code"], "citation_anchor_absent_from_range")
        self.assertIn('names "silently no-op"', finding["message"])
        self.assertIn("cli/dashboard.py does not carry it anywhere", finding["message"])

    def test_a_quote_inside_a_code_span_is_not_a_quote_anchor(self) -> None:
        """``"launch" | "reset"`` is a TypeScript union, not two anchors."""
        anchors, skipped = model.anchors_in('`"launch" \\| "reset"`')
        self.assertEqual(anchors, ())
        self.assertEqual(skipped, 1)

    def test_a_separator_inside_a_code_span_does_not_split_a_source(self) -> None:
        self.assertEqual(model.split_segments("`a; b` x; `c` y"), ["`a; b` x", " `c` y"])

    def test_a_multi_backtick_span_keeps_its_contents(self) -> None:
        self.assertEqual(model.code_span_texts("``ariaLabel={`x`}``"), ["ariaLabel={`x`}"])

    def test_the_written_form_names_the_kind(self) -> None:
        self.assertEqual(model.Anchor(kind=model.SYMBOL, text="x").written, "`x`")
        self.assertEqual(model.Anchor(kind=model.QUOTE, text="x").written, '"x"')


class BoundsTests(TreeCase):
    """A range past the end of the file its own citation names."""

    def plant(self, cited: str, lines: int = 30) -> dict:
        self.tree.source("controlplane/store.py", numbered(lines))
        self.tree.card("controlplane/caller.py", f"| The window. | `line4` | {cited} |")
        return self.tree.run()

    def test_a_range_past_the_end_of_the_file_fails(self) -> None:
        result = self.plant("controlplane/store.py:468-492")
        self.assertFalse(result["ok"])
        finding = result["findings"][0]
        self.assertEqual(finding["code"], "citation_range_out_of_bounds")
        self.assertEqual(finding["severity"], "error")
        self.assertEqual(finding["path"], "controlplane/caller.py.md")
        self.assertIn("controlplane/store.py:468-492", finding["message"])
        self.assertIn("462 line(s) past", finding["message"])
        self.assertIn("which has 30", finding["message"])

    def test_the_message_says_what_it_means_and_who_clears_it(self) -> None:
        message = self.plant("controlplane/store.py:468-492")["findings"][0]["message"]
        self.assertIn("memory document, not code", message)
        self.assertIn("nothing is broken in the build", message)
        self.assertIn("Clear it in the memory repository", message)

    def test_the_last_line_of_the_file_is_in_bounds(self) -> None:
        self.assert_clean(self.plant("controlplane/store.py:1-30"))

    def test_bounds_are_per_citation_never_pooled(self) -> None:
        """The format glues each range to its own path, so one good file cannot cover another."""
        self.tree.source("a/long.py", numbered(200))
        self.tree.source("a/short.py", numbered(5))
        self.tree.card(
            "a/caller.py",
            "| Two files. | `line3` | a/long.py:1-100; a/short.py:1-100 |",
        )
        findings = self.tree.run()["findings"]
        self.assertEqual([one["code"] for one in findings], ["citation_range_out_of_bounds"])
        self.assertIn("a/short.py", findings[0]["message"])

    def test_the_offender_list_is_complete_and_worst_first(self) -> None:
        self.tree.source("a/target.py", numbered(10))
        for name, cited in (("one", "1-20"), ("two", "1-400"), ("three", "1-60")):
            self.tree.card(f"a/{name}.py", f"| {name}. | `line3` | a/target.py:{cited} |")
        findings = self.tree.run()["findings"]
        self.assertEqual([one["code"] for one in findings], ["citation_range_out_of_bounds"] * 3)
        self.assertEqual(
            [one["path"] for one in findings], ["a/two.py.md", "a/three.py.md", "a/one.py.md"]
        )

    def test_a_directory_named_like_a_document_is_skipped(self) -> None:
        (self.tree.onboarding / "not-a-file.md").mkdir(parents=True)
        self.assertEqual(self.tree.run()["filesChecked"], 0)


class AnchorPresenceTests(TreeCase):
    """The anchor half, and the hard facts a finding owes the curator."""

    def plant(self, anchor: str, cited: str) -> dict:
        self.tree.source(
            "data/stream.ts",
            numbered(40) + 'source.addEventListener("ready", onReady);\n' + numbered(20),
        )
        self.tree.card("data/seatEvents.ts", f"| The transport. | {anchor} | {cited} |")
        return self.tree.run()

    def test_an_anchor_absent_from_its_range_fails(self) -> None:
        result = self.plant("`ready`", "data/stream.ts:20-36")
        self.assertFalse(result["ok"])
        finding = result["findings"][0]
        self.assertEqual(finding["code"], "citation_anchor_absent_from_range")
        self.assertEqual(finding["severity"], "error")
        self.assertIn("names `ready`", finding["message"])

    def test_the_finding_names_the_lines_that_do_carry_the_anchor(self) -> None:
        """A range that starts right and stops short is the measured defect shape."""
        message = self.plant("`ready`", "data/stream.ts:20-36")["findings"][0]["message"]
        self.assertIn("data/stream.ts carries it at line(s) [41]", message)

    def test_the_finding_names_the_two_honest_edits(self) -> None:
        message = self.plant("`ready`", "data/stream.ts:20-36")["findings"][0]["message"]
        self.assertIn("widen the range", message)
        self.assertIn("name the anchor the range actually holds", message)

    def test_an_anchor_the_file_does_not_carry_at_all_says_so(self) -> None:
        message = self.plant("`RailChat`", "data/stream.ts:1-5")["findings"][0]["message"]
        self.assertIn("does not carry it anywhere", message)

    def test_an_anchor_inside_the_range_passes(self) -> None:
        self.assert_clean(self.plant("`ready`", "data/stream.ts:38-44"))

    def test_an_anchor_satisfied_by_the_rows_other_range_passes(self) -> None:
        self.assert_clean(self.plant("`ready`", "data/stream.ts:1-4; data/stream.ts:38-44"))

    def test_an_unresolved_range_cannot_satisfy_an_anchor_and_is_not_measured(self) -> None:
        result = self.plant("`ready`", "data/stream.ts:38-44; uvicorn/main.py:604-607")
        self.assert_clean(result)
        self.assertEqual(result["unresolvedSources"], 1)
        self.assertEqual(result["resolvedCitations"], 1)


class PairingTests(TreeCase):
    """Half a citation. Neither half means anything alone."""

    def test_a_row_that_cites_lines_and_names_no_anchor_fails(self) -> None:
        self.tree.source("kernel/store.py", numbered(30))
        self.tree.card("kernel/caller.py", "| The window. | — | kernel/store.py:1-4 |")
        result = self.tree.run()
        finding = result["findings"][0]
        self.assertEqual(finding["code"], "citation_anchor_missing")
        self.assertIn("nothing says what those lines are supposed to contain", finding["message"])
        self.assertIn("double-quoted literal", finding["message"])

    def test_a_row_whose_only_backticked_span_is_not_an_anchor_fails(self) -> None:
        self.tree.source("panels/rail.css", numbered(20, marker="rule"))
        self.tree.card(
            "panels/RailChat.tsx", "| The row grammar. | `rail-row-*` | panels/rail.css:1-4 |"
        )
        result = self.tree.run()
        self.assertEqual(self.codes(result), ["citation_anchor_missing"])
        self.assertEqual(result["uncheckedSpans"], 1)

    def test_a_row_that_names_an_anchor_and_cites_nothing_fails(self) -> None:
        self.tree.card("kernel/caller.py", "| The window. | `run_git` | — |")
        finding = self.tree.run()["findings"][0]
        self.assertEqual(finding["code"], "citation_source_missing")
        self.assertIn("There is no rangeless row", finding["message"])


class ResolutionTests(TreeCase):
    """Two roots, tried in one order."""

    def trees(self) -> resolution.Trees:
        return resolution.Trees(code_root=self.tree.code, memory_root=self.tree.memory)

    def test_the_code_repository_is_tried_first(self) -> None:
        code = self.tree.source("README.md", "code\n")
        self.tree.memory_file("README.md", "memory\n")
        self.assertEqual(self.trees().resolve("README.md"), code)

    def test_the_memory_repository_is_tried_second(self) -> None:
        target = self.tree.memory_file("system/tools.md", "x\n")
        self.assertEqual(self.trees().resolve("system/tools.md"), target)

    def test_a_path_in_neither_tree_resolves_to_nothing(self) -> None:
        self.assertIsNone(self.trees().resolve("uvicorn/main.py"))

    def test_the_memory_root_is_the_onboarding_roots_parent(self) -> None:
        self.tree.memory_file("system/tools.md", "DEFAULT_CRAP_THRESHOLD\n")
        self.tree.card(
            "kernel/caller.py",
            "| The threshold. | `DEFAULT_CRAP_THRESHOLD` | system/tools.md:1-1 |",
        )
        self.assert_clean(self.tree.run())


class BiteTests(TreeCase):
    """L6-R16: every code this check can emit has a probe that provokes it."""

    def test_one_document_provokes_every_finding_code(self) -> None:
        self.tree.source("kernel/store.py", numbered(30))
        self.tree.card(
            "kernel/caller.py",
            "| Out of bounds. | `line4` | kernel/store.py:468-492 |",
            "| Absent anchor. | `absentName` | kernel/store.py:1-4 |",
            "| No anchor. | — | kernel/store.py:1-4 |",
            "| No source. | `line4` | — |",
            "| Malformed. | `line4` | [store.py](store.py) |",
            "| Duplicate. | `line4` | kernel/store.py:1-4; kernel/store.py:1-4 |",
            "| The file moved. | `line4` | kernel/gone.py:1-4 |",
            "| Wrong form cit:([`line4`], kernel/store.py:1-4). | — | — |",
            "",
            "The prose half: cit:([`line4`] kernel/store.py:1-4) drops its comma, and",
            "`line4` (L1-L4) is the superseded spelling.",
            at="kernel/caller.py.md",
        )
        self.tree.write(
            self.tree.onboarding,
            "kernel/old.py.md",
            "# old\n\n" + "\n".join(SUPERSEDED_HEADER) + "\n| Old. | L1 | [a](a.py) |\n",
        )
        result = self.tree.run()
        self.assertFalse(result["ok"])
        self.assertEqual(set(self.codes(result)), EVERY_CODE)

    def test_the_shipped_codes_are_exactly_the_probed_ones(self) -> None:
        """A code added without a probe fails here rather than shipping unproven."""
        module = (
            MCP_SRC / "agents_remember/memory_quality/style/citations/range_resolution.py"
        ).read_text(encoding="utf-8")
        shipped = {
            line.split('"')[1]
            for line in module.splitlines()
            if line.strip().startswith('"citation_')
        }
        self.assertEqual(shipped, EVERY_CODE)


class ProseGrammarTests(TreeCase):
    """`cit:([anchors], path:start-end)` in running text, sharing every table rule."""

    def prose(self, *body: str) -> dict:
        self.tree.source("kernel/route_index.py", "def build_route_indexes():\n    pass\n" * 30)
        self.tree.card("kernel/caller.py", "| Nothing here. | — | — |", "", *body)
        return self.tree.run()

    def test_a_well_formed_citation_resolves_and_passes(self) -> None:
        result = self.prose(
            "The census freezes membership cit:([`build_route_indexes`], "
            "kernel/route_index.py:1-2) and nothing else does."
        )
        self.assert_clean(result)
        self.assertEqual(result["proseCitations"], 1)
        self.assertEqual(result["resolvedCitations"], 1)

    def test_several_anchors_and_several_sources_pool(self) -> None:
        self.tree.source("kernel/other.py", "def pass_through():\n    return 1\n")
        result = self.prose(
            "Both halves cit:([`build_route_indexes`, `pass_through`], "
            "kernel/route_index.py:1-2; kernel/other.py:1-2) hold."
        )
        self.assert_clean(result)
        self.assertEqual(result["resolvedCitations"], 2)

    def test_a_citation_that_wraps_across_a_line_is_one_construct(self) -> None:
        result = self.prose(
            "The census freezes tracked membership and path-rule eligibility, which is",
            "cit:([`build_route_indexes`],",
            "kernel/route_index.py:1-2) and nothing wider.",
        )
        self.assert_clean(result)
        self.assertEqual(result["proseCitations"], 1)

    def test_a_heading_and_a_quoted_anchor_work_the_same_in_prose(self) -> None:
        self.tree.memory_file("system/coding-guidelines.md", "# G\n\n## File Size Budget\n\nx\n")
        result = self.prose(
            "Reclamation cit:([`## File Size Budget`], system/coding-guidelines.md:1-5) "
            'and cit:(["def build_route_indexes"], kernel/route_index.py:1-2) both hold.'
        )
        self.assert_clean(result)
        self.assertEqual(result["proseCitations"], 2)

    def test_a_quoted_anchor_may_carry_the_brackets_the_parser_counts(self) -> None:
        """`matching` steps over quotes whole, so `]` and `)` inside one do not close it."""
        self.tree.source("cli/dashboard.py", 'raise SystemExit("exit(1) [fatal]")\n')
        result = self.prose('The refusal cit:(["exit(1) [fatal]"], cli/dashboard.py:1-1) is loud.')
        self.assert_clean(result)
        self.assertEqual(result["proseCitations"], 1)

    def test_an_out_of_bounds_prose_range_fails_with_the_shared_code(self) -> None:
        result = self.prose(
            "The census cit:([`build_route_indexes`], kernel/route_index.py:1-900)."
        )
        self.assertEqual(self.codes(result), ["citation_range_out_of_bounds"])

    def test_an_absent_prose_anchor_fails_with_the_shared_code(self) -> None:
        result = self.prose("The census cit:([`absentName`], kernel/route_index.py:1-2).")
        self.assertEqual(self.codes(result), ["citation_anchor_absent_from_range"])

    def test_an_empty_anchor_list_fails_with_the_shared_code(self) -> None:
        result = self.prose("The census cit:([], kernel/route_index.py:1-2).")
        self.assertEqual(self.codes(result), ["citation_anchor_missing"])

    def test_a_prose_source_in_the_superseded_spelling_is_malformed(self) -> None:
        result = self.prose(
            "The census cit:([`build_route_indexes`], [route_index.py](kernel/route_index.py))."
        )
        self.assertEqual(self.codes(result), ["citation_source_malformed"])

    def test_a_citation_missing_its_comma_or_its_brackets_does_not_parse(self) -> None:
        for written in (
            "cit:([`build_route_indexes`] kernel/route_index.py:1-2)",
            "cit:(`build_route_indexes`, kernel/route_index.py:1-2)",
            "cit:([`build_route_indexes`, kernel/route_index.py:1-2)",
        ):
            self.setUp()
            result = self.prose(f"The census {written} says so.")
            self.assertEqual(self.codes(result), ["citation_prose_malformed"], written)
            self.assertIn("`cit:` prefix is mandatory", result["findings"][0]["message"])

    def test_an_unclosed_citation_does_not_parse(self) -> None:
        result = self.prose("The census cit:([`build_route_indexes`], kernel/route_index.py:1-2 .")
        self.assertEqual(self.codes(result), ["citation_prose_malformed"])

    def test_a_citation_inside_a_code_span_is_not_a_citation(self) -> None:
        result = self.prose("Write it as `cit:([`x`], a.py:1-2)` in the card.")
        self.assert_clean(result)
        self.assertEqual(result["proseCitations"], 0)

    def test_a_citation_inside_a_fence_is_not_scanned(self) -> None:
        result = self.prose("```", "cit:([`gone`], kernel/route_index.py:900-999)", "```")
        self.assert_clean(result)
        self.assertEqual(result["proseCitations"], 0)

    def test_prose_inside_a_table_row_is_the_tables_business(self) -> None:
        """Table lines are excluded from the prose scan, so a row is read once."""
        self.tree.source("kernel/route_index.py", "def build_route_indexes():\n    pass\n")
        self.tree.card(
            "kernel/caller.py",
            "| The census. | `build_route_indexes` | kernel/route_index.py:1-2 |",
        )
        result = self.tree.run()
        self.assert_clean(result)
        self.assertEqual(result["proseCitations"], 0)
        self.assertEqual(result["resolvedCitations"], 1)


class MisplacedSerialisationTests(TreeCase):
    """A `cit:` in a table cell is the wrong serialisation, and silence there is the defect."""

    def test_a_cit_written_into_a_finding_cell_is_reported(self) -> None:
        self.tree.source("kernel/route_index.py", "def build_route_indexes():\n    pass\n")
        self.tree.card(
            "kernel/caller.py",
            "| The census cit:([`build_route_indexes`], kernel/route_index.py:1-2). | `x` | — |",
        )
        result = self.tree.run()
        codes = self.codes(result)
        self.assertIn("citation_prose_form_in_table_cell", codes)
        message = next(one["message"] for one in result["findings"] if one["code"] == codes[0])
        self.assertIn("Anchor and Source columns", message)
        self.assertIn("delete the `cit:`", message)

    def test_it_is_reported_once_per_row_however_many_it_holds(self) -> None:
        self.tree.card(
            "kernel/caller.py",
            "| Two of them cit:([`a`], a.py:1-2) and cit:([`b`], b.py:1-2). | — | — |",
        )
        self.assertEqual(self.codes(self.tree.run()), ["citation_prose_form_in_table_cell"])

    def test_a_backticked_cit_in_a_table_cell_is_a_quotation(self) -> None:
        """Documenting the grammar inside the documents it governs has to stay possible."""
        self.tree.card(
            "kernel/caller.py",
            "| Write it as `cit:([`x`], a.py:1-2)` in prose. | — | — |",
        )
        self.assert_clean(self.tree.run())

    def test_a_cit_in_a_superseded_table_is_reported_too(self) -> None:
        self.tree.write(
            self.tree.onboarding,
            "kernel/old.py.md",
            "# old\n\n"
            + "\n".join(SUPERSEDED_HEADER)
            + "\n| Old cit:([`x`], a.py:1-2). | L1 | [a](a.py) |\n",
        )
        self.assertEqual(
            sorted(set(self.codes(self.tree.run()))),
            ["citation_prose_form_in_table_cell", "citation_table_columns_wrong"],
        )

    def test_a_cit_inside_a_fenced_table_is_not_reported(self) -> None:
        self.tree.card(
            "kernel/caller.py",
            "",
            "```markdown",
            "| Finding | Anchor | Source |",
            "| --- | --- | --- |",
            "| Quoted cit:([`x`], a.py:1-2). | — | — |",
            "```",
        )
        self.assert_clean(self.tree.run())


class SupersededProseTests(TreeCase):
    """The spelling `cit:` replaces, and the leaf shorthand it must not swallow."""

    def prose(self, *body: str) -> dict:
        self.tree.card("kernel/caller.py", "| Nothing here. | — | — |", "", *body)
        return self.tree.run()

    def test_the_four_measured_shapes_are_all_reported(self) -> None:
        for written in (
            "The reader `task_reopen` (L11) opens it.",
            "The pair `SeriesSubTaskNode` (L380-L387) moved.",
            "The count is `> 0` only (`Cockpit.tsx` L775-L795); recorded.",
            "The seam (`store.ts`, L110-L112) is the one.",
        ):
            self.setUp()
            result = self.prose(written)
            self.assertEqual(self.codes(result), ["citation_prose_not_in_cit_form"], written)
            self.assertIn("the anchor beside it", result["findings"][0]["message"])

    def test_a_quoted_test_name_beside_a_range_is_an_anchor_too(self) -> None:
        result = self.prose('The case "workspace rollup — the handoff" (L436-L469) pins it.')
        self.assertEqual(self.codes(result), ["citation_prose_not_in_cit_form"])

    def test_a_bare_two_endpoint_range_is_reported_as_denoting_nothing(self) -> None:
        result = self.prose("Added its Repo-Internal References row (L36-L52) and realised.")
        self.assertEqual(self.codes(result), ["citation_prose_not_in_cit_form"])
        self.assertIn("names NO anchor", result["findings"][0]["message"])

    def test_the_leaf_identifier_shorthand_is_not_a_citation(self) -> None:
        """`(L4)` means leaf 4 here. No grammar separates it from a line, so it is counted."""
        result = self.prose(
            "The HFX-L6 split (L4) landed, 260731-EFA-L2 reformatted, and 260703 L3 shipped."
        )
        self.assert_clean(result)
        self.assertEqual(result["uncheckedProseRanges"], 1)

    def test_a_parenthesised_range_with_a_word_in_front_of_it_is_prose(self) -> None:
        """`(since L11)`, `(now L368-L375)` and `(slice L5)` are all live in this tree."""
        result = self.prose("It has been so (since L11), moved (now L368-L375), one (slice L5).")
        self.assert_clean(result)
        self.assertEqual(result["uncheckedProseRanges"], 0)

    def test_a_range_inside_a_code_span_is_not_a_citation(self) -> None:
        result = self.prose("The span `L18-L103` starts at the hoisted block.")
        self.assert_clean(result)


class ProseScannerTests(unittest.TestCase):
    """The block builder and the bracket walker, which nothing else exercises directly."""

    def test_blocks_break_at_a_blank_line_and_keep_their_line_numbers(self) -> None:
        found = prose.blocks(["one", "two", "", "four"])
        self.assertEqual([one.text for one in found], ["one two", "four"])
        self.assertEqual(found[0].line_at(0), 1)
        self.assertEqual(found[0].line_at(5), 2)
        self.assertEqual(found[1].line_at(0), 4)

    def test_a_document_ending_mid_block_still_yields_it(self) -> None:
        self.assertEqual([one.text for one in prose.blocks(["tail"])], ["tail"])

    def test_matching_steps_over_code_spans_and_quotes_whole(self) -> None:
        text = '([`f(x)`, "a)b"], p.py:1-2)'
        spans = model.inline_scan.code_span_ranges(text)
        self.assertEqual(model.matching(text, 0, spans, ")"), len(text) - 1)

    def test_matching_answers_none_when_nothing_closes(self) -> None:
        self.assertEqual(model.matching("(a", 0, [], ")"), None)

    def test_an_unterminated_quote_advances_rather_than_hanging(self) -> None:
        self.assertEqual(model.skip_quoted('"abc', 0), 1)


class DeletedClassTests(TreeCase):
    """L6-R13: the two classes R27 made unrepresentable are gone, not dormant."""

    def test_no_link_depth_or_beyond_named_source_code_survives_anywhere(self) -> None:
        package = MCP_SRC / "agents_remember/memory_quality/style/citations"
        body = "\n".join(path.read_text(encoding="utf-8") for path in sorted(package.rglob("*.py")))
        self.assertNotIn("citation_link_depth_wrong", body)
        self.assertNotIn("citation_range_beyond_named_source", body)
        self.assertNotIn("one-level-too-deep", body)

    def test_a_parent_step_can_no_longer_reach_a_file_at_a_shallower_depth(self) -> None:
        """The link-depth class needed a relative link that climbs. The grammar refuses one."""
        self.tree.source("dashboard/webtui-scope.config.cjs", "x\n")
        self.tree.card(
            "dashboard/src/styles/webtui.css",
            "| The scoping options. | `x` | ../../../webtui-scope.config.cjs:1-1 |",
        )
        self.assertEqual(self.codes(self.tree.run()), ["citation_source_malformed"])

    def test_a_range_is_never_measured_against_a_file_the_row_did_not_name(self) -> None:
        """`imported at LNN` needed the documenting card's own source in the pool. It is not."""
        self.tree.source("panels/MarkdownBlock.tsx", numbered(88))
        self.tree.source("panels/InteractionItem.tsx", numbered(101))
        self.tree.card(
            "panels/InteractionItem.tsx",
            "| Streaming-safe renderer used for the prompt body. | `line87` "
            "| panels/MarkdownBlock.tsx:87-91 |",
        )
        result = self.tree.run()
        self.assertEqual(self.codes(result), ["citation_range_out_of_bounds"])
        self.assertIn("panels/MarkdownBlock.tsx", result["findings"][0]["message"])


class StyleSurfaceTests(unittest.TestCase):
    """How the check reaches the gate, and what it says when it cannot resolve."""

    def test_the_check_is_one_entry_in_the_one_style_mapping(self) -> None:
        self.assertIn(range_resolution.CHECK_NAME, STYLE_CHECKS)
        self.assertEqual(range_resolution.CHECK_NAME, "style.citations.range_resolution")

    def test_without_a_code_root_the_result_says_so_instead_of_passing_quietly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "onboarding"
            root.mkdir(parents=True)
            result = run_memory_quality_check(root)
            block = result["checks"][range_resolution.CHECK_NAME]
            self.assertEqual(block["status"], "no-code-repository-root")
            self.assertEqual(block["filesChecked"], 0)

    def test_the_drift_context_is_what_supplies_the_code_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tree = Tree(Path(tmp))
            tree.source("a/target.py", numbered(10))
            tree.card("a/one.py", "| One. | `line3` | a/target.py:1-20 |")
            result = run_memory_quality_check(
                tree.onboarding,
                checks=[range_resolution.CHECK_NAME],
                drift_context=DriftCheckContext(code_repository_root=tree.code, context=None),
            )
            self.assertFalse(result["ok"])
            self.assertEqual(
                [one["code"] for one in result["findings"]], ["citation_range_out_of_bounds"]
            )

    def test_an_onboarding_only_check_still_receives_just_the_tree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "onboarding"
            root.mkdir(parents=True)
            (root / "example.md").write_text("# Example\n\n+## Broken\n", encoding="utf-8")
            result = STYLE_CHECKS["style.document_shape.diff_markers"](StyleCheckInputs(root))
            self.assertEqual(result["findingCount"], 1)

    def test_no_baseline_allowlist_or_exemption_lives_in_this_package(self) -> None:
        """L6-R12: nothing here may skip a path, a code or a count."""
        package = MCP_SRC / "agents_remember" / "memory_quality" / "style" / "citations"
        banned = ("baseline", "allowlist", "allow_list", "exempt", "grandfather", "ratchet")
        for path in sorted(package.rglob("*.py")):
            body = "\n".join(
                line
                for line in path.read_text(encoding="utf-8").splitlines()
                if not line.lstrip().startswith("#")
            )
            for marker in banned:
                self.assertNotIn(f"{marker.upper()} = ", body, f"{path.name} declares a {marker}")
                self.assertNotIn(f"def {marker}", body, f"{path.name} defines a {marker}")

    def test_nothing_here_reports_rather_than_gates(self) -> None:
        """R17 withdrew the report-only half; both halves fail the gate now."""
        package = MCP_SRC / "agents_remember" / "memory_quality" / "style" / "citations"
        for path in sorted(package.rglob("*.py")):
            self.assertNotIn("report_only", path.read_text(encoding="utf-8"), path.name)


class OrderingTests(unittest.TestCase):
    def test_a_finding_with_no_overrun_sorts_after_every_overrun(self) -> None:
        deep = QualityFinding(
            check=range_resolution.CHECK_NAME,
            path="a.md",
            line=1,
            severity="error",
            code="citation_range_out_of_bounds",
            message="ends 3 line(s) past the end of a.py",
        )
        flat = QualityFinding(
            check=range_resolution.CHECK_NAME,
            path="a.md",
            line=2,
            severity="error",
            code="citation_table_columns_wrong",
            message="the format is `Finding | Anchor | Source`",
        )
        self.assertEqual(range_resolution.worst_first([flat, deep]), [deep, flat])
        self.assertEqual(range_resolution.overshoot(flat), 0)


if __name__ == "__main__":
    unittest.main()
