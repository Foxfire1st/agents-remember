"""Tests for path citations in package comments and docstrings.

The repository test proves the scanner watches a non-empty production population.
Synthetic bites cover missing/moved targets and out-of-bounds lines; known-good fixtures
pin string operands, placeholders, external/runtime/sidecar paths, bare filenames, URLs,
and code-span backslashes. Offender tests require complete remediation text.
"""

from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember_test_support.code_quality import citations

PACKAGE_ROOT = MCP_SRC / "agents_remember"
REPO_ROOT = MCP_SRC.parents[1]
ANCHORS = citations.anchors(REPO_ROOT, PACKAGE_ROOT)


def _cited(text: str) -> list[str]:
    """The in-grammar citations the rule finds in a docstring, as their raw text."""
    source = f'"""{text}"""\n'
    found = citations.citations_in_source(source, ast.parse(source), "fixture.py", ANCHORS)
    return [citation.text for citation in found]


def _commented(text: str) -> list[str]:
    """The same, for a ``#`` comment."""
    source = f"# {text}\nX = 1\n"
    found = citations.citations_in_source(source, ast.parse(source), "fixture.py", ANCHORS)
    return [citation.text for citation in found]


class CitationResolutionTests(unittest.TestCase):
    """The armed check. It runs in the ordinary suite, so it runs wherever the suite does."""

    def test_every_cited_path_in_the_package_resolves(self) -> None:
        offenders = citations.unresolved_citations(REPO_ROOT, PACKAGE_ROOT)
        self.assertEqual(
            [str(offender) for offender in offenders],
            [],
            msg=citations.report(
                offenders,
                headline="prose cites a path this repository does not have",
                remediation=citations.CITATION_REMEDIATION,
            ),
        )

    def test_the_check_is_actually_watching_something(self) -> None:
        # A grammar tightened until nothing is in scope passes forever and proves nothing.
        # 155 citations / 82 distinct measured at the time of writing; assert an order of
        # magnitude, not the exact number, so ordinary edits do not fail this.
        found = citations.all_citations(REPO_ROOT, PACKAGE_ROOT)
        self.assertGreater(len(found), 100)
        self.assertGreater(len({citation.path for citation in found}), 50)

    def test_the_anchors_are_derived_from_the_tree_not_written_down(self) -> None:
        for expected in ("kernel", "serving", "worktrees", "controlplane", "mcp", "dashboard"):
            self.assertIn(expected, ANCHORS)
        # Dotted and cache directories are never anchors.
        self.assertFalse([name for name in ANCHORS if name.startswith(".")])
        self.assertNotIn("__pycache__", ANCHORS)


class CitationGrammarTests(unittest.TestCase):
    """The grammar has to reach a broken citation, or the check is a no-op."""

    def test_a_moved_module_is_caught(self) -> None:
        # The event this check exists for, and one that really happened in this leaf:
        # reopen.py moved out of tasks/ into worktrees/. `tasks` is still a real package
        # directory, so the stale pointer is squarely in grammar.
        self.assertEqual(_cited("see ``tasks/reopen.py`` for the reset"), ["tasks/reopen.py"])
        self.assertIsNone(
            citations.resolve(
                citations.Citation("f.py", 1, "tasks/reopen.py"), (REPO_ROOT, PACKAGE_ROOT)
            )
        )

    def test_a_citation_resolves_against_the_package_root(self) -> None:
        target = citations.resolve(
            citations.Citation("f.py", 1, "kernel/git_command.py"), (REPO_ROOT, PACKAGE_ROOT)
        )
        self.assertIsNotNone(target)

    def test_a_citation_resolves_against_the_repository_root(self) -> None:
        target = citations.resolve(
            citations.Citation("f.py", 1, "dashboard/src/panels/FlowTab.tsx"),
            (REPO_ROOT, PACKAGE_ROOT),
        )
        self.assertIsNotNone(target)

    def test_citations_are_found_in_comments_as_well_as_docstrings(self) -> None:
        self.assertEqual(_commented("see kernel/git_command.py"), ["kernel/git_command.py"])

    def test_a_line_anchor_is_parsed_and_carried(self) -> None:
        self.assertEqual(_cited("``kernel/git_command.py:12``"), ["kernel/git_command.py:12"])
        self.assertEqual(_cited("``kernel/git_command.py:12-30``"), ["kernel/git_command.py:12-30"])

    def test_a_line_anchor_past_end_of_file_is_reported(self) -> None:
        source = (
            '"""see ``mcp/test_support/agents_remember_test_support/'
            'code_quality/citations.py:99999`` for the grammar."""\n'
        )
        offenders = citations.module_citation_offenders(
            source,
            ast.parse(source),
            "fixture.py",
            roots=(REPO_ROOT, PACKAGE_ROOT),
            known=ANCHORS,
        )
        self.assertEqual(len(offenders), 1)
        self.assertEqual(offenders[0].form, "line past end of file")

    def test_an_unresolved_in_grammar_citation_is_reported_with_its_text(self) -> None:
        # The offender path itself: in grammar (`tasks` is a real package directory) and
        # resolving nowhere. The armed sweep above passes by finding none of these, so
        # without this test the reporting branch never executes.
        source = '"""the reset lives in ``tasks/reopen.py`` now."""\n'
        offenders = citations.module_citation_offenders(
            source,
            ast.parse(source),
            "fixture.py",
            roots=(REPO_ROOT, PACKAGE_ROOT),
            known=ANCHORS,
        )
        self.assertEqual(len(offenders), 1)
        self.assertEqual(offenders[0].form, "unresolved citation")
        self.assertIn("tasks/reopen.py", offenders[0].detail)

    def test_a_resolving_citation_beside_an_unresolved_one_is_left_alone(self) -> None:
        source = '"""``kernel/git_command.py`` is the runner; ``tasks/reopen.py`` is gone."""\n'
        offenders = citations.module_citation_offenders(
            source,
            ast.parse(source),
            "fixture.py",
            roots=(REPO_ROOT, PACKAGE_ROOT),
            known=ANCHORS,
        )
        self.assertEqual(len(offenders), 1)
        self.assertIn("tasks/reopen.py", offenders[0].detail)

    def test_a_source_that_cannot_be_tokenized_does_not_crash_the_scan(self) -> None:
        # prose_spans tokenizes for comments; a source it cannot tokenize must degrade to
        # "no comments found" rather than take the whole sweep down.
        spans = citations.prose_spans("x = '''unterminated\n", ast.parse(""))
        self.assertEqual(spans, [])

    def test_a_line_anchor_inside_the_file_is_not_reported(self) -> None:
        source = '"""see ``code_quality/citations.py:5`` for the grammar."""\n'
        offenders = citations.module_citation_offenders(
            source,
            ast.parse(source),
            "fixture.py",
            roots=(REPO_ROOT, PACKAGE_ROOT),
            known=ANCHORS,
        )
        self.assertEqual(offenders, [])

    def test_every_source_extension_is_in_grammar(self) -> None:
        # SOURCE_EXTENSIONS builds CITATION_PATTERN, so this asserts the alternation works
        # rather than that two hand-written lists agree.
        for ext in citations.SOURCE_EXTENSIONS:
            self.assertEqual(_cited(f"``kernel/thing.{ext}``"), [f"kernel/thing.{ext}"], ext)


class CitationFalsePositiveTests(unittest.TestCase):
    """Measured known-good constructs. None of these may EVER be reported."""

    def test_illustrative_placeholders_are_out_of_grammar(self) -> None:
        # kernel/sidecar_pairing.py explains the sidecar layout with meaningless segments.
        # They name nothing on purpose, which is exactly why the anchor rule skips them.
        self.assertEqual(_cited("a source ``a/b/c.py`` pairs with ``a/b/overview.index.json``"), [])
        self.assertEqual(_cited("``a/overview.index.json`` governs ``a/b/c.py``"), [])
        self.assertEqual(_cited("for example x/y/z.py and foo/bar.md"), [])

    def test_runtime_artifact_paths_are_out_of_grammar(self) -> None:
        # Files that exist only once something has RUN. Reporting these would be four
        # permanent failures no edit could clear, which is how a check gets ignored.
        for artifact in (
            "workspace/events.jsonl",
            "system/settings.json",
            "provider-runtime/provider-state.json",
            "quality/complexity-baseline.txt",
        ):
            self.assertEqual(_cited(f"written to ``{artifact}`` at run time"), [], artifact)

    def test_third_party_and_provider_runtime_citations_are_out_of_grammar(self) -> None:
        # Correct, useful, and unresolvable in this tree: they point into installed
        # dependencies and provider runtimes, not into this repository.
        for external in (
            "uvicorn/main.py",
            "uvicorn/_subprocess.py",
            "supervisors/basereload.py",
            "tiktoken_ext/openai_public.py",
            "cgc/seed.py",
            "grepai/lifecycle/backend.py",
            "grepai/seed.py",
            "context/__init__.py",
            "viz/server.py",
            "app-server-protocol/src/protocol/v2/thread.rs",
        ):
            self.assertEqual(_cited(f"mirrors ``{external}``"), [], external)

    def test_the_one_external_path_that_collides_with_a_package_directory(self) -> None:
        # `cli/cli_helpers.py` is CGC's, but `cli` is also a real subpackage here, so the
        # anchor rule cannot exclude it. What does is rule 1: in the real tree it appears
        # inside an error-message string in providers/cgc/context/patches.py, which is an
        # operand and not prose. Written as prose it WOULD be reported -- documented as
        # false-positive mode 5 rather than left to be discovered by whoever hits it.
        operand = (
            "def check(text):\n"
            "    raise ContextProviderError(\n"
            '        "CGC cli/cli_helpers.py did not match the expected snippet"\n'
            "    )\n"
        )
        found = citations.citations_in_source(operand, ast.parse(operand), "fixture.py", ANCHORS)
        self.assertEqual([citation.text for citation in found], [])
        self.assertEqual(_cited("CGC's ``cli/cli_helpers.py``"), ["cli/cli_helpers.py"])

    def test_string_operands_are_data_not_citations(self) -> None:
        # benchmarks/runner_modules/constants.py and install/runtime.py map source paths to
        # destinations for a GENERATED workspace. Neither points at anything to open.
        source = (
            "AGENTS_MD_TARGETS = {\n"
            '    Path("runtime/agents-md-files/system/AGENTS.md"): Path("system/AGENTS.md"),\n'
            '    "agents-md-files/coordinator/AGENTS.md": "AGENTS.md",\n'
            "}\n"
            'TEMPLATE = Path("templates/workspace-AGENTS.md")\n'
        )
        found = citations.citations_in_source(source, ast.parse(source), "fixture.py", ANCHORS)
        self.assertEqual([citation.text for citation in found], [])

    def test_a_bare_module_citation_without_a_directory_is_out_of_grammar(self) -> None:
        # The class measured and dropped: a large minority of these tokens name no file in
        # the tree. A directory separator is required precisely to exclude them.
        for prose in (
            "Coverage.py writes the JSON report",
            "see CGC_REPORT.md",
            "app.py:329 merges the tail",
            "reducer.py holds the projection",
        ):
            self.assertEqual(_cited(prose), [], prose)

    def test_a_url_does_not_yield_a_bare_path_citation(self) -> None:
        # The trailing/leading guards must stop a match starting mid-path. Both forms carry
        # a separator AND a source extension, so the guards are the only thing excluding them.
        self.assertEqual(_cited("https://github.com/org/repo/blob/main/kernel/git_command.py"), [])
        self.assertEqual(_cited("see http://example.com/docs/thing.md for the upstream spec"), [])

    def test_memory_sidecar_paths_are_out_of_grammar(self) -> None:
        # Rule 4. These name onboarding documents in the EXTERNAL memory repository, which
        # mirrors each source file as `<source>.md`. Their first segment is a real top-level
        # directory of this repository, so only the doubled extension excludes them -- and
        # without that, every prose pointer into onboarding is an unfixable finding.
        for prose in (
            "see dashboard/src/panels/EngineRoom.tsx.md for the rendered table",
            "the sidecar mcp/src/agents_remember/observer/reducer.py.md records it",
            "dashboard/src/dev/benchProbes.ts.md holds the union",
            "AGENTS.md.md is the sidecar for the root brief",
        ):
            self.assertEqual(_cited(prose), [], prose)
        # And the source file itself, one extension shorter, is still a citation.
        self.assertEqual(
            _cited("see dashboard/src/panels/EngineRoom.tsx"),
            ["dashboard/src/panels/EngineRoom.tsx"],
        )

    def test_a_code_span_containing_a_backslash_does_not_derail_the_scan(self) -> None:
        # A previous shape checker in this repository mis-parsed exactly this.
        self.assertEqual(
            _cited(r"``text.replace('\\\\', '/')`` normalises, see ``kernel/git_command.py``"),
            ["kernel/git_command.py"],
        )

    def test_an_unparseable_or_empty_module_does_not_crash_the_sweep(self) -> None:
        found = citations.citations_in_source("", ast.parse(""), "fixture.py", ANCHORS)
        self.assertEqual(found, [])


class OffenderReportTests(unittest.TestCase):
    """L6-R15: the message names every offender and the fix, or the check is unusable."""

    def test_the_message_carries_the_whole_list_and_the_remediation(self) -> None:
        offenders = [
            citations.Offender("a/one.py", 3, "unresolved citation", "cites tasks/reopen.py"),
            citations.Offender("b/two.py", 9, "unresolved citation", "cites kernel/gone.py"),
        ]
        message = citations.report(
            offenders, headline="prose cites missing paths", remediation="point it at the file"
        )
        self.assertIn("(2 found)", message)
        self.assertIn("a/one.py:3", message)
        self.assertIn("b/two.py:9", message)
        self.assertIn("remediation: point it at the file", message)

    def test_the_remediation_names_both_ways_out(self) -> None:
        self.assertIn("point the citation at the file that exists", citations.CITATION_REMEDIATION)
        self.assertIn("delete it", citations.CITATION_REMEDIATION)


if __name__ == "__main__":
    unittest.main()
